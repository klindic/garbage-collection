"""Hum na Sutli: Humkom d.o.o. (humkom.hr), two mixed-waste routes (Tuesday, Thursday) and municipal-wide Mondays.

    python3 -m izvori.humkom [--year 2026]

The page "Gospodarenje otpadom" links the year PDF (RASPORED-ODVOZA-OTPADA-U-<year>.-GODINI.pdf), a 300 dpi
scan without a text layer: one page with a 12-month calendar and the two routes, one page of sorting rules.
Cell colours: green = mixed waste (every other Tuesday on the Tuesday route, Thursday on the Thursday route),
yellow = plastic, blue = paper, grey = metal packaging (Mondays, whole municipality), red = holiday. The dates
are kept in this script (DATES, read from the cell colours and checked by eye) with the PDF's sha256; every run
extracts the scan (pdfimages), samples every day cell on the fixed grid and stops if a cell disagrees.
Holiday shifts are built into the calendar (red holiday, the replacement day coloured), so those dates are
marked as moved.
"""
import argparse
import calendar
import hashlib
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "humkom"
SITE = "https://humkom.hr"
PAGE = SITE + "/gospodarenje-otpadom/"
# year -> sha256 of the PDF and the dates read from it ("!" = moved because of a holiday); M1 Tuesday route,
# M2 Thursday route, P plastic, K paper, L metal packaging, red = holiday cells
DATES = {2026: {
    "sha256": "2176838faf62bfbe7f2365cd8ba1698ac239a0cb7f5bb027610a58f92af1216d",
    "M1": "01-07! 01-20 02-03 02-17 03-03 03-17 04-07 04-21 05-05 05-19 06-02 06-16 06-30 07-14 07-28 08-11 "
          "08-25 09-08 09-22 10-06 10-20 11-03 11-17 12-01 12-15",
    "M2": "01-08 01-22 02-05 02-19 03-05 03-19 04-09 04-23 05-07 05-21 06-05! 06-18 07-02 07-16 07-30 08-13 "
          "08-27 09-10 09-24 10-08 10-22 11-05 11-19 12-03 12-17",
    "P": "01-12 02-09 03-09 04-13 05-11 06-08 07-06 08-03 09-14 10-12 11-09 12-07",
    "K": "01-26 02-23 03-23 04-27 05-25 06-23! 07-20 08-17 09-28 10-26 11-23 12-21",
    "L": "02-16 05-18 08-31 11-30",
    "red": "01-01 01-06 04-06 05-01 05-30 06-04 06-22 08-05 08-15 12-25 12-26",
}}
ROUTES = {  # zone: (code, weekday, route as printed)
    "1": ("M1", 1, "Lupinjak – Strmec – Klenovec - Hum na Sutli - Vrbišnica - Orešje - Rusnica - Druškovec"),
    "2": ("M2", 3, "Druškovec- Druškovec Gora – Grletinec – Lastine - Hum na Sutli (Drajža) - Prišlin - Mal "
                   "Poredje – Zalug -Brezno Gora -Gornje Brezno - Donje Brezno"),
}
# fixed grid of the 2480x3508 scan: left edge of the month blocks and bottom of the weekday header, per block row
BLOCK_X = [(104, 900, 1693), (103, 899, 1692), (102, 897, 1691), (102, 897, 1690)]
HEAD_Y = [953, 1472, 1983, 2486]
PX, PY, DY = 100.0, 63.5, 34
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Humkom d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Krapinsko-zagorska",
    "jls": ["Hum na Sutli"],
    "nazivi": {"M": "Miješani komunalni otpad (zeleni spremnik)", "P": "Plastika (žuti spremnik)",
               "K": "Papir i karton (plavi spremnik)", "L": "Metalna ambalaža (PVC vreće)"},
    "bioNapomena": "Biootpad (smeđi spremnik) nije u kalendaru odvoza; informacije 049/340-097.",
}
NAPOMENE = [
    "Miješani komunalni otpad odvozi se svaki drugi tjedan: utorkom na trasi UTORAK, četvrtkom na trasi ČETVRTAK. "
    "Plastika, papir i metalna ambalaža odvoze se ponedjeljkom za cijelu općinu prema kalendaru.",
    "Blagdani su u kalendaru označeni crveno, a zamjenski dan odvoza obojen; takvi su datumi označeni kao "
    "premješteni.",
    "Staklo: kontejneri u Lupinjaku (kod Debeljaka), Klenovcu (nasuprot Cantine), Humu gornjem (raskrižje za "
    "Vrbišnicu) i Malom Taboru (poslovna zgrada Humkoma).",
    "Reciklažno dvorište: svake srijede od 9 do 17 sati i svake prve subote u mjesecu od 8 do 16 sati.",
    "Informacije: 049/340-097, humkom@humkom.hr.",
]


def dates(text, year):
    """'01-07! 01-20' -> [(date, moved)]."""
    return [(date(year, *map(int, t.rstrip("!").split("-"))), t.endswith("!")) for t in text.split()]


def colour_classes(a):
    """Per pixel: M green, P yellow, K blue, L light grey, R red, '.' anything else."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    out = np.full(r.shape, ".", dtype="<U1")
    out[(g > r + 20) & (g > b + 50) & (r > 90) & (r < 200)] = "M"
    out[(r > 220) & (g > 200) & (b < 150)] = "P"
    out[(b > 170) & (r < 90)] = "K"
    out[(abs(r - g) < 15) & (abs(g - b) < 15) & (r > 165) & (r < 220) & (b >= r)] = "L"
    out[(r > 180) & (g < 100) & (b < 100)] = "R"
    return out


def read_cells(path, year):
    """({date: "M" | "P" | "K" | "L" | "R" | ""}, problems) for every day cell of the scan."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    if a.shape[:2] != (3508, 2480):
        return {}, [f"neočekivana veličina skena {a.shape[1]}x{a.shape[0]}"]
    cls = colour_classes(a)
    found, problems = {}, []
    for m in range(1, 13):
        x0, y0 = BLOCK_X[(m - 1) // 3][(m - 1) % 3] + PX / 2, HEAD_Y[(m - 1) // 3] + DY
        first = date(year, m, 1)
        for day in range(1, calendar.monthrange(year, m)[1] + 1):
            d = date(year, m, day)
            cx, cy = round(x0 + d.weekday() * PX), round(y0 + (first.weekday() + day - 1) // 7 * PY)
            cell = cls[cy - 22:cy + 23, cx - 36:cx + 37]
            n = Counter(cell.ravel().tolist())
            n.pop(".", None)
            code, k = n.most_common(1)[0] if n else ("", 0)
            share = k / cell.size
            if share < 0.2:  # empty cell (red Sunday digits give < 0.1)
                found[d] = ""
            elif share < 0.45 or sum(n.values()) - k > 0.1 * cell.size:
                problems.append(f"{d}: nejasna boja ćelije {dict(n)}")
            else:
                found[d] = code
    return found, problems


def check(cal, year):
    """Rule problems: weekdays, holiday moves, gaps and monthly counts; and the notes about long gaps."""
    problems, notes = [], []
    hol = set(pravila.blagdani(year))
    red = {d for d, _ in dates(cal["red"], year)}
    if red - hol:
        problems.append(f"crveno, a nije blagdan: {sorted(red - hol)}")
    for code, wd in (("M1", 1), ("M2", 3), ("P", 0), ("K", 0), ("L", 0)):
        rows = dates(cal[code], year)
        due = []
        for d, moved in rows:
            regular = d - timedelta(days=d.weekday() - wd)
            if d in hol:
                problems.append(f"{code}: odvoz na blagdan {d}")
            if moved != (d != regular) or moved and regular not in red:
                problems.append(f"{code}: {d} ({DAN[d.weekday()]}) – oznaka pomaka ne odgovara danu {DAN[wd]}")
            due.append(regular)
        months = Counter(d.month for d in due)
        if code in ("P", "K") and sorted(months.items()) != [(m, 1) for m in range(1, 13)]:
            problems.append(f"{code}: nije jednom mjesečno: {dict(months)}")
        if code.startswith("M"):
            if any(not 2 <= months[m] <= 3 for m in range(1, 13)):
                problems.append(f"{code}: neuobičajen broj odvoza po mjesecima {dict(months)}")
            for a, b in zip(due, due[1:]):
                if (b - a).days not in (14, 21):
                    problems.append(f"{code}: razmak {a} – {b} je {(b - a).days} dana")
                elif (b - a).days == 21:
                    notes.append((a, b))
            if (date(year, 12, 31) - due[-1]).days >= 14:
                notes.append((due[-1], None))
    return problems, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    if year not in DATES:
        sys.exit(f"Za {year}. nema prepisanog kalendara u skripti (DATES).")
    cal = DATES[year]
    page = fetch(PAGE).decode("utf-8", "replace")
    links = sorted({u for u in re.findall(r'href="([^"]+\.pdf)"', page)
                    if re.search(rf"raspored[^/]*{year}", u, re.I)})
    if len(links) != 1:
        sys.exit(f"Na {PAGE} nije pronađen jedan PDF rasporeda za {year}: {links}")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "raspored.pdf"
        fetch(links[0], pdf)
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if sha != cal["sha256"]:
            sys.exit(f"slika se promijenila, prepisati ponovno: {links[0]} (sha256 {sha})")
        subprocess.run(["pdfimages", "-j", "-f", "1", "-l", "1", str(pdf), str(Path(tmp) / "img")], check=True)
        cells, problems = read_cells(Path(tmp) / "img-000.jpg", year)
    mine = {}
    for code in ("M1", "M2", "P", "K", "L", "red"):
        for d, _ in dates(cal[code], year):
            mine[d] = "R" if code == "red" else code[0]
    for d, code in cells.items():
        if mine.get(d, "") != code:
            problems.append(f"{d}: u skripti {mine.get(d) or '-'}, na skenu {code or '-'}")
    rule_problems, gaps = check(cal, year)
    problems += rule_problems
    unmarked = [h for h in pravila.blagdani(year) if h.weekday() < 5 and h not in mine]
    if unmarked:
        print("Blagdani koji nisu označeni crveno (tada nema odvoza po kalendaru):",
              ", ".join(f"{d:%d.%m.}" for d in unmarked))
    data = {**PROVIDER, "napomene": list(NAPOMENE), "zone": {}}
    inner = [f"{a.day}.{a.month}. → {b.day}.{b.month}." for a, b in sorted(gaps) if b]
    last = [f"{a.day}.{a.month}." for a, b in sorted(gaps) if not b]
    if inner or last:
        parts = ([f"razmak između dva odvoza miješanog otpada ponegdje je tri tjedna ({', '.join(inner)})"]
                 if inner else []) + ([f"posljednji odvoz u godini je {' odnosno '.join(last)}"] if last else [])
        data["napomene"].insert(1, f"Prema kalendaru {', a '.join(parts)}; u nedoumici provjerite kod Humkoma.")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    monday = [(d, code, moved) for code in ("P", "K", "L") for d, moved in dates(cal[code], year)]
    for z, (code, wd, route) in ROUTES.items():
        names = [n.strip() for n in re.split(r"\s*[-–]\s*", route) if n.strip()]
        rows = [(d, "M", moved) for d, moved in dates(cal[code], year)] + monday
        zone = {
            "jls": "Hum na Sutli",
            "podrucje": f"Trasa {DAN[wd]} – " + ", ".join(names[:4]) + " …",
            "opis": route,
            "ulice": names,
            "napomena": "Druškovec i Hum na Sutli nalaze se na obje trase; trasu svoje kuće provjerite kod Humkoma.",
        }
        if "Mal Poredje" in names:
            zone["napomena"] += " U rasporedu piše „Mal Poredje” (vjerojatno Mali Tabor i Poredje)."
        prev = old.get(z, {}).get("raw", {})
        zone["raw"] = {**{y: v for y, v in prev.items() if y != str(year)}, str(year): podaci.month_lines(rows)}
        data["zone"][z] = zone
        n = Counter(c for _, codes, _ in rows for c in codes)
        print(f"Trasa {DAN[wd]}: {len(names)} naselja, {dict(n)}, premješteno: "
              f"{', '.join(f'{d:%d.%m.}' for d, _, mv in sorted(rows) if mv) or '-'}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zone)")


if __name__ == "__main__":
    main()
