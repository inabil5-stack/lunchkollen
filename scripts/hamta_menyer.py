"""
Morgonprogrammet för Lunchkollen.

Vad det gör, steg för steg:
  1. Läser restaurangregistret (data/restauranger.json).
  2. Går in på varje restaurangs lunchsida och hämtar texten.
     Om sidan länkar till en lunch-PDF hämtas texten ur den också.
  3. Tolkar texten och plockar ut dagens rätter och priser. Två lägen:
     - Gratis (standard): mönsterregler letar efter dagens veckodag och
       rader med pris, t.ex. "Köttbullar med mos 145 kr".
     - AI (valfritt): om ANTHROPIC_API_KEY finns tolkar Claude texten
       i stället. Det blir träffsäkrare på röriga menyer men kostar lite.
  4. Sparar resultatet i data/menyer.json, som hemsidan läser.

Kör så här:
  python scripts/hamta_menyer.py                 # vanlig körning
  python scripts/hamta_menyer.py --torrkorning   # visar bara hämtad text och vad reglerna hittar, sparar inget
"""

import datetime
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROT = Path(__file__).resolve().parent.parent
REGISTER = ROT / "data" / "restauranger.json"
UTFIL = ROT / "data" / "menyer.json"

USER_AGENT = "Lunchkollen/0.1 (samlar dagens lunch; kontakt via lunchkollen-projektet)"
AI_MODELL = "claude-haiku-5-5"   # en snabb och billig modell räcker för att läsa menyer
MAX_TECKEN = 15000                # så mycket text skickas till AI:n per restaurang
VECKODAGAR = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]

PDF_MARKOR = "--- Text från PDF"

# Ord som tyder på att en PDF-länk är en lunchmeny (t.ex. "V45_Svenska.pdf" eller "lunchmeny.pdf")
PDF_LEDTRADAR = re.compile(r"lunch|meny|menu|dagens|v\.?\s?\d{1,2}|vecka", re.IGNORECASE)


# ---------- Steg 2: hämta text ----------

def far_hamta(url: str) -> bool:
    """Kollar robots.txt, alltså sidans egna regler för automatiska besök."""
    delar = urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{delar.scheme}://{delar.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        return True  # ingen robots.txt går att läsa = inga uttalade regler
    return rp.can_fetch(USER_AGENT, url)


def hamta(url: str) -> requests.Response:
    svar = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    svar.raise_for_status()
    return svar


def text_ur_pdf(data: bytes) -> str:
    lasare = PdfReader(io.BytesIO(data))
    text = "\n".join((sida.extract_text() or "") for sida in lasare.pages)
    # Snygga till tecken som PDF:er ibland lagrar konstigt: "ﬀ" -> "ff", "/f_läsk" -> "fläsk"
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"/f_(?=\w)", "f", text)


def text_ur_html(html: str) -> tuple[str, list[str]]:
    """Returnerar sidans synliga text och en lista med länkar till PDF:er som ser ut som menyer."""
    soppa = BeautifulSoup(html, "html.parser")
    for tagg in soppa(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tagg.decompose()
    vecka = datetime.date.today().isocalendar().week
    kandidater = []
    for a in soppa.find_all("a", href=True):
        href, lanktext = a["href"], a.get_text(" ")
        if ".pdf" not in href.lower():
            continue
        bada = href + " " + lanktext
        if not (PDF_LEDTRADAR.search(href) or PDF_LEDTRADAR.search(lanktext)):
            continue
        # Poäng: "lunch" väger tyngst, sedan veckans nummer (t.ex. v45, v-45, V.45), sist "meny"
        poang = 0
        if re.search(r"lunch|dagens", bada, re.IGNORECASE):
            poang += 5
        if re.search(rf"(?<![a-z])(v|vecka|week)[\s._-]*{vecka}(?!\d)", bada, re.IGNORECASE):
            poang += 3
        if re.search(r"middag|dinner|vin|wine|dryck|drink|brunch|catering|julbord", bada, re.IGNORECASE):
            poang -= 4
        kandidater.append((poang, href))
    pdf_lankar = [h for p, h in sorted(kandidater, key=lambda k: -k[0]) if p >= 0]
    text = re.sub(r"\n\s*\n+", "\n", soppa.get_text("\n")).strip()
    return text, pdf_lankar


def hamta_menytext(url: str) -> str:
    svar = hamta(url)
    if "pdf" in svar.headers.get("Content-Type", "").lower() or url.lower().endswith(".pdf"):
        return text_ur_pdf(svar.content)

    text, pdf_lankar = text_ur_html(svar.text)
    # Hittade vi en meny-PDF? Hämta den första och lägg till texten.
    if pdf_lankar:
        pdf_url = urljoin(url, pdf_lankar[0])
        try:
            text += "\n\n" + PDF_MARKOR + " (" + pdf_url + ") ---\n" + text_ur_pdf(hamta(pdf_url).content)
        except Exception as fel:
            text += f"\n\n(Kunde inte läsa PDF:en {pdf_url}: {fel})"
    return text


# ---------- Steg 3a: tolka texten med mönsterregler (gratis) ----------

# Veckodagar som de kan stå i menyer: svenska, utan prickar, och engelska
DAGORD = [
    ["måndag", "mandag", "monday", "mån"],
    ["tisdag", "tuesday", "tis"],
    ["onsdag", "wednesday", "ons"],
    ["torsdag", "thursday", "tors"],
    ["fredag", "friday", "fre"],
    ["lördag", "lordag", "saturday"],
    ["söndag", "sondag", "sunday"],
]
# Ett pris: "145 kr", "145:-", "145 SEK", "145 kronor" eller ett ensamt tal sist på raden
PRIS = re.compile(r"(?<![\d:.,/-])(\d{2,3})\s*(?:kr\b|kr\.|:-|sek\b|kronor\b)|(?<![\d:.,/-])(\d{2,3})\s*$", re.IGNORECASE)
# En rad som BARA är ett pris, t.ex. "260" eller "195 kr"
BARA_PRIS = re.compile(r"^\s*(\d{2,3})\s*(?:kr\.?|:-|sek|kronor)?\s*$", re.IGNORECASE)
# Ord som visar att raden inte är en rätt
SKRAP = re.compile(r"öppet|öppettid|boka|telefon|tel\.|adress|e-?post|@|www\.|http|serveras|vecka\s*\d|"
                   r"inklusive|ingår|allergi|kontakta|följ oss|cookies|\b0\d{1,3}[- ]\d{2,3}", re.IGNORECASE)
# Rubriker för sådant som inte är lunchrätter; allt under dem hoppas över tills nästa rubrik
INTE_LUNCH = re.compile(r"sött|dessert|efterrätt|dryck|drink|tillbehör|barn|kids|frukost|brunch|middag|dinner", re.IGNORECASE)
# Ord som visar att rätten gäller hela veckan
VECKOORD = re.compile(r"hela veckan|veckans|alla dagar|varje dag|all week", re.IGNORECASE)
LUNCHORD = re.compile(r"lunch|dagens|veckans", re.IGNORECASE)
VEG_ORD = re.compile(r"\bveg\b|vegetari|vegan|halloumi|tofu|falafel|tempeh|linser|linsgryta|kikärt|svamprisotto|butternut|pumpa", re.IGNORECASE)
FISK_ORD = re.compile(r"fisk|lax|torsk|sill|strömming|räk|tonfisk|kolja|sej\b|spätta|skaldjur|mussl|gös|abborre|röding|"
                      r"hälleflundra|bläckfisk|scampi|krabb|hummer|kräft|fish|salmon|cod\b", re.IGNORECASE)
# En rad som slutar så här fortsätter på nästa rad ("mango &" / "chilisalsa, ...")
FORTSATTER = re.compile(r"(&|,|\boch|\bmed|\bsamt|\bpå|\bi)\s*$", re.IGNORECASE)


def las_dag(rad: str) -> tuple[int | None, str]:
    """Om raden börjar med en veckodag, t.ex. 'Måndag', 'TISDAG 3/11', 'Onsdag: Pasta 145 kr'
    eller 'Alltid på torsdagar', returneras (dagens nummer, resten av raden). 0 = måndag.
    Annars (None, raden)."""
    m = re.match(r"\s*(?:alltid\s+på\s+|varje\s+)?([a-zåäö]+)\.?\s*(?:\d{1,2}\s*/\s*\d{1,2}|\d{1,2}\s+\w+)?\s*[:\-–]?\s*(.*)$",
                 rad, re.IGNORECASE)
    if m:
        forsta = m.group(1).lower()
        if forsta.endswith("dagar"):
            forsta = forsta[:-2]  # "torsdagar" -> "torsdag"
        korta = {"mån", "tis", "ons", "tors", "fre"}
        for nr, ord_ in enumerate(DAGORD):
            # korta former som "mån" räknas bara om de står ensamma på raden
            if forsta in ord_ and (forsta not in korta or not m.group(2)):
                return nr, m.group(2).strip()
    return None, rad


def ar_dagrubrik(rad: str) -> bool:
    nr, rest = las_dag(rad)
    return nr is not None and not rest


def ar_versalrubrik(rad: str) -> bool:
    """'KUNG CARL LUNCH V.45', 'NÅT SÖTT', 'ALLTID PÅ CRÈME': kort rad med bara stora bokstäver."""
    bokstaver = [c for c in PRIS.sub("", rad) if c.isalpha()]
    return 3 <= len(bokstaver) and len(rad) <= 45 and all(c.isupper() for c in bokstaver)


def gissa_typ(namn: str) -> str:
    if VEG_ORD.search(namn):
        return "veg"
    if FISK_ORD.search(namn):
        return "fisk"
    return "kott"


def hitta_pris(rad: str) -> int | None:
    m = PRIS.search(rad)
    return int(m.group(1) or m.group(2)) if m else None


def tolka_med_regler(text: str, idag: datetime.date) -> list[dict]:
    """Om texten innehåller en meny-PDF tolkas den först, eftersom webbsidans egen text oftast
    bara är meny, sidfot och liknande. Hittas inget där tolkas hela texten."""
    if PDF_MARKOR in text:
        pdf_text = text.split(PDF_MARKOR, 1)[1].split("\n", 1)[-1]  # hoppa över raden med PDF-adressen
        ratter = tolka_text(pdf_text, idag)
        if ratter:
            return ratter
    return tolka_text(text, idag)


def tolka_text(text: str, idag: datetime.date) -> list[dict]:
    """Läser menytexten rad för rad och bygger 'block': en rätt kan bestå av flera rader,
    med priset på samma rad som namnet, på en egen rad efteråt, eller gemensamt för hela menyn."""
    rader = [re.sub(r"\s+", " ", r).strip() for r in text.splitlines()]
    rader = [r for r in rader if r and re.search(r"[a-zåäö0-9]", r, re.IGNORECASE)]

    # 1. Slå ihop rader som uppenbart fortsätter på nästa ("mango &" + "chilisalsa, ...")
    hopslagna = []
    for r in rader:
        if hopslagna and FORTSATTER.search(hopslagna[-1]) and not hitta_pris(hopslagna[-1]) \
                and las_dag(r)[0] is None and not BARA_PRIS.match(r) and not ar_versalrubrik(r):
            hopslagna[-1] += " " + r
        else:
            hopslagna.append(r)

    # 2. Gå igenom raderna och bygg block. Håll koll på vilken dag och vilken sorts avsnitt vi är i.
    block = []              # varje block: {"dag", "texter", "pris", "hoppa"}
    aktuellt = None
    dag = None
    hoppa_avsnitt = False   # sant under rubriker som "Nåt sött" eller "Dryck"
    vantande_pris = None    # t.ex. "Fredag 195" där rätten kommer på nästa rad
    gemensamt = None
    har_dagar = False

    def avsluta():
        nonlocal aktuellt
        if aktuellt and aktuellt["texter"]:
            block.append(aktuellt)
        aktuellt = None

    def nytt_block(rad, text, pris):
        nonlocal nummer_i_dag
        nummer_i_dag += 1
        return {"dag": dag, "texter": [text], "pris": pris, "hoppa": hoppa_avsnitt,
                "tillagg": rad.lstrip().startswith(("-", "+")), "nr_i_dag": nummer_i_dag,
                "efter_gemensamt": gemensamt is not None}

    nummer_i_dag = 0        # vilket block i ordningen inom dagens avsnitt
    for r in hopslagna:
        nr, rest = las_dag(r)
        if nr is not None:
            avsluta()
            dag, har_dagar, hoppa_avsnitt = nr, True, False
            nummer_i_dag = 0
            vantande_pris = None
            if not rest:
                continue
            r = rest
            if BARA_PRIS.match(r):
                vantande_pris = int(BARA_PRIS.match(r).group(1))
                continue

        if ar_versalrubrik(r):
            avsluta()
            pris = hitta_pris(r)
            if pris and LUNCHORD.search(r) and gemensamt is None:
                gemensamt = pris          # "LUNCH PÅ CRÈME – 180 kr"
            dag = None                    # en ny rubrik avslutar veckodagens avsnitt
            hoppa_avsnitt = bool(INTE_LUNCH.search(r))
            continue

        if SKRAP.search(r):
            continue

        if BARA_PRIS.match(r):
            pris = int(BARA_PRIS.match(r).group(1))
            if aktuellt and aktuellt["pris"] is None:
                aktuellt["pris"] = pris
                avsluta()
            else:
                avsluta()
                vantande_pris = pris
            continue

        pris = hitta_pris(r)
        utan_pris = PRIS.sub("", r).strip(" .:-–|•*")
        if pris is not None:
            # Kort rad med pris och ett lunchord = gemensamt pris, t.ex. "Dagens lunch 135:-"
            if len(utan_pris) < 18 and LUNCHORD.search(utan_pris) and not VECKOORD.search(utan_pris.split()[0] if utan_pris else ""):
                if gemensamt is None:
                    gemensamt = pris
                continue
            if re.fullmatch(r"(dagens|veckans)\s+(lunch|meny|rätt|pris)\W*", utan_pris, re.IGNORECASE):
                if gemensamt is None:
                    gemensamt = pris
                continue
            avsluta()
            aktuellt = nytt_block(r, utan_pris, pris)
        else:
            # Under en veckodag med gemensamt pris är varje ny rad med stor bokstav en ny rätt
            # ("Pasta carbonara" / "Halloumibiffar"), annars en fortsättning på beskrivningen
            ny_ratt = (aktuellt is not None and dag is not None and gemensamt is not None
                       and aktuellt["pris"] is None and utan_pris[:1].isupper())
            if aktuellt is None or ny_ratt:
                avsluta()
                aktuellt = nytt_block(r, utan_pris, vantande_pris)
                vantande_pris = None
            else:
                aktuellt["texter"].append(utan_pris)
    avsluta()

    # 3. Välj ut dagens block: rätter för idag, plus rätter som inte hör till någon särskild dag
    ratter = []
    for b in block:
        if b["hoppa"]:
            continue
        rubrik = b["texter"][0]
        veckoratt = bool(VECKOORD.search(rubrik)) and b["nr_i_dag"] > 1
        if b["dag"] is not None and b["dag"] != idag.weekday() and not veckoratt:
            continue
        if b["tillagg"]:
            continue  # tillägg som "- ett litet glas punsch"
        pris = b["pris"]
        if pris is None and (b["dag"] is not None or b["efter_gemensamt"]):
            pris = gemensamt
        if pris is None or not 60 <= pris <= 300:
            continue
        if len(rubrik) > 90 or len(rubrik.split()) > 14:
            continue  # löptext, inte en rätt
        texter = b["texter"]
        # "Veckans pasta" + beskrivning på nästa rad: använd beskrivningen som namn
        if len(texter) > 1 and re.fullmatch(r"(veckans|dagens)\s+\w+", rubrik, re.IGNORECASE):
            texter = texter[1:]
        delar = [texter[0]] + [t[0].lower() + t[1:] if len(t) > 1 and t[1].islower() else t for t in texter[1:]]
        namn = ", ".join(d.strip(" ,") for d in delar if d.strip(" ,"))
        namn = re.sub(r"^(kött|fisk|veg(etariskt|etarisk|ansk|an|etarian)?|dagens( rätt)?|meat|fish)\s*[:\-–]\s*",
                      "", namn, flags=re.IGNORECASE)
        if len(namn) < 12 or not re.search(r"[a-zåäö]{3}", namn, re.IGNORECASE):
            continue
        ratter.append({"typ": gissa_typ(rubrik + " " + namn), "namn": namn[:120], "pris": pris})
        if len(ratter) == 8:
            break
    return ratter


# ---------- Steg 3b: låt AI:n tolka texten (valfritt, kräver nyckel) ----------

def bygg_fraga(restaurang: str, idag: datetime.date, text: str) -> str:
    return f"""Här är texten från lunchsidan för restaurangen {restaurang}.
Idag är det {VECKODAGAR[idag.weekday()]} {idag.isoformat()} (vecka {idag.isocalendar().week}).

Plocka ut de lunchrätter som serveras IDAG. Om menyn gäller hela veckan:
ta med rätter som gäller alla dagar plus dem som gäller just idag.
Hitta inte på något. Står det inget pris, sätt "pris": null.
Gäller menyn en annan vecka än idag, svara med en tom lista.

Svara ENDAST med JSON i exakt detta format:
{{"ratter": [{{"typ": "kott" | "fisk" | "veg", "namn": "kort beskrivning på svenska", "pris": heltal i kronor eller null}}]}}

Text:
\"\"\"
{text[:MAX_TECKEN]}
\"\"\""""


def tolka_med_ai(klient, restaurang: str, idag: datetime.date, text: str) -> list[dict]:
    svar = klient.messages.create(
        model=AI_MODELL,
        max_tokens=1000,
        messages=[{"role": "user", "content": bygg_fraga(restaurang, idag, text)}],
    )
    rasvar = "".join(b.text for b in svar.content if b.type == "text")
    return las_ratter(rasvar)


def las_ratter(rasvar: str) -> list[dict]:
    """Plockar ut JSON ur AI:ns svar och kontrollerar att varje rätt ser rimlig ut."""
    traff = re.search(r"\{.*\}", rasvar, re.DOTALL)
    if not traff:
        return []
    data = json.loads(traff.group(0))
    ratter = []
    for r in data.get("ratter", []):
        rå_typ = str(r.get("typ", "")).lower()
        typ = "veg" if "veg" in rå_typ else "fisk" if "fisk" in rå_typ else "kott"
        pris = r.get("pris")
        if isinstance(pris, str):  # t.ex. "135" eller "135 kr"
            siffror = re.search(r"\d+", pris)
            pris = int(siffror.group(0)) if siffror else None
        pris = int(pris) if isinstance(pris, (int, float)) and 40 <= pris <= 600 else None
        namn = str(r.get("namn", "")).strip()
        if namn:
            ratter.append({"typ": typ, "namn": namn[:120], "pris": pris})
    return ratter


# ---------- Steg 1 och 4: läs registret, spara resultatet ----------

def main() -> None:
    torrkorning = "--torrkorning" in sys.argv
    nu = datetime.datetime.now(ZoneInfo("Europe/Stockholm"))
    idag = nu.date()
    restauranger = json.loads(REGISTER.read_text(encoding="utf-8"))

    # Finns en AI-nyckel används AI, annars de gratis mönsterreglerna
    klient = None
    if not torrkorning and os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        klient = anthropic.Anthropic()
        print("Läge: AI (Claude)\n")
    else:
        print("Läge: mönsterregler (gratis)\n")

    menyer = {}
    for r in restauranger:
        rid, namn, url = r["id"], r["namn"], r.get("menyadress", "").strip()
        if not url:
            print(f"–  {namn}: ingen menyadress i registret")
            menyer[rid] = {"status": "saknas", "ratter": [], "orsak": "ingen menyadress"}
            continue
        try:
            if not far_hamta(url):
                print(f"✗  {namn}: robots.txt säger nej, hoppar över")
                menyer[rid] = {"status": "saknas", "ratter": [], "orsak": "robots.txt"}
                continue
            text = hamta_menytext(url)
            if torrkorning:
                print(f"\n===== {namn} ({len(text)} tecken) =====\n{text[:1500]}\n")
                print("Reglerna hittade:", json.dumps(tolka_med_regler(text, idag), ensure_ascii=False, indent=1))
                continue
            ratter = tolka_med_ai(klient, namn, idag, text) if klient else tolka_med_regler(text, idag)
            status = "ok" if any(d["pris"] for d in ratter) else "saknas"
            print(f"{'✓' if status == 'ok' else '?'}  {namn}: {len(ratter)} rätter")
            menyer[rid] = {"status": status, "ratter": ratter, "kalla": url}
        except Exception as fel:
            print(f"✗  {namn}: {fel}")
            menyer[rid] = {"status": "fel", "ratter": [], "orsak": str(fel)[:200]}
        time.sleep(1)  # var snäll mot restaurangernas servrar

    if torrkorning:
        return

    UTFIL.write_text(json.dumps({
        "datum": idag.isoformat(),
        "uppdaterad": nu.isoformat(timespec="seconds"),
        "exempel": False,
        "menyer": menyer,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lyckade = sum(1 for m in menyer.values() if m["status"] == "ok")
    print(f"\nKlart: {lyckade} av {len(restauranger)} restauranger fick en meny. Sparat i {UTFIL.name}")


if __name__ == "__main__":
    main()
