"""Vrbovsko: Komunalac d.o.o. Vrbovsko, the Grad Vrbovsko in five weekday groups (Monday to Friday).

    python3 -m izvori.komunalac_vrbovsko [--year 2026]

The waste-management page embeds the year plan from Google Drive; it is downloaded through the direct
form (drive.google.com/uc?export=download&id=...). Page 1 is an Excel year calendar where whole weeks are
coloured: yellow is a mixed-waste week, yellow hatched (a pattern fill) a week of mixed waste plus the
recyclables bag (paper, cardboard, plastic, metal together), a green frame the glass collection on the
first Tuesday of the month, orange business customers only, blue weekends, red text public holidays.
Page 2 lists the streets and settlements of each weekday group; a household is collected on its group's
weekday in the coloured weeks. Holidays: the notes under the months ("Odvoz po rasporedu 6. siječnja
izvršiti će se 13. siječnja (miješani komunalni i reciklabilni otpad)") move the group of that weekday
to the new date with the types named (or those of the holiday's week); those dates are marked as moved.
A day drawn in two month grids (30.11. also starts the December grid) takes the colour of the coloured one.
"""
import argparse
import html as html_lib
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.perusic import read_grid
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalac-vrbovsko"
SITE = "https://www.komunalac-vrbovsko.hr"
PAGE = SITE + "/djelatnosti/gospodarenje-otpadom"
DRIVE = "https://drive.google.com/uc?export=download&id={id}"
GEN = ["siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja", "kolovoza", "rujna",
       "listopada", "studenog", "prosinca"]
DANI = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK"]
YELLOW, ORANGE, BLUE, GREEN = (1.0, 1.0, 0.0), (1.0, 0.753, 0.0), (0.0, 0.69, 0.941), (0.0, 0.69, 0.314)
KINDS = {YELLOW: "M", "pattern": "MP", ORANGE: "business", BLUE: "weekend", (1.0, 1.0, 1.0): ""}
SMALL = {"OD", "PREMA", "NA", "I", "RASKRŠĆA"}
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Vrbovsko",
    "web": SITE,
    "zupanija": "Primorsko-goranska",
    "jls": ["Vrbovsko"],
    "nazivi": {"P": "Reciklabilni otpad (papir, karton, plastika, metal)"},
    "napomene": [
        "Miješani komunalni otpad odvozi se svaki drugi tjedan, a reciklabilni otpad (papir, karton, plastika, "
        "aluminijska i metalna ambalaža) svaki četvrti tjedan zajedno s miješanim, na dan skupine.",
        "Staklo se odvozi prvog utorka u mjesecu (zeleni okvir u kalendaru); u raspored je upisano svim skupinama.",
        "Blagdanski pomaci prema napomenama u kalendaru su uključeni.",
        "Poslovni subjekti imaju zaseban raspored odvoza miješanog otpada (narančasto u kalendaru); nije uključen.",
        "Informacije: Komunalac d.o.o. Vrbovsko, Željeznička 1A, tel. 051 875 278.",
    ],
}


def fill(c):
    """'pattern' for a pattern fill, else the rounded RGB tuple."""
    if isinstance(c, str):
        return "pattern"
    if isinstance(c, (int, float)):
        c = (c, c, c)
    return tuple(round(float(v), 3) for v in c)


def nice(name):
    name = re.sub(r"(?<=\w)\(", " (", re.sub(r"-\s+", "-", name))
    out = []
    for w in name.split():
        core = w.strip("()")
        out.append(w.lower() if core in SMALL else w if core == "MO"
                   else re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), w))
    return re.sub(r"\.(?=[^\s.])", ". ", " ".join(out))


def streets(text):
    text = re.sub(r"\)\s+(?=[A-ZČĆŠŽĐ])", "), ", " ".join(text.split()))  # "ŠKOLSKA (VRBOVSKO) STUBICA (...)"
    return list(dict.fromkeys(nice(s.strip()) for s in text.split(",") if s.strip()))


def read_plan(path, year):
    """(groups [(weekday, MO text, streets)], {date: kinds}, moves [(old, new, codes or None)], problems)."""
    pdf = pdfplumber.open(path)
    page = pdf.pages[0]
    text = page.extract_text() or ""
    if f"ZA {year}. GODINU" not in " ".join(text.split()):
        return [], {}, [], [f"no '{year}' title"]
    cells, outside, problems = read_grid(page, year)
    if problems:
        return [], {}, [], problems
    rects = [r for r in page.rects if r.get("fill") and r["x1"] - r["x0"] < 200]
    frames = [r for r in rects
              if fill(r["non_stroking_color"]) == GREEN and min(r["x1"] - r["x0"], r["bottom"] - r["top"]) < 3]

    def kinds_at(cx, cy, hw, hh):
        under = [r for r in rects if r["x0"] - 0.5 <= cx <= r["x1"] + 0.5 and r["top"] - 0.5 <= cy <= r["bottom"] + 0.5
                 and min(r["x1"] - r["x0"], r["bottom"] - r["top"]) > 5]
        out = set()
        if under:
            c = fill(min(under, key=lambda r: (r["x1"] - r["x0"]) * (r["bottom"] - r["top"]))["non_stroking_color"])
            out.add(KINDS.get(c, f"? {c}"))
        flat = [r for r in frames if r["x0"] <= cx <= r["x1"] and r["x1"] - r["x0"] > r["bottom"] - r["top"]]
        if any(0 < cy - r["bottom"] < hh + 3 for r in flat) and any(0 < r["top"] - cy < hh + 3 for r in flat):
            out.add("S")  # a green frame above and below the number
        return out - {""}
    found = {d: kinds_at(*c) for d, c in cells.items()}
    for d, c in outside:  # the same day in a neighbouring month's grid
        k = kinds_at(*c)
        if k != found.get(d, set()):
            print(f"   {d:%d.%m.} je u dvije mreže: {sorted(found.get(d, set())) or 'bez boje'} / "
                  f"{sorted(k) or 'bez boje'} – uzeta obojena")
            found[d] = found.get(d, set()) | k
    bad = sorted({k for ks in found.values() for k in ks if k.startswith("?")})
    if bad:
        problems.append(f"unknown colours {bad}")
    # holiday notes
    flat = " ".join(text.split())
    moves = []
    for m in re.finditer(r"Odvoz po rasporedu (\d+)\.\s*(\w+) izvršit?i će se (\d+)\.\s*(\w+)(?:\s*\(([^)]*)\))?", flat):
        try:
            old = date(year, GEN.index(m.group(2)) + 1, int(m.group(1)))
            new = date(year, GEN.index(m.group(4)) + 1, int(m.group(3)))
        except ValueError:
            problems.append(f"note not understood: {m.group(0)}")
            continue
        what = m.group(5) or ""
        codes = ("MP" if "reciklabil" in what else "M") if "miješani" in what else None
        moves.append((old, new, codes))
    if len(moves) != flat.count("Odvoz po rasporedu"):
        problems.append(f"{flat.count('Odvoz po rasporedu')} notes, {len(moves)} understood")
    # groups from the table on page 2
    groups = []
    rows = [r for t in pdf.pages[1].extract_tables() for r in t]
    rows = [[" ".join((c or "").split()) for c in r] for r in rows]
    if len(rows) < 5 or rows[0][:4] != DANI[:4]:
        return [], {}, [], problems + ["street table not understood"]
    for i in range(4):
        groups.append((i, rows[1][i], streets(rows[2][i])))
    m = re.match(r"PETAK (.+)", rows[3][0])
    if not m:
        return [], {}, [], problems + ["no Friday row in the street table"]
    groups.append((4, m.group(1), streets(rows[4][0])))
    return groups, found, moves, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = html_lib.unescape(fetch(PAGE).decode("utf-8", "replace"))
    m = re.search(rf'drive\.google\.com/uc\?export=download&id=([\w-]+)"\s+'
                  rf'title="PLAN ODVOZA OTPADA[^"]*ZA {year}\. GODINU"', html)
    if not m:
        sys.exit(f"Nema plana odvoza za {year} na {PAGE}")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    hol = set(pravila.blagdani(year))
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "plan.pdf"
        fetch(DRIVE.format(id=m.group(1)), dest)
        groups, found, moves, problems = read_plan(dest, year)
    for old, new, codes in moves:
        if old not in hol or abs((new - old).days) > 7:
            problems.append(f"move {old} -> {new} is not a holiday shift")
        if found.get(old, set()) & {"M", "MP"}:
            problems.append(f"{old} is a holiday but coloured")
    glass = sorted(d for d, k in found.items() if "S" in k)
    if any(d.weekday() != 1 for d in glass) or len(glass) not in (12, 13):
        problems.append(f"glass days {[f'{d:%d.%m.}' for d in glass]}")
    for p in problems[:15]:
        print(f"PROBLEM {p}")
    ok, zones = not problems, {}
    for wd, mo, names in groups:
        rows = {}
        for d, k in found.items():
            codes = "".join(c for c in ("M", "MP") if c in k)
            if d.weekday() == wd and codes:
                rows[d] = ["MP" if "MP" in codes else "M", False]
        for old, new, codes in moves:
            if old.weekday() != wd:
                continue
            rows.pop(old, None)
            if codes is None:  # the types of the holiday's week (the other weekdays of that week)
                week = [found.get(old - timedelta(days=old.weekday() - i), set()) for i in range(5) if i != old.weekday()]
                codes = "MP" if any("MP" in k for k in week) else "M"
            rows[new] = [codes if new.weekday() != wd else ("MP" if "MP" in found.get(new, set()) | {codes} else "M"), True]
        for d in glass:
            rows.setdefault(d, ["", False])[0] += "S"
        problems = []
        # mixed waste every two weeks (every week in July and August), recyclables every four (two in summer)
        for t, most in (("M", 21), ("P", 35)):
            days = sorted(d for d, (c, _) in rows.items() if t in c)
            gap = max((b - a).days for a, b in zip(days, days[1:]))
            late = not days or days[0] > date(year, 1, 1) + timedelta(days=most)
            if late or days[-1] < date(year, 12, 31) - timedelta(days=most) or gap > most:
                problems.append(f"{t}: {len(days)} dates, longest gap {gap} days")
        odd = [d for d, (c, mv) in rows.items() if d.weekday() != wd and not mv and c != "S"]
        if odd:
            problems.append(f"off the group weekday: {odd}")
        dates = sorted(d for d, (c, _) in rows.items() if "M" in c)
        gaps = Counter((b - a).days for a, b in zip(dates, dates[1:]))
        name = DANI[wd].capitalize()
        print(f"{name} ({mo}): {len(names)} ulica/naselja, odvoza {len(dates)}, razmaci {dict(sorted(gaps.items()))}, "
              f"pomaknuto {', '.join(f'{d:%d.%m.}' for d, (_, mv) in sorted(rows.items()) if mv) or '-'}")
        for p in problems:
            print(f"   PROBLEM {p}")
        ok = ok and not problems
        key = str(wd + 1)
        old_zone = data["zone"].get(key, {})
        zones[key] = {
            "jls": "Vrbovsko",
            "podrucje": f"{name} – {nice(mo)}",
            "opis": f"{mo}: " + ", ".join(names),
            "ulice": names,
            "raw": {**old_zone.get("raw", {}), str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])},
        }
    if not ok or not zones:
        sys.exit("Ništa nije upisano.")
    print("Pretpostavka: staklo (prvi utorak u mjesecu, zeleni okvir) upisano je svim skupinama.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
