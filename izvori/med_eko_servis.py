"""Medulin: MED EKO SERVIS d.o.o., three household areas of the Općina Medulin (upper and lower Medulin, other settlements).

    python3 -m izvori.med_eko_servis [--year 2026]

The "Kalendar odvoza otpada" page links one PDF per area for the year (page 2 is a vector year calendar,
CMYK fills): green mixed waste, blue paper, yellow plastic, grey metal, brown biowaste, orange nappies
(white bags, a separate service that has no type in the common format, so it is only described in
napomene). A cell split diagonally holds two collections: the second colour is a thick clipped diagonal
stroke. The PDF font maps Č/Ž to '#'/'Æ', so month names and the weekday row are repaired before reading.
Colours are checked against the legend samples; streets/settlements come from the legend text (upper and
lower Medulin) or the calendar title (other settlements). July and August have more collections (mixed
waste twice a week). Holidays: the calendar is drawn with the shifts ("moguća odstupanja u vrijeme
blagdana"); a collection on a weekday that type does not use around that month, within a week of a
holiday, is marked as moved, anywhere else it is an error. The separate renters' calendar is left out.
"""
import argparse
import html as html_lib
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.perusic import read_grid
from izvori.slavonski_brod_komunalac import fetch

SLUG = "med-eko-servis"
SITE = "https://www.medekoservis.hr"
PAGE = SITE + "/kalendar-odvoza-otpada"
FIX = {"SIJE#ANJ": "SIJEČANJ", "VELJA#A": "VELJAČA", "OÆUJAK": "OŽUJAK", "#": "Č"}
PALETTE = {(0.33, 0.04, 0.75, 0.0): "M", (0.6, 0.0, 0.0, 0.0): "K", (0.03, 0.0, 0.9, 0.0): "P",
           (0.1, 0.1, 0.15, 0.5): "L", (0.2, 0.5, 0.75, 0.0): "B", (0.0, 0.5, 0.9, 0.0): "pelene"}
IGNORE = {(0.0, 0.0, 0.0, 0.0)}
LEGEND = {"MIJEŠANI": "M", "PAPIR": "K", "PLASTIKA": "P", "METAL": "L", "BIOOTPAD": "B", "PELENE": "pelene"}
PER_MONTH = {"M": (3, 10), "K": (1, 5), "P": (1, 5), "L": (1, 1), "B": (0, 5)}
FIX_NAMES = {"PREMANUTRA": "Premantura"}  # typo in the calendar title
PROVIDER = {
    "davatelj": "MED EKO SERVIS d.o.o.",
    "web": SITE,
    "zupanija": "Istarska",
    "jls": ["Medulin"],
    "nazivi": {"L": "Metal (crne vreće)"},
    "napomene": [
        "U srpnju i kolovozu odvoz je češći (miješani otpad dvaput tjedno, papir i plastika svaki tjedan).",
        "Spremnike iznijeti najranije večer prije, a najkasnije do 6 sati na dan odvoza.",
        "Otpadne pelene (bijele vreće) odvoze se svaki petak, a u srpnju i kolovozu svaki četvrtak; "
        "nisu uključene u ovaj raspored.",
        "Staklo se odlaže u zelena zvona na javnim površinama.",
        "Glomazni i zeleni otpad s adrese odvozi se na zahtjev (052/573-733, odvoz@medekoservis.hr), "
        "osim od 1.7. do 31.8.",
        "Moguća odstupanja u vrijeme blagdana; pomaci objavljeni u kalendaru su uključeni.",
        "Iznajmljivači imaju zaseban kalendar na stranici davatelja; nije uključen.",
    ],
}


def cmyk(c):
    if c is None or isinstance(c, str):
        return c
    if isinstance(c, (int, float)):
        c = (c,)
    return tuple(round(float(v), 3) for v in c)


def code_of(c):
    for col, code in PALETTE.items():
        if len(col) == len(c) and all(abs(a - b) <= 0.03 for a, b in zip(col, c)):
            return code
    return "?"


def read_area(path, year):
    """(title words, legend street text, {date: codes}, pelene dates, problems) of one area PDF."""
    page = next((p for p in pdfplumber.open(path).pages if "KALENDAR ODVOZA OTPADA U" in (p.extract_text() or "")), None)
    if page is None:
        return "", "", {}, [], ["no calendar page"]
    words = page.extract_words()
    head = " ".join(w["text"] for w in words if w["top"] < 50)
    m = re.match(rf"KALENDAR ODVOZA OTPADA U {year}\. GODINI ZA NASELJ[EA] (.+)", head)
    if not m:
        return "", "", {}, [], [f"no '{year}' title"]
    cells, outside, problems = read_grid(page, year, fix=FIX)
    if problems:
        return m.group(1), "", {}, [], problems
    fills = [r for r in page.rects if r.get("fill") and r["x1"] - r["x0"] < 100 and r["bottom"] - r["top"] < 100]
    strokes = [l for l in page.lines if l.get("linewidth", 0) > 5]
    found, pelene = {}, []
    for d, (cx, cy, hw, hh) in cells.items():
        cols = []
        under = [r for r in fills if r["x0"] - 0.5 <= cx <= r["x1"] + 0.5 and r["top"] - 0.5 <= cy <= r["bottom"] + 0.5]
        if under:
            cols.append(cmyk(min(under, key=lambda r: (r["x1"] - r["x0"]) * (r["bottom"] - r["top"]))["non_stroking_color"]))
        for l in strokes:  # the other half of a split cell
            mx, my = (l["x0"] + l["x1"]) / 2, (l["top"] + l["bottom"]) / 2
            if abs(mx - cx) < hw and abs(my - cy) < hh:
                cols.append(cmyk(l["stroking_color"]))
        codes = []
        for c in cols:
            if c in IGNORE:
                continue
            code = code_of(c)
            if code == "?":
                problems.append(f"{d}: unknown colour {c}")
            elif code == "pelene":
                pelene.append(d)
            else:
                codes.append(code)
        if len(set(codes)) != len(codes):
            problems.append(f"{d}: the same colour twice")
        if codes:
            found[d] = "".join(codes)
    used = {id(l) for l in strokes for d, (cx, cy, hw, hh) in cells.items()
            if abs((l["x0"] + l["x1"]) / 2 - cx) < hw and abs((l["top"] + l["bottom"]) / 2 - cy) < hh}
    if len(used) != len(strokes):
        problems.append(f"{len(strokes) - len(used)} diagonal strokes outside the day cells")
    # legend: the sample left of each label must have the label's colour
    grid_bottom = max(cy for _, cy, _, _ in cells.values())
    for word, code in LEGEND.items():
        w = next((w for w in words if w["text"] == word and grid_bottom < w["top"] < grid_bottom + 80), None)
        mid = w and (w["top"] + w["bottom"]) / 2
        sample = w and [r for r in fills if 0 < w["x0"] - r["x1"] < 20 and r["top"] - 3 < mid < r["bottom"] + 3]
        if not sample or code_of(cmyk(sample[0]["non_stroking_color"])) != code:
            problems.append(f"legend {word}: no sample of the expected colour")
    streets = ""
    sw = next((w for w in words if w["text"] in ("GORNJI", "DONJI") and grid_bottom < w["top"] < grid_bottom + 80), None)
    stop = next((w["top"] for w in words if w["text"] == "RASPORED" and w["top"] > grid_bottom), None)
    if sw and stop:
        lines = {}
        for w in words:
            if w["x0"] >= sw["x0"] - 2 and sw["bottom"] + 2 < w["top"] < stop - 2:
                lines.setdefault(round(w["top"]), []).append(w)
        streets = " ".join(" ".join(x["text"] for x in sorted(ws, key=lambda x: x["x0"])) for _, ws in sorted(lines.items()))
    return m.group(1), streets, found, pelene, problems


def irregular(rows, hol, skip="G", summer=(7, 8)):
    """(moved dates, problems) for {date: codes}. A type's date is irregular when its weekday is rare for
    that type (used once, or under 40 % as often as its most used weekday) in its month and the months
    either side of the same season (summer months, by default July-August, or the rest) and it is not the
    type's most common weekday in that season; it is a holiday shift if a holiday is within a week,
    otherwise a problem. Types in skip (bulky waste a few times a year) are not checked."""
    moved, problems = set(), []
    for t in {t for c in rows.values() for t in c} - set(skip):
        dates = [d for d, c in rows.items() if t in c]
        for d in dates:
            season = [x for x in dates if (x.month in summer) == (d.month in summer)]
            near = Counter(x.weekday() for x in season if abs(x.month - d.month) <= 1)
            if near[d.weekday()] >= max(2, 0.4 * max(near.values())):
                continue
            if Counter(x.weekday() for x in season).most_common(1)[0][0] == d.weekday():
                continue
            if any(abs((d - h).days) <= 6 for h in hol):
                moved.add(d)
            else:
                problems.append(f"{d} {t}: lone {podaci.DAYS[d.weekday()]} without a holiday near")
    return moved, problems


def names(text):
    return [FIX_NAMES.get(s, s if not s.isupper() else s.title()) for s in (x.strip() for x in re.split(r",", text)) if s]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    # the accordion "KALENDAR ODVOZA OTPADA 01.01.- 31.12. <year>." holds the household PDFs
    m = re.search(rf"KALENDAR ODVOZA OTPADA 01\.01\.\s*-\s*31\.12\.\s*{year}\.(.*?)accordion-header", html, re.S)
    links = re.findall(r'href="([^"]+\.pdf)"[^>]*>([^<]+)</a>', m.group(1)) if m else []
    if len(links) != 3:
        sys.exit(f"Na {PAGE} nisu pronađena tri kalendara za {year} (pronađeno {len(links)})")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    hol = set(pravila.blagdani(year))
    ok, zones = True, {}
    with tempfile.TemporaryDirectory() as tmp:
        for i, (url, label) in enumerate(links):
            dest = Path(tmp) / f"{i}.pdf"
            fetch(url, dest)
            title, streets, found, pelene, problems = read_area(dest, year)
            label = unicodedata.normalize("NFC", html_lib.unescape(label)).replace("(PDF)", "").strip()
            moved, probs = irregular(found, hol)
            problems += probs
            counts = Counter(t for c in found.values() for t in c)
            for t, (lo, hi) in PER_MONTH.items():
                per = Counter(d.month for d, c in found.items() if t in c)
                if t in counts and any(not lo <= per[mo] <= hi for mo in range(1, 13)):
                    problems.append(f"{t} per month {dict(sorted(per.items()))}")
            for t in "MKPL":
                if not counts[t]:
                    problems.append(f"no {t} collections")
            if "DIO" in title:
                places = names(streets)
                if not places:
                    problems.append("no street list in the legend")
            else:
                places = names(title)
            print(f"{label}: {dict(sorted(counts.items()))}, pelene {len(pelene)}, {len(places)} ulica/naselja, "
                  f"pomaknuto {', '.join(f'{d:%d.%m.}' for d in sorted(moved)) or '-'}")
            for p in problems[:15]:
                print(f"   PROBLEM {p}")
            if problems:
                ok = False
                continue
            key = str(i + 1)
            old = data["zone"].get(key, {})
            opis = re.sub(r"\s*,\s*", ", ", streets or title)
            zones[key] = {
                "jls": "Medulin",
                "podrucje": f"{label}: " + ", ".join(places[:4]) + (", …" if len(places) > 4 else ""),
                "opis": opis,
                "ulice": places,
                "raw": {**(old.get("raw", {}) if old.get("opis") == opis else {}),
                        str(year): podaci.month_lines([(d, c, d in moved) for d, c in found.items()])},
            }
            if "B" not in counts:
                zones[key]["napomena"] = "Biootpad: korisnici će biti pravovremeno obaviješteni."
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zone)")


if __name__ == "__main__":
    main()
