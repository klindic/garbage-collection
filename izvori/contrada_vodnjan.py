"""Vodnjan – Dignano: CONTRADA d.o.o., five settlement calendars of the Grad Vodnjan (half-year PDFs).

    python3 -m izvori.contrada_vodnjan [--year 2026]

The waste-collection page links one PDF per settlement and language; the Croatian/Italian ones
(<name>_hr_it.pdf, Excel "Print To PDF") are read. Each month is a block with one row per weekday
(pon/lun ... ned/dom) and one column per week; the day number sits in a cell whose fill is the waste type
(colours taken from the legend: light green mixed waste, red metal, yellow plastic, blue paper, dark green
glass, peach biowaste, purple bulky waste at the Galižana football pitch, grey no collection). A weekday
with two collections is a taller row: the number on top and two coloured half cells under it. Only one
half-year is published at a time (in 2026 July–December); months of the year already in
podaci/contrada-vodnjan.json that the current files do not cover are kept. In Vodnjan biowaste is only
collected in the streets listed in the legend, so the town is two zones (with and without biowaste).
Holidays: the calendar is drawn with the shifts (grey cells on holidays); a collection on a weekday that
type does not use around that month, within a week of a holiday, is marked as moved, otherwise an error.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.med_eko_servis import irregular
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import colour, lookup

SLUG = "contrada-vodnjan"
SITE = "https://contrada.hr"
PAGE = SITE + "/djelatnosti/prikupljanje-odlaganje-i-zbrinjavanje-komunalnog-otpada/"
MONTHS = ["Siječanj", "Veljača", "Ožujak", "Travanj", "Svibanj", "Lipanj", "Srpanj", "Kolovoz", "Rujan",
          "Listopad", "Studeni", "Prosinac"]
ROWS = ["pon/lun", "uto/mar", "sri/mer", "čet/gio", "pet/ven", "sub/sab", "ned/dom"]
LEGEND = [("MIJEŠANI", "M"), ("METAL", "L"), ("PLASTIKA", "P"), ("PAPIR", "K"), ("STAKLO", "S"),
          ("BIOOTPAD", "B"), ("GLOMAZNI", "G"), ("GL. OTPAD", "G")]
IGNORE = ((1.0, 1.0, 1.0), (0.867, 0.922, 0.969))
FIX = {"uto/mer": "uto/mar"}  # misprint in the Krnjaloža calendar
ZONES = {"vodnjan": ("1", "2"), "galizana": ("3",), "peroj": ("4",), "gajana": ("5",), "krnjaloza": ("6",)}
PER_MONTH = {"M": (4, 14), "K": (1, 5), "P": (1, 5), "L": (1, 3), "S": (1, 3), "B": (4, 14), "G": (0, 2)}
PROVIDER = {
    "davatelj": "CONTRADA d.o.o. za obavljanje komunalnih djelatnosti",
    "web": SITE,
    "zupanija": "Istarska",
    "jls": ["Vodnjan – Dignano"],
    "nazivi": {"L": "Metal (Al-metali)"},
    "napomene": [
        "Kalendari se objavljuju za pola godine; za 2026. objavljen je raspored srpanj–prosinac, "
        "siječanj–lipanj više nije dostupan.",
        "Spremnike iznijeti do 6:15 (ljetni raspored, 1.6.–31.8.) odnosno do 7:15 (zimski raspored, 1.9.–31.12.).",
        "Sive ćelije u kalendaru (nedjelje i blagdani) znače da nema odvoza; pomaci su ugrađeni u kalendar.",
        "Reciklažno dvorište Vodnjan, A. Smareglia 67: pon i pet 9–15, uto i sri 9–17, čet i sub 7–13; "
        "tel. 052 647 096, reciklazno@contrada.hr.",
        "Informacije: Contrada d.o.o., Trg Slobode 2, Vodnjan, 052 382 009, contrada@contrada.hr.",
    ],
}


def nice(s):
    s = re.sub(r"\.(?=\S)", ". ", s.strip())
    return " ".join(w if w[:1].isdigit() else w.capitalize() for w in s.split())


def read_calendar(path, year):
    """(title, bio streets, {date: codes}, months, bulky waste place, problems) of one settlement PDF."""
    page = pdfplumber.open(path).pages[0]
    words = [{**w, "text": FIX.get(w["text"], w["text"])} for w in page.extract_words(extra_attrs=["size"])]
    text = " ".join((page.extract_text() or "").split())
    if f"KALENDAR ODVOZA OTPADA U {year}. GODINI" not in text:
        return "", [], {}, [], "", [f"no '{year}' title"]
    top = [w for w in words if w["top"] < 120]
    big = max(w["size"] for w in top)
    first = min(w["top"] for w in top if w["size"] > big - 0.3)
    title = " ".join(w["text"] for w in top if w["size"] > big - 0.3 and abs(w["top"] - first) < 3)
    fills = [r for r in page.rects if r.get("fill") and colour(r)]
    problems = []

    def at(x, y):
        under = [r for r in fills if r["x0"] <= x <= r["x1"] and r["top"] <= y <= r["bottom"]]
        return colour(min(under, key=lambda r: (r["x1"] - r["x0"]) * (r["bottom"] - r["top"]))) if under else None
    # legend: a column of sample cells, each with its label to the right
    palette, bulky = {}, ""
    for r in fills:
        if not (r["top"] > 400 and r["x0"] > 500 and 20 < r["x1"] - r["x0"] < 40):
            continue
        label = " ".join(w["text"] for w in words
                         if w["x0"] > r["x1"] and r["top"] <= (w["top"] + w["bottom"]) / 2 <= r["bottom"])
        hit = [code for key, code in LEGEND if key in label.upper()]
        if hit:
            palette[colour(r)] = hit[0]
            if hit[0] == "G":  # where bulky waste is left: "(nogometno igralište/campo di calcio)"
                m = re.search(r"\((.+?)[/-]", label)
                bulky = m.group(1).strip().lower() if m else ""
    if not {"M", "K", "P"} <= set(palette.values()):
        problems.append(f"legend colours {palette}")
    heads = [w for w in words if w["text"].split("/")[0] in MONTHS and "/" in w["text"]]
    labels = [w for w in words if w["text"] in ROWS]
    found, months, seen = {}, [], Counter()
    for h in heads:
        mo = MONTHS.index(h["text"].split("/")[0]) + 1
        months.append(mo)
        bar = min((r for r in fills if r["x0"] <= h["x0"] and h["x1"] <= r["x1"] and r["top"] <= h["top"] <= r["bottom"]),
                  key=lambda r: r["x1"] - r["x0"])
        rows = sorted((l for l in labels
                       if bar["x0"] - 2 <= l["x0"] <= bar["x0"] + 40 and 0 < l["top"] - bar["bottom"] < 150),
                      key=lambda l: l["top"])
        if [l["text"] for l in rows] != ROWS:
            problems.append(f"{h['text']}: weekday rows {[l['text'] for l in rows]}")
            continue
        nums = [w for w in words if w["text"].isdigit() and rows[0]["x1"] < w["x0"] < bar["x1"]
                and bar["bottom"] < w["top"] < rows[-1]["bottom"] + 3]
        xs = sorted({round((w["x0"] + w["x1"]) / 2) for w in nums})
        colw = min(b - a for a, b in zip(xs, xs[1:]) if b - a > 10)
        for w in nums:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            row = min(rows, key=lambda l: abs((l["top"] + l["bottom"]) / 2 - cy))
            try:
                d = date(year, mo, int(w["text"]))
            except ValueError:
                problems.append(f"{h['text']}: day {w['text']}")
                continue
            seen[d] += 1
            if d.weekday() != ROWS.index(row["text"]):
                problems.append(f"{d}: in row {row['text']}")
            ly = (row["top"] + row["bottom"]) / 2
            if ly - cy > 4:  # a taller row: two half cells under the number
                points = [(cx - colw / 4, 2 * ly - cy), (cx + colw / 4, 2 * ly - cy)]
            else:
                points = [(cx, cy)]
            codes = []
            for x, y in points:
                col = at(x, y)
                if col is None or col in IGNORE or (max(col) - min(col) < 0.03 and 0.6 < col[0] < 0.85):
                    continue  # white, header or grey (no collection)
                code = lookup(col, palette, tol=0.02)
                if code == "?":
                    problems.append(f"{d}: unknown colour {col}")
                elif code and code not in codes:
                    codes.append(code)
            if codes:
                found[d] = "".join(codes)
    for mo in months:
        for day in range(1, 32):
            try:
                d = date(year, mo, day)
            except ValueError:
                break
            if seen[d] != 1:
                problems.append(f"{d} appears {seen[d]} times")
    if len(set(months)) != len(months) or sorted(months) != list(range(min(months, default=1), max(months, default=0) + 1)):
        problems.append(f"months {months}")
    # Vodnjan: the streets with biowaste, on the "Ulice:" line of the legend (other text overlaps it)
    streets = []
    u = next((w for w in words if w["text"] == "Ulice:"), None)
    if u:
        line = page.filter(lambda o: o.get("object_type") != "char" or abs(o["top"] - u["top"]) < 1)
        txt = " ".join(w["text"] for w in line.extract_words() if abs(w["top"] - u["top"]) < 1 and w["x0"] >= u["x0"])
        streets = [nice(s) for s in txt.replace("Ulice:", "").split("/") if s.strip()]
    return title, streets, found, sorted(months), bulky, problems


def settlements(title):
    hr = title.split("/")[0]
    if "DIGNANO" in hr:
        return ["Vodnjan"]
    return [nice(s) for s in re.split(r"\s*-\s*", hr) if s.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    links = {}
    pattern = r'href="(https://contrada\.hr/wp-content/uploads/[^"]*/([a-z]+)_hr_it(?:-\d+)?\.pdf)"'
    for url, name in re.findall(pattern, html):
        links.setdefault(name, []).append(url)
    if set(links) != set(ZONES):
        sys.exit(f"Kalendari na {PAGE}: {sorted(links)}, očekivano {sorted(ZONES)}")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    hol = set(pravila.blagdani(year))
    ok, zones, covered = True, {}, set()
    with tempfile.TemporaryDirectory() as tmp:
        for name, keys in ZONES.items():
            found, months, problems, title, streets, bulky = {}, set(), [], "", [], ""
            for i, url in enumerate(sorted(set(links[name]))):  # later uploads (YYYY/MM) win
                dest = Path(tmp) / f"{name}-{i}.pdf"
                fetch(url, dest)
                t, s, f, mos, b, probs = read_calendar(dest, year)
                if probs and probs[0].startswith("no '"):
                    print(f"   {url.rsplit('/', 1)[1]}: nije za {year}, preskočeno")
                    continue
                problems += probs
                title, streets, bulky = t or title, s or streets, b or bulky
                found = {d: c for d, c in found.items() if d.month not in mos} | f
                months |= set(mos)
            if not months:
                problems.append("no calendar for the year")
            covered |= months
            moved, probs = irregular(found, hol)
            problems += probs
            counts = Counter(t for c in found.values() for t in c)
            for t, (lo, hi) in PER_MONTH.items():
                per = Counter(d.month for d, c in found.items() if t in c)
                if t in counts and any(not lo <= per[mo] <= hi for mo in months):
                    problems.append(f"{t} per month {dict(sorted(per.items()))}")
            places = settlements(title)
            if name == "vodnjan" and not streets:
                problems.append("no biowaste street list")
            print(f"{', '.join(places)}: mjeseci {sorted(months)}, {dict(sorted(counts.items()))}, "
                  f"pomaknuto {', '.join(f'{d:%d.%m.}' for d in sorted(moved)) or '-'}")
            for p in problems[:15]:
                print(f"   PROBLEM {p}")
            if problems:
                ok = False
                continue
            variants = [(keys[0], found, None)]
            if name == "vodnjan":
                variants = [(keys[0], found, True), (keys[1], {d: c.replace("B", "") for d, c in found.items()}, False)]
            for key, rows, bio in variants:
                rows = {d: c for d, c in rows.items() if c}
                old = data["zone"].get(key, {})
                keep = [(d, c, m) for d, c, m in podaci.iter_dates(old, year) if d.month not in months] if old else []
                new = keep + [(d, c, d in moved) for d, c in rows.items()]
                if bio is None:
                    zone = {"jls": "Vodnjan – Dignano", "podrucje": ", ".join(places), "ulice": places}
                    if "G" in counts:
                        zone["napomena"] = f"Glomazni otpad: na označene dane odlaže se na lokaciji: {bulky}."
                elif bio:
                    zone = {"jls": "Vodnjan – Dignano", "podrucje": "Vodnjan – ulice s odvozom biootpada: "
                            + ", ".join(streets), "ulice": streets}
                else:
                    zone = {"jls": "Vodnjan – Dignano", "podrucje": "Vodnjan – ostale ulice (bez biootpada)",
                            "ulice": ["Vodnjan"], "napomena": "Biootpad se odvozi samo u ulicama "
                            + ", ".join(streets) + "."}
                zone["raw"] = {**old.get("raw", {}), str(year): podaci.month_lines(new)}
                zones[key] = zone
    if not ok:
        sys.exit("Ništa nije upisano.")
    first, last = min(covered), max(covered)
    print(f"Pretpostavka: objavljeno {first}.–{last}. mjesec {year}.; ostali mjeseci zadržani iz postojeće datoteke.")
    data["zone"] = dict(sorted(zones.items(), key=lambda kv: int(kv[0])))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
