# Lunchkollen

Dagens lunch i närheten, i en lista som sorteras på avstånd och pris samtidigt.

## Så hänger delarna ihop

```
Varje vardag 07:20                         Hela dagen
┌──────────────────────────┐               ┌──────────────────────┐
│ GitHub startar           │   sparar      │ Hemsidan (index.html)│
│ scripts/hamta_menyer.py  │ ───────────▶  │ läser filerna och    │
│ • besöker lunchsidorna   │ data/         │ visar listan för     │
│ • tolkar menyerna        │ menyer.json   │ besökaren            │
└──────────────────────────┘               └──────────────────────┘
          ▲
          │ läser
   data/restauranger.json  (registret, det enda du behöver redigera)
```

| Fil | Vad den gör |
|---|---|
| `index.html` | Själva hemsidan. |
| `data/restauranger.json` | Registret: namn, adress, koordinater, lunchtid och var menyn finns. |
| `data/menyer.json` | Dagens menyer. Skrivs om automatiskt varje morgon. Innehåller exempeldata tills första körningen. |
| `scripts/hamta_menyer.py` | Morgonprogrammet som hämtar och tolkar menyerna. |
| `scripts/bygg_register.py` | Registerprogrammet som hittar nya restauranger på OpenStreetMap och letar upp deras lunchsidor. |
| `scripts/requirements.txt` | Lista över Python-paket som programmen behöver. |
| `.github/workflows/morgon.yml` | Schemat som kör morgonprogrammet varje vardag. |
| `.github/workflows/register.yml` | Schemat som kör registerprogrammet den 1:a varje månad. |

---

## Lägg upp sidan, steg för steg

Du behöver inte installera något på datorn. Allt görs i webbläsaren. Knapparnas namn på GitHub kan skilja sig lite från beskrivningen nedan.

### 1. Skaffa ett GitHub-konto
Gå till github.com och skapa ett gratis konto. GitHub är en tjänst där man sparar kod. Där kommer både hemsidan och morgonprogrammet att bo.

### 2. Skapa ett projekt (ett "repository")
1. Klicka på **+** uppe till höger och välj **New repository**.
2. Döp det till `lunchkollen`.
3. Välj **Public**. Det krävs för att publicera hemsidan gratis. Koden blir synlig för andra, men en eventuell AI-nyckel (steg 6) förblir hemlig.
4. Klicka **Create repository**.

### 3. Ladda upp filerna
1. Packa upp zip-filen på din dator.
2. Klicka på länken **uploading an existing file** på projektsidan.
3. Dra in `index.html`, `README.md` och mapparna `data` och `scripts`. Klicka **Commit changes**. En *commit* betyder ungefär "spara en version".
4. Mappen `.github` är dold på de flesta datorer eftersom namnet börjar med en punkt, så de två schemafilerna skapar du för hand:
   - Klicka **Add file → Create new file**.
   - Skriv filnamnet `.github/workflows/morgon.yml`. Snedstrecken skapar mapparna automatiskt.
   - Öppna `morgon.yml` från zip-filen i en textredigerare, kopiera allt och klistra in.
   - Klicka **Commit changes**.
   - Gör likadant med `.github/workflows/register.yml`.

### 4. Slå på hemsidan (GitHub Pages)
1. Gå till **Settings → Pages**.
2. Under *Source*, välj **Deploy from a branch**. Välj grenen **main** och mappen **/ (root)**. Klicka **Save**.
3. Efter någon minut ligger sidan på `https://DITT-ANVÄNDARNAMN.github.io/lunchkollen/`.

Nu fungerar sidan, med exempelmenyerna.

### 5. Kör morgonprogrammet första gången
1. Gå till fliken **Actions**. Om GitHub frågar, slå på workflows.
2. Klicka på **Hämta dagens luncher** i listan till vänster och sedan på **Run workflow**.
3. Klicka på körningen för att se loggen. Steget *Hämta och tolka menyerna* visar en rad per restaurang:
   - `✓` betyder att menyn hittades.
   - `?` betyder att sidan lästes, men inga rätter med pris hittades.
   - `–` betyder att det saknas en menyadress i registret.
   - `✗` betyder att något gick fel, och felet står på raden.
4. Ladda om hemsidan. Nu visas riktiga menyer, och exempelvarningen är borta.

Därefter sköter sig körningen själv varje vardag. Allt hittills är gratis.

### 5b. Hitta fler restauranger automatiskt
Registret innehåller från början tio restauranger. Registerprogrammet fyller på det med alla matställen i närheten som finns på OpenStreetMap.

1. Gå till **Actions → Uppdatera restaurangregistret → Run workflow**.
2. Välj radie i meter. 1000 är en bra start. Klicka **Run workflow**.
3. Loggen visar en rad per nytt ställe:
   - `+` betyder att en lunchsida hittades automatiskt.
   - `·` betyder att stället lades till, men utan menyadress. Orsaken står på raden.
4. Kör sedan **Hämta dagens luncher** igen, så hämtas menyer även för de nya ställena.

Programmet körs sedan automatiskt den 1:a varje månad och lägger då bara till ställen som är nya. Det du har ändrat för hand i registret rör det aldrig.

### 6. Valfritt: gör tolkningen smartare med AI
Programmet läser menyerna med **mönsterregler**: det letar upp dagens veckodag och rader med pris, som "Köttbullar med mos 145 kr". Det klarar menyer med tydlig struktur, men missar röriga menyer och kan ibland gissa fel på om en rätt är kött, fisk eller vegetarisk.

Om du märker att för många restauranger får `?` i loggen kan du låta AI tolka menyerna i stället. Det kostar lite per körning.

1. Gå till console.anthropic.com, skapa ett konto och fyll på en liten summa krediter.
2. Skapa en nyckel under **API keys** och kopiera den.
3. På GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
4. Namn: `ANTHROPIC_API_KEY`. Värde: nyckeln. Spara.

Programmet märker själv att nyckeln finns och byter läge. Överst i loggen står `Läge: AI (Claude)` eller `Läge: mönsterregler (gratis)`. Nyckeln syns aldrig för någon annan, inte ens i ditt publika projekt. Klistra aldrig in den direkt i en fil. Tar du bort nyckeln går programmet tillbaka till de gratis reglerna.

---

## Underhåll

**Lägga till eller rätta en restaurang:** Öppna `data/restauranger.json` på GitHub och klicka på pennan för att redigera. Kopiera ett befintligt block och ändra värdena. Koordinater hittar du i Google Maps: högerklicka på platsen, så visas siffrorna överst. Den första är `lat`, den andra `lng`. Glöm inte kommatecknet mellan blocken.

**Granska automatiskt tillagda restauranger:** Varje sådan har en `kommentar` som börjar med "Automatiskt tillagd" och säger vad programmet hittade. Börja med de som saknar `menyadress` eller där kommentaren säger "bara en allmän menylänk". Fyll i rätt adress, eller ta bort blocket om stället inte serverar lunch. `lunchtid` är tom för nya ställen, fyll i den om du vill att den syns på sidan.

**Fyra av de första restaurangerna saknar menyadress:** L'angolo, Dogtown, Proviant och Panini. Leta upp var de lägger sin lunch och fyll i `menyadress`.

**Varför finns ett ställe inte med?** Antingen saknas det på OpenStreetMap, eller så säger kartan att det bara har kvällsöppet. Du kan lägga till det för hand i registret. Den som vill kan också lägga till eller rätta det på openstreetmap.org, så får alla nytta av det.

**En restaurang visar fel eller ingenting:** Titta i loggen under Actions. Vanliga orsaker är att restaurangen har flyttat sin meny, bara lägger ut den på Instagram eller publicerar den som en bild. Bilder kan programmet inte läsa ännu.

**En restaurang får `?` trots att menyn finns:** Menyn är troligen skriven på ett sätt som reglerna inte känner igen, till exempel utan pris på samma rad som rätten. Antingen slår du på AI-läget (steg 6), eller så ber du mig anpassa reglerna. Klistra då in hur menytexten ser ut.

**Se vad programmet ser:** Om du kör `python scripts/hamta_menyer.py --torrkorning` på din egen dator visas texten som hämtas från varje sida, och vad reglerna hittar i den. Inget sparas.

## Senare
- **Egen adress:** Köp en domän, till exempel `lunchkollen.se`, och koppla den under **Settings → Pages → Custom domain**.
- **Fler restauranger:** Bygg ut område för område och håll koll på andelen `✓` i loggen.
- **Fråga restaurangerna:** Be om lov att visa deras lunch, med länk tillbaka. Det är både artigt och tryggare.
