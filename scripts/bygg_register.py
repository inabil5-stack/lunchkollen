"""
Registerprogrammet för Lunchkollen.

Fyller på data/restauranger.json automatiskt. Körs sällan, t.ex. en gång i månaden.

Vad det gör, steg för steg:
  1. Frågar OpenStreetMap (en fri, öppen karta) efter alla restauranger,
     kaféer och snabbmatsställen inom en viss radie från en punkt.
  2. Sorterar bort ställen som enligt öppettiderna bara har öppet på kvällen.
  3. Går in på varje ny restaurangs hemsida och letar efter en länk
     till lunchmenyn ("lunch", "dagens", "meny" ...).
  4. Lägger till de nya restaurangerna i registret. Det som redan finns
     där, inklusive allt du har ändrat för hand, lämnas orört.

Kör så här:
  python scripts/bygg_register.py                      # 1 km runt Birger Jarlsgatan 25
  python scripts/bygg_register.py --radie 2000         # 2 km
  python scripts/bygg_register.py --lat 59.33 --lng 18.06 --radie 800
  python scripts/bygg_register.py --torrkorning        # visar vad som skulle läggas till, sparar inget
  python scripts/bygg_register.py --utan-hemsidor      # snabbare: letar inte efter lunchsidor
"""

import argparse
import json
import math
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Återanvänd hämtfunktionerna från morgonprogrammet (samma artighetsregler)
from hamta_menyer import USER_AGENT, far_hamta, hamta

ROT = Path(__file__).resolve().parent.parent
REGISTER = ROT / "data" / "restauranger.json"
OVERPASS = "https://overpass-api.de/api/interpreter"

START_LAT, START_LNG = 59.33675, 18.07175   # Birger Jarlsgatan 25
SAMMA_STALLE_METER = 60                      # så nära två ställen med samma namn får ligga för att räknas som samma


# ---------- Steg 1: fråga OpenStreetMap ----------

def hamta_fran_osm(lat: float, lng: float, radie: int) -> list[dict]:
    # Frågespråket heter Overpass QL. Det här betyder: "alla restauranger, kaféer och
    # snabbmatsställen med ett namn, inom <radie> meter från punkten. Ge mig deras taggar
    # och en mittpunkt."
    fraga = f"""
    [out:json][timeout:60];
    nwr["amenity"~"^(restaurant|cafe|fast_food)$"]["name"](around:{radie},{lat},{lng});
    out center tags;
    """
    svar = requests.post(OVERPASS, data={"data": fraga}, headers={"User-Agent": USER_AGENT}, timeout=90)
    svar.raise_for_status()
    stallen = []
    for el in svar.json().get("elements", []):
        t = el.get("tags", {})
        plats = el if "lat" in el else el.get("center", {})
        if "lat" not in plats:
            continue
        gata, nummer = t.get("addr:street", ""), t.get("addr:housenumber", "")
        stallen.append({
            "osm": f"{el['type']}/{el['id']}",
            "namn": t["name"],
            "adress": f"{gata} {nummer}".strip(),
            "lat": round(plats["lat"], 6),
            "lng": round(plats["lon"], 6),
            "hemsida": t.get("website") or t.get("contact:website") or "",
            "oppettider": t.get("opening_hours", ""),
        })
    return stallen


# ---------- Steg 2: lunch eller bara kväll? ----------

def bara_kvall(oppettider: str) -> bool:
    """Sant om alla öppningstider börjar kl 15 eller senare, t.ex. 'Mo-Sa 17:00-23:00'.
    Saknas öppettider vet vi inte, och då behåller vi stället."""
    starter = re.findall(r"(\d{1,2}):\d{2}\s*-", oppettider)
    return bool(starter) and all(int(h) >= 15 for h in starter)


# ---------- Steg 3: hitta lunchsidan på hemsidan ----------

LANKPOANG = [(re.compile(r"lunch", re.I), 5), (re.compile(r"dagens", re.I), 5),
             (re.compile(r"veckans|vecka", re.I), 2), (re.compile(r"meny|menu|mat\b|food", re.I), 1)]
DALIG_LANK = re.compile(r"instagram|facebook|tiktok|boka|book|bord|table|jobb|career|presentkort|gift|mailto:|tel:", re.I)


def hitta_lunchsida(hemsida: str) -> tuple[str, str]:
    """Returnerar (menyadress, förklaring). Tom adress om inget hittades."""
    if not hemsida.startswith("http"):
        hemsida = "https://" + hemsida
    if not far_hamta(hemsida):
        return "", "robots.txt säger nej"
    svar = hamta(hemsida)
    soppa = BeautifulSoup(svar.text, "html.parser")
    sidtext = soppa.get_text(" ").lower()

    basta, basta_poang = "", 0
    for a in soppa.find_all("a", href=True):
        href, text = a["href"], a.get_text(" ").strip()
        if DALIG_LANK.search(href) or DALIG_LANK.search(text):
            continue
        url = urljoin(svar.url, href)
        if urlparse(url).netloc and urlparse(url).netloc != urlparse(svar.url).netloc and not url.lower().endswith(".pdf"):
            continue  # länkar till andra sajter är sällan menyn (utom PDF:er)
        poang = sum(p for monster, p in LANKPOANG if monster.search(text) or monster.search(href))
        if poang > basta_poang:
            basta, basta_poang = url, poang

    if basta_poang >= 5:
        return basta, "lunchlänk hittad"
    if "dagens lunch" in sidtext or "veckans lunch" in sidtext or "lunchmeny" in sidtext:
        return svar.url, "lunchen verkar stå på startsidan"
    if basta:
        return basta, "bara en allmän menylänk, kontrollera att lunchen finns där"
    return "", "ingen lunchsida hittad"


# ---------- Steg 4: slå ihop med registret ----------

def avstand(lat1, lng1, lat2, lng2) -> float:
    R, rad = 6371000, math.radians
    h = math.sin(rad(lat2 - lat1) / 2) ** 2 + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(rad(lng2 - lng1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def forenkla(namn: str) -> str:
    """'Crème Stockholm' -> 'cremestockholm', för att kunna jämföra namn."""
    s = unicodedata.normalize("NFKD", namn.lower())
    return re.sub(r"[^a-z0-9]", "", s.encode("ascii", "ignore").decode())


def gor_id(namn: str, upptagna: set) -> str:
    bas = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-",
          unicodedata.normalize("NFKD", namn.lower()).encode("ascii", "ignore").decode())).strip("-") or "restaurang"
    rid, n = bas, 2
    while rid in upptagna:
        rid, n = f"{bas}-{n}", n + 1
    return rid


def finns_redan(stalle: dict, register: list[dict]) -> dict | None:
    for r in register:
        if r.get("osm") == stalle["osm"]:
            return r
        a, b = forenkla(r["namn"]), forenkla(stalle["namn"])
        liknande = a == b or (min(len(a), len(b)) >= 5 and (a in b or b in a))
        if liknande and avstand(r["lat"], r["lng"], stalle["lat"], stalle["lng"]) < SAMMA_STALLE_METER:
            return r
    return None


def main() -> None:
    p = argparse.ArgumentParser(description="Fyll på restaurangregistret från OpenStreetMap.")
    p.add_argument("--lat", type=float, default=START_LAT)
    p.add_argument("--lng", type=float, default=START_LNG)
    p.add_argument("--radie", type=int, default=1000, help="meter")
    p.add_argument("--torrkorning", action="store_true")
    p.add_argument("--utan-hemsidor", action="store_true")
    arg = p.parse_args()

    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    print(f"Registret har {len(register)} restauranger.")

    stallen = hamta_fran_osm(arg.lat, arg.lng, arg.radie)
    print(f"OpenStreetMap: {len(stallen)} matställen inom {arg.radie} m.\n")

    nya, kvall, kanda = [], 0, 0
    upptagna = {r["id"] for r in register}
    for s in sorted(stallen, key=lambda s: avstand(arg.lat, arg.lng, s["lat"], s["lng"])):
        befintlig = finns_redan(s, register)
        if befintlig:
            befintlig.setdefault("osm", s["osm"])  # kom ihåg kopplingen till kartan, ändrar inget annat
            kanda += 1
            continue
        if bara_kvall(s["oppettider"]):
            kvall += 1
            continue

        menyadress, forklaring = "", "ingen hemsida i kartdatan"
        if s["hemsida"] and not arg.utan_hemsidor:
            try:
                menyadress, forklaring = hitta_lunchsida(s["hemsida"])
            except Exception as fel:
                forklaring = f"hemsidan gick inte att öppna ({str(fel)[:80]})"
            time.sleep(1)
        elif s["hemsida"]:
            forklaring = "hemsidan inte undersökt"

        ny = {
            "id": gor_id(s["namn"], upptagna),
            "namn": s["namn"],
            "adress": s["adress"],
            "lat": s["lat"],
            "lng": s["lng"],
            "lunchtid": "",
            "menyadress": menyadress,
            "kommentar": f"Automatiskt tillagd: {forklaring}",
            "osm": s["osm"],
            "hemsida": s["hemsida"],
        }
        upptagna.add(ny["id"])
        nya.append(ny)
        print(f"{'+' if menyadress else '·'}  {s['namn']}: {forklaring}")

    print(f"\n{len(nya)} nya, {kanda} fanns redan, {kvall} hoppades över (bara kvällsöppet).")
    print(f"{sum(1 for n in nya if n['menyadress'])} av de nya fick en menyadress automatiskt.")

    if arg.torrkorning:
        print("Torrkörning: inget sparat.")
        return
    REGISTER.write_text(json.dumps(register + nya, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Sparat: registret har nu {len(register) + len(nya)} restauranger.")


if __name__ == "__main__":
    main()
