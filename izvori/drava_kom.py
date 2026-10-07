"""Ferdinandovac, Gola, Hlebine, Kalinovac, Kloštar Podravski, Molve, Novo Virje, Podravske Sesvete, Rasinja, Virje: Drava Kom d.o.o.

    python3 -m izvori.drava_kom [--year 2026]

The schedule page links one PDF per municipality (two zones for Kloštar Podravski, Rasinja and Virje, and a
separate one for the Molve settlement Čingi-Lingi). Each is a half-year table (rows = months, columns = waste
types, told apart by the fill colour of the column heading) drawn in CorelDRAW with the text converted to curves,
so there is no text layer. The dates are read from the glyph outlines: a glyph's shape (number of points of its
outline parts, aspect ratio, position of the hole for 6/9) identifies the digit, and an unknown shape stops the
script. Page 1 of the Kloštar Podravski PDF is a PowerPoint raster image instead and is transcribed below.
Holiday shifts are built into the dates; a date off the type's usual weekday in a week with a public holiday on
that weekday is marked as moved. Only half a year is published at a time (under the same file names), so a
re-run keeps the other months already in podaci/drava-kom.json. The sha256 of every PDF is pinned.
"""
import argparse
import hashlib
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import blagdani

SLUG = "drava-kom"
SITE = "https://drava-kom.hr"
PAGE = SITE + "/defaultcont.asp?id=2&n=8"
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
BLACK = (0.0, 0.0, 0.0, 1.0)
LABEL = (0.435, 0.145, 0.008, 0.0)  # light blue month cells
HEADS = {(0.525, 0.0, 0.875, 0.0): "M", (0.722, 0.122, 0.0, 0.0): "K", (0.004, 0.145, 0.702, 0.0): "P",
         (0.051, 0.329, 1.0, 0.255): "B", (0.0, 0.0, 0.0, 0.251): "A", (0.004, 0.384, 0.506, 0.0): "G",
         (0.804, 0.0, 0.925, 0.0): "R"}  # A agro-film, R mobile recycling yard
# digit glyphs: unique outline points of each part -> character ("1"/"7" and "6"/"9" are split by shape)
SHAPES = {(19,): "2", (26,): "3", (18,): "5", (4, 11): "4", (8, 9): "0", (8, 8, 13): "8"}
ON_REQUEST, NO_NOTICE = 13, 15  # glyphs in "treba najaviti" / "ne treba najaviti"
FILES = {  # file name on the page -> (sha256, zones per page)
    "Ferdinandovac": ("6d45ec6d3ac094ebca76d028cc3674fad05d2a22def6c0872a8a4a448d21ca44",
                      [dict(jls="Ferdinandovac", naziv="Općina Ferdinandovac")]),
    "Gola": ("3cadd910829b0138eae6e10a1f7582d14b9fba0202f7dfbf25c4081744717a9f",
             [dict(jls="Gola", naziv="Općina Gola",
                   napomena="Radno vrijeme mobilnog reciklažnog dvorišta: Gotalovo 8:00-9:00, Otočka 9:30-10:30, "
                            "Novačka 11:00-12:00, Ždala 12:30-13:30, Gola 14:00-16:00.")]),
    "Hlebine": ("8c95cdcf11e17a43d8d5140fdd1834c068faf186b1279953c2a3c4b7f7569c92",
                [dict(jls="Hlebine", naziv="Općina Hlebine")]),
    "Kalinovac": ("923ffb1863065defefecaa3bfd662b2ac7e5b0ad338814bc4be2501fc011588b",
                  [dict(jls="Kalinovac", naziv="Općina Kalinovac")]),
    "Kloštar Podravski 1-2": ("5fb509155279965a48c5dbb6450ec649891ba84f21141621359f5cd88718b2b3", [
        dict(jls="Kloštar Podravski", naziv="Kloštar Podravski 1",
             opis="U naselju Kloštar Podravski ulice: Matije Gupca, Pridvorje, Ulička i ulica 1. Svibnja. "
                  "Naselja: Budančevica i Prugovac.",
             ulice=["Matije Gupca", "Pridvorje", "Ulička", "1. svibnja", "Budančevica", "Prugovac"],
             # page 1 is a raster image: transcribed (checked twice at different zoom)
             raster={"M": "8.7. 22.7. 6.8. 19.8. 2.9. 16.9. 30.9. 14.10. 28.10. 11.11. 25.11. 9.12. 23.12.",
                     "K": "8.7. 3.8. 16.9. 14.10. 11.11. 9.12.",
                     "P": "22.7. 19.8. 30.9. 28.10. 25.11. 23.12.",
                     "B": "8.7. 22.7. 6.8. 19.8. 2.9. 16.9. 30.9. 14.10. 28.10. 11.11. 25.11. 9.12. 23.12.",
                     "A": "28.8. 26.11.", "G": "2.9."},
             raster_months=(7, 2026), raster_notes={"A": ON_REQUEST, "G": NO_NOTICE}),
        dict(jls="Kloštar Podravski", naziv="Kloštar Podravski 2",
             opis="U naselju Kloštar Podravski ulice: Ljudevita Gaja, Mirogojska, Oderjan, Petra Preradovića, "
                  "Trg Svete Obitelji, Kralja Tomislava, Veseli Breg. Naselja: Kozarevac.",
             ulice=["Ljudevita Gaja", "Mirogojska", "Oderjan", "Petra Preradovića", "Trg Svete Obitelji",
                    "Kralja Tomislava", "Veseli Breg", "Kozarevac"])]),
    "Molve": ("ecd9dfc8bbc5f65dae4bf37278d53f287db17ab628621908e23b275c1d5d58be",
              [dict(jls="Molve", naziv="Općina Molve (osim naselja Čingi-Lingi)",
                    ulice=["Molve (općina, osim Čingi-Lingi)"])]),
    "Čingi Lingi": ("c7e52362a14981802764931983938df9d62e8c8a1dc936eb9e6e5abcc342665f",
                    [dict(jls="Molve", naziv="Molve – naselje Čingi-Lingi", ulice=["Čingi-Lingi"])]),
    "Novo Virje": ("7c44bf152f258991a44a38f97d5048f555a5bac11f7c972f9682a5c6904a5f7a",
                   [dict(jls="Novo Virje", naziv="Općina Novo Virje")]),
    "Podravske Sesvete": ("c282d3b75248c5b8b2aaf4aefe706faf5fe1f663d61c9a0652aac15b4efcfa50",
                          [dict(jls="Podravske Sesvete", naziv="Općina Podravske Sesvete")]),
    "Rasinja 1": ("81a245453edaaf69b0ebe4d6d8cb621b081a548ce3d10aab44235558bcd47215", [
        dict(jls="Rasinja", naziv="Rasinja 1",
             opis="Naselja: Rasinja, Prkos, Veliki Grabičani, Veliki Poganac, Ludbreški Ivanac, Mala Rijeka, "
                  "Duga Rijeka, Radeljevo Selo, Ribnjak, Velika Rasinjica, Mala Rasinjica, Ivančec, Belanovo Selo "
                  "i Lukovec.",
             ulice=["Rasinja", "Prkos", "Veliki Grabičani", "Veliki Poganac", "Ludbreški Ivanac", "Mala Rijeka",
                    "Duga Rijeka", "Radeljevo Selo", "Ribnjak", "Velika Rasinjica", "Mala Rasinjica", "Ivančec",
                    "Belanovo Selo", "Lukovec"]),
        dict(jls="Rasinja", naziv="Rasinja 2",
             opis="Naselja: Subotica Podravska, Kuzminec, Cvetkovec, Grbaševec, Vojvodinec, Koledinec i Gorica.",
             ulice=["Subotica Podravska", "Kuzminec", "Cvetkovec", "Grbaševec", "Vojvodinec", "Koledinec",
                    "Gorica"])]),
    "Virje 1": ("b5771dfab9c3482673074c39637c1ab3583adb88ed18861dd1c25eae1160eda6", [
        dict(jls="Virje", naziv="Virje 1",
             opis="U naselju Virje ulice: Andrije Hebranga, Franje Viktora Šignjara, Gorička, Josipa Kucela, "
                  "Kralja Tomislava, Kostanjić, Miholjanska, Novigradska, Trg bana Josipa Jelačića, Trg Matije "
                  "Gupca, Vinogradska i Vrbas. Naselja: Donje Zdjelice, Hampovica, Jabučeta, Miholjanec, Rakitnica "
                  "i Šemovci.",
             ulice=["Andrije Hebranga", "Franje Viktora Šignjara", "Gorička", "Josipa Kucela", "Kralja Tomislava",
                    "Kostanjić", "Miholjanska", "Novigradska", "Trg bana Josipa Jelačića", "Trg Matije Gupca",
                    "Vinogradska", "Vrbas", "Donje Zdjelice", "Hampovica", "Jabučeta", "Miholjanec", "Rakitnica",
                    "Šemovci"]),
        dict(jls="Virje", naziv="Virje 2",
             opis="U naselju Virje ulice: Ante Starčevića, Augusta Cesarca, Augusta Šenoe, Braće Radića, Brestova, "
                  "Ciglenska, Đure Sudete, Eugena Kvaternika, Ferde Rusana, Franje Fanceva, Gajeva, Gradišće, "
                  "Hrvatskih domobrana, Istarska, Ivana Gundulića, Kolodvorska, M.P. Miškine, Mitrovica, "
                  "Paromlinska, P.Preradovića, Šemovačka, Trg Matije Gupca, Trg Prodavić, Trg Stjepana Radića, "
                  "Trnovac, Vladimira Nazora, Trg dr. Franje Tuđmana, Frana Lugarića.",
             ulice=["Ante Starčevića", "Augusta Cesarca", "Augusta Šenoe", "Braće Radića", "Brestova", "Ciglenska",
                    "Đure Sudete", "Eugena Kvaternika", "Ferde Rusana", "Franje Fanceva", "Gajeva", "Gradišće",
                    "Hrvatskih domobrana", "Istarska", "Ivana Gundulića", "Kolodvorska", "M.P. Miškine",
                    "Mitrovica", "Paromlinska", "P. Preradovića", "Šemovačka", "Trg Matije Gupca", "Trg Prodavić",
                    "Trg Stjepana Radića", "Trnovac", "Vladimira Nazora", "Trg dr. Franje Tuđmana",
                    "Frana Lugarića"])]),
}
PROVIDER = {
    "davatelj": "Drava Kom d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Koprivničko-križevačka",
    "jls": ["Ferdinandovac", "Gola", "Hlebine", "Kalinovac", "Kloštar Podravski", "Molve", "Novo Virje",
            "Podravske Sesvete", "Rasinja", "Virje"],
    "nazivi": {"B": "Biorazgradivi otpad"},
    "napomene": [
        "Otpad treba staviti ispred kuće do 7 sati.",
        "Drava Kom objavljuje raspored po pola godine (isti nazivi datoteka); upisani su objavljeni mjeseci, "
        "a mjeseci iz ranijih objava zadržani su iz prijašnjih preuzimanja.",
        "Pomaknuti datumi su odvozi premješteni zbog blagdana, kako su upisani u rasporedu.",
        "Glomazni otpad upisan je za termine označene \"ne treba najaviti\"; agro-folije se odvoze samo uz "
        "najavu (datumi u napomeni zone), a mobilno reciklažno dvorište po datumima u napomeni zone.",
    ],
}


def colour(obj):
    c = obj.get("non_stroking_color")
    if c is None:
        return None
    return tuple(round(float(v), 3) for v in ((c,) if isinstance(c, (int, float)) else c))


def parts(page):
    """Filled black outline parts (curves and small rects) of the converted text."""
    out = []
    for o, kind in [(c, "c") for c in page.curves] + [(r, "r") for r in page.rects]:
        if o.get("fill") and colour(o) == BLACK:
            pts = [(round(x, 2), round(y, 2)) for x, y in o.get("pts") or []]
            uniq = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
            if len(uniq) > 1 and uniq[0] == uniq[-1]:
                uniq.pop()
            out.append(dict(x0=o["x0"], x1=o["x1"], top=o["top"], bottom=o["bottom"],
                            n=4 if kind == "r" else len(uniq), kind=kind))
    return out


def runs(items, lo, hi, tol):
    """Group items whose [lo, hi] intervals overlap (or are within tol)."""
    out = []
    for it in sorted(items, key=lambda t: t[lo]):
        if out and it[lo] <= out[-1][1] + tol:
            out[-1][0].append(it)
            out[-1][1] = max(out[-1][1], it[hi])
        else:
            out.append([[it], it[hi]])
    return [g for g, _ in out]


def char(glyph, height):
    """Character of one glyph (list of outline parts) in a line whose tallest glyph is `height` high."""
    x0, x1 = min(p["x0"] for p in glyph), max(p["x1"] for p in glyph)
    top, bottom = min(p["top"] for p in glyph), max(p["bottom"] for p in glyph)
    w, h = x1 - x0, bottom - top
    shape = tuple(sorted(p["n"] for p in glyph))
    if any(p["kind"] != "c" for p in glyph):
        return "?"
    if len(glyph) == 1 and h < 0.4 * height and w < 0.4 * height:
        return "."
    if shape == (4,) and h < 0.3 * height:
        return "-"
    if shape == (6,):
        return "1" if w < 0.5 * height else "7"
    if shape == (8, 12):
        hole = min(glyph, key=lambda p: p["n"])
        return "6" if ((hole["top"] + hole["bottom"]) / 2 - top) / h > 0.5 else "9"
    return SHAPES.get(shape, "?")


def read_table(page):
    """[{code: [text lines]} per month row] of the half-year table, or None if the page has no table."""
    heads = sorted((r for r in page.rects if r.get("fill") and colour(r) in HEADS and 15 < r["bottom"] - r["top"] < 45),
                   key=lambda r: r["x0"])
    labels = [r for r in page.rects if r.get("fill") and colour(r) == LABEL]
    if not heads or not labels:
        return None
    top, bottom = max(r["bottom"] for r in heads), max(r["bottom"] for r in labels)
    body = [p for p in parts(page) if p["top"] > top and p["bottom"] < bottom]
    cols = [("L", 0, heads[0]["x0"])] + [(HEADS[colour(r)], r["x0"], r["x1"]) for r in heads]
    lines = {}
    for code, x0, x1 in cols:
        cell = [p for p in body if x0 <= (p["x0"] + p["x1"]) / 2 < x1]
        out = []
        for line in runs(cell, "top", "bottom", 0.3):
            glyphs = runs(line, "x0", "x1", 0.05)
            height = max(max(p["bottom"] for p in g) - min(p["top"] for p in g) for g in glyphs)
            mid = (min(p["top"] for p in line) + max(p["bottom"] for p in line)) / 2
            out.append((mid, "".join(char(g, height) for g in glyphs)))
        lines[code] = out
    rows = [mid for mid, _ in lines.pop("L")]  # one line per month name
    table = [defaultdict(list) for _ in rows]
    for code, out in lines.items():
        for mid, text in out:
            table[min(range(len(rows)), key=lambda i: abs(rows[i] - mid))][code].append(text)
    return table


def entries(table, year):
    """[(month row, code, date, note)] from the table text; note = glyph count or text of following lines."""
    out, problems = [], []
    for i, row in enumerate(table):
        for code, texts in row.items():
            for text in texts:
                m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.", text)
                if m:
                    try:
                        out.append([i, code, date(year, int(m.group(2)), int(m.group(1))), []])
                    except ValueError:
                        problems.append(f"red {i + 1} {code}: nemoguć datum {text}")
                elif out and out[-1][:2] == [i, code]:
                    out[-1][3].append(text)
                else:
                    problems.append(f"red {i + 1} {code}: nepročitan tekst {text!r}")
    return out, problems


def raster_entries(spec, year):
    first, y = spec["raster_months"]
    out = []
    for code, text in spec["raster"].items():
        for t in text.split():
            d, m = map(int, t.rstrip(".").split("."))
            note = ["?" * spec["raster_notes"][code]] if code in spec.get("raster_notes", {}) else []
            out.append([m - first, code, date(year, m, d), note])
    return out, ([] if y == year else [f"prepisani raster je za {y}., traženo {year}."])


def check(items, year):
    """Months of the window, {(date, code): moved}, problems."""
    problems = []
    first = {i: d.month - i for i, code, d, _ in items}
    if len(set(first.values())) != 1 or len({i for i, *_ in items}) != 6:
        return [], {}, [f"retci ne odgovaraju uzastopnim mjesecima: {sorted(set(first.values()))}"]
    start = first.popitem()[1]
    if start not in (1, 7):
        problems.append(f"razdoblje počinje u mjesecu {start}, očekivano siječanj ili srpanj")
    months = list(range(start, start + 6))
    hol = set(blagdani(year)) | set(blagdani(year + 1))
    moved = {}
    for code in "MKPB":
        days = sorted(d for _, c, d, _ in items if c == code)
        if not days:
            problems.append(f"nema odvoza {code}")
            continue
        weekdays = {wd for wd, n in Counter(d.weekday() for d in days).items() if n >= 3}
        for d in days:
            ok = d.weekday() in weekdays
            monday = d - timedelta(days=d.weekday())
            moved[d, code] = not ok and any(monday + timedelta(days=k) in hol for k in range(7)
                                            if k in weekdays)
            if not ok and not moved[d, code]:
                problems.append(f"{d:%d.%m.} {code}: {DAN[d.weekday()]}, a u tom tjednu nema blagdana")
        count = Counter(d.month for d in days)
        lo, hi = (2, 3) if code in "MB" else (1, 1)
        for m in months:
            if not lo <= count[m] <= hi:
                problems.append(f"mjesec {m}: {count[m]} odvoza {code}, očekivano {lo}-{hi}")
        if code in "MB":
            regular = [d for d in days if not moved[d, code]]
            if any((d - regular[0]).days % 14 for d in regular):
                problems.append(f"{code}: datumi nisu svaki drugi tjedan")
    return months, moved, problems


def zone_data(items, moved, spec):
    """(rows for month_lines, zone napomena, podrucje)."""
    rows, extra = defaultdict(str), defaultdict(list)
    for _, code, d, note in items:
        n = len(note[0]) if note else 0
        if code in "MKPB":
            rows[d] += code
        elif code == "G" and n == NO_NOTICE:
            rows[d] += "G"
        elif code == "G":
            extra["Glomazni otpad (treba najaviti)"].append(f"{d.day}.{d.month}.")
        elif code == "A":
            extra["Agro-folije" + (" (treba najaviti)" if n == ON_REQUEST else "")].append(f"{d.day}.{d.month}.")
        elif code == "R":
            hours = next((t for t in note if re.fullmatch(r"\d{1,2}-\d{1,2}", t)), None)
            extra["Mobilno reciklažno dvorište"].append(f"{d.day}.{d.month}." + (f" ({hours} h)" if hours else ""))
    out = [(d, c, any(moved.get((d, x)) for x in c)) for d, c in rows.items()]
    notes = ["; ".join(f"{k}: {', '.join(v)}" for k, v in extra.items())] if extra else []
    if spec.get("napomena"):
        notes.append(spec["napomena"])
    mk = Counter(d.weekday() for d, c, mv in out if "M" in c and not mv).most_common(1)[0][0]
    every = ["svaki drugi ponedjeljak", "svaki drugi utorak", "svaku drugu srijedu", "svaki drugi četvrtak",
             "svaki drugi petak", "svaku drugu subotu", "svaku drugu nedjelju"][mk]
    podrucje = f"{spec['naziv']} – miješani i biootpad {every}, papir i plastika jednom mjesečno"
    return out, " ".join(n if n.endswith(".") else n + "." for n in notes).replace("..", "."), podrucje


def merge(old_raw, year, months, rows):
    """Year lines: the old dates outside the published months plus the new rows."""
    keep = []
    for d, codes, mv in podaci.iter_dates({"raw": old_raw}, year):
        if d.month not in months:
            keep.append((d, codes, mv))
    return podaci.month_lines(keep + rows)


def links(html):
    """{file name: url} of the schedule PDFs (comments removed), and the published window text."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    found = {m.group(1): f"{SITE}/dokumenti/{m.group(1)}.pdf"
             for m in re.finditer(r'href="dokumenti/([^"]+)\.pdf"', html)}
    window = re.search(r"važi od\s*(\d+\.\d+\.\d{4})\.\s*-\s*(\d+\.\d+\.\d{4})\.", html)
    return found, (f"{window.group(1)}. – {window.group(2)}." if window else None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    found, window = links(fetch(PAGE).decode("utf-8", "replace"))
    print(f"Stranica: raspored {window or '(nema teksta o razdoblju)'}")
    if not window or str(year) not in window:
        sys.exit(f"Na stranici nema rasporeda za {year}. Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    ok, zone, new = True, 0, {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, (sha, specs) in FILES.items():
            if name not in found:
                print(f"{name}: nema poveznice na {PAGE}")
                ok = False
                zone += len(specs)
                continue
            pdf = Path(tmp) / "raspored.pdf"
            fetch(found[name], pdf)
            got = hashlib.sha256(pdf.read_bytes()).hexdigest()
            if got != sha:
                print(f"{name}: slika se promijenila, prepisati ponovno (novi sha256 {got}); provjeriti ispis "
                      "pročitanih datuma i upisati novi sha256 u FILES")
                ok = False
                zone += len(specs)
                continue
            pages = pdfplumber.open(pdf).pages
            if len(pages) != len(specs):
                print(f"{name}: {len(pages)} stranica, očekivano {len(specs)}")
                ok = False
                zone += len(specs)
                continue
            for page, spec in zip(pages, specs):
                zone += 1
                if "raster" in spec:
                    items, problems = raster_entries(spec, year)
                else:
                    table = read_table(page)
                    if table is None or len(table) != 6:
                        print(f"Zona {zone} ({spec['naziv']}): tablica nije pronađena")
                        ok = False
                        continue
                    items, problems = entries(table, year)
                months, moved, more = check(items, year)
                problems += more
                rows, napomena, podrucje = zone_data(items, moved, spec) if not problems else ([], "", "")
                shifts = [f"{d:%d.%m.} {c}" for d, c, mv in sorted(rows) if mv]
                print(f"Zona {zone} ({spec['naziv']}): {len(rows)} dana odvoza u mjesecima {months[0] if months else '?'}.–{months[-1] if months else '?'}."
                      f"; pomaknuto: {', '.join(shifts) or '-'}")
                if napomena:
                    print(f"   {napomena}")
                for p in problems:
                    print(f"   PROBLEM {p}")
                if problems:
                    ok = False
                    continue
                old = data["zone"].get(str(zone), {})
                new[str(zone)] = {
                    "jls": spec["jls"], "podrucje": podrucje,
                    **({"opis": spec["opis"]} if spec.get("opis") else {}),
                    "ulice": spec.get("ulice") or [f"{spec['jls']} (cijela općina)"],
                    "napomena": f"Objavljeni raspored: {window} {napomena}".strip(),
                    "raw": {**old.get("raw", {}), str(year): merge(old.get("raw", {}), year, months, rows)},
                }
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"].update(new)
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
