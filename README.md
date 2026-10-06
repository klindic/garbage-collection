# Odvoz otpada, Zona 5 (Sisak), 2026.

Mala web aplikacija koja pokazuje koje kante treba iznijeti danas, sutra i bilo koji drugi dan, plus Excel tablica s cijelim rasporedom.

- `odvoz-zona5.html`: aplikacija (izvor; raspored je upisan u `RAW` u skripti).
- `Raspored_odvoza_Zona5_2026.xlsx`: raspored po datumima i vrstama otpada.
- `gen_ics.py`: iz istih podataka generira `odvoz.ics` (pretplata na kalendar, obavijest u 20:00 večer prije).
- `build.sh`: složi samostalnu stranicu i `odvoz.ics` u `_site/`.
- `.github/workflows/pages.yml`: svaki push na `main` deploya na GitHub Pages.

Izvor podataka: službeni raspored GOS d.o.o. za Zonu 5, 2026. Za 2027. treba zamijeniti `RAW` i `YEAR` u `odvoz-zona5.html`.
