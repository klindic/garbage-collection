"""Lopar: Lopar Vrutak d.o.o., the Općina Lopar (one calendar for mixed waste, one for separated waste).

    python3 -m izvori.lopar_vrutak [--year 2026]

The "Odvoz otpada" page links two Excel PDFs for the year: "Kalendar odvoza miješanog komunalnog otpada"
(purple cells: Mondays in winter, more often in May and October, every day in June to August) and "... selektivno
prikupljenog otpada" (orange biowaste, yellow plastic, blue paper and cardboard, green glass; colours from
the legend). A cell can hold two collections: a narrow coloured strip on the left of the cell and a
second colour on the right, so each cell is sampled left and right. Both PDFs print "SJEČANJ", show
17 January as "14" and add a non-existent 31 June (in the place of 1 July); these are known misprints and
the grid position gives the date. Holidays: red numbers; the plan is drawn with the shifts. A date that
stands in for a collection lost to a nearby holiday is marked as moved (see shifted()).
"""
import argparse
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.perusic import fill_at, read_grid
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import colour, lookup

SLUG = "lopar-vrutak"
SITE = "https://www.lopar-vrutak.hr"
PAGE = SITE + "/odvoz-otpada/"
FIX = {"SJEČANJ": "SIJEČANJ"}
TYPOS = {date(2026, 1, 17): "14", date(2026, 7, 1): "31"}  # 17 January printed as 14; "31 June" in the 1 July cell
MKO = {(0.729, 0.549, 0.863): "M"}
LEGEND = {"BIO": "B", "PLASTIKA": "P", "PAPIR": "K", "STAKLO": "S"}
EMPTY = ((1.0, 1.0, 1.0), (0.851, 0.851, 0.851))
PROVIDER = {
    "davatelj": "Lopar Vrutak d.o.o.",
    "web": SITE,
    "zupanija": "Primorsko-goranska",
    "jls": ["Lopar"],
    "nazivi": {"K": "Papir i karton"},
    "napomene": [
        "Miješani komunalni otpad zimi se odvozi ponedjeljkom, u svibnju, rujnu i listopadu češće, a od lipnja "
        "do kolovoza svaki dan; "
        "biootpad, plastika, papir i staklo prema kalendaru odvojeno prikupljenog otpada.",
        "Krupni (glomazni) otpad odvozi se na zahtjev (obrazac na stranici davatelja).",
        "Blagdanski pomaci ugrađeni su u kalendare.",
    ],
}


def read(path, year, palette=None):
    """({date: codes}, problems) of one calendar page; palette None: read it from the legend."""
    page = pdfplumber.open(path).pages[0]
    text = " ".join((page.extract_text() or "").split())
    if f"KALENDAR {year}." not in text:
        return {}, [f"no 'KALENDAR {year}.' title"]
    cells, outside, problems = read_grid(page, year, fix=FIX, typos=TYPOS)
    if problems:
        return {}, problems
    fills = [s for s in page.rects if s.get("fill") and colour(s) and s["x1"] - s["x0"] < 200]  # a week row at most
    if palette is None:  # legend: a sample cell left of each label, under the grids
        bottom = max(cy for _, cy, _, _ in cells.values())
        palette = {}
        for w in page.extract_words():
            code = next((c for k, c in LEGEND.items() if w["text"].upper().startswith(k)), None)
            if code and w["top"] > bottom:
                mid = (w["top"] + w["bottom"]) / 2
                sample = [s for s in fills if 0 < w["x0"] - s["x1"] < 40 and s["top"] - 2 < mid < s["bottom"] + 2]
                if sample:
                    palette[colour(sample[0])] = code
        if sorted(palette.values()) != sorted(LEGEND.values()):
            return {}, [f"legend {palette}"]

    def codes_at(cx, cy, hw, hh):
        out = ""
        for x in (cx - 0.7 * hw, cx + 0.5 * hw):  # left strip and the rest of the cell
            col = fill_at(fills, x, cy)
            if col is None or col in EMPTY:
                continue
            code = lookup(col, palette, tol=0.02)
            if code == "?":
                problems.append(f"{x:.0f},{cy:.0f}: unknown colour {col}")
            elif code not in out:
                out += code
        return out
    found = {d: c for d, cell in cells.items() if (c := codes_at(*cell))}
    for d, cell in outside:
        if codes_at(*cell) != found.get(d, ""):
            print(f"   {d:%d.%m.} prikazan i u susjednom mjesecu s drugom bojom ({codes_at(*cell) or '-'} / "
                  f"{found.get(d, '-')}); uzet je mjesec {d.month}.")
    return found, problems


def shifted(found, hol):
    """Dates that replace a collection lost to a holiday: the type is collected on that weekday the week
    before or after but not on the holiday, and on a day within three days of it that it does not use the
    week before or after. (The weekdays change during the season, so a weekday rule would not fit.)"""
    moved = set()
    for h in hol:
        for t in {t for c in found.values() for t in c}:
            def has(d):
                return t in found.get(d, "")
            if has(h) or not (has(h - timedelta(7)) or has(h + timedelta(7))):
                continue
            moved |= {h + timedelta(k) for k in (-3, -2, -1, 1, 2, 3) if has(h + timedelta(k))
                      and not has(h + timedelta(k - 7)) and not has(h + timedelta(k + 7))}
    return moved


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    links = set(re.findall(r'href="([^"]*KALENDAR-ODVOZA-[^"]*\.pdf)"', html))
    mko = sorted(u for u in links if "MIJES" in u.upper() and str(year) in u)
    sel = sorted(u for u in links if "SELEKTIV" in u.upper() and str(year) in u)
    if not mko or not sel:
        sys.exit(f"Nema oba kalendara za {year} na {PAGE}")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    hol = set(pravila.blagdani(year))
    with tempfile.TemporaryDirectory() as tmp:
        fetch(mko[-1], Path(tmp) / "mko.pdf")
        fetch(sel[-1], Path(tmp) / "sel.pdf")
        m_found, problems = read(Path(tmp) / "mko.pdf", year, MKO)
        s_found, probs = read(Path(tmp) / "sel.pdf", year)
    problems += probs
    found = {d: m_found.get(d, "") + s_found.get(d, "") for d in set(m_found) | set(s_found)}
    moved = shifted(found, hol)
    counts = Counter(t for c in found.values() for t in c)
    per = Counter(d.month for d, c in found.items() if "M" in c)
    winter = [m for m in (1, 2, 3, 4, 11, 12) if not 3 <= per[m] <= 11]
    summer = [m for m in (6, 7, 8) if per[m] < 20]
    if winter or summer:
        problems.append(f"M per month {dict(sorted(per.items()))}")
    for t in "BPKS":
        months = Counter(d.month for d, c in found.items() if t in c)
        if any(months[m] < 1 for m in range(1, 13)):
            problems.append(f"{t} missing in some months: {dict(sorted(months.items()))}")
    print(f"Lopar: {dict(sorted(counts.items()))}, M po mjesecima {dict(sorted(per.items()))}, "
          f"pomaknuto {', '.join(f'{d:%d.%m.}' for d in sorted(moved)) or '-'}")
    for p in problems[:15]:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    old = data["zone"].get("1", {})
    data["zone"] = {"1": {
        "jls": "Lopar", "podrucje": "Općina Lopar", "ulice": ["Lopar"],
        "raw": {**old.get("raw", {}), str(year): podaci.month_lines([(d, c, d in moved) for d, c in found.items()])},
    }}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} (1 zona)")


if __name__ == "__main__":
    main()
