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
    return "\n".join((sida.extract_text() or "") for sida in lasare.pages)


def text_ur_html(html: str) -> tuple[str, list[str]]:
    """Returnerar sidans synliga text och en lista med länkar till PDF:er som ser ut som menyer."""
    soppa = BeautifulSoup(html, "html.parser")
    for tagg in soppa(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tagg.decompose()
    pdf_lankar = []
    for a in soppa.find_all("a", href=True):
        href = a["href"]
        if ".pdf" in href.lower() and (PDF_LEDTRADAR.search(href) or PDF_LEDTRADAR.search(a.get_text())):
            pdf_lankar.append(href)
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
            text += "\n\n--- Text från PDF (" + pdf_url + ") ---\n" + text_ur_pdf(hamta(pdf_url).content)
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
PRIS = re.compile(r"(?<![\d:.,/])(\d{2,3})\s*(?:kr\b|kr\.|:-|sek\b|kronor\b)|(?<![\d:.,/])(\d{2,3})\s*$", re.IGNORECASE)
# Ord som visar att raden inte är en rätt
SKRAP = re.compile(r"öppet|öppettid|boka|telefon|tel\.|adress|e-?post|@|www\.|http|serveras|vecka\s*\d|"
                   r"inklusive|ingår|allergi|kontakta|följ oss|cookies", re.IGNORECASE)
# Ord som visar att rätten gäller hela veckan
VECKOORD = re.compile(r"hela veckan|veckans|alla dagar|varje dag|all week", re.IGNORECASE)
VEG_ORD = re.compile(r"\bveg\b|vegetari|vegan|halloumi|tofu|falafel|tempeh|linser|linsgryta|kikärt|svamprisotto", re.IGNORECASE)
FISK_ORD = re.compile(r"fisk|lax|torsk|sill|strömming|räk|tonfisk|kolja|sej|spätta|skaldjur|mussl|gös|abborre|röding|hälleflundra|bläckfisk|fish|salmon|cod", re.IGNORECASE)


def las_dag(rad: str) -> tuple[int | None, str]:
    """Om raden börjar med en veckodag, t.ex. 'Måndag', 'TISDAG 3/11' eller 'Onsdag: Pasta 145 kr',
    returneras (dagens nummer, resten av raden). 0 = måndag. Annars (None, raden)."""
    m = re.match(r"\s*([a-zåäö]+)\.?\s*(?:\d{1,2}\s*/\s*\d{1,2}|\d{1,2}\s+\w+)?\s*[:\-–]?\s*(.*)$", rad, re.IGNORECASE)
    if m:
        forsta = m.group(1).lower()
        korta = {"mån", "tis", "ons", "tors", "fre"}
        for nr, ord_ in enumerate(DAGORD):
            # korta former som "mån" räknas bara om de står ensamma på raden
            if forsta in ord_ and (forsta not in korta or not m.group(2)):
                return nr, m.group(2).strip()
    return None, rad


def ar_dagrubrik(rad: str) -> bool:
    nr, rest = las_dag(rad)
    return nr is not None and not rest


def gissa_typ(namn: str) -> str:
    if VEG_ORD.search(namn):
        return "veg"
    if FISK_ORD.search(namn):
        return "fisk"
    return "kott"


def tolka_med_regler(text: str, idag: datetime.date) -> list[dict]:
    rader = [re.sub(r"\s+", " ", r).strip() for r in text.splitlines()]
    rader = [r for r in rader if r]

    # 1. Dela upp texten per veckodag, om menyn är uppdelad så.
    #    En dag kan stå som rubrik på egen rad, eller först på samma rad som rätten.
    dag_for_rad, rensade = [], []
    aktuell = None
    for r in rader:
        nr, rest = las_dag(r)
        if nr is not None:
            aktuell = nr
            if not rest:
                continue  # ren rubrik, ingen rätt på raden
            r = rest
        rensade.append(r)
        dag_for_rad.append(aktuell)
    rader = rensade
    har_dagar = any(d is not None for d in dag_for_rad)

    # 2. Välj ut de rader som gäller idag
    if har_dagar:
        if idag.weekday() not in dag_for_rad:
            return []  # menyn har dagar, men inte dagens (t.ex. helg eller fel vecka)
        valda = [r for r, d in zip(rader, dag_for_rad)
                 if d == idag.weekday() or (VECKOORD.search(r) and PRIS.search(r))]
    else:
        valda = rader

    # 3. Gemensamt pris? T.ex. en rad som bara säger "Dagens lunch 145 kr" eller "145:-".
    #    Den kan stå bland dagens rader eller överst, före första veckodagen.
    gemensamt = None
    fore_forsta_dag = [r for r, d in zip(rader, dag_for_rad) if d is None] if har_dagar else []
    for r in valda + fore_forsta_dag:
        m = PRIS.search(r)
        if m and len(PRIS.sub("", r).strip(" .:-–")) < 18 and 60 <= int(m.group(1) or m.group(2)) <= 300:
            gemensamt = int(m.group(1) or m.group(2))
            break

    # 4. Plocka ut rätterna
    ratter = []
    for r in valda:
        if SKRAP.search(r):
            continue
        m = PRIS.search(r)
        namn = PRIS.sub("", r).strip(" .:-–|•*")
        namn = re.sub(r"^(kött|fisk|veg(etariskt|etarisk|ansk|an|etarian)?|dagens( rätt)?|veckans \w+|meat|fish)\s*[:\-–]\s*",
                      "", namn, flags=re.IGNORECASE)
        if len(namn) < 12 or not re.search(r"[a-zåäö]{3}", namn, re.IGNORECASE):
            continue
        if re.fullmatch(r"(dagens|veckans)\s+(lunch|meny|rätt|pris)\W*", namn, re.IGNORECASE):
            continue  # en rubrik som "Veckans lunch, 125 kr", ingen rätt
        pris = int(m.group(1) or m.group(2)) if m else gemensamt
        if pris is None or not 60 <= pris <= 300:
            continue
        ratter.append({"typ": gissa_typ(r), "namn": namn[:120], "pris": pris})
        if len(ratter) == 6:
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
