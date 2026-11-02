"""
Morgonprogrammet för Lunchkollen.

Vad det gör, steg för steg:
  1. Läser restaurangregistret (data/restauranger.json).
  2. Går in på varje restaurangs lunchsida och hämtar texten.
     Om sidan länkar till en lunch-PDF hämtas texten ur den också.
  3. Skickar texten till Claude (AI) och ber om dagens rätter och priser
     i ett fast format.
  4. Sparar resultatet i data/menyer.json, som hemsidan läser.

Kör så här:
  python scripts/hamta_menyer.py                 # vanlig körning (kräver ANTHROPIC_API_KEY)
  python scripts/hamta_menyer.py --torrkorning   # hämtar bara texten och visar den, ingen AI, sparar inget
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


# ---------- Steg 3: låt AI:n tolka texten ----------

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

    klient = None
    if not torrkorning:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("Saknar ANTHROPIC_API_KEY. Lägg in nyckeln som en hemlighet (secret) på GitHub, se README.")
        import anthropic
        klient = anthropic.Anthropic()

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
                continue
            ratter = tolka_med_ai(klient, namn, idag, text)
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
