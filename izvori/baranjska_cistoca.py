"""Baranjska čistoća d.o.o. (Beli Manastir): Beli Manastir, Čeminac, Darda, Draž, Jagodnjak, Kneževi Vinogradi,
Petlovac, Popovac.

    python3 -m izvori.baranjska_cistoca [--year 2026]

The pages "Redovni odvoz" and "Odvoz razvrstanog otpada" link one workbook (Rasporedi-YYYY.xlsx), read with
openpyxl. Its left block holds the two-weekly mixed waste dates as strings 'DD.MM.' under Pon..Pet columns per
month; the legend under it ('Ponedjeljak: Općine Draž, Kneževi Vinogradi, Popovac', ...) gives the
municipalities of each weekday, and a sentence gives biowaste (Beli Manastir every Friday, Darda the last
Thursday of the month). The right block holds, per month and group of municipalities, the dates of paper,
plastic and glass or metal (glass and metal alternate months). One zone per municipality. The company
publishes no holiday shifts (01.01. is listed as a normal mixed waste day), so the dates are used as listed
and a note says so.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import openpyxl

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "baranjska-cistoca"
SITE = "https://baranjskacistoca.hr"
PAGES = [SITE + "/redovni-odvoz/", SITE + "/odvoz-razvrstanog-otpada/"]
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
HEAD = ["pon", "uto", "sri", "čet", "pet"]
WEEKDAY = {"ponedjeljak": 0, "utorak": 1, "srijeda": 2, "četvrtak": 3, "petak": 4}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
EVERY_OTHER = ["svaki drugi ponedjeljak", "svaki drugi utorak", "svaku drugu srijedu", "svaki drugi četvrtak"]
ON_DAYS = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom"]
TYPES = {"papir": "K", "plastika": "P", "staklo": "S", "metal": "L"}
JLS = ["Beli Manastir", "Čeminac", "Darda", "Draž", "Jagodnjak", "Kneževi Vinogradi", "Petlovac", "Popovac"]
BIO = r"biorazgradivog otpada za Grad Beli Manastir je petkom, a u Općini Darda posli?jednji četvrtak u mjesecu"
PROVIDER = {
    "davatelj": "Baranjska čistoća d.o.o.",
    "web": SITE,
    "zupanija": "Osječko-baranjska",
    "jls": JLS,
    "bioNapomena": "Biootpad se odvozi samo u Gradu Belom Manastiru (svaki petak) i u Općini Darda "
                   "(posljednji četvrtak u mjesecu).",
    "napomene": [
        "Miješani komunalni otpad odvozi se svaka dva tjedna; papir, plastika te staklo ili metal (izmjenjuju "
        "se po mjesecima) prema mjesečnom rasporedu razvrstanog otpada.",
        "Pomaci odvoza zbog blagdana nisu objavljeni: datumi su preneseni kako su u rasporedu (npr. 01.01. je "
        "naveden kao redovni dan odvoza miješanog otpada). Provjerite obavijesti na baranjskacistoca.hr.",
        "Kontakt: 031 700 810, info@baranjskacistoca.hr (Ulica Republike 11, Beli Manastir).",
    ],
}


def text(v):
    return " ".join(str(v).split()) if v is not None else ""


def parse_date(s, year):
    """'16.03.' or '16.03' -> date, else None."""
    m = re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.?", s)
    return date(year, int(m.group(2)), int(m.group(1))) if m else None


def places(label):
    """'Popovac i Kn. Vinogradi' / 'Općine Draž, Kneževi Vinogradi, Popovac' -> JLS names (None if unknown)."""
    label = re.sub(r"^(grad|općin[ae])\s+", "", label.strip(), flags=re.I).replace("Kn. ", "Kneževi ")
    names = [p.strip() for p in re.split(r",| i ", label) if p.strip()]
    return names if all(n in JLS for n in names) else None


def read(path, year, problems):
    """(mko {weekday: [dates]}, weekday legend {JLS: weekday}, sorted {JLS: [(date, code)]}, bio text found)."""
    ws = openpyxl.load_workbook(path).worksheets[0]
    cells = {(c.row, c.column): text(c.value) for row in ws.iter_rows() for c in row if text(c.value)}
    split = next((c for (r, c), v in cells.items() if "sortiranog otpada" in v), None)
    title = " ".join(v for v in cells.values() if v.startswith("Raspored odvoza"))
    if split is None or f"{year}. godina" not in title:
        problems.append(f"tablica nije raspored za {year}: {title!r}")
        return {}, {}, {}, False
    months = [(r, c, MONTHS.index(v.lower())) for (r, c), v in cells.items() if v.lower() in MONTHS]
    mko, legend, sorted_ = defaultdict(list), {}, defaultdict(list)
    left = sorted((m for m in months if m[1] < split), key=lambda m: m[2])
    right = sorted((m for m in months if m[1] >= split), key=lambda m: m[2])
    if [m[2] for m in left] != list(range(12)) or [m[2] for m in right] != list(range(12)):
        problems.append(f"mjeseci u tablici: lijevo {[m[2] + 1 for m in left]}, desno {[m[2] + 1 for m in right]}")
    for r, c, mi in left:  # mixed waste: weekday header row, then rows of dates
        if [cells.get((r + 1, c + i), "").lower() for i in range(5)] != HEAD:
            problems.append(f"{MONTHS[mi]}: nema zaglavlja Pon..Pet")
            continue
        row = r + 2
        while any(parse_date(cells.get((row, c + i), ""), year) for i in range(5)):
            for i in range(5):
                v = cells.get((row, c + i), "")
                d = parse_date(v, year) if v else None
                if v and not d:
                    problems.append(f"miješani {MONTHS[mi]}: nepoznat datum {v!r}")
                elif d and (d.month != mi + 1 or d.weekday() != i):
                    problems.append(f"miješani {MONTHS[mi]}: {v} nije {DAN[i]} u tom mjesecu")
                elif d:
                    mko[i].append(d)
            row += 1
    for (r, c), v in cells.items():
        m = re.fullmatch(r"(Ponedjeljak|Utorak|Srijeda|Četvrtak|Petak)\s*:\s*(.+)", v)
        if m and c < split:
            names = places(m.group(2))
            if names is None:
                problems.append(f"nepoznate općine u legendi: {v!r}")
            for n in names or []:
                if n in legend:
                    problems.append(f"{n} je u legendi dvaput")
                legend[n] = WEEKDAY[m.group(1).lower()]
    for r, c, mi in right:  # sorted waste: 'Općina / grad' header with type columns, then one row per group
        if cells.get((r + 1, c), "").lower() != "općina / grad":
            problems.append(f"razvrstani {MONTHS[mi]}: nema zaglavlja")
            continue
        cols = {}
        while cells.get((r + 1, c + len(cols) + 1), "").lower() in TYPES:
            cols[c + len(cols) + 1] = TYPES[cells[(r + 1, c + len(cols) + 1)].lower()]
        if not {"K", "P"} <= set(cols.values()):
            problems.append(f"razvrstani {MONTHS[mi]}: stupci {cols}")
        row = r + 2
        while cells.get((row, c)) and cells[(row, c)].lower() not in MONTHS:
            names = places(cells[(row, c)])
            if names is None:
                problems.append(f"razvrstani {MONTHS[mi]}: nepoznata skupina {cells[(row, c)]!r}")
            for col, code in cols.items():
                v = cells.get((row, col), "")
                d = parse_date(v, year) if v else None
                if v and (not d or d.month != mi + 1):
                    problems.append(f"razvrstani {MONTHS[mi]} {cells[(row, c)]}: datum {v!r}")
                elif d:
                    for n in names or []:
                        sorted_[n].append((d, code))
            row += 1
    bio = any(re.search(BIO, v) for v in cells.values())
    return mko, legend, sorted_, bio


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []

    links = set()
    for page in PAGES:
        html = fetch(page).decode("utf-8", "replace")
        links |= set(re.findall(rf'href="([^"]+/uploads/(\d{{4}}/\d\d)/Rasporedi-{year}\.xlsx)"', html))
    if not links:
        sys.exit(f"Nema tablice Rasporedi-{year}.xlsx na {', '.join(PAGES)}")
    newest = max(links, key=lambda l: l[1])  # newest upload folder
    url = newest[0]
    others = sorted(l[0] for l in links - {newest})
    print(f"Tablica: {url}" + (f" (ostale poveznice: {', '.join(others)})" if others else ""))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raspored.xlsx"
        fetch(url, path)
        mko, legend, sorted_, bio = read(path, year, problems)
    if not bio:
        problems.append("rečenica o biootpadu se promijenila (očekivano: BM petkom, Darda posljednji četvrtak)")
    if sorted(legend) != sorted(JLS):
        problems.append(f"legenda dana miješanog otpada: {legend}")
    if mko.get(4):
        print(f"Napomena: stupac Pet u tablici miješanog otpada ({len(mko[4])} datuma) nema općinu u legendi; "
              "ti datumi nisu korišteni.")

    zones = {}
    totals = Counter()
    for i, jls in enumerate(JLS, 1):
        if jls not in legend or jls not in sorted_:
            problems.append(f"{jls}: nema u tablici")
            continue
        wd = legend[jls]
        rows = [(d, "M") for d in mko[wd]]
        rows += sorted_[jls]
        if jls == "Beli Manastir":
            rows += [(d, "B") for d in pravila.tjedno(year, "pet")]
        elif jls == "Darda":
            rows += [(d, "B") for d in pravila.mjesecno(year, "čet", -1)]
        merged = defaultdict(str)
        for d, code in rows:
            if code in merged[d]:
                problems.append(f"{jls}: {code} dvaput {d}")
            merged[d] += code
        sorted_days = Counter(d.weekday() for d, _ in sorted_[jls])
        sday = sorted_days.most_common(1)[0][0]
        if len(sorted_days) != 1:
            problems.append(f"{jls}: razvrstani otpad na više dana u tjednu {dict(sorted_days)}")
        got = Counter(c for codes in merged.values() for c in codes)
        per_month = Counter((d.month, c) for d, codes in merged.items() for c in codes)
        for m in range(1, 13):
            if not 2 <= per_month[(m, "M")] <= 3:
                problems.append(f"{jls}: miješani {per_month[(m, 'M')]} puta u {m}. mjesecu")
        for code, (lo, hi) in {"K": (11, 13), "P": (11, 13), "S": (3, 6), "L": (3, 6)}.items():
            if not lo <= got[code] <= hi:
                problems.append(f"{jls}: {code} {got[code]} puta u godini")
        totals.update(got)
        desc = f"miješani {EVERY_OTHER[wd]}, razvrstani otpad {ON_DAYS[sday]}"
        if jls == "Beli Manastir":
            desc += ", biootpad svaki petak"
        elif jls == "Darda":
            desc += ", biootpad posljednji četvrtak u mjesecu"
        kind = "Grad" if jls == "Beli Manastir" else "Općina"
        zones[str(i)] = {
            "jls": jls,
            "podrucje": f"{kind} {jls} – {desc}",
            "ulice": [f"{jls} (cijel{'i grad' if kind == 'Grad' else 'a općina'})"],
            "raw": {str(year): podaci.month_lines([(d, c, False) for d, c in merged.items()])},
        }

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "izvor": url, "zone": zones}
    data["napomene"] = PROVIDER["napomene"] + [f"Izvor rasporeda: {url} (poveznica na {PAGES[0]})."]
    out = podaci.PODACI / f"{SLUG}.json"
    if out.exists():  # keep other years of zones that did not change
        old = podaci.load(out)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("jls") == zone["jls"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    print(f"Zona: {len(zones)}; odvoza {year} (zbroj po zonama): "
          + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {out.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
