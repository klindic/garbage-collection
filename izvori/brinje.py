"""Brinje: Komunalno društvo Brinje d.o.o., the Općina Brinje in five weekday groups (Monday to Friday).

    python3 -m izvori.brinje [--year 2026]

The year plan is a CorelDRAW PDF in the WordPress media library ("Raspored-Brinje-<year>*.pdf"; the
"Odvoz otpada" page may still link last year's). All text is drawn as curves, so nothing can be read as
text: the 12 month grids are found from their white separator lines (3 rows of 4 months, a weekday header
and six week rows each), a cell's date follows from its row and column, and every cell is checked
against the digit shapes drawn in it (number of digits, red for weekends and holidays). Whole weeks are
coloured: green mixed waste, yellow plastic and glass (one bin), blue paper; a household is collected on
its group's weekday in a coloured week. Holidays are red and uncoloured: no collection, and the plan
shows no replacement day. The weekday groups (streets and settlements) are drawn as curves too and are
copied by hand below, so the script stops when the PDF changes (sha256) until they are checked again.
"""
import argparse
import calendar
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "brinje"
SITE = "https://komunalnodrustvobrinje.hr"
PAGE = SITE + "/odvoz-otpada/"
MEDIA = SITE + "/wp-json/wp/v2/media?search=raspored&mime_type=application/pdf&per_page=50&_fields=date,source_url"
SHA256 = {2026: "4a16302875a661b16a3d6cda03bd6c190aaf54b252a18217969d2238f66bfc16"}
PALETTE = {(0.59, 0.0, 0.89, 0.0): "M", (0.11, 0.0, 1.0, 0.0): "P", (0.88, 0.0, 0.0, 0.0): "K"}
HEADER = (0.0, 0.0, 0.0, 0.251)
# copied from the plan's table (drawn as curves): weekday -> (description, streets and settlements)
GROUPS = {
    0: ("HAC, INA, Millem, javne površine i groblja", ["HAC", "INA", "Millem", "javne površine", "groblja"]),
    1: ("Frankopanska, Lučani, Lovačka, Radotići, Sertići, Blažani, Letinačka, Trg admirala J. V. P., Stipe Javora, "
        "Zadgrad, Senjska, Sv. Fabijan",
        ["Frankopanska", "Lučani", "Lovačka", "Radotići", "Sertići", "Blažani", "Letinačka", "Trg admirala J. V. P.",
         "Stipe Javora", "Zadgrad", "Senjska", "Sv. Fabijan"]),
    2: ("Kapelska, Križpolje, Veliki Kut, Jelvica, Jezerane, Črnač, Mokro polje, Razvala, Stajnica, Lipice; "
        "poslovni subjekti",
        ["Kapelska", "Križpolje", "Veliki Kut", "Jelvica", "Jezerane", "Črnač", "Mokro polje", "Razvala", "Stajnica",
         "Lipice"]),
    3: ("Krpani, Mali Kut, Donja Kamenica, Gornja Kamenica; dio Križpolja (Pavlovići, Lukani, Asani, B. Kamenica); "
        "Prokike, Žuta Lokva, Grabova Lokva, Rapain Klanac",
        ["Krpani", "Mali Kut", "Donja Kamenica", "Gornja Kamenica", "Križpolje (Pavlovići, Lukani, Asani, B. Kamenica)",
         "Pavlovići", "Lukani", "Asani", "B. Kamenica", "Prokike", "Žuta Lokva", "Grabova Lokva", "Rapain Klanac"]),
    4: ("Letinac; dio Brinja (Draženovići, Bićanići, Vučetići, Drenovac, Kalanji, Bublići, Plašćica, Sv. Stipan, "
        "Holjevci, Linarići, Potok, Sv. Vid, Hobari, Karakaši, Rajkovići, Lokmeri, Blažani-Gerići)",
        ["Letinac", "Brinje", "Draženovići", "Bićanići", "Vučetići", "Drenovac", "Kalanji", "Bublići", "Plašćica",
         "Sv. Stipan", "Holjevci", "Linarići", "Potok", "Sv. Vid", "Hobari", "Karakaši", "Rajkovići", "Lokmeri",
         "Blažani-Gerići"]),
}
DANI = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak"]
PROVIDER = {
    "davatelj": "Komunalno društvo Brinje d.o.o.",
    "web": SITE,
    "zupanija": "Ličko-senjska",
    "jls": ["Brinje"],
    "nazivi": {"P": "Plastika i staklo"},
    "napomene": [
        "Odvoz miješanog komunalnog otpada vrši se najmanje jednom u dva tjedna; na poziv korisnika odvoz je moguć "
        "srijedom, u danima bez redovnog odvoza.",
        "Odvoz je na dan skupine u tjednu označenom bojom: zeleno miješani otpad, žuto plastika i staklo, plavo papir.",
        "Na blagdane (crveni datumi) nema odvoza; zamjenski dan nije objavljen.",
    ],
}


def cmyk(c):
    return tuple(round(float(v), 3) for v in c) if isinstance(c, (tuple, list)) else c


def blocks(page):
    """[(x0, x1, row lines, column edges)] of the 12 month grids, in reading order."""
    seps_h = [r for r in page.rects if r["bottom"] - r["top"] < 1 and r["x1"] - r["x0"] > 100]
    seps_v = [r for r in page.rects if r["x1"] - r["x0"] < 1 and 12 < r["bottom"] - r["top"] < 14]
    groups = defaultdict(list)
    for r in seps_h:
        groups[(round(r["x0"]), round(r["x1"]))].append(r["top"])
    out = []
    for (x0, x1), tops in groups.items():
        tops = sorted(tops)
        # one grid has 7 lines: above the weekday header and under it and each of the first five week rows
        for i in range(0, len(tops) - 6):
            run = tops[i:i + 7]
            steps = [b - a for a, b in zip(run, run[1:])]
            if max(steps) - min(steps) < 0.6 and 12 < steps[0] < 14.5:
                head = [v for v in seps_v if x0 < v["x0"] < x1 and run[0] <= v["top"] < run[1]]
                cols = [x0] + sorted((v["x0"] + v["x1"]) / 2 for v in head) + [x1]
                out.append((x0, x1, run + [run[-1] + steps[0]], cols))
    rows = sorted({round(b[2][0]) for b in out})
    return sorted(out, key=lambda b: (rows.index(round(b[2][0])), b[0]))


def read_plan(path, year):
    """({date: code}, problems): the colour of every day cell, checked against the digit shapes."""
    page = pdfplumber.open(path).pages[0]
    grids = blocks(page)
    if len(grids) != 12 or any(len(c) != 8 for *_, c in grids):
        return {}, [f"found {len(grids)} month grids with columns {[len(c) - 1 for *_, c in grids]}"]
    hol = set(pravila.blagdani(year))
    fills = [r for r in page.rects if r.get("fill")]
    glyphs = [c for c in page.curves if 5 < c["bottom"] - c["top"] < 9.5 and c["x1"] - c["x0"] < 7]
    found, problems = {}, []
    for mo, (x0, x1, lines, cols) in enumerate(grids, 1):
        first = date(year, mo, 1).weekday()
        for row in range(6):
            top, bottom = lines[row + 1], lines[row + 2]
            for col in range(7):
                left, right = cols[col], cols[col + 1]
                day = row * 7 + col - first + 1
                d = date(year, mo, day) if 1 <= day <= calendar.monthrange(year, mo)[1] else None
                inside = [g for g in glyphs
                          if left < (g["x0"] + g["x1"]) / 2 < right and top < (g["top"] + g["bottom"]) / 2 < bottom]
                digits = []  # glyph curves that overlap side by side are one digit (outline and its hole)
                for g in sorted(inside, key=lambda g: g["x0"]):
                    if digits and g["x0"] < digits[-1][1] - 0.3:
                        digits[-1][1] = max(digits[-1][1], g["x1"])
                        digits[-1][2].append(g)
                    else:
                        digits.append([g["x0"], g["x1"], [g]])
                red = any(cmyk(g["non_stroking_color"])[:2] in ((0.0, 0.92), (0.0, 0.93)) for g in inside
                          if isinstance(g["non_stroking_color"], (tuple, list)) and len(g["non_stroking_color"]) == 4)
                want = len(str(day)) if d else 0
                if len(digits) != want:
                    problems.append(f"{year}-{mo:02d} row {row + 1} column {col + 1}: {len(digits)} digits, expected {want}")
                    continue
                if d and red != (d.weekday() >= 5 or d in hol):
                    problems.append(f"{d}: digits {'red' if red else 'black'}")
                if not d:
                    continue
                # the cell's colour: the fill covering most of it
                area = Counter()
                for r in fills:
                    w = min(r["x1"], right) - max(r["x0"], left)
                    h = min(r["bottom"], bottom) - max(r["top"], top)
                    if w > 0 and h > 0:
                        area[cmyk(r["non_stroking_color"])] += w * h
                col_ = max((c for c in area if c != 1.0 and c != (1.0,)), key=lambda c: area[c], default=None)
                if col_ is None or area[col_] < 0.5 * (right - left) * (bottom - top):
                    continue
                code = PALETTE.get(col_)
                if code is None:
                    problems.append(f"{d}: unknown colour {col_}")
                else:
                    found[d] = code
    return found, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    media = json.loads(fetch(MEDIA).decode("utf-8"))
    files = sorted((m["date"], m["source_url"]) for m in media
                   if re.search(rf"/Raspored[^/]*-{year}(-\d+)?\.pdf$", m["source_url"]))
    if not files:
        sys.exit(f"Nema rasporeda za {year} u medijima ({MEDIA})")
    url = files[-1][1]  # the newest upload
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = url
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "raspored.pdf"
        fetch(url, dest)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        if SHA256.get(year) != digest:
            sys.exit(f"PDF se promijenio ili nije poznat ({url}, sha256 {digest}): popis naselja po danima prepisan je "
                     "ručno, provjeriti ga i upisati novi sha256 u SHA256.")
        found, problems = read_plan(dest, year)
    weeks = defaultdict(set)  # colour of each week (Monday to Friday)
    for d, c in found.items():
        weeks[d.isocalendar()[:2]].add(c)
    for wk, codes in weeks.items():
        if len(codes) > 1:
            problems.append(f"week {wk}: several colours {sorted(codes)}")
    for d in found:
        if d.weekday() >= 5:
            problems.append(f"{d}: coloured weekend day")
    for p in problems[:15]:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    hol = set(pravila.blagdani(year))
    zones = {}
    for wd, (opis, names) in GROUPS.items():
        rows = [(d, c, False) for d, c in found.items() if d.weekday() == wd]
        counts = Counter(c for _, c, _ in rows)
        dates = sorted(d for d, c, _ in rows if c == "M")
        gaps = Counter((b - a).days for a, b in zip(dates, dates[1:]))
        missed = sorted(h for h in hol if h.weekday() == wd and h.isocalendar()[:2] in weeks)
        print(f"{DANI[wd]}: {dict(sorted(counts.items()))}, razmaci M {dict(sorted(gaps.items()))}, "
              f"bez odvoza zbog blagdana {', '.join(f'{h:%d.%m.}' for h in missed) or '-'}")
        if not 22 <= counts["M"] <= 27 or not 10 <= counts["P"] <= 14 or not 10 <= counts["K"] <= 14 or max(gaps) > 28:
            sys.exit("Broj odvoza nije uvjerljiv. Ništa nije upisano.")
        key = str(wd + 1)
        old = data["zone"].get(key, {})
        zones[key] = {"jls": "Brinje",
                      "podrucje": f"{DANI[wd]} – " + ", ".join(names[:4]) + (", …" if len(names) > 4 else ""),
                      "opis": opis, "ulice": names,
                      "raw": {**old.get("raw", {}), str(year): podaci.month_lines(rows)}}
        if wd == 0:
            zones[key]["napomena"] = "Skupina za HAC, INA, Millem, javne površine i groblja (nije za kućanstva)."
    print("Blagdani: crveni i neobojeni, zamjenski dan nije objavljen – odvoz tog dana izostaje.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
