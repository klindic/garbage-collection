"""Visoko (Varaždinska): Babić d.o.o., one calendar for the whole municipality (mixed waste and plastic).

    python3 -m izvori.babic_visoko [--year 2026]

The municipality's page "Odvoz otpada <year>" on visoko.hr links the year PDF ("RASPORED OVOZA OTPADA ZA
2026. GODINU - BABIĆ D.O.O."): 12 month grids (P U S Č P S N) whose cell fill gives the waste type, green
mixed waste, blue plastic, grey a non-working day. Collection is on Wednesdays, mixed waste and plastic in
turn; when the Wednesday is a holiday the PDF already shows the collection on the next day, so such dates
are marked as moved. The legend samples are checked against their labels, every collection must be a
Wednesday or the day after a grey Wednesday, and the two types must alternate.
"""
import argparse
import re
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import colour, lookup, read_page

SLUG = "babic-visoko"
SITE = "https://visoko.hr"
PAGE = SITE + "/odvoz-otpada-{year}/"
GREY = "X"  # non-working day
PALETTE = {(0.573, 0.816, 0.314): "M", (0.0, 0.69, 0.941): "P", (0.651, 0.651, 0.651): GREY}
IGNORE = ((1.0, 1.0, 1.0), (0.949, 0.863, 0.859), (0.773, 0.851, 0.945))  # white, weekend, heading
LEGEND = {"MIJEŠANI": "M", "PLASTIKA": "P", "NERADNI": GREY}
PROVIDER = {
    "davatelj": "Babić d.o.o.",
    "web": SITE,
    "zupanija": "Varaždinska",
    "jls": ["Visoko"],
    "napomene": [
        "Raspored objavljuje Općina Visoko na visoko.hr; uslugu obavlja Babić d.o.o. (naslov rasporeda i "
        "cjenik).",
        "Miješani komunalni otpad i plastika odvoze se srijedom, naizmjence svaki drugi tjedan.",
        "Kad je srijeda neradni dan, odvoz je sljedeći dan; takvi su datumi već upisani u rasporedu.",
    ],
}


def without_year(page, year):
    """The page without the year after each month name ("Siječanj 2026"), which read_page would take for a day."""
    spots = [(w["x0"], w["top"], w["x1"], w["bottom"]) for w in page.extract_words() if w["text"] == str(year)]

    def keep(obj):
        if obj.get("object_type") != "char":
            return True
        cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
        return not any(x0 <= cx <= x1 and t <= cy <= b for x0, t, x1, b in spots)
    return page.filter(keep)


def legend_problems(page):
    """Each legend label must have a sample of its colour just left of it."""
    problems = []
    words = page.extract_words()
    fills = [r for r in page.rects if r.get("fill") and colour(r)]
    for word, code in LEGEND.items():
        w = next((w for w in words if w["text"].upper() == word), None)
        if not w:
            problems.append(f"legend label {word} missing")
            continue
        cy = (w["top"] + w["bottom"]) / 2
        left = [r for r in fills if w["x0"] - 30 < r["x1"] <= w["x0"] + 1 and r["top"] - 1 <= cy <= r["bottom"] + 1]
        got = {lookup(colour(r), PALETTE) for r in left}
        if got != {code}:
            problems.append(f"legend {word}: sample colours {sorted(map(str, got))}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page_url = PAGE.format(year=year)
    html = fetch(page_url).decode("utf-8", "replace")
    links = sorted(set(re.findall(r"""href=["']?([^"' >]+\.pdf)""", html, re.I)))
    links = [u for u in links if "raspored" in u.lower() and str(year) in u]
    if len(links) != 1:
        sys.exit(f"Na {page_url} očekivan je jedan PDF rasporeda za {year}, nađeno: {links}")
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "raspored.pdf"
        fetch(links[0], pdf_path)
        page = pdfplumber.open(pdf_path).pages[0]
        text = " ".join((page.extract_text() or "").split())
        if f"ZA {year}. GODINU" not in text.upper() or "BABIĆ" not in text.upper():
            problems.append(f"naslov PDF-a nije 'RASPORED ... ZA {year}. GODINU - BABIĆ D.O.O.'")
        found, grid = read_page(without_year(page, year), year, PALETTE, ignore=IGNORE)
        problems += grid + legend_problems(page)
    grey = {d for d, c in found.items() if c == GREY}
    rows = sorted((d, c, d.weekday() != 2) for d, c in found.items() if c != GREY)
    for d, c, moved in rows:
        if moved and not (d.weekday() == 3 and d - timedelta(days=1) in grey):
            problems.append(f"{d}: {c} nije srijeda niti dan nakon neradne srijede")
    for (d1, c1, _), (d2, c2, _) in zip(rows, rows[1:]):
        if c1 == c2 or not 6 <= (d2 - d1).days <= 8:
            problems.append(f"{d1} {c1} -> {d2} {c2}: vrste se ne izmjenjuju tjedno")
    for m in range(1, 13):
        n = sum(1 for d, _, _ in rows if d.month == m)
        if not 4 <= n <= 5:
            problems.append(f"{year}-{m:02d}: {n} odvoza")
    moved = [f"{d:%d.%m.}" for d, _, mv in rows if mv]
    print(f"Visoko: {len(rows)} odvoza ({sum(c == 'M' for _, c, _ in rows)} M, {sum(c == 'P' for _, c, _ in rows)} P), "
          f"neradnih dana {len(grey)}, pomaknuto {len(moved)} ({', '.join(moved)})")
    for p in problems[:20]:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"].get("1", {}) if path.exists() else {}
    data = {**PROVIDER, "izvor": page_url, "zone": {"1": {
        "jls": "Visoko", "podrucje": "Cijela općina (srijeda)", "ulice": [],
        "raw": {**old.get("raw", {}), str(year): podaci.month_lines(rows)}}}}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
