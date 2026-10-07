# Odvoz otpada, GOS Sisak, zone 1 do 10, 2026.

Mala web aplikacija koja pokazuje koje kante treba iznijeti danas, sutra i bilo koji drugi dan, za odabranu zonu, plus Excel tablica s cijelim rasporedom za svaku zonu.

- `odvoz.html`: aplikacija (izvor). Raspored svih zona je u JSON bloku `<script type="application/json" id="zones">`. Zona se bira padajućim izbornikom (zadano Zona 1), pamti se u `localStorage` (`odvoz-zona`), a može se zadati i linkom `?zona=N`.
- `excel/Raspored_odvoza_ZonaN_2026.xlsx`: raspored po datumima i vrstama otpada za svaku zonu. Generira ih `gen_xlsx.py` (treba `openpyxl`) iz istih podataka; nakon promjene rasporeda pokreni `python3 gen_xlsx.py` i commitaj tablice.
- `gen_ics.py`: iz istih podataka generira kalendare za pretplatu, za svaku zonu po jedan za svako vrijeme obavijesti (`odvoz-zonaN-1600.ics` do `odvoz-zonaN-2200.ics` dan prije, `odvoz-zonaN-jutro-0530.ics`, `odvoz-zonaN-jutro-0600.ics`, `odvoz-zonaN-bez.ics`). `odvoz-zonaN.ics` je isto što i `odvoz-zonaN-1800.ics`. Stari nazivi bez zone (`odvoz.ics`, `odvoz-1800.ics` itd.) i dalje postoje i sadrže Zonu 5, zbog postojećih pretplata.
- `build.sh`: složi samostalnu stranicu, kalendare i Excel tablice u `_site/`.
- `.github/workflows/pages.yml`: svaki push na `main` deploya na GitHub Pages.
- Posjete broji GoatCounter (https://klindic.goatcounter.com, bez kolačića). Skriptu dodaje `build.sh` samo na objavljenu stranicu. Događaji: `zona-odabrana-N`, `kalendar-iphone-zona-N`, `kalendar-google-zona-N`, `kalendar-url-zona-N`, `excel-zona-N`.

Izvor podataka: službeni rasporedi GOS d.o.o. za 2026. (PDF po zoni). Za 2027. treba zamijeniti `year` i `raw` u JSON bloku u `odvoz.html`, pa ponovno pokrenuti `gen_xlsx.py`.
