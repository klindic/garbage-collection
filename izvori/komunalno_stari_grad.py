"""Komunalno Stari Grad d.o.o.: Grad Stari Grad, six areas, weekly rules from January 2022.

    python3 -m izvori.komunalno_stari_grad [--year 2026]

The page "Odvoz otpada" links "Raspored odvoza 2022." (a text PDF, January 2022), still the only schedule. It is
one table, weekdays in columns and two rows of cells such as "Basina i Mudri Dolac -MKO i papir/karton"; every
area has two collection days a week, one for mixed waste with paper/cardboard and one for mixed waste with
plastic/glass. The table is read with pdfplumber: the cell borders come from the drawn lines and each word goes
to the cell it lies in. The Sunday cell ("Stari Grad -centar i poslovni subjekti (u sezoni)") has no season
dates and is only mentioned in a note. The standing rules are applied to the requested year with a note that
they date from 2022. No holiday rule is published: the regular dates are kept and a note says so.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalno-stari-grad"
SITE = "https://komunalno-stari-grad.hr"
PAGE = SITE + "/servisne-informacije/odvoz-otpada"
RULES_YEAR = 2022
HEADS = {"PONEDJELJAK": "pon", "UTORAK": "uto", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet",
         "SUBOTA": "sub", "NEDJELJA": "ned"}
KINDS = {"papir/karton": "MK", "plastika/staklo": "MPS"}
PLACES = {  # area as written (dashes normalised) -> (podrucje, places for search)
    "Stari Grad – Sjeverni dio": ("Stari Grad – sjeverni dio", ["Stari Grad (sjeverni dio)"]),
    "Stari Grad – Južni dio i Selca": ("Stari Grad – južni dio i Selca", ["Stari Grad (južni dio)", "Selca"]),
    "Basina i Mudri Dolac": ("Basina i Mudri Dolac", ["Basina", "Mudri Dolac"]),
    "Vrbanj": ("Vrbanj", ["Vrbanj"]),
    "Dol": ("Dol", ["Dol"]),
    "Rudina": ("Rudina", ["Rudina"]),
}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DANI = list(pravila.DANI)
OLD = (f"Raspored prema pravilima iz {RULES_YEAR}.; davatelj nije objavio novi raspored – provjerite prije "
       "odlaganja.")
PROVIDER = {
    "davatelj": "Komunalno Stari Grad d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Stari Grad"],
    "nazivi": {"K": "Papir i karton (plavi spremnik)", "P": "Plastika (žuti spremnik)",
               "S": "Staklo (vreća za staklo)"},
    "napomene": [
        OLD,
        "Svako područje ima dva odvoza tjedno: miješani otpad s papirom i kartonom te miješani otpad s plastikom "
        "i staklom.",
        "Stari Grad – centar i poslovni subjekti: u sezoni i nedjeljom (razdoblje sezone nije navedeno, pa nije "
        "upisano).",
        "Pomaci zbog blagdana nisu objavljeni; upisani su redovni dani odvoza.",
        "Glomazni otpad samo po prethodnoj narudžbi srijedom i petkom (do 2 m³ godišnje po kućanstvu).",
        "Komunalno Stari Grad d.o.o., Trg Ploča 7, Stari Grad; 021/765-299, info@komunalno-stari-grad.hr.",
    ],
}


def read_table(pdf_path, problems):
    """{area: {weekday: codes}} and the texts of cells not understood."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        xs = sorted({round(r["x0"]) for r in page.rects if r["width"] < 2 and r["height"] > 10})
        ys = sorted({round(r["top"]) for r in page.rects if r["height"] < 2 and r["width"] > 10})
        words = page.extract_words()
    if len(xs) != 8 or len(ys) < 4:
        problems.append(f"tablica: {len(xs)} okomitih i {len(ys)} vodoravnih crta")
        return {}, []
    cells = defaultdict(list)
    for w in words:
        cx = next((i for i in range(7) if xs[i] <= w["x0"] < xs[i + 1]), None)
        cy = next((i for i in range(len(ys) - 1) if ys[i] <= w["top"] < ys[i + 1]), None)
        if cx is not None and cy is not None:
            cells[(cy, cx)].append(w["text"])
    heads = [" ".join(cells[(0, i)]) for i in range(7)]
    if heads != list(HEADS):
        problems.append(f"zaglavlje tablice: {heads}")
        return {}, []
    areas, other = defaultdict(dict), []
    for (cy, cx), ws in sorted(cells.items()):
        if cy == 0:
            continue
        text = " ".join(ws)
        m = re.fullmatch(r"(.+?)\s*-MKO i (papir/karton|plastika/staklo)", text)
        if not m:
            other.append(f"{heads[cx].lower()}: {text}")
            continue
        place = re.sub(r"\s*[–-]\s*", " – ", m.group(1))
        day = HEADS[heads[cx]]
        if day in areas[place]:
            problems.append(f"{place}: dva odvoza u {day}")
        areas[place][day] = KINDS[m.group(2)]
    return areas, other


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = fetch(PAGE).decode("utf-8", "replace")
    link = re.search(r'href="([^"]+\.pdf)"[^>]*>(?:\s|<i[^>]*></i>)*Raspored odvoza (\d{4})\.', page)
    if not link:
        sys.exit(f"Na {PAGE} nema poveznice 'Raspored odvoza YYYY.'.")
    url = link.group(1) if link.group(1).startswith("http") else SITE + link.group(1)
    problems = []
    if int(link.group(2)) != RULES_YEAR:
        problems.append(f"objavljen je novi raspored ({link.group(2)}.) – provjeriti pravila i napomene")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "raspored.pdf"
        fetch(url, pdf)
        areas, other = read_table(pdf, problems)
    print(f"PDF {url}: {len(areas)} područja; pravila iz {RULES_YEAR}. primijenjena na {year}.")
    for o in other:
        print(f"Nije upisano (nema pravila s datumima): {o}")
    if set(areas) != set(PLACES):
        problems.append(f"područja u PDF-u: {sorted(areas)}")
    if any(not o.startswith("nedjelja: Stari Grad -centar") for o in other):
        problems.append(f"nepoznate ćelije: {other}")
    print("Pretpostavka: blagdani bez pomaka (pravilo nije objavljeno).")
    zones = {}
    for z, (place, (podrucje, ulice)) in enumerate(PLACES.items(), 1):
        days = areas.get(place, {})
        if sorted(days.values()) != ["MK", "MPS"]:
            problems.append(f"{place}: očekivana dva odvoza (papir, plastika/staklo): {days}")
            continue
        rows = sorted((d, codes, False) for k, codes in days.items() for d in pravila.tjedno(year, k))
        for d, n in Counter(d for d, _, _ in rows).items():
            if n > 1:
                problems.append(f"zona {z}: {d} dvaput")
        for m in range(1, 13):
            n = sum(1 for d, _, _ in rows if d.month == m)
            if not 8 <= n <= 10:
                problems.append(f"zona {z}: {m}. mjesec {n} odvoza")
        say = {k: DAN[DANI.index(k)] for k in days}
        desc = "; ".join(f"{say[k]}: miješani otpad i " + ("papir/karton" if c == "MK" else "plastika/staklo")
                         for k, c in sorted(days.items(), key=lambda kv: DANI.index(kv[0])))
        zones[str(z)] = {"jls": "Stari Grad", "podrucje": podrucje, "ulice": ulice,
                         "napomena": f"Pravila iz {RULES_YEAR}. – {desc}.",
                         "raw": {str(year): podaci.month_lines(rows)}}
        print(f"Zona {z} ({podrucje}): {desc}")
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
