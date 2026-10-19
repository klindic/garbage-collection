# Rasporedi odvoza otpada u Hrvatskoj: istraživanje i stanje (8. 10. 2026.)

## Stanje

- **144 davatelja** imaju skriptu (`izvori/<davatelj>.py`) koja s njihove stranice preuzima raspored, pretvara ga
  u zajednički oblik (`podaci/<slug>.json`) i provjerava ga. `gen_xlsx.py --podaci` iz toga radi Excel predložak
  (`excel/<slug>/`).
- Raspored ima **470 od 556** gradova i općina, **93 % stanovnika** (2.071 zona).
- Svaki je davatelj provjeren ponovnim pokretanjem skripte i usporedbom s izvorom: slikom kalendara, PDF-om ili
  živim upitom.
- `python3 pokrivenost.py [--popis]` u svakom trenutku računa pokrivenost iz `podaci/`.

## Datoteke

- `jls_davatelj.csv`: svih 556 gradova i općina, njihov davatelj (s OIB-om), broj stanovnika i kućanstava
  (Popis 2021.).
  - Izvor je ministarska tablica godišnjih izvješća davatelja za 2024. (ISGO portal).
  - Stupac `promjena_2026` navodi jedinice kojima je davatelj promijenjen prema evidenciji iz listopada 2026.
- `jls_davatelj_crosscheck.csv`: usporedba s popisom iz 2022. i evidencijom iz 2026.
- `izvori.csv`: za svakog davatelja ili grad sadrži:
  - stranicu s rasporedom, format i razinu detalja;
  - oblik rasporeda (pravila ili datumi) i način objave pomaka zbog blagdana;
  - vrste otpada i procjenu napora;
  - status: `gotovo` (sve JLS imaju raspored), `djelomično` ili `istraženo`.
- `val3_*.jsonl`: detalji istraživanja manjih davatelja po regijama (uzorak izvora, način čitanja).
- `bez_rasporeda.csv`: 85 JLS bez rasporeda (oko 254 tisuće stanovnika), s razlogom.

## Kako davatelji objavljuju raspored

| Način | Primjeri | Kako ga skripte čitaju |
|---|---|---|
| Javni API ili podaci u stranici | Zagreb, Osijek, Zadar, Varaždin, Pula, Požega, Ivakop, Petrinja, Metković | Upiti po adresi, istovjetni rasporedi spajaju se u zone; predmemorija, najviše 2–5 upita u sekundi |
| PDF ili HTML s datumima | Karlovac, Koprivnica, Ivkom, Križevci, Lukom, Delnice, Pag | Tekst po položaju riječi (pdfplumber), provjera dana u tjednu |
| Pravila („ponedjeljkom“, „2. i 4. utorak“) | Velika Gorica, Zaprešić, Šibenik, otoci, Zelina | Datumi iz `pravila.py`; sezone s točnim razdobljima iz izvora |
| Kalendar u boji (vektorski PDF) | Rijeka, Mull-Trans (43 općine), Čakovec, Opatija, Pazin, Umag | Boja polja = vrsta otpada (`kalendar_boje.py`), legenda iz PDF-a |
| Slike i skenovi | Pre-Kom, Trogir, Drniš, Kijevo, Konjščina, Vojnić | Boje očitane po mreži ili prepisano; sha256 izvora upisan je u skriptu, pa se skripta zaustavi kad se slika promijeni |

## Što treba znati o podacima

- **Blagdani** se rješavaju na četiri načina:
  - pomak je ugrađen u datume i označen kao pomaknut (`!`);
  - davatelj je objavio pravilo, pa se ono primjenjuje;
  - pomak je objavljen u obavijesti, koju skripta pročita;
  - ništa nije objavljeno, pa ostaju datumi prema pravilu, uz napomenu.
- **Stara pravila:** za dio otoka i manjih mjesta postoje samo stariji dokumenti (npr. Brač 2019., Vis 2020./21.,
  Vrgorac i Stari Grad 2022., Hvar 2024., Kom-Ilok 2025.). Datumi za 2026. izračunati su iz njih, a svaka takva
  zona to navodi u napomeni.
- **Djelomična godina:** neki davatelji objavljuju samo dio godine (Pula i Požega pomični prozor, Drava Kom i
  Contrada polugodište, Flora VTC od 1.8.). Pri ponovnom pokretanju skripte čuvaju ranije mjesece. Te skripte
  treba pokretati redovito.
- **Promjena davatelja tijekom godine:**
  - Viljevo, Magadenovac i Podravska Moslavina prelaze s Mull-Transa na Doroslov, a Koška na Urbanizam Valpovo.
    Stari raspored završava kad počne novi.
  - Gornji Mihaljevec ima Čakomov raspored tek od 10.7.
  - Lasinja je od 2026. kod Vojnić komunalca.
- **Isto ime, dvije jedinice:** Privlaka (Zadarska i Vukovarsko-srijemska) i Sveta Nedelja (Zagrebačka i Istarska)
  razlikuju se po županiji davatelja.
- **Evidencija i stvarnost:** gdje se davatelj iz službene evidencije razlikuje od onoga koji objavljuje raspored,
  koristi se objavljeni raspored, a napomena navodi evidenciju. Primjeri su Mikleuš, Donji Andrijevci, Stankovci
  i Polača.

## Što nedostaje (detalji u `bez_rasporeda.csv`)

- **Split** je uključen samo djelomično. Čistoća Split nema kalendar s datumima, pa su datumi izračunati iz stalnih
  pravila po blokovima. Centar se prazni svaki dan iz zajedničkih spremnika i zato nema dana odvoza.
- **Blokirano iz ovog okruženja (Cloudflare):** Rovinj, Bale, Kanfanar, Žminj, Novi Marof i Ljubešćica,
  te Zelenjak (Klanjec i okolica). Ove rasporede treba ručno preuzeti.
- **Poreč (grad):** PDF postoji, ali se preuzimanje prekida i kopija nije pronađena.
- **Ništa objavljeno:** oko 40 manjih jedinica, npr. Benkovac, Otočac, Ozalj, Marija Bistrica, Muć, Ston, Lastovo,
  Plitvička Jezera i Cres–Lošinj (spremnici s karticom).
- **Nejasno:** Slatina (nije poznato koji je tjedan „A“) i Tučepi (nema datuma sezona).

## Pitanja za davatelje (izbor)

- Mull-Trans: popis naselja po terenu, za više od 10 općina.
- Općine oko Bjelovara: dani miješanog otpada (dio je preuzet iz obavijesti iz 2025.).
- Buzet: naselja po mjesnim odborima.
- Novalja: raspored za studeni i prosinac.
- Davatelji sa starijim pravilima: potvrda da ta pravila vrijede i u 2026.
