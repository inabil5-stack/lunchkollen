# Lunchkollen

Dagens lunch i närheten, i en lista som sorteras på avstånd och pris samtidigt.

## Så hänger delarna ihop

```
Varje vardag 07:20                         Hela dagen
┌──────────────────────────┐               ┌──────────────────────┐
│ GitHub startar           │   sparar      │ Hemsidan (index.html)│
│ scripts/hamta_menyer.py  │ ───────────▶  │ läser filerna och    │
│ • besöker lunchsidorna   │ data/         │ visar listan för     │
│ • AI tolkar menyerna     │ menyer.json   │ besökaren            │
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
| `scripts/requirements.txt` | Lista över Python-paket som programmet behöver. |
| `.github/workflows/morgon.yml` | Schemat som får GitHub att köra programmet varje vardag. |

---

## Lägg upp sidan, steg för steg

Du behöver inte installera något på datorn. Allt görs i webbläsaren. Knapparnas namn på GitHub kan skilja sig lite från beskrivningen nedan.

### 1. Skaffa ett GitHub-konto
Gå till github.com och skapa ett gratis konto. GitHub är en tjänst där man sparar kod. Där kommer både hemsidan och morgonprogrammet att bo.

### 2. Skapa ett projekt (ett "repository")
1. Klicka på **+** uppe till höger och välj **New repository**.
2. Döp det till `lunchkollen`.
3. Välj **Public**. Det krävs för att publicera hemsidan gratis. Koden blir synlig för andra, men din API-nyckel i steg 5 förblir hemlig.
4. Klicka **Create repository**.

### 3. Ladda upp filerna
1. Packa upp zip-filen på din dator.
2. Klicka på länken **uploading an existing file** på projektsidan.
3. Dra in `index.html`, `README.md` och mapparna `data` och `scripts`. Klicka **Commit changes**. En *commit* betyder ungefär "spara en version".
4. Mappen `.github` är dold på de flesta datorer eftersom namnet börjar med en punkt, så den skapar du för hand:
   - Klicka **Add file → Create new file**.
   - Skriv filnamnet `.github/workflows/morgon.yml`. Snedstrecken skapar mapparna automatiskt.
   - Öppna `morgon.yml` från zip-filen i en textredigerare, kopiera allt och klistra in.
   - Klicka **Commit changes**.

### 4. Slå på hemsidan (GitHub Pages)
1. Gå till **Settings → Pages**.
2. Under *Source*, välj **Deploy from a branch**. Välj grenen **main** och mappen **/ (root)**. Klicka **Save**.
3. Efter någon minut ligger sidan på `https://DITT-ANVÄNDARNAMN.github.io/lunchkollen/`.

Nu fungerar sidan, med exempelmenyerna.

### 5. Skaffa en AI-nyckel till morgonprogrammet
Programmet använder Claude för att läsa menyerna. Det kräver en API-nyckel, ungefär ett lösenord som ger programmet rätt att använda AI:n.

1. Gå till console.anthropic.com, skapa ett konto och fyll på en liten summa krediter. Varje morgonkörning kostar lite, eftersom texten från tio restauranger är kort.
2. Skapa en nyckel under **API keys** och kopiera den.
3. På GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
4. Namn: `ANTHROPIC_API_KEY`. Värde: nyckeln. Spara.

Nyckeln syns aldrig för någon annan, inte ens i ditt publika projekt. Klistra aldrig in den direkt i en fil.

### 6. Kör morgonprogrammet första gången
1. Gå till fliken **Actions**. Om GitHub frågar, slå på workflows.
2. Klicka på **Hämta dagens luncher** i listan till vänster och sedan på **Run workflow**.
3. Klicka på körningen för att se loggen. Steget *Hämta och tolka menyerna* visar en rad per restaurang:
   - `✓` betyder att menyn hittades.
   - `?` betyder att sidan lästes, men inga rätter med pris hittades.
   - `–` betyder att det saknas en menyadress i registret.
   - `✗` betyder att något gick fel, och felet står på raden.
4. Ladda om hemsidan. Nu visas riktiga menyer, och exempelvarningen är borta.

Därefter sköter sig körningen själv varje vardag.

---

## Underhåll

**Lägga till eller rätta en restaurang:** Öppna `data/restauranger.json` på GitHub och klicka på pennan för att redigera. Kopiera ett befintligt block och ändra värdena. Koordinater hittar du i Google Maps: högerklicka på platsen, så visas siffrorna överst. Den första är `lat`, den andra `lng`. Glöm inte kommatecknet mellan blocken.

**Fyra restauranger saknar menyadress:** L'angolo, Dogtown, Proviant och Panini. Leta upp var de lägger sin lunch och fyll i `menyadress`.

**En restaurang visar fel eller ingenting:** Titta i loggen under Actions. Vanliga orsaker är att restaurangen har flyttat sin meny, bara lägger ut den på Instagram eller publicerar den som en bild. Bilder kan programmet inte läsa ännu.

**Testa utan AI:** Om du kör programmet på din egen dator med `python scripts/hamta_menyer.py --torrkorning` visas bara texten som hämtas från varje sida. Det är bra för att se vad AI:n får att jobba med.

## Senare
- **Egen adress:** Köp en domän, till exempel `lunchkollen.se`, och koppla den under **Settings → Pages → Custom domain**.
- **Fler restauranger:** Bygg ut område för område och håll koll på andelen `✓` i loggen.
- **Fråga restaurangerna:** Be om lov att visa deras lunch, med länk tillbaka. Det är både artigt och tryggare.
