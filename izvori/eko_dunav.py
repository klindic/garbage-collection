"""Borovo: Eko-Dunav d.o.o., mixed waste by three street groups (Wednesday, Thursday, Friday), paper and plastic
by two halves of the village.

    python3 -m izvori.eko_dunav [--year 2026]

The documents page eko-dunav.hr/dokumenti.asp links "KALENDAR ODVOZA OTPADA ZA <year>.pdf" (a Word document):
page 1 has the rules (the streets of the Wednesday, Thursday and Friday mixed-waste rounds; paper on the first
Monday and plastic on the first Tuesday of the month from Crepulje to Bulićeva, on the last Monday and Tuesday
from Bulićeva to Savulja), page 2 the colour calendar (green mixed waste, blue paper, yellow plastic, red
non-working days), page 3 the table "Nema odvoza -> Zamjenski odvoz". The dates come from the rules with the
swap table applied (swapped dates are marked as moved); the colour calendar is the check: every day's colours
must equal the union of all rounds that day, and no collection may be left on a red day.

The provider does not say which mixed-waste group lies in which paper/plastic half, so every street group
appears twice, once with each half (pick the one for your side of Bulićeva).
"""
import argparse
import calendar
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import MONTHS, colour

SLUG = "eko-dunav"
SITE = "https://www.eko-dunav.hr"
PAGE = SITE + "/dokumenti.asp"
WEEK = ["po", "ut", "sr", "če", "pe", "su", "ne"]
PALETTE = {(0.0, 0.69, 0.314): "M", (0.0, 0.69, 0.941): "K", (1.0, 1.0, 0.0): "P", (1.0, 0.0, 0.0): "X"}
SKIP = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.988, 0.835, 0.706)]  # white, grid lines, month headings
LEGEND = {"Miješani komunalni": "M", "Papir i karton": "K", "Plastika": "P", "Neradni dani": "X"}
GROUPS = {"Srijeda": "sri", "Četvrtak": "čet", "Petak": "pet"}
PROVIDER = {
    "davatelj": "Eko-Dunav d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Vukovarsko-srijemska",
    "jls": ["Borovo"],
    "nazivi": {"P": "Plastika"},
    "napomene": [
        "Miješani komunalni otpad odvozi se jednom tjedno: srijedom, četvrtkom ili petkom, ovisno o ulici.",
        "Papir (ponedjeljak) i plastika (utorak): prvi ponedjeljak i utorak u mjesecu od Crepulja do Bulićeve, "
        "zadnji ponedjeljak i utorak u mjesecu od Bulićeve do Savulje. Eko-Dunav ne navodi koje skupine ulica "
        "pripadaju kojoj polovici, pa je svaka skupina ulica upisana dvaput (po jedna zona za svaku polovicu).",
        "Na neradne dane nema odvoza; zamjenski dani iz tablice u kalendaru upisani su i označeni kao pomaknuti.",
    ],
}


def find(pattern, text, what, problems):
    m = re.search(pattern, text, re.S)
    if not m:
        problems.append(f"na 1. stranici nema: {what}")
    return m


LINE_FIX = {"do Bulićeve Bulićeva": "do Bulićeve, Bulićeva"}  # a list item that ends at a line break without a comma


def streets(text):
    text = re.sub(r"\s+", " ", text)
    for a, b in LINE_FIX.items():
        text = text.replace(a, b)
    return [s.strip(" .") for s in re.split(r",\s*", text) if s.strip(" .")]


def read_rules(page, problems):
    text = " ".join((page.extract_text() or "").split())
    groups = {}
    for name in GROUPS:
        m = find(rf"{name}\s*:\s*(.*?)(?=(?:Četvrtak|Petak)\s*:|Raspored odvoza papira|$)", text, name, problems)
        if m:
            groups[name] = m.group(1).strip()
    halves = []
    for which in ("Prvi", "Zadnji"):
        m = find(rf"{which} ponedjeljak\s*\(papir\) i {which.lower()} utorak\s*\(plastika\) u mjesecu odvoz će se "
                 rf"vršiti od (\w+) do (\w+)", text, f"{which.lower()} ponedjeljak/utorak", problems)
        if m:
            halves.append((which.lower(), m.group(1), m.group(2)))
    words = page.extract_words()
    fills = [r for r in page.rects if r.get("fill") and colour(r)]
    for label, code in LEGEND.items():
        w = next((w for w in words if label.startswith(w["text"]) and label in " ".join(
            x["text"] for x in words if abs(x["top"] - w["top"]) < 2)), None)
        cy = (w["top"] + w["bottom"]) / 2 if w else -1
        left = [r for r in fills if w and r["x1"] <= w["x0"] + 1 and r["x1"] > w["x0"] - 15 and r["top"] <= cy <= r["bottom"]
                and r["x1"] - r["x0"] > 10]
        if not left or lookup(colour(left[0])) != code:
            problems.append(f"legenda '{label}': uzorak boje ne odgovara")
    return groups, halves


def lookup(col):
    return next((c for rgb, c in PALETTE.items() if all(abs(a - b) < 0.03 for a, b in zip(col, rgb))), "?")


def read_grid(page, year, problems):
    """({date: set of codes}, days present) from the colour calendar.

    A fill belongs to every day number in a column it covers (by at least a third of the column) whose centre
    lies within its height, so split cells and fills spanning several days both work."""
    words = page.extract_words()
    fills = [r for r in page.rects if r.get("fill") and colour(r)
             and not any(all(abs(a - b) < 0.03 for a, b in zip(colour(r), s)) for s in SKIP)]
    heads = [w for w in words if w["text"].lower() in WEEK]
    rows = []
    for top in sorted({round(w["top"]) for w in heads}):
        line = sorted((w for w in heads if abs(w["top"] - top) < 2), key=lambda w: w["x0"])
        for i in range(len(line) - 6):
            if [w["text"].lower() for w in line[i:i + 7]] == WEEK:
                rows.append(line[i:i + 7])
    titles = [w for w in words if w["text"].upper() in MONTHS]
    if len(titles) != 12:
        problems.append(f"kalendar: naslovi mjeseci {[w['text'] for w in titles]}")
    cells = []  # (date, x0, x1, cy) per day number
    for t in titles:
        mo = MONTHS.index(t["text"].upper()) + 1
        tx = (t["x0"] + t["x1"]) / 2
        g = [g for g in rows if 0 < g[0]["top"] - t["bottom"] < 15 and g[0]["x0"] - 10 < tx < g[-1]["x1"] + 10]
        if len(g) != 1:
            problems.append(f"{t['text']}: {len(g)} weekday rows")
            continue
        g = g[0]
        cx = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (cx[-1] - cx[0]) / 6
        bounds = [cx[0] - pitch / 2] + [(a + b) / 2 for a, b in zip(cx, cx[1:])] + [cx[-1] + pitch / 2]
        first, ndays = date(year, mo, 1).weekday(), calendar.monthrange(year, mo)[1]
        below = [o["top"] for o in titles if o["top"] > t["top"] + 5 and bounds[0] <= (o["x0"] + o["x1"]) / 2 <= bounds[-1]]
        limit = min(below, default=page.height)
        nums = [w for w in words if w["text"].isdigit() and bounds[0] <= (w["x0"] + w["x1"]) / 2 <= bounds[-1]
                and g[0]["bottom"] < w["top"] < limit]
        tops = {}
        for w in nums:
            v = int(w["text"])
            col = next((i for i in range(7) if bounds[i] <= (w["x0"] + w["x1"]) / 2 < bounds[i + 1]), None)
            if not 1 <= v <= ndays or col != date(year, mo, v).weekday():
                problems.append(f"{year}-{mo:02d}: {w['text']} in column {col}")
                continue
            tops.setdefault((v + first - 1) // 7, []).append((w["top"] + w["bottom"]) / 2)
            cells.append((date(year, mo, v), bounds[col], bounds[col + 1], w["top"], w["bottom"]))
        order = sorted(tops)
        mids = [sorted(tops[r])[len(tops[r]) // 2] for r in order]
        if any(b - a < 8 for a, b in zip(mids, mids[1:])):
            problems.append(f"{year}-{mo:02d}: week rows out of order")
    seen = Counter(c[0] for c in cells)
    present = set(seen)
    if any(n > 1 for n in seen.values()):
        problems.append(f"days twice in the calendar: {[d for d, n in seen.items() if n > 1]}")
    out = {}
    for f in fills:
        code = lookup(colour(f))
        fx = (f["x0"] + f["x1"]) / 2
        hit = [d for d, x0, x1, top, bottom in cells
               if (x0 <= fx <= x1 or min(x1, f["x1"]) - max(x0, f["x0"]) >= (x1 - x0) / 3)
               and f["top"] <= bottom - 2 and f["bottom"] >= top + 2]
        if code == "?":
            problems.append(f"fill {colour(f)} at {f['x0']:.0f},{f['top']:.0f}: unknown colour")
        elif not hit:
            print(f"   obojena ćelija bez datuma ({code}) na {f['x0']:.0f},{f['top']:.0f}")
        for d in hit:
            out.setdefault(d, set()).add(code)
    return out, present


def read_swaps(page, year, problems):
    """{no-collection date: replacement date} from the table on page 3."""
    text = page.extract_text() or ""
    if "Nema odvoza" not in text or "Zamjenski odvoz" not in text:
        problems.append("3. stranica: nema tablice 'Nema odvoza / Zamjenski odvoz'")
    swaps = {}
    for a, b in re.findall(r"(\d{2}\.\d{2}\.\d{4})\.\s+(\d{2}\.\d{2}\.\d{4})\.", text):
        da, db = (date(int(x[6:]), int(x[3:5]), int(x[:2])) for x in (a, b))
        if da.year != year and db.year == year:
            print(f"   NAPOMENA: u tablici zamjena piše {a}. -> {b}.; uzeto {da.day:02d}.{da.month:02d}.{year}.")
            da = da.replace(year=year)
        if abs((da - db).days) > 6:
            problems.append(f"zamjena {a} -> {b} je predaleko")
        swaps[da] = db
    if len(swaps) < 5:
        problems.append(f"samo {len(swaps)} zamjena u tablici")
    return swaps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("windows-1250", "replace")
    m = re.search(rf'href="([^"]*KALENDAR ODVOZA OTPADA ZA {year}[^"]*\.pdf)"', html, re.I)
    if not m:
        sys.exit(f"Nema kalendara za {year} na {PAGE}")
    url = m.group(1) if m.group(1).startswith("http") else SITE + "/" + m.group(1).lstrip("/")
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        fetch(url, Path(tmp) / "k.pdf")
        pages = pdfplumber.open(Path(tmp) / "k.pdf").pages
        if len(pages) != 3 or f"ZA {year}. GODINU" not in (pages[0].extract_text() or ""):
            sys.exit(f"Kalendar nema očekivane 3 stranice / naslov za {year}.")
        groups, halves = read_rules(pages[0], problems)
        grid, present = read_grid(pages[1], year, problems)
        swaps = read_swaps(pages[2], year, problems)
    hol = set(pravila.blagdani(year))
    red = {d for d, c in grid.items() if "X" in c}
    off = red | set(swaps)
    if hol - red - {d for d in hol if d.weekday() == 6}:
        print(f"   blagdani koji nisu crveni u kalendaru: {sorted(hol - red)}")

    def shifted(dates, code):
        out = []
        for d in dates:
            if d in swaps:
                out.append((swaps[d], code, True))
            elif d in off:
                problems.append(f"{d} ({code}) je neradni dan, a nema zamjene u tablici")
            else:
                out.append((d, code, False))
        return out

    mixed = {name: shifted(pravila.tjedno(year, key), "M") for name, key in GROUPS.items()}
    pk = {}
    for which, a, b in halves:
        n = 1 if which == "prvi" else -1
        pk[which] = shifted(pravila.mjesecno(year, "pon", n), "K") + shifted(pravila.mjesecno(year, "uto", n), "P")
    # the colour calendar must show exactly the union of all rounds
    expected = {}
    for rows in list(mixed.values()) + list(pk.values()):
        for d, c, _ in rows:
            expected.setdefault(d, set()).add(c)
    missing = [date.fromordinal(o) for o in range(date(year, 1, 1).toordinal(), date(year, 12, 31).toordinal() + 1)
               if date.fromordinal(o) not in present]
    print(f"   dani kojih nema u kalendaru u boji: {', '.join(f'{d:%d.%m.}' for d in missing)}")
    unmet = {(d, c) for d, cs in expected.items() for c in cs if c not in grid.get(d, set())}
    extra = {(d, c) for d, cs in grid.items() for c in cs - {"X"} if c not in expected.get(d, set())}
    dropped = []  # rule date missing from the grid, the grid shows that bin on another day of the week instead
    for d, c in sorted(unmet):
        alt = [(d2, c2) for d2, c2 in extra if c2 == c and d2.month == d.month and abs((d2 - d).days) <= 7]
        if d in missing and len(alt) == 1:
            dropped.append((d, c, alt[0][0]))
            unmet.discard((d, c))
            extra.discard(alt[0])
    for d, c in sorted(unmet | extra):
        problems.append(f"{d}: {c} {'po pravilima, a ne u kalendaru' if (d, c) in unmet else 'u kalendaru, a ne po pravilima'}")
    drop = {(d, c) for d, c, _ in dropped}
    notes = {}
    for d, c, alt in dropped:
        what = {"K": "papir", "P": "plastika", "M": "miješani otpad"}[c]
        print(f"   SUKOB: {what} {d:%d.%m.} po pravilu, kalendar u boji nema taj dan i ima {what} {alt:%d.%m.}; nije upisano")
        for rows in list(mixed.values()) + list(pk.values()):
            if any((r[0], r[1]) == (d, c) for r in rows):
                key = id(rows)
                notes[key] = notes.get(key, []) + [f"{d:%m.%Y.}: po pravilu {what} {d:%d.%m.}, a kalendar u boji "
                                                   f"(bez {d.day}. dana) {alt:%d.%m.}; taj odvoz nije upisan."]
                rows[:] = [r for r in rows if (r[0], r[1]) != (d, c)]
    zones, n = {}, 0
    for name, key in GROUPS.items():
        for which, a, b in halves:
            n += 1
            rows = {}
            for d, c, moved in mixed[name] + pk[which]:
                code, mv = rows.get(d, ("", False))
                rows[d] = (code + c, mv or moved)
            rows = [(d, c, mv) for d, (c, mv) in rows.items()]
            gaps = {(d.month, c) for d, c in drop}
            for mo in range(1, 13):
                cnt = Counter(c for d, cs, _ in rows if d.month == mo for c in cs)
                if not 4 <= cnt["M"] <= 5 or cnt["K"] != 1 - ((mo, "K") in gaps and which == "zadnji" and cnt["K"] == 0) \
                        or cnt["P"] != 1:
                    problems.append(f"zona {n} {year}-{mo:02d}: {dict(cnt)}")
            ulice = streets(groups.get(name, ""))
            if len(ulice) < 5:
                problems.append(f"{name}: samo {len(ulice)} ulica")
            zones[str(n)] = {
                "jls": "Borovo",
                "podrucje": f"{name} (miješani); papir i plastika {which} ponedjeljak/utorak u mjesecu (od {a} do {b})",
                "opis": f"{name}: {groups.get(name, '')}", "ulice": ulice, "raw": {str(year): podaci.month_lines(rows)}}
            note = notes.get(id(mixed[name]), []) + notes.get(id(pk[which]), [])
            if note:
                zones[str(n)]["napomena"] = " ".join(note)
            mv = sorted(f"{d:%d.%m.}" for d, _, m in rows if m)
            print(f"Zona {n} ({name}, {which}): {len(rows)} dana {dict(sorted(Counter(c for _, cs, _ in rows for c in cs).items()))}, "
                  f"pomaknuto {len(mv)} ({', '.join(mv)}), {len(ulice)} ulica")
    print(f"Kalendar u boji: {len(grid)} obojenih dana, crvenih {len(red)}, zamjena {len(swaps)}")
    for p in problems[:30]:
        print(f"   PROBLEM {p}")
    if problems or len(zones) != 6:
        sys.exit("Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    for key, zone in zones.items():
        prev = {y: v for y, v in old.get(key, {}).get("raw", {}).items() if y != str(year)}
        zone["raw"] = {**prev, **zone["raw"]}
    podaci.save(SLUG, {**PROVIDER, "napomene": PROVIDER["napomene"] + [f"Izvor: {url}"], "zone": zones})
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
