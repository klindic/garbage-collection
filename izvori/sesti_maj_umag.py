"""6. MAJ d.o.o. Umag: Umag, Novigrad, Buje, Brtonigla, Grožnjan and Oprtalj, one calendar PDF per town.

    python3 -m izvori.sesti_maj_umag [--year 2026]

The "Plan odvoza" page links one calendar PDF per town (Kalendar-<year>-<Town>.pdf, vector, text + shapes).
A group's collection days are the day numbers framed by the group's shape (square, circle, diamond or
triangle); the legend under the grid gives each shape a weekday and a list of settlements, so a group is a
zone. An empty shape is mixed waste only; a blue, yellow or green filled shape is mixed waste plus paper,
plastic and metal or glass that day (the legend calls mixed waste weekly, and every week's collection day
carries a shape, coloured or not). Shapes are read from the drawing paths, colours by family (the PDFs use
two shades of blue and yellow).
Holidays: the shapes sit on the real collection days (1.1. and 6.1. are normal collection days, for
example), so the calendar is used as it is; a shape off the group's weekday next to a holiday would be
marked as moved, anywhere else it is an error. A legend group without a shape has no dates and is left out.
"""
import argparse
import calendar
import csv
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

SLUG = "6-maj-umag"
SITE = "https://www.6maj.hr"
PAGE = SITE + "/plan-odvoza/"
TOWNS = {  # file name part -> JLS (exact registry name)
    "Umag": "Umag – Umago", "Novigrad": "Novigrad – Cittanova", "Buje": "Buje – Buie",
    "Brtonigla": "Brtonigla – Verteneglio", "Groznjan": "Grožnjan – Grisignana", "Oprtalj": "Oprtalj – Portole",
}
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ",
          "KOLOVOZ", "RUJAN", "LISTOPAD", "STUDENI", "PROSINAC"]
WEEK = ["Po", "Ut", "Sr", "Če", "Pe", "Su", "Ne"]
DANI = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak", "Subota", "Nedjelja"]
CODES = {"blue": "K", "yellow": "P", "green": "S"}
LEGEND = {"MIJEŠANI": "white", "PAPIR": "blue", "PLASTIKA": "yellow", "STAKLO": "green"}
PER_YEAR = {"M": (50, 53), "K": (11, 13), "P": (11, 13), "S": (5, 7)}
FIX = {"Vilanija Volparija": ["Vilanija", "Volparija"]}  # missing comma in the Umag legend
PROVIDER = {
    "davatelj": "6. MAJ d.o.o. za komunalne usluge Umag",
    "web": SITE,
    "zupanija": "Istarska",
    "jls": list(TOWNS.values()),
    "napomene": [
        "Miješani komunalni otpad odvozi se svaki tjedan na dan skupine; papir i karton te plastika i metal "
        "jednom mjesečno, staklo svaki drugi mjesec, isti dan kad i miješani otpad.",
        "Spremnike za miješani i reciklabilni otpad iznijeti na javnu površinu na dan odvoza od 5:00 sati; "
        "otpad ostavljen uz spremnik neće se odvesti.",
        "Odvoz se obavlja i na blagdane: kalendar označava stvarne dane odvoza.",
        "Reciklažna dvorišta: Buje (Momjanska ulica 6), Novigrad (Salvela 44), Umag (Bujska ulica 6); "
        "zimi (01.10.–31.05.) pon–pet 9–16, sub 9–14, ljeti (01.06.–30.09.) pon–pet 10–17, sub 8–13, "
        "nedjeljom i praznikom zatvoreno. Višak reciklabilnog otpada predaje se besplatno u reciklažnom dvorištu.",
        "Informacije: 6. MAJ d.o.o., +385 52 741 585, info@6maj.hr.",
    ],
}


def family(col):
    if col is None:
        return None
    if isinstance(col, (int, float)) or len(col) == 1:
        col = (float(col if isinstance(col, (int, float)) else col[0]),) * 3
    if len(col) != 3:
        return "?"
    r, g, b = col
    if min(col) > 0.95:
        return "white"
    if b > 0.75 and r < 0.3 and 0.55 < g < 0.8:
        return "blue"
    if r > 0.9 and g > 0.9 and b < 0.2:
        return "yellow"
    if 0.4 < r < 0.7 and g > 0.65 and b < 0.4:
        return "green"
    return "?"


def shape_kind(o):
    """'kvadrat', 'krug', 'romb' or 'trokut' for a day frame or legend sample, else None."""
    w, h = o["x1"] - o["x0"], o["bottom"] - o["top"]
    if not (7 <= w <= 22 and 7 <= h <= 22):
        return None
    if o["object_type"] == "rect":
        return "kvadrat" if abs(w - h) < 1 else None
    cmds = "".join(c[0] for c in o.get("path") or [])
    if not re.fullmatch(r"m[lc]+h?", cmds):
        return None
    if "c" in cmds:
        return "krug" if cmds.count("c") == 4 and abs(w - h) < 1 else None
    cx, cy = (o["x0"] + o["x1"]) / 2, (o["top"] + o["bottom"]) / 2

    def has(x, y):
        return any(abs(px - x) < 0.6 and abs(py - y) < 0.6 for px, py in o["pts"])
    if all(has(x, y) for x, y in ((cx, o["top"]), (o["x0"], cy), (o["x1"], cy), (cx, o["bottom"]))):
        return "romb"
    if all(has(x, y) for x, y in ((cx, o["top"]), (o["x0"], o["bottom"]), (o["x1"], o["bottom"]))):
        return "trokut"
    if all(has(x, y) for x in (o["x0"], o["x1"]) for y in (o["top"], o["bottom"])):
        return "kvadrat"
    return None


def month_blocks(words, problems):
    """[(month, the 7 weekday heading words)]."""
    heads = [w for w in words if w["text"] in WEEK]
    rows = []
    for top in sorted({round(w["top"]) for w in heads}):
        row = sorted((w for w in heads if abs(w["top"] - top) < 3), key=lambda w: w["x0"])
        i = 0
        while i + 7 <= len(row):
            if [w["text"] for w in row[i:i + 7]] == WEEK:
                rows.append(row[i:i + 7])
                i += 7
            else:
                i += 1
    rows = list({(round(g[0]["top"]), round(g[0]["x0"])): g for g in rows}.values())
    blocks = []
    for w in words:
        if w["text"].upper() not in MONTHS:
            continue
        below = [g for g in rows if 0 <= g[0]["top"] - w["bottom"] < 25
                 and g[0]["x0"] - 15 <= w["x0"] and w["x1"] <= g[-1]["x1"] + 60]
        if len(below) != 1:
            problems.append(f"{w['text']}: {len(below)} weekday rows under the heading")
            continue
        blocks.append((MONTHS.index(w["text"].upper()) + 1, below[0]))
    if sorted(m for m, _ in blocks) != list(range(1, 13)):
        problems.append(f"month grids {sorted(m for m, _ in blocks)}")
    return blocks


def day_cells(words, blocks, year, problems):
    """{date: (cx, cy)} of every day number, checked against its week row and weekday column."""
    cells = {}
    for mo, g in blocks:
        cols = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (cols[-1] - cols[0]) / 6
        inside = [w for w in words if w["text"].isdigit() and 0 < w["top"] - g[0]["bottom"] < 6.5 * pitch
                  and cols[0] - pitch / 2 <= (w["x0"] + w["x1"]) / 2 <= cols[-1] + pitch / 2]
        lines = []
        for w in sorted(inside, key=lambda w: w["top"]):
            if not lines or w["top"] - lines[-1] > 4:
                lines.append(w["top"])
        offset = date(year, mo, 1).weekday()
        for w in inside:
            cx = (w["x0"] + w["x1"]) / 2
            col = min(range(7), key=lambda i: abs(cols[i] - cx))
            row = min(range(len(lines)), key=lambda i: abs(lines[i] - w["top"]))
            day = row * 7 + col - offset + 1
            if int(w["text"]) != day or not 1 <= day <= calendar.monthrange(year, mo)[1]:
                problems.append(f"{year}-{mo:02d}: number {w['text']} in week row {row + 1}, column {WEEK[col]}")
                continue
            d = date(year, mo, day)
            if d in cells:
                problems.append(f"{d} twice in the grid")
            cells[d] = (cx, (w["top"] + w["bottom"]) / 2)
    for mo in range(1, 13):
        for day in range(1, calendar.monthrange(year, mo)[1] + 1):
            if date(year, mo, day) not in cells:
                problems.append(f"{date(year, mo, day)} missing from the grid")
    return cells


def read_town(path, town, year):
    """(zones [(weekday, kind, opis)], {date: (kind, colour family)}, problems) of one town's calendar."""
    problems = []
    page = pdfplumber.open(path).pages[0]
    words = page.extract_words()
    text = " ".join((page.extract_text() or "").split())
    if not re.search(rf"KALENDAR ODVOZA OTPADA {year}", text):
        return [], {}, [f"no 'KALENDAR ODVOZA OTPADA {year}' title"]
    blocks = month_blocks(words, problems)
    if problems:
        return [], {}, problems
    cells = day_cells(words, blocks, year, problems)
    grid_bottom = max(cy for _, cy in cells.values()) + 12
    grid_top = min(g[0]["top"] for _, g in blocks)
    shapes = [(shape_kind(o), o) for o in page.rects + page.curves]
    shapes = [(k, o) for k, o in shapes if k]
    found = {}
    for d, (cx, cy) in cells.items():
        on = [(k, o) for k, o in shapes if o["x0"] - 0.5 <= cx <= o["x1"] + 0.5
              and o["top"] - 0.5 <= cy <= o["bottom"] + 0.5 and abs((o["x0"] + o["x1"]) / 2 - cx) < 5]
        kinds = {k for k, _ in on}
        fills = {family(o["non_stroking_color"]) for _, o in on if o["fill"]} - {"white"}
        if len(kinds) > 1 or len(fills) > 1:
            problems.append(f"{d}: shapes {sorted(kinds)}, colours {sorted(fills)}")
        elif kinds:
            if "?" in fills:
                problems.append(f"{d}: unknown colour")
            found[d] = (kinds.pop(), fills.pop() if fills else "white")
    for k, o in shapes:  # every frame in the grid must sit on a day
        cx, cy = (o["x0"] + o["x1"]) / 2, (o["top"] + o["bottom"]) / 2
        if grid_top < cy < grid_bottom and not any(abs(cx - x) < 5 and abs(cy - y) < 9 for x, y in cells.values()):
            problems.append(f"{k} at {cx:.0f},{cy:.0f} is not on a day")
    # legend: weekday labels ("Utorak/Martedi"), the shape left of each, the settlements right of it
    labels = sorted((w for w in words if w["top"] > grid_bottom
                     and re.match(r"(Ponedjeljak|Utorak|Srijeda|Četvrtak|Petak|Subota)/", w["text"])),
                    key=lambda w: w["top"])
    samples = [(k, o) for k, o in shapes if o["top"] > grid_bottom]
    stop = min((w["top"] for w in words if w["top"] > grid_bottom and w["text"] == "MIJEŠANI"), default=None)
    if stop is None or not labels:
        return [], {}, problems + ["legend not found"]
    zones = []
    for i, lab in enumerate(labels):
        end = labels[i + 1]["top"] - 2 if i + 1 < len(labels) else stop - 2
        lines = {}
        for w in words:
            if w["x0"] > lab["x1"] + 5 and lab["top"] - 2 <= w["top"] < end:
                lines.setdefault(round(w["top"]), []).append(w)
        opis = ""
        for _, ws in sorted(lines.items()):  # a settlement list line without a final comma still ends a name
            line = " ".join(x["text"] for x in sorted(ws, key=lambda x: x["x0"]))
            opis += ("" if not opis else " " if opis.endswith((",", ".")) or "." in line else ", ") + line
        opis = re.sub(r"\s*,[\s,]*", ", ", re.sub(r"\s*Sono inclusi.*", "", opis)).strip(" ,")
        mid = (lab["top"] + lab["bottom"]) / 2
        left = [k for k, o in samples if 0 < lab["x0"] - o["x1"] < 15 and o["top"] - 3 < mid < o["bottom"] + 3]
        wd = DANI.index(lab["text"].split("/")[0])
        if len(left) > 1:
            problems.append(f"legend {lab['text']}: shapes {left}")
        zones.append((wd, left[0] if left else None, opis))
    # colour legend samples must match their labels
    for word, fam in LEGEND.items():
        w = next((w for w in words if w["text"] == word and w["top"] >= stop - 1), None)
        mid = w and (w["top"] + w["bottom"]) / 2
        sample = w and [o for k, o in samples if 0 < w["x0"] - o["x1"] < 20 and o["top"] - 3 < mid < o["bottom"] + 3]
        fills = sample and {family(o["non_stroking_color"]) for o in sample if o["fill"]} or {"white"}
        if not sample or fills != {fam}:
            problems.append(f"colour legend {word}: {sorted(fills) if sample else 'no sample'}, expected {fam}")
    used = {k for k, _ in found.values()}
    named = {k for _, k, _ in zones if k}
    if used - named:
        problems.append(f"shapes {sorted(used - named)} in the calendar but not in the legend")
    return zones, found, problems


def jls_names():
    with open(podaci.ROOT / "istrazivanje" / "jls_davatelj.csv", encoding="utf-8") as f:
        return {r["jls"] for r in csv.DictReader(f)}


def settlements(opis, town):
    if opis.startswith("Obuhvaćena su sva mjesta"):
        return [town]
    out = []
    for s in re.split(r"\s*,\s*", opis):
        s = s.strip()
        if s:
            out += FIX.get(s, [s])
    return out


def podrucje(wd, opis, names, legend):
    if opis.startswith("Obuhvaćena su sva mjesta"):
        others = [o for w, _, o in legend if w != wd and not o.startswith("Obuhvaćena")]
        rest = f" osim: {', '.join(others)}" if "osim" in opis else ""
        return f"{DANI[wd]} – sva naselja općine{rest}"
    return f"{DANI[wd]} – " + ", ".join(names[:4]) + (", …" if len(names) > 4 else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    bad = set(TOWNS.values()) - jls_names()
    if bad:
        sys.exit(f"Nazivi JLS nisu u registru: {sorted(bad)}")
    html = fetch(PAGE).decode("utf-8", "replace")
    links = {}
    for url in re.findall(rf'href="([^"]*/Kalendar-{year}-([A-Za-z]+)\.pdf)"', html):
        links.setdefault(url[1], url[0])
    missing = [t for t in TOWNS if t not in links]
    extra = [t for t in links if t not in TOWNS]
    if extra:
        print(f"PROBLEM nepoznati kalendari na stranici: {extra}")
    if missing or extra:
        sys.exit(f"Nema kalendara {year} za {missing} na {PAGE}" if missing else "Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    hol = set(pravila.blagdani(year))
    ok, zones, notes = True, {}, []
    with tempfile.TemporaryDirectory() as tmp:
        for town, jls in sorted(TOWNS.items(), key=lambda t: list(links).index(t[0])):
            dest = Path(tmp) / f"{town}.pdf"
            fetch(links[town], dest)
            legend, found, problems = read_town(dest, town, year)
            short = jls.split(" – ")[0]
            print(f"{short}: {len(found)} dana s oznakom, skupine "
                  + ", ".join(f"{DANI[wd].lower()} ({k or 'bez oznake'})" for wd, k, _ in legend))
            for wd, kind, opis in legend:
                dates = sorted(d for d, (k, _) in found.items() if k == kind)
                name = f"{short} {DANI[wd].lower()}"
                if kind is None:
                    print(f"   {name}: legenda nema oznaku, u kalendaru nema datuma – preskočeno ({opis})")
                    notes.append(f"{short}: za skupinu {DANI[wd].lower()} ({opis}) legenda kalendara nema oznaku "
                                 "i u kalendaru nema datuma, pa nije uključena.")
                    continue
                rows, moved = [], []
                for d in dates:
                    fam = found[d][1]
                    shift = d.weekday() != wd
                    if shift and not any(abs((d - h).days) <= 6 for h in hol):
                        problems.append(f"{name}: {d} ({DANI[d.weekday()].lower()}) without a holiday near")
                    if shift:
                        moved.append(d)
                    rows.append((d, "M" + CODES.get(fam, ""), shift))
                counts = Counter(t for _, c, _ in rows for t in c)
                for t, (lo, hi) in PER_YEAR.items():
                    if not lo <= counts[t] <= hi:
                        problems.append(f"{name}: {counts[t]}x {t} in the year")
                per_month = Counter(d.month for d in dates)
                if any(not 3 <= per_month[m] <= 5 for m in range(1, 13)):
                    problems.append(f"{name}: collections per month {dict(sorted(per_month.items()))}")
                on_hol = [f"{d:%d.%m.}" for d in dates if d in hol]
                print(f"   {name} ({kind}): {dict(sorted(counts.items()))}, na blagdane {', '.join(on_hol) or '-'}, "
                      f"pomaknuto {', '.join(f'{d:%d.%m.}' for d in moved) or '-'}")
                names = settlements(opis, short)
                key = str(len(zones) + 1)
                zone = {"jls": jls, "podrucje": podrucje(wd, opis, names, legend), "opis": opis, "ulice": names}
                old = data["zone"].get(key, {})
                if old.get("jls") == jls and old.get("podrucje") == zone["podrucje"]:
                    zone["raw"] = {**old.get("raw", {}), str(year): podaci.month_lines(rows)}
                else:
                    zone["raw"] = {str(year): podaci.month_lines(rows)}
                zones[key] = zone
            for p in problems[:15]:
                print(f"   PROBLEM {p}")
            ok = ok and not problems
    if not ok:
        sys.exit("Ništa nije upisano.")
    print("Pretpostavka: obojeni oblik je odvoz miješanog otpada i vrste te boje (miješani je tjedni, a oblik "
          "stoji na svakom tjednom danu skupine).")
    data["napomene"] = PROVIDER["napomene"] + notes
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
