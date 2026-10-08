"""Perušić: Perušić d.o.o. za komunalne djelatnosti, two areas of the Općina Perušić (Perušić and Kosinj).

    python3 -m izvori.perusic [--year 2026]

The calendar page links one PDF per area ("kalendar-odvoza-<year>-perusic.pdf", "...-kosinj.pdf"): a vector
year calendar (12 month grids, PON..NED) where the cell fill is the waste type: dark green mixed waste,
yellow plastic, light blue paper and cardboard, metal and glass (one collection), orange bulky waste
(on request). The colours are taken from the legend samples, and the settlements of an area from the
heading. Holidays: the dates are the published ones (holiday numbers are red and uncoloured, the
collection is drawn on the replacement day); a collection off the usual weekday within a week of a
holiday is marked as moved, anywhere else it is an error. A day of the previous year shown in the
January grid (31.12.) is left out.
"""
import argparse
import calendar
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import MONTHS, colour, lookup, weekday_rows

SLUG = "perusic"
SITE = "https://www.komunalac-perusic.hr"
PAGE = SITE + "/usluga-prikupljanja-otpada/kalendar-odvoz-mijesanog-komunalnog-otpada"
LEGEND = {"MIJEŠANI": "M", "PLASTIKA": "P", "PAPIR": "K", "GLOMAZNI": "G"}
EXPECTED = {(0.247, 0.459, 0.169): "M", (1.0, 1.0, 0.4): "P", (0.518, 0.863, 0.976): "K", (1.0, 0.6, 0.0): "G"}
IGNORE = ((1.0, 1.0, 1.0), (0.949, 0.949, 0.949))
GAPS = {date(2026, 11, 30)}  # the Perušić 2026 calendar has no row for 30 November (a Monday, no collection)
PER_YEAR = {"M": (25, 28), "P": (11, 13), "K": (11, 13), "G": (0, 2)}
PROVIDER = {
    "davatelj": "Perušić d.o.o. za komunalne djelatnosti",
    "web": SITE,
    "zupanija": "Ličko-senjska",
    "jls": ["Perušić"],
    "nazivi": {"K": "Papir i karton, metal, staklo", "G": "Glomazni otpad (obavezna najava)"},
    "napomene": [
        "Miješani komunalni otpad odvozi se svaka dva tjedna; plastika te papir i karton, metal i staklo "
        "jednom mjesečno.",
        "Glomazni otpad odvozi se samo uz obaveznu najavu (053/679-269, perusicdoo@gmail.com).",
        "Blagdanski pomaci ugrađeni su u kalendar.",
    ],
}


# read_grid and fill_at are also used by the med_eko_servis, rakovica, komunalac_vrbovsko and lopar_vrutak
# scripts (they would fit in kalendar_boje.py: CMYK and pattern fills, days of other months, misprints).
def read_grid(page, year, fix=None, typos=None, gaps=()):
    """({date: cell}, [(day of another month shown, its cell)], problems) of 12 month grids; a cell is
    (column centre, number centre y, half column width, half row height).

    Every day number must sit in its week row and weekday column and every day of the year must appear
    once. fix: {word: replacement} for broken text, or a function words -> words; typos: {date: shown
    number} for known misprints (the grid position gives the date); gaps: days known to be missing."""
    typos = typos or {}
    words = page.extract_words()
    words = fix(words) if callable(fix) else [{**w, "text": (fix or {}).get(w["text"], w["text"])} for w in words]
    rows = weekday_rows(words)
    heads = [w for w in words if w["text"].upper() in MONTHS]
    problems, cells, outside, blocks = [], {}, [], []
    for h in heads:
        hx = (h["x0"] + h["x1"]) / 2
        below = [g for g in rows if 0 <= g[0]["top"] - h["bottom"] < 40 and g[0]["x0"] - 5 <= hx <= g[-1]["x1"] + 5]
        if not below:  # a month name in other text
            continue
        g = min(below, key=lambda g: g[0]["top"])
        mo = MONTHS.index(h["text"].upper()) + 1
        if mo in blocks:
            problems.append(f"{h['text']}: two month grids")
        blocks.append(mo)
        cols = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (cols[-1] - cols[0]) / 6
        nxt = [x["top"] for x in heads if x["top"] > h["bottom"] + 5 and abs((x["x0"] + x["x1"]) / 2 - hx) < 3 * pitch]
        nxt += [r[0]["top"] - 25 for r in rows if r[0]["top"] > g[0]["top"] + 5 and abs(r[0]["x0"] - g[0]["x0"]) < pitch]
        limit = min(nxt, default=g[0]["bottom"] + 7 * pitch)
        inside = [w for w in words if w["text"].isdigit() and g[0]["bottom"] < w["top"] < limit
                  and cols[0] - pitch / 2 <= (w["x0"] + w["x1"]) / 2 <= cols[-1] + pitch / 2]
        lines = []
        for w in sorted(inside, key=lambda w: w["top"]):
            if not lines or w["top"] - lines[-1] > 4:
                lines.append(w["top"])
        steps = sorted(b - a for a, b in zip(lines, lines[1:]))
        for i in range(1, len(lines)):  # the grid ends at the first big vertical gap (a footer below it)
            if steps and lines[i] - lines[i - 1] > 1.6 * steps[len(steps) // 2]:
                inside = [w for w in inside if w["top"] < lines[i] - 2]
                lines = lines[:i]
                break
        rowpitch = (lines[-1] - lines[0]) / max(len(lines) - 1, 1)
        first = date(year, mo, 1)
        for w in inside:
            cx = (w["x0"] + w["x1"]) / 2
            col = min(range(7), key=lambda i: abs(cols[i] - cx))
            row = min(range(len(lines)), key=lambda i: abs(lines[i] - w["top"]))
            d = first + timedelta(days=row * 7 + col - first.weekday())
            if abs(cols[col] - cx) > pitch / 3 or (int(w["text"]) != d.day and typos.get(d) != w["text"]):
                problems.append(f"{year}-{mo:02d}: number {w['text']} in week row {row + 1}, column {col + 1}")
            elif d.month != mo:
                outside.append((d, (cols[col], (w["top"] + w["bottom"]) / 2, pitch / 2, rowpitch / 2)))
            elif d in cells:
                problems.append(f"{d} twice in the grid")
            else:
                cells[d] = (cols[col], (w["top"] + w["bottom"]) / 2, pitch / 2, rowpitch / 2)
    if sorted(blocks) != list(range(1, 13)):
        return {}, [], problems + [f"month grids {sorted(blocks)}"]
    for d in (date(year, 1, 1) + timedelta(days=i) for i in range(366 if calendar.isleap(year) else 365)):
        if d not in cells and d not in gaps:
            problems.append(f"{d} missing from the grid")
        elif d not in cells:
            print(f"   {d:%d.%m.%Y.} nema u kalendaru (poznati propust u PDF-u)")
    return cells, outside, problems


def fill_at(fills, x, y):
    """Colour of the smallest filled rectangle around a point (None if there is none)."""
    under = [s for s in fills if s["x0"] - 0.5 <= x <= s["x1"] + 0.5 and s["top"] - 0.5 <= y <= s["bottom"] + 0.5]
    if not under:
        return None
    return colour(min(under, key=lambda s: (s["x1"] - s["x0"]) * (s["bottom"] - s["top"])))


def read_area(path, year):
    """(area name, settlements, {date: code}, problems) of one area PDF."""
    page = pdfplumber.open(path).pages[0]
    text = (page.extract_text() or "").splitlines()
    problems = []
    title = next((l for l in text if re.match(rf"PODRUČJE (.+) - {year}$", l.strip())), None)
    if not title:
        return None, [], {}, [f"no 'PODRUČJE ... - {year}' title"]
    start = next(i for i, l in enumerate(text) if "KALENDAR ODVOZA" in l) + 1
    end = next(i for i, l in enumerate(text) if l.startswith("MIJEŠANI KOMUNALNI OTPAD"))
    places = [p.strip() for p in " ".join(text[start:end]).split(",") if p.strip()]
    cells, outside, problems = read_grid(page, year, gaps=GAPS)
    if not cells:
        return None, [], {}, problems
    fills = [s for s in page.rects if s.get("fill") and colour(s) and s["x1"] - s["x0"] < 100]
    # the legend: a sample square left of each label, its colour must be the expected one
    words = page.extract_words()
    palette = {}
    for word, code in LEGEND.items():
        w = next((w for w in words if w["text"] == word and w["top"] < min(c[1] for c in cells.values())), None)
        col = w and fill_at(fills, w["x0"] - 15, (w["top"] + w["bottom"]) / 2)
        if not col or lookup(col, EXPECTED) != code:
            problems.append(f"legend {word}: colour {col}")
        else:
            palette[col] = code
    found = {}
    for d, (cx, cy, _, _) in cells.items():
        col = fill_at(fills, cx, cy)
        if col is None or col in IGNORE:
            continue
        code = lookup(col, palette, tol=0.02)
        if code == "?":
            problems.append(f"{d}: unknown colour {col}")
        else:
            found[d] = code
    for d, _ in outside:
        print(f"   {d:%d.%m.%Y.} je prikazan u kalendaru {year}. i nije uključen")
    return re.match(rf"PODRUČJE (.+) - {year}", title.strip()).group(1), places, found, problems


def nice(name):
    return " ".join(w.capitalize() if len(w) > 1 else w.lower() for w in name.split())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    links = list(dict.fromkeys(re.findall(rf'href="(https://[^"]*/kalendar-odvoza-{year}-([a-z]+)\.pdf)"', html)))
    if not links:
        sys.exit(f"Nema kalendara za {year} na {PAGE}")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    hol = set(pravila.blagdani(year))
    ok, zones = True, {}
    with tempfile.TemporaryDirectory() as tmp:
        for url, name in links:
            dest = Path(tmp) / f"{name}.pdf"
            fetch(url, dest)
            area, places, found, problems = read_area(dest, year)
            usual = {t: Counter(d.weekday() for d, c in found.items() if t in c).most_common(1)[0][0]
                     for t in "MPK" if any(t in c for c in found.values())}
            rows = []
            for d, c in sorted(found.items()):
                off = c in usual and d.weekday() != usual[c]
                if off and not any(abs((d - h).days) <= 6 for h in hol):
                    problems.append(f"{d}: {c} on {podaci.DAYS[d.weekday()]} without a holiday near")
                rows.append((d, c, off))
            counts = Counter(c for _, c, _ in rows)
            for t, (lo, hi) in PER_YEAR.items():
                if not lo <= counts[t] <= hi:
                    problems.append(f"{counts[t]}x {t} in the year")
            for t in "PK":
                if any(sum(1 for d, c, _ in rows if c == t and d.month == m) != 1 for m in range(1, 13)):
                    problems.append(f"{t} is not once a month")
            m_month = Counter(d.month for d, c, _ in rows if c == "M")
            if any(not 2 <= m_month[m] <= 3 for m in range(1, 13)):
                problems.append(f"M per month {dict(sorted(m_month.items()))}")
            moved = [f"{d:%d.%m.}" for d, _, m in rows if m]
            print(f"{area}: {dict(sorted(counts.items()))}, {len(places)} naselja, pomaknuto {', '.join(moved) or '-'}")
            for p in problems[:15]:
                print(f"   PROBLEM {p}")
            if problems or not area:
                ok = False
                continue
            key = str(len(zones) + 1)
            old = data["zone"].get(key, {})
            names = [nice(p) for p in places]
            zones[key] = {
                "jls": "Perušić",
                "podrucje": f"Područje {nice(area)}: " + ", ".join(names[:4]) + (", …" if len(names) > 4 else ""),
                "opis": ", ".join(places),
                "ulice": names,
                "raw": {**(old.get("raw", {}) if old.get("opis") == ", ".join(places) else {}),
                        str(year): podaci.month_lines(rows)},
            }
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zone)")


if __name__ == "__main__":
    main()
