"""Rakovica: Rakovica d.o.o. za obavljanje komunalnih djelatnosti, the Općina Rakovica in four routes.

    python3 -m izvori.rakovica [--year 2026]

The "Plan odvoza otpada" page (Wix) links one PDF per route (Excel, vector): page 1 has the mixed-waste
days as green cells of a 12-month calendar, page 2 the recyclables as yellow (plastic) and blue (paper)
cells; the route and its settlements are in the title ("RUTA 3: LIPOVAC, ..."). The PDFs have no legend:
yellow = plastic and blue = paper follow the usual bin colours (the title of page 2 repeats "miješani
komunalni otpad" by mistake). Route 5 (Friday, "ostali korisnici") has no calendar. The August grid's
weekday header has PON and UTO swapped; the columns are where they always are, so the two labels are
swapped back before reading. Holidays: the calendar is drawn with the shifts (holidays are red and
uncoloured, the collection moves to a coloured neighbouring day, e.g. 6.4. -> 7.4.); a date on a weekday
the type does not use around that month, within a week of a holiday, is marked as moved.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.med_eko_servis import irregular
from izvori.perusic import fill_at, read_grid
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import colour, lookup

SLUG = "rakovica"
SITE = "https://www.komunalac-rakovica.hr"
PAGE = SITE + "/planodvozaotpada"
PALETTE = {(0.0, 0.69, 0.314): "M", (1.0, 1.0, 0.0): "P", (0.0, 0.439, 0.753): "K"}
EXPECT = {0: "M", 1: "PK"}  # page index -> types it may hold
DANI = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak"]
WEEK = ["PON", "UTO", "SRI", "ČET", "PET", "SUB", "NED"]
PROVIDER = {
    "davatelj": "Rakovica d.o.o. za obavljanje komunalnih djelatnosti",
    "web": SITE,
    "zupanija": "Karlovačka",
    "jls": ["Rakovica"],
    "napomene": [
        "Miješani komunalni otpad odvozi se jednom tjedno na dan rute; papir (plavo) otprilike jednom mjesečno, "
        "plastika (žuto) jednom mjesečno, od svibnja do rujna svaka dva tjedna. Kalendari nemaju legendu: "
        "boje su protumačene prema uobičajenim bojama spremnika.",
        "Ruta 5 (petak) je za ostale korisnike i nema objavljen kalendar.",
        "Blagdanski pomaci ugrađeni su u kalendar.",
    ],
}


def fix_header(words):
    """Swap the labels of a weekday header that reads UTO PON SRI ... (August 2026)."""
    heads = [w for w in words if w["text"] in WEEK]
    for top in {round(w["top"]) for w in heads}:
        row = sorted((w for w in heads if abs(w["top"] - top) < 2), key=lambda w: w["x0"])
        for i in range(len(row) - 6):
            if [w["text"] for w in row[i:i + 7]] == ["UTO", "PON"] + WEEK[2:]:
                row[i]["text"], row[i + 1]["text"] = "PON", "UTO"
                print(f"   zaglavlje s UTO/PON zamijenjenim (x={row[i]['x0']:.0f}, y={top}) ispravljeno")
    return words


def read_route(path, year):
    """(route number, settlements, {date: codes}, problems)."""
    pdf = pdfplumber.open(path)
    problems, found, title = [], {}, None
    for i, page in enumerate(pdf.pages[:2]):
        text = " ".join((page.extract_text() or "").split())
        m = re.search(rf"- {year}\. RUTA (\d+): (.+?) (?:SIJEČANJ|$)", text)
        if not m:
            return None, [], {}, [f"page {i + 1}: no 'RUTA' title for {year}"]
        if title and m.groups() != title:
            problems.append(f"page {i + 1}: title differs")
        title = m.groups()
        cells, _, probs = read_grid(page, year, fix=fix_header)
        problems += [f"page {i + 1}: {p}" for p in probs]
        fills = [s for s in page.rects if s.get("fill") and colour(s) and s["x1"] - s["x0"] < 100]
        for d, (cx, cy, _, _) in cells.items():
            col = fill_at(fills, cx, cy)
            if col is None or col in ((1.0, 1.0, 1.0), (0.0, 0.0, 0.0)):
                continue
            code = lookup(col, PALETTE, tol=0.02)
            if code not in EXPECT[i]:
                problems.append(f"page {i + 1} {d}: colour {col}")
            else:
                found[d] = found.get(d, "") + code
    if len(pdf.pages) < 2:
        problems.append("no recyclables page")
    places = [p.strip() for p in title[1].split(",")] if title else []
    return int(title[0]) if title else None, places, found, problems


def nice(name):
    return " ".join(w.capitalize() for w in name.split())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    links = list(dict.fromkeys(re.findall(r'https://www\.komunalac-rakovica\.hr/_files/ugd/[\w]+\.pdf', html)))
    if not links:
        sys.exit(f"Nema PDF-ova na {PAGE}")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    hol = set(pravila.blagdani(year))
    ok, zones = True, {}
    with tempfile.TemporaryDirectory() as tmp:
        for i, url in enumerate(links):
            dest = Path(tmp) / f"{i}.pdf"
            fetch(url, dest)
            route, places, found, problems = read_route(dest, year)
            moved, probs = irregular(found, hol)
            problems += probs
            counts = Counter(t for c in found.values() for t in c)
            per = Counter(d.month for d, c in found.items() if "M" in c)
            if any(not 4 <= per[m] <= 5 for m in range(1, 13)):
                problems.append(f"M per month {dict(sorted(per.items()))}")
            for t, lo, hi in (("P", 11, 24), ("K", 11, 15)):  # plastic every two weeks from May to September
                if not lo <= counts[t] <= hi:
                    problems.append(f"{counts[t]}x {t}")
            day = Counter(d.weekday() for d, c in found.items() if "M" in c and d not in moved).most_common(1)
            if route is None or str(route) in zones or not day:
                problems.append(f"route {route} not understood or twice")
            print(f"Ruta {route} ({DANI[day[0][0]] if day else '?'}): {dict(sorted(counts.items()))}, "
                  f"{len(places)} naselja, "
                  f"pomaknuto {', '.join(f'{d:%d.%m.}' for d in sorted(moved)) or '-'}")
            for p in problems[:15]:
                print(f"   PROBLEM {p}")
            if problems:
                ok = False
                continue
            key = str(route)
            names = [nice(p) for p in places]
            old = data["zone"].get(key, {})
            zones[key] = {
                "jls": "Rakovica",
                "podrucje": f"Ruta {route} ({DANI[day[0][0]]}): " + ", ".join(names),
                "ulice": names,
                "raw": {**old.get("raw", {}), str(year): podaci.month_lines([(d, c, d in moved) for d, c in found.items()])},
            }
    if not ok or len(zones) != 4:
        sys.exit("Ništa nije upisano.")
    print("Pretpostavka: žuto = plastika, plavo = papir (PDF nema legendu).")
    data["zone"] = dict(sorted(zones.items()))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} rute)")


if __name__ == "__main__":
    main()
