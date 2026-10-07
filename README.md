# Odvoz otpada, GOS Sisak, zone 1 do 10, 2026.

Mala web aplikacija koja pokazuje koje kante treba iznijeti danas, sutra i bilo koji drugi dan, za odabranu zonu, plus Excel tablica s cijelim rasporedom za svaku zonu.

- `odvoz.html`: aplikacija (izvor). Raspored svih zona je u JSON bloku `<script type="application/json" id="zones">`, po godinama (`"raw": {"2026": [...], "2027": [...]}`), pa stranica može držati više godina odjednom i prirodno prelazi u novu. Zona se bira padajućim izbornikom (zadano Zona 1), pamti se u `localStorage` (`odvoz-zona`), a može se zadati i linkom `?zona=N`.
- `extract_pdf.py`: čita GOS-ove PDF rasporede (datume iz teksta, vrste otpada iz boje kanti) i upisuje godinu u `odvoz.html`. Svaki datum provjerava prema tiskanom danu u tjednu, pa PDF drugačijeg izgleda ili kriva godina javljaju PROBLEM umjesto krivih podataka. Treba `pdfplumber`, `pillow` i `pdftoppm` (poppler-utils).
- `excel/Raspored_odvoza_ZonaN_GGGG.xlsx`: raspored po datumima i vrstama otpada za svaku zonu i godinu. Generira ih `gen_xlsx.py` (treba `openpyxl`) iz istih podataka; nakon promjene rasporeda pokreni `python3 gen_xlsx.py` i commitaj tablice.
- `gen_ics.py`: iz istih podataka generira kalendare za pretplatu, za svaku zonu po jedan za svako vrijeme obavijesti (`odvoz-zonaN-1600.ics` do `odvoz-zonaN-2200.ics` dan prije, `odvoz-zonaN-jutro-0530.ics`, `odvoz-zonaN-jutro-0600.ics`, `odvoz-zonaN-bez.ics`). `odvoz-zonaN.ics` je isto što i `odvoz-zonaN-1800.ics`. Kalendari sadrže sve godine, pa pretplatnici novu godinu dobiju sami. Stari nazivi bez zone (`odvoz.ics`, `odvoz-1800.ics` itd.) i dalje postoje i sadrže Zonu 5, zbog postojećih pretplata.
- `build.sh`: složi samostalnu stranicu, kalendare i Excel tablice u `_site/`.
- `.github/workflows/pages.yml`: svaki push na `main` deploya na GitHub Pages.
- Posjete broji GoatCounter (https://klindic.goatcounter.com, bez kolačića). Skriptu dodaje `build.sh` samo na objavljenu stranicu. Događaji: `zona-odabrana-N`, `kalendar-iphone-zona-N`, `kalendar-google-zona-N`, `kalendar-url-zona-N`, `excel-zona-N`.

Izvor podataka: službeni rasporedi GOS d.o.o. (PDF po zoni, https://gos.hr). Raspored za 2026. objavljen je 1. 12. 2025.

## Nova godina (npr. 2027.)

1. Skini PDF-ove svih zona s gos.hr (obično krajem studenoga ili početkom prosinca).
2. `python3 extract_pdf.py 2027 1=RASPORED_2027_Zona_1.pdf 2=RASPORED_2027_Zona_2.pdf ...` (sve zone). Ako javi PROBLEM, pogledaj taj datum u PDF-u; ništa se ne upisuje dok sve zone ne prođu.
3. Ponovi istu naredbu s `--write`: godina se doda u `odvoz.html`, 2026. ostaje.
4. Pregledaj napomene zona u `odvoz.html` (`note`, `noBioIn`, `pilotFrom`, opis `area`) prema drugoj stranici PDF-ova, jer se mogu promijeniti.
5. `python3 gen_xlsx.py`, pa `./build.sh` za provjeru, pa commit i push.

Staru godinu možeš kasnije maknuti iz `raw` (i njene Excel tablice iz `excel/`).
