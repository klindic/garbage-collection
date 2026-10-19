"""Županja, Štitar, Cerna, Bošnjaci, Gradište, Vrbanja, Babina Greda: Čistoća Županja d.o.o. (cistoca-zu.hr).

    python3 -m izvori.cistoca_zupanja [--year 2026]

The page "Raspored odvoza" links one CorelDraw PDF per town, municipality or settlement group: page 1
is a 12-month calendar, page 2 the notice with the mixed waste day per street ("PONEDJELJAK: Bosutska,
...") and the biowaste rule. The calendar has no text layer (day numbers are drawn as outlines), so the
month tables are found from their ruling lines, each cell's date comes from its row and column, and
the outlines in every cell are counted and compared with the digits of that date (a digit is one
outline plus one per hole), which proves the grid. The page is rendered with pdftoppm and the colour
at the four edge midpoints of each cell is matched with the legend swatches (cells split diagonally
show two types): blue paper, yellow plastic, green mixed waste, brown biowaste, orange the bulky waste
date (on request). Županja's calendar tints whole weeks instead: the blue and yellow bins go out on
the street's mixed waste day within the tinted week. Mixed waste or biowaste that the notice gives as
a weekly rule without coloured cells (Županja, Štitar, biowaste in Vrbanja, Soljani, Strošinci) is
computed. Holidays: the red note under some calendars ("UMJESTO 25.12. MKO ĆE SE VOZITI 26.12.") is
drawn as outlines, so it is transcribed here and its outline count checked; otherwise the published
cells are used, and the company's notices say collection runs as usual on the other holidays.
"""
import argparse
import io
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cistoca-zupanja"
SITE = "https://cistoca-zu.hr"
PAGE = SITE + "/raspored-odvoza/"
DPI = 150
DAYS = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DAN_I = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
LEGEND = {(0.67, 0.16, 0.09, 0.0): "K", (0.13, 0.0, 0.82, 0.0): "P", (0.77, 0.02, 0.84, 0.0): "M",
          (0.1, 0.62, 0.7, 0.6): "B", (0.02, 0.65, 0.89, 0.0): "G"}
TEXT = {(0.0, 0.0, 0.0, 1.0), (0.0, 0.93, 0.99, 0.0)}  # black and red day numbers
HOLES = {"0": 1, "4": 1, "6": 1, "8": 2, "9": 1}
GROUPS = r"(PONEDJELJAK|UTORAK|SRIJEDA|ČETVRTAK|PETAK):\s*(?:CERNA\s*-\s*)?(.+?)\.?(?=\s+(?:PONEDJELJAK|UTORAK|SRIJEDA|ČETVRTAK|PETAK):|\s+SPREMNIK|$)"
# file: (JLS, settlements, page-2 rule texts that must be present, mixed waste, biowaste)
#   mixed: "cells" (green cells, streets per weekday from GROUPS if any) or a weekday for a weekly rule
#   bio: "cells" or a weekday for a weekly rule
FILES = {
    "Zupanja": ("Županja", ["Županja"], ["SE PRAZNI PETKOM"], "streets", 4),
    "Stitar": ("Štitar", ["Štitar"], ["na specijalnom vozilu svaki ponedjeljak", "SE PRAZNI PETKOM"], 0, 4),
    "Cerna": ("Cerna", ["Cerna"], ["svaki drugi ponedjeljak i utorak", "SVAKI DRUGI PONEDJELJAK (SVI KORISNICI)"],
              "cells", "cells"),
    "Bosnjaci": ("Bošnjaci", ["Bošnjaci"], ["se prazni svaki DRUGI:", "SVAKI DRUGI PETAK (SVI KORISNICI)"], "cells", "cells"),
    "Gradiste": ("Gradište", ["Gradište"], ["SE PRAZNI SVAKI DRUGI TJEDAN:", "SE PRAZNI SVAKE DRUGE SRIJEDE"], "cells", "cells"),
    "Vrbanja": ("Vrbanja", ["Vrbanja"], ["OTPAD SE PRAZNI ČETVRTKOM", "(SMEĐI SPREMNIK) SE PRAZNI ČETVRTKOM"], "cells", 3),
    "Soljani-i-Strosinci": ("Vrbanja", ["Soljani", "Strošinci"], ["OTPAD SE PRAZNI SRIJEDOM", "(SMEĐI SPREMNIK) SE PRAZNI ČETVRTKOM"],
                            "cells", 3),
    "Babina-greda": ("Babina Greda", ["Babina Greda"], ["svaki DRUGI PONEDJELJAK", "se prazni svaki DRUGI PONEDJELJAK u mjesecu"],
                     "cells", "cells"),
    "Siskovci": ("Cerna", ["Šiškovci"], ["SVAKU DRUGU SRIJEDU putem", "SE PRAZNI SVAKU DRUGU SRIJEDU"], "cells", "cells"),
}
# red notes under the calendars, drawn as outlines: file -> (text, {holiday: substitute}, number of outlines)
NOTES = {"Zupanja": ("UMJESTO 25.12. MKO ĆE SE VOZITI 26.12.", {(12, 25): (12, 26)}, 37),
         "Bosnjaci": ("UMJESTO 25.12. MKO ĆE SE VOZITI 26.12.", {(12, 25): (12, 26)}, 37)}
PROVIDER = {
    "davatelj": "Čistoća Županja d.o.o.",
    "web": SITE,
    "zupanija": "Vukovarsko-srijemska",
    "jls": ["Županja", "Štitar", "Cerna", "Bošnjaci", "Gradište", "Vrbanja", "Babina Greda"],
    "nazivi": {"M": "Miješani komunalni otpad (zelena kanta)", "K": "Papir i karton (plava kanta)",
               "P": "Plastika (žuta kanta)", "B": "Biootpad (smeđa kanta)", "G": "Glomazni otpad (uz prijavu)"},
}


def tables(page):
    """The 12 month tables as {x0, x1, ys: ruling lines top to bottom}, in reading order."""
    segs = [(l["x0"], l["top"], l["x1"], l["bottom"]) for l in page.lines]
    segs += [(r["x0"], r["top"], r["x1"], r["bottom"]) for r in page.rects
             if r.get("fill") and min(r["width"], r["height"]) < 1.2]
    out = []
    for s in sorted((s for s in segs if s[3] - s[1] < 1.2 and 80 < s[2] - s[0] < 95), key=lambda s: (s[1], s[0])):
        t = next((t for t in out if abs(s[0] - t["x0"]) < 3 and 0 <= s[1] - t["ys"][0] < 80), None)
        if t is None:
            out.append({"x0": s[0], "x1": s[2], "ys": [s[1]]})
        elif all(abs(s[1] - y) > 2 for y in t["ys"]):
            t["ys"].append(s[1])
    return sorted(out, key=lambda t: (round(t["ys"][0] / 40), t["x0"]))


def read_calendar(path, page, year, problems, name):
    """({date: set of codes}, legend codes) from page 1."""
    from PIL import Image
    import numpy as np
    tabs = tables(page)
    if len(tabs) != 12 or any(len(t["ys"]) < 8 for t in tabs):
        problems.append(f"{name}: našao {len(tabs)} tablica mjeseci")
        return {}, set()
    png = subprocess.run(["pdftoppm", "-r", str(DPI), "-f", "1", "-l", "1", "-png", str(path)],
                         capture_output=True, check=True).stdout
    im = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    k = DPI / 72

    def pixel(x, y):
        return im[int(y * k) - 1:int(y * k) + 2, int(x * k) - 1:int(x * k) + 2].reshape(-1, 3).mean(axis=0)
    # palette: the legend swatches under the calendar (and white)
    bottom = max(max(t["ys"]) for t in tabs)
    palette, legend = {"-": np.array([255, 255, 255])}, set()
    for s in page.rects + page.curves:
        col = tuple(round(float(v), 2) for v in (s.get("non_stroking_color") or ()))
        if s.get("fill") and s["top"] > bottom + 5 and 10 < s["width"] < 16 and 10 < s["height"] < 18 \
                and col not in TEXT | {(0.0, 0.0, 0.0, 0.0)}:
            if col not in LEGEND:
                problems.append(f"{name}: nepoznata boja u legendi {col}")
                continue
            palette[LEGEND[col]] = pixel((s["x0"] + s["x1"]) / 2, (s["top"] + s["bottom"]) / 2)
            legend.add(LEGEND[col])
    glyphs = [c for c in page.curves if c.get("fill") and 0.3 < c["x1"] - c["x0"] < 5 and 0.5 < c["bottom"] - c["top"] < 6
              and tuple(round(float(v), 2) for v in (c.get("non_stroking_color") or ())) in TEXT]
    cells, exact, total = {}, 0, 0
    for m, t in enumerate(tabs, 1):
        ys = sorted(t["ys"])
        pitch = statistics.median(b - a for a, b in zip(ys[2:], ys[3:]))
        cw = (t["x1"] - t["x0"]) / 7
        start = date(year, m, 1) - timedelta(days=date(year, m, 1).weekday())
        for i in range(42):
            x0, y0 = t["x0"] + i % 7 * cw, ys[2] + i // 7 * pitch
            d = start + timedelta(days=i)
            n = sum(1 for g in glyphs if x0 < (g["x0"] + g["x1"]) / 2 < x0 + cw and y0 < (g["top"] + g["bottom"]) / 2 < y0 + pitch)
            want = sum(1 + HOLES.get(ch, 0) for ch in str(d.day))
            if d.month != m:
                if n not in (0, want):
                    problems.append(f"{name}: izvan mjeseca {m} na mjestu {d:%d.%m.} ima nešto napisano")
                continue
            total += 1
            exact += n == want
            if not want <= n <= want + 3:  # PDFs printed through Foxit add slivers in some coloured cells
                problems.append(f"{name}: na mjestu {d:%d.%m.} nije broj {d.day} ({n} oblika, očekivano {want})")
            samples = []
            for x, y in ((x0 + cw / 2, y0 + 1.5), (x0 + cw / 2, y0 + pitch - 2.2), (x0 + 1.5, y0 + pitch / 2),
                         (x0 + cw - 1.5, y0 + pitch / 2)):
                rgb = pixel(x, y)
                dist = sorted((abs(palette[c] - rgb).max(), c) for c in palette)
                if dist[0][0] <= 60 and dist[0][0] < 0.6 * dist[1][0]:  # skip pixels on a diagonal split
                    samples.append((dist[0][1], dist[0][0]))
            counts = Counter(c for c, _ in samples)
            codes = {c for c, n in counts.items() if c != "-" and (n > 1 or any(x == c and e <= 25 for x, e in samples))}
            if len(samples) < 3 or set(counts) - {"-"} != codes:
                problems.append(f"{name}: nejasna boja na {d:%d.%m.} ({samples})")
            cells[d] = codes
    if exact < 0.95 * total:
        problems.append(f"{name}: samo {exact} od {total} brojeva točno prepoznato")
    return cells, legend


def red_note(page, tabs_bottom):
    """Number of red outlines below the calendar (the holiday note)."""
    return sum(1 for c in page.curves if c.get("fill") and c["top"] > tabs_bottom + 5 and c["bottom"] - c["top"] < 6
               and tuple(round(float(v), 2) for v in (c.get("non_stroking_color") or ())) == (0.0, 0.93, 0.99, 0.0))


def streets(text):
    return [s.strip(" .") for s in re.split(r",\s*", text) if s.strip(" .")]


def spans(dates):
    """Consecutive working days (Mon-Fri) -> list of runs."""
    out = []
    for d in sorted(dates):
        prev = d - timedelta(days=3 if d.weekday() == 0 else 1)
        if out and out[-1][-1] == prev:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def often(wd, n):
    """'ponedjeljkom' for a weekly round, 'svaki drugi ponedjeljak' / 'svaku drugu srijedu' for every other week."""
    if n > 40:
        return DAN_I[wd]
    return "svaku drugu srijedu" if wd == 2 else f"svaki drugi {DAN[wd]}"


def weekly(year, wd):
    return pravila.tjedno(year, ["pon", "uto", "sri", "čet", "pet", "sub", "ned"][wd])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    urls = {}
    for url in re.findall(r'href="([^"]+\.pdf)"', html):
        stem = re.sub(r"\d+$", "", url.rsplit("/", 1)[1][:-4])
        if stem in FILES:
            urls.setdefault(stem, url)
    missing = [f for f in FILES if f not in urls]
    if missing:
        sys.exit(f"Nema PDF-a za {', '.join(missing)} na {PAGE}")
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))

    def holiday_week(d):
        return any(d - timedelta(days=d.weekday()) + timedelta(days=i) in hol for i in range(5))
    problems, zones = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for stem, (jls, places, rules, mixed, bio) in FILES.items():
            path = Path(tmp) / f"{stem}.pdf"
            fetch(urls[stem], path)
            notice = " ".join(subprocess.run(["pdftotext", "-f", "2", "-l", "2", str(path), "-"],
                                             capture_output=True, text=True, check=True).stdout.split())
            with pdfplumber.open(path) as pdf:
                cal, legend = read_calendar(path, pdf.pages[0], year, problems, stem)
                tabs = tables(pdf.pages[0])
                reds = red_note(pdf.pages[0], max(max(t["ys"]) for t in tabs)) if tabs else 0
            if f"{year}. godina" not in notice:
                problems.append(f"{stem}: obavijest nije za {year}. ({urls[stem]})")
            for r in rules:
                if re.sub(r"\s+", "", r).upper() not in re.sub(r"\s+", "", notice).upper():
                    problems.append(f"{stem}: u obavijesti nema '{r}' (pravilo se promijenilo?)")
            note_text, note_moves, note_n = NOTES.get(stem, ("", {}, 0))
            if reds != note_n:
                problems.append(f"{stem}: napomena ispod kalendara ima {reds} crvenih oblika, a prepisana ima {note_n}: "
                                "kalendar se promijenio, ponovno pročitati napomenu")
            moves = {date(year, *a): date(year, *b) for a, b in note_moves.items()}
            if not cal:
                continue
            found = Counter(c for codes in cal.values() for c in codes)
            for code in found:
                if code not in legend:
                    problems.append(f"{stem}: boja {code} nije u legendi")
            print(f"{stem}: {urls[stem]}\n   ćelije: " + " ".join(f"{c}{found[c]}" for c in "MBKPG" if found[c])
                  + (f"; napomena: {note_text}" if note_text else ""))
            common = defaultdict(list)  # code -> [(date, moved)] shared by all zones of the file
            for code in "KPG" + ("B" if bio == "cells" else ""):
                days = sorted(d for d, codes in cal.items() if code in codes)
                if code in "KP" and mixed == "streets":
                    continue  # tinted weeks, per street weekday below
                wd = Counter(d.weekday() for d in days).most_common(1)[0][0] if days else None
                for d in days:
                    moved = d.weekday() != wd and holiday_week(d)
                    if d.weekday() != wd and not moved and code != "G":
                        problems.append(f"{stem}: {code} {d:%d.%m.} nije {DAN[wd]}")
                    common[code].append((d, moved))
            if isinstance(bio, int):
                common["B"] = [(d, False) for d in weekly(year, bio)]
            # zones: (weekday, streets)
            groups = [(DAYS.index(g), streets(s)) for g, s in re.findall(GROUPS, notice)]
            if stem == "Babina-greda":
                m = re.search(r"DRUGI PONEDJELJAK \)?(.+?)\) i svaki drugi UTORAK \((.+?)\)", notice)
                groups = [(0, streets(m.group(1))), (1, streets(m.group(2)))] if m else []
            green = sorted(d for d, codes in cal.items() if "M" in codes)
            if mixed == "streets" and len(groups) != 5 or mixed == "cells" and len(groups) not in (0, 2):
                problems.append(f"{stem}: {len(groups)} skupina ulica u obavijesti")
                continue
            if not groups:
                wds = Counter(d.weekday() for d in green).most_common(1) if green else [(mixed, 0)]
                groups = [(wds[0][0], places)]
            for wd, ulice in groups:
                rows = defaultdict(lambda: ["", False])
                if mixed == "cells":  # a green day off the groups' weekdays replaces a holiday week's round
                    mine = []
                    for d in green:
                        if d.weekday() == wd:
                            mine.append((d, False))
                        elif d.weekday() not in [g for g, _ in groups]:
                            owners = [g for g, _ in groups if d + timedelta(days=g - d.weekday()) in hol] or \
                                     ([groups[0][0]] if len(groups) == 1 and holiday_week(d) else [])
                            if owners == [wd]:
                                mine.append((d, True))
                            elif len(owners) != 1 and wd == groups[0][0]:
                                problems.append(f"{stem}: miješani {d:%d.%m.} ne pripada ni jednoj skupini")
                else:
                    mine = [(moves.get(d, d), d in moves) for d in weekly(year, wd)]
                for d, moved in mine:
                    rows[d][0] += "M"
                    rows[d][1] |= moved
                for code, days in common.items():
                    for d, moved in days:
                        rows[d][0] += code
                        rows[d][1] |= moved
                if mixed == "streets":  # tinted weeks: the street's weekday inside each run
                    for code in "KP":
                        for run in spans(d for d, codes in cal.items() if code in codes):
                            if sorted(d.weekday() for d in run) != [0, 1, 2, 3, 4]:
                                problems.append(f"{stem}: {code} tjedan {run[0]:%d.%m.}-{run[-1]:%d.%m.} nije pet radnih dana")
                                continue
                            d = next(d for d in run if d.weekday() == wd)
                            rows[d][0] += code
                zone_rows = [(d, c, m) for d, (c, m) in sorted(rows.items()) if d.year == year]
                desc = (f"{', '.join(places)} – {DAN[wd]}" + (f": {', '.join(ulice[:3])}{' …' if len(ulice) > 3 else ''}"
                                                              if ulice != places else ""))
                note = f"Miješani otpad {often(wd, len(mine))}" + (
                    " (prema obavijesti, u kalendaru nije označen)." if isinstance(mixed, int) else ".")
                b = Counter(d.weekday() for d, _ in common["B"])
                if b:
                    note += f" Biootpad {often(b.most_common(1)[0][0], len(common['B']))}" + (
                        " (prema obavijesti, u kalendaru nije označen)." if isinstance(bio, int) else ".")
                if mixed == "streets":
                    note += (" Papir (plava kanta) i plastika (žuta kanta) odvoze se na dan odvoza miješanog otpada u tjednu "
                             "označenom plavom odnosno žutom bojom.")
                if "G" in found:
                    note += " Glomazni otpad na označeni datum uz prijavu najkasnije dan ranije."
                if note_text:
                    note += f" Napomena na kalendaru: „{note_text}”"
                zones.append(({"jls": jls, "podrucje": desc, "ulice": ulice, "napomena": note}, zone_rows))
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": PAGE, "napomene": [
        "Komunalni otpad odvozi se od 6:00 do 20:00; spremnici se iznose najranije večer uoči dana odvoza.",
        "Na blagdane se otpad odvozi redovno (obavijesti Čistoće Županja za 1.1., 1.5., 4.6. i 5.8.2026.), osim gdje je "
        "u kalendaru napisano drukčije (Županja i Bošnjaci: umjesto 25.12. miješani otpad 26.12.).",
        "Glomazni otpad: jednom godišnje besplatno na datum označen narančasto u kalendaru, uz prijavu najkasnije dan "
        "ranije (032/827-995, ivana@cistoca-zu.hr).",
        "Metal i staklo prikupljaju se po pozivu korisnika; ostalo u reciklažna i mobilna reciklažna dvorišta.",
    ], "zone": {}}
    for i, (zone, rows) in enumerate(zones, 1):
        per = defaultdict(Counter)
        seen = set()
        for d, codes, _ in rows:
            per[d.month].update(codes)
            if d in seen or len(set(codes)) != len(codes):
                problems.append(f"zona {i}: {d} dvaput")
            seen.add(d)
        for m in range(1, 13):
            n = per[m]
            if not 2 <= n["M"] <= 5 or not 1 <= n["K"] <= 2 or not 1 <= n["P"] <= 2 or n["B"] and not 1 <= n["B"] <= 5:
                problems.append(f"zona {i} ({zone['podrucje']}): {m}. mjesec {dict(n)}")
        if not zone["ulice"]:
            problems.append(f"zona {i}: nema ulica")
        prev = old.get(str(i), {})
        prev = prev.get("raw", {}) if prev.get("podrucje") == zone["podrucje"] else {}
        data["zone"][str(i)] = {**zone, "raw": {**prev, str(year): podaci.month_lines(rows)}}
        n = Counter(c for _, codes, _ in rows for c in codes)
        print(f"zona {i:2} {zone['jls']:13} {zone['podrucje'][:60]:60} " + " ".join(f"{c}{n[c]}" for c in "MBKPG" if n[c]))
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
