"""Mursko Središće, Selnica, Sveti Martin na Muri, Vratišinec: Murs-Ekom d.o.o. (murs-ekom.hr), six calendars.

    python3 -m izvori.murs_ekom [--year 2026]

The home page ("Kalendar odvoza" menu) links one PDF per area: the town of Mursko Središće, its settlements
Peklenica/Križovec and Hlapičina/Štrukovec, and the municipalities Selnica, Sveti Martin na Muri and
Vratišinec. Page 1 is a raster image: twelve month rows of date tiles ("8.1.") with bin icons (brown biowaste,
green mixed, blue paper, yellow plastic, grey metal/tetrapak/glass) and, under them, the bulky waste and
branch collection dates in green text; page 2 is text (notices, mobile recycling yard dates).
The dates are transcribed below. Every run checks the transcription against the image: the PDF's sha256,
the tile count of every month, the icons of every tile (exact icon colours), the number of glyphs of every
date, and a template check that every glyph looks most like the other glyphs transcribed as the same digit.
The dates must follow the area's rule (biowaste + mixed every 2nd week, paper + plastic and the grey bin every
4th week); a date off the rule must replace a rule date on a public holiday within 8 days and is marked moved.
"""
import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import DANI, blagdani

SLUG = "murs-ekom"
SITE = "https://murs-ekom.hr"
ICONS = {"B": (156, 110, 81), "M": (82, 163, 78), "K": (5, 149, 246), "P": (252, 241, 0), "L": (134, 134, 134)}
TILE_BG = ((197, 203, 189), (221, 232, 207))
# file stem -> zone data. tiles: one line per month, "date codes" per tile in page order;
# extra: green text "Božićna drvca ... | Glomazni komunalni otpad ... | Glomazni otpad granje ...";
# rules: codes -> (weekday, every n weeks, first date "MM-DD")
CALENDARS = {
    "grad-Mursko-Sredisce": dict(
        jls="Mursko Središće", title="GRAD MURSKO SREDIŠĆE", podrucje="Mursko Središće (grad, bez naselja s vlastitim kalendarom)",
        ulice=["Mursko Središće"], sha="df0ba9f26a11773a788ad405d9be06c8ea23edd36f603b599c423b2b896894cc",
        rules={"BM": ("sri", 2, "01-07"), "KP": ("uto", 4, "01-13"), "L": ("pet", 4, "01-16")},
        tiles="""
        7.1. BM | 13.1. KP | 16.1. L | 21.1. BM
        4.2. BM | 10.2. KP | 13.2. L | 18.2. BM
        4.3. BM | 10.3. KP | 13.3. L | 18.3. BM
        1.4. BM | 7.4. KP | 10.4. L | 15.4. BM | 29.4. BM
        5.5. KP | 8.5. L | 13.5. BM | 27.5. BM
        2.6. KP | 5.6. L | 10.6. BM | 24.6. BM | 30.6. KP
        3.7. L | 8.7. BM | 22.7. BM | 28.7. KP | 31.7. L
        7.8. BM | 19.8. BM | 25.8. KP | 28.8. L
        2.9. BM | 16.9. BM | 22.9. KP | 25.9. L | 30.9. BM
        14.10. BM | 20.10. KP | 23.10. L | 28.10. BM
        11.11. BM | 17.11. KP | 20.11. L | 25.11. BM
        9.12. BM | 15.12. KP | 18.12. L | 23.12. BM""",
        extra="07. 21.01.2026. | 25.02. 22.04. 17.06. 12.08. 07.10. | 18.03. 02.12."),
    "naselja-Peklenica-i-Krizovec": dict(
        jls="Mursko Središće", title="NASELJA PEKLENICA I KRIŽOVEC", podrucje="Peklenica i Križovec",
        ulice=["Peklenica", "Križovec"], sha="461fd128946e421e24cf71c630cf7193f492619142d871c638289a70b13b0e24",
        rules={"BM": ("uto", 2, "01-06"), "KP": ("sri", 4, "01-14"), "L": ("sub", 4, "01-17")},
        tiles="""
        9.1. BM | 14.1. KP | 17.1. L | 20.1. BM
        3.2. BM | 11.2. KP | 14.2. L | 17.2. BM
        3.3. BM | 11.3. KP | 14.3. L | 17.3. BM | 31.3. BM
        8.4. KP | 11.4. L | 14.4. BM | 28.4. BM
        6.5. KP | 9.5. L | 12.5. BM | 26.5. BM
        3.6. KP | 6.6. L | 9.6. BM | 23.6. BM
        1.7. KP | 4.7. L | 7.7. BM | 21.7. BM | 29.7. KP
        1.8. L | 4.8. BM | 18.8. BM | 26.8. KP | 29.8. L
        1.9. BM | 15.9. BM | 23.9. KP | 26.9. L | 29.9. BM
        13.10. BM | 21.10. KP | 24.10. L | 27.10. BM
        10.11. BM | 13.11. KP | 21.11. L | 24.11. BM
        8.12. BM | 16.12. KP | 19.12. L | 22.12. BM""",
        extra="09. 20.01.2026. | 25.02. 22.04. 17.06. 12.08. 07.10. | 18.03. 02.12."),
    "naselja-Hlapicina-i-Strukovec": dict(
        jls="Mursko Središće", title="NASELJA HLAPIČINA I ŠTRUKOVEC", podrucje="Hlapičina i Štrukovec",
        ulice=["Hlapičina", "Štrukovec"], sha="096cd056e8e6fe99c3d37ab7deffabb7e7795cbde35f7735cf1b0bbeeca50e3e",
        rules={"BM": ("uto", 2, "01-06"), "KP": ("uto", 4, "01-13"), "L": ("pet", 4, "01-16")},
        tiles="""
        9.1. BM | 13.1. KP | 16.1. L | 20.1. BM
        3.2. BM | 10.2. KP | 13.2. L | 17.2. BM
        3.3. BM | 10.3. KP | 13.3. L | 17.3. BM | 31.3. BM
        7.4. KP | 10.4. L | 14.4. BM | 28.4. BM
        5.5. KP | 8.5. L | 12.5. BM | 26.5. BM
        2.6. KP | 5.6. L | 9.6. BM | 23.6. BM | 30.6. KP
        3.7. L | 7.7. BM | 21.7. BM | 28.7. KP | 31.7. L
        4.8. BM | 18.8. BM | 25.8. KP | 28.8. L
        1.9. BM | 15.9. BM | 22.9. KP | 25.9. L | 29.9. BM
        13.10. BM | 20.10. KP | 23.10. L | 27.10. BM
        10.11. BM | 17.11. KP | 20.11. L | 24.11. BM
        8.12. BM | 15.12. KP | 18.12. L | 22.12. BM""",
        extra="09. 20.01.2026. | 25.02. 22.04. 17.06. 12.08. 07.10. | 18.03. 02.12."),
    "opcina-Selnica": dict(
        jls="Selnica", title="OPĆINU SELNICA", podrucje="Općina Selnica",
        ulice=["Selnica (cijela općina)"], sha="4782a3be67cc2ea9d5c419feed7cb88e6646b41798810a736738c5c2e406ed9a",
        rules={"BM": ("čet", 2, "01-08"), "KP": ("pon", 4, "01-26"), "L": ("uto", 4, "01-27")},
        tiles="""
        8.1. BM | 22.1. BM | 26.1. KP | 27.1. L
        5.2. BM | 19.2. BM | 23.2. KP | 24.2. L
        5.3. BM | 19.3. BM | 23.3. KP | 24.3. L
        2.4. BM | 16.4. BM | 20.4. KP | 21.4. L | 30.4. BM
        14.5. BM | 18.5. KP | 19.5. L | 28.5. BM
        11.6. BM | 15.6. KP | 16.6. L | 25.6. BM
        9.7. BM | 13.7. KP | 14.7. L | 23.7. BM
        6.8. BM | 10.8. KP | 11.8. L | 20.8. BM
        3.9. BM | 7.9. KP | 8.9. L | 17.9. BM
        1.10. BM | 5.10. KP | 6.10. L | 15.10. BM | 29.10. BM
        2.11. KP | 3.11. L | 12.11. BM | 26.11. BM | 30.11. KP
        1.12. L | 10.12. BM | 24.12. BM | 28.12. KP | 29.12. L""",
        extra="08. 22.01.2026. | 26.03. 21.05. 16.07. 10.09. 05.11. | 21.03. 06.11."),
    "opcina-Sveti-Martin-na-Muri": dict(
        jls="Sveti Martin na Muri", title="OPĆINU SVETI MARTIN NA MURI", podrucje="Općina Sveti Martin na Muri",
        ulice=["Sveti Martin na Muri (cijela općina)"],
        sha="f2d7c9a43fc6a2c0c52ade3392b06d33eaaf0a9376640d616380a04544b2a7ed",
        rules={"BM": ("pon", 2, "01-05"), "KP": ("pon", 4, "01-12"), "L": ("čet", 4, "01-15")},
        tiles="""
        5.1. BM | 12.1. KP | 15.1. L | 19.1. BM
        2.2. BM | 9.2. KP | 12.2. L | 16.2. BM
        2.3. BM | 9.3. KP | 12.3. L | 16.3. BM | 30.3. BM
        3.4. KP | 9.4. L | 13.4. BM | 27.4. BM
        4.5. KP | 7.5. L | 11.5. BM | 25.5. BM | 29.5. L
        1.6. KP | 8.6. BM | 19.6. BM | 29.6. KP
        2.7. L | 6.7. BM | 20.7. BM | 27.7. KP | 30.7. L
        3.8. BM | 17.8. BM | 24.8. KP | 27.8. L | 31.8. BM
        14.9. BM | 21.9. KP | 24.9. L | 28.9. BM
        12.10. BM | 19.10. KP | 22.10. L | 26.10. BM
        9.11. BM | 16.11. KP | 19.11. L | 23.11. BM
        7.12. BM | 14.12. KP | 17.12. L | 21.12. BM""",
        extra="05. 19.01.2026. | 25.03. 20.05. 15.07. 09.09. 04.11. | 19.03. 03.12."),
    "opcina-Vratisinec": dict(
        jls="Vratišinec", title="OPĆINU VRATIŠINEC", podrucje="Općina Vratišinec",
        ulice=["Vratišinec (cijela općina)"], sha="cb1667c761eb1b2ba1c3e4ec075da66a9b96663b63132aa96889122f2135fbda",
        rules={"BM": ("uto", 2, "01-06"), "KP": ("sri", 4, "01-14"), "L": ("sub", 4, "01-17")},
        tiles="""
        9.1. BM | 14.1. KP | 17.1. L | 20.1. BM
        3.2. BM | 11.2. KP | 14.2. L | 17.2. BM
        3.3. BM | 11.3. KP | 14.3. L | 17.3. BM | 31.3. BM
        8.4. KP | 11.4. L | 14.4. BM | 28.4. BM
        6.5. KP | 9.5. L | 12.5. BM | 26.5. BM
        3.6. KP | 6.6. L | 9.6. BM | 23.6. BM
        1.7. KP | 4.7. L | 7.7. BM | 21.7. BM | 29.7. KP
        1.8. L | 4.8. BM | 18.8. BM | 26.8. KP | 29.8. L
        1.9. BM | 15.9. BM | 23.9. KP | 26.9. L | 29.9. BM
        13.10. BM | 21.10. KP | 24.10. L | 27.10. BM
        10.11. BM | 13.11. KP | 21.11. L | 24.11. BM
        8.12. BM | 16.12. KP | 19.12. L | 22.12. BM""",
        extra="09. 20.01.2026. | 26.02. 23.04. 18.06. 13.08. 08.10. | 20.03. 04.12."),
}
PROVIDER = {
    "davatelj": "Murs-Ekom d.o.o.",
    "web": SITE,
    "izvor": SITE + "/",
    "zupanija": "Međimurska",
    "jls": ["Mursko Središće", "Selnica", "Sveti Martin na Muri", "Vratišinec"],
    "nazivi": {"P": "Plastika", "L": "Metal, tetrapak i staklena ambalaža (siva kanta)",
               "G": "Glomazni otpad (uz prethodnu prijavu)", "Z": "Granje (uz prethodnu prijavu)"},
    "bioNapomena": "Biootpad (smeđa kanta) odvozi se istog dana kad i miješani komunalni otpad.",
    "napomene": [
        "Spremnike pripremiti do 6:00 sati ujutro na dan odvoza; naknadno pripremljeni spremnici se ne prazne.",
        "Glomazni otpad i granje odvoze se samo uz prethodnu prijavu, najkasnije dva dana prije termina "
        "ili do ispunjenja kvote, na 040/543-314 (svaki dan 8-14 h); glomazni do 4 m³ godišnje po kućanstvu.",
        "Pomaknuti datumi su odvozi premješteni zbog blagdana, kako su upisani u kalendaru.",
        "Reciklažno dvorište i kompostana: Martinska 151A, Mursko Središće (pon 9-12, sri 15-18, pet 9-14, "
        "sub 8-12; zatvoreno 24.12.2026.-11.01.2027.).",
        "Ured i dodatne vreće MURS-EKOM: Trg bana Josipa Jelačića 10, Mursko Središće; tel. 040 543 314.",
    ],
}
LIMITS = {"B": (2, 3), "M": (2, 3), "K": (0, 2), "P": (0, 2), "L": (0, 2)}  # per month


def runs(mask, gap=1, minlen=1):
    """[(start, end)] of the True runs of a 1-d mask, joining runs separated by at most `gap` False."""
    out = []
    for x in np.where(mask)[0]:
        if out and x - out[-1][1] <= gap:
            out[-1][1] = x
        else:
            out.append([x, x])
    return [(int(a), int(b)) for a, b in out if b - a + 1 >= minlen]


def cut(dark):
    """Glyph bitmaps of a text band, left to right (split at blank columns, cropped to ink)."""
    out = []
    for c0, c1 in runs(dark.any(0)):
        g = dark[:, c0:c1 + 1]
        ys = np.where(g.any(1))[0]
        out.append((c0, g[ys[0]:ys[-1] + 1]))
    return out


def read_image(png):
    """([(month, glyphs, icon codes)] for the non-empty tiles, [glyph tokens per green line], problems)."""
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    bg = np.zeros(a.shape[:2], bool)
    for c in TILE_BG:
        bg |= np.all(a == c, axis=2)
    rows = runs(bg.mean(1) > 0.3, gap=4, minlen=100)
    problems, tiles = [], []
    if len(rows) != 6:
        return [], [], [f"našao {len(rows)} redova pločica, očekivano 6"]
    white = np.all(a == 255, axis=2)
    for i, (r0, r1) in enumerate(rows):
        cols = runs(~white[r0:r1 + 1].all(0), gap=1, minlen=100)
        if len(cols) != 12:
            problems.append(f"red {i + 1}: {len(cols)} pločica, očekivano 12")
            continue
        for half in (0, 1):
            for c0, c1 in cols[half * 6 + 1:half * 6 + 6]:
                tile = a[r0:r1, c0:c1]
                codes = "".join(k for k, v in ICONS.items() if np.all(tile == v, axis=2).sum() > 1000)
                glyphs = [g for _, g in cut(a[r0 + 5:r0 + 70, c0 + 3:c1 - 3].sum(2) < 300)]
                if glyphs or codes:
                    tiles.append((half * 6 + i + 1, glyphs, codes))
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    y0 = rows[-1][1] + 328  # the three lines of green dates under the legend (layout pinned by the sha256)
    green = ((g - r > 40) & (g - b > 80))[y0:y0 + 280]
    lines = []
    for l0, l1 in runs(green.any(1), gap=4, minlen=8):
        tokens = []
        for x, glyph in cut(green[l0:l1 + 1]):
            if tokens and x - tokens[-1][-1][0] - tokens[-1][-1][1].shape[1] <= 12:
                tokens[-1].append((x, glyph))
            else:
                tokens.append([(x, glyph)])
        lines.append([[gl for _, gl in t] for t in tokens])
    return sorted(tiles, key=lambda t: t[0]), lines, problems


def norm(glyph, h=20, w=16):
    """Glyph scaled to height h, centred in an h x w canvas."""
    nw = min(w, max(1, round(glyph.shape[1] * h / glyph.shape[0])))
    im = Image.fromarray((glyph * 255).astype(np.uint8)).resize((nw, h), Image.BILINEAR)
    out = np.zeros((h, w))
    out[:, (w - nw) // 2:(w - nw) // 2 + nw] = np.asarray(im) / 255
    return out


def glyph_check(samples):
    """Problems where a glyph looks more like another transcribed digit than its own (or a dot is not a dot)."""
    problems, digits = [], []
    for glyph, ch, where in samples:
        small = max(glyph.shape) <= 8
        if small != (ch == "."):
            problems.append(f"{where}: znak '{ch}' ne odgovara slici")
        elif ch != ".":
            digits.append((norm(glyph), ch, where))
    mean = {ch: np.mean([v for v, c, _ in digits if c == ch], 0) for ch in {c for _, c, _ in digits}}
    for v, ch, where in digits:
        dist = {c: np.abs(v - t).sum() for c, t in mean.items()}
        best = min(dist, key=dist.get)
        if best != ch or dist[ch] > 0.9 * min(d for c, d in dist.items() if c != ch):
            problems.append(f"{where}: znak '{ch}' više sliči na '{best}'")
    return problems


def parse(cal, year):
    """[[(date, codes), ...] per month] from the transcribed tiles."""
    months = []
    for line in cal["tiles"].strip().splitlines():
        cells = []
        for cell in line.split("|"):
            day, codes = cell.split()
            d, m = map(int, day.rstrip(".").split("."))
            cells.append((date(year, m, d), codes, day))
        months.append(cells)
    return months


def check_rules(cal, rows, year):
    """{(date, code): moved} and problems: every date must follow the rule or replace a holiday."""
    hol = set(blagdani(year)) | set(blagdani(year + 1))
    rule = set()
    for codes, (dan, n, first) in cal["rules"].items():
        d = date(year, *map(int, first.split("-")))
        assert d.weekday() == DANI[dan], (codes, first)
        while d.year <= year or d.month == 1:
            rule |= {(d, c) for c in codes}
            d += timedelta(weeks=n)
    have = {(d, c) for d, codes, _ in rows for c in codes}
    missing = {x for x in rule - have if x[0].year == year or x[0] in hol}
    moved, problems, notes = set(), [], []
    for d, c in sorted(have - rule):
        src = min((m for m in missing if m[1] == c and m[0] in hol and abs((m[0] - d).days) <= 8),
                  key=lambda m: abs((m[0] - d).days), default=None)
        if src:
            missing.discard(src)
            moved.add((d, c))
            notes.append(f"{c} {src[0]:%d.%m.} -> {d:%d.%m.}")
        else:
            problems.append(f"{d:%d.%m.} {c}: nije po pravilu, a nema blagdana u blizini")
    for d, c in sorted(missing):
        if d.year == year:
            (notes if d in hol else problems).append(
                f"{c} {d:%d.%m.} " + ("(blagdan) bez odvoza" if d in hol else "po pravilu, a nema ga u kalendaru"))
    count = defaultdict(int)
    for d, c in have:
        count[d.month, c] += 1
    for m in range(1, 13):
        for c, (lo, hi) in LIMITS.items():
            if not lo <= count[m, c] <= hi:
                problems.append(f"mjesec {m}: {count[m, c]} odvoza {c}, očekivano {lo}-{hi}")
    return moved, problems, notes


def describe(rules):
    """'biootpad i miješani svaka 2 tjedna četvrtkom, papir i plastika svaka 4 tjedna ponedjeljkom, ...'"""
    names = {"BM": "biootpad i miješani", "KP": "papir i plastika", "L": "metal, tetrapak i staklo"}
    ins = {"pon": "ponedjeljkom", "uto": "utorkom", "sri": "srijedom", "čet": "četvrtkom", "pet": "petkom",
           "sub": "subotom"}
    return ", ".join(f"{names[codes]} svaka {n} tjedna {ins[dan]}" for codes, (dan, n, _) in rules.items())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    home = fetch(SITE + "/").decode("utf-8", "replace")
    urls = {m.group(1): m.group(0) for m in re.finditer(
        rf'https?://[^"\'\s]*/Kalendar-odvoza-otpada-([^"\'\s/]+)-za-{year}\.pdf', home)}
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    ok, samples, zones = True, [], {}
    with tempfile.TemporaryDirectory() as tmp:
        for zone, (stem, cal) in enumerate(CALENDARS.items(), start=1):
            if stem not in urls:
                print(f"{cal['podrucje']}: nema kalendara za {year} na {SITE}")
                ok = False
                continue
            pdf = Path(tmp) / f"{stem}.pdf"
            fetch(urls[stem], pdf)
            got = hashlib.sha256(pdf.read_bytes()).hexdigest()
            if got != cal["sha"]:
                print(f"{cal['podrucje']}: slika se promijenila, prepisati ponovno ({urls[stem]}, sha256 {got})")
                ok = False
                continue
            text = subprocess.run(["pdftotext", "-f", "2", "-l", "2", str(pdf), "-"],
                                  capture_output=True, text=True, check=True).stdout
            subprocess.run(["pdfimages", "-png", "-f", "1", "-l", "1", str(pdf), str(Path(tmp) / stem)], check=True)
            tiles, lines, problems = read_image(Path(tmp) / f"{stem}-000.png")
            if cal["title"] not in " ".join(text.split()):
                problems.append(f"na 2. stranici nema naslova '{cal['title']}'")
            months = parse(cal, year)
            by_month = defaultdict(list)
            for month, glyphs, codes in tiles:
                by_month[month].append((glyphs, codes))
            rows = []
            for m, cells in enumerate(months, start=1):
                if len(cells) != len(by_month[m]):
                    problems.append(f"mjesec {m}: {len(by_month[m])} pločica na slici, prepisano {len(cells)}")
                    continue
                for (d, codes, text_), (glyphs, icons) in zip(cells, by_month[m]):
                    if d.month != m:
                        problems.append(f"{text_} je u retku za mjesec {m}")
                    if set(codes) != set(icons):
                        problems.append(f"{text_}: prepisano {codes}, na slici {icons or '-'}")
                    if len(glyphs) != len(text_):
                        problems.append(f"{text_}: {len(glyphs)} znakova na slici")
                    samples += [(g, ch, f"{stem} {text_}") for g, ch in zip(glyphs, text_)]
                    rows.append((d, codes, text_))
            extra = [part.split() for part in cal["extra"].split("|")]
            if [len(t) for t in lines] != [len(t) for t in extra]:
                problems.append(f"zeleni tekst: {[len(t) for t in lines]} datuma na slici, prepisano {[len(t) for t in extra]}")
            else:
                for tokens, words in zip(lines, extra):
                    for glyphs, word in zip(tokens, words):
                        if len(glyphs) != len(word):
                            problems.append(f"zeleni tekst {word}: {len(glyphs)} znakova na slici")
                        samples += [(g, ch, f"{stem} {word}") for g, ch in zip(glyphs, word)]
            moved, more, notes = check_rules(cal, rows, year)
            problems += more
            out = defaultdict(str)
            flag = set()
            for d, codes, _ in rows:
                out[d] += codes
                flag |= {d for c in codes if (d, c) in moved}
            bulky = [date(year, int(w[3:5]), int(w[:2])) for w in extra[1]]
            branches = [date(year, int(w[3:5]), int(w[:2])) for w in extra[2]]
            for d in bulky:
                out[d] += "G"
            for d in branches:
                out[d] += "Z"
            xmas = " i ".join(w.rstrip(".") + "." for w in extra[0])
            mrd = sorted({date(int(y), int(m), int(d)) for d, m, y in re.findall(
                r"\b(\d\d)\.(\d\d)\.(\d{4})\.", text.split("mobilnog reciklažnog")[-1])}) if "mobilnog reciklažnog" in text else []
            napomena = f"Božićna drvca odvoze se {xmas} uz biootpad."
            if mrd:
                days = [f"{d:%d.%m.}" for d in mrd]
                napomena += (f" Mobilno reciklažno dvorište: {', '.join(days[:-1])} i {days[-1]}{year}. "
                             "(lokacije i vrijeme u PDF-u).")
            print(f"Zona {zone} ({cal['podrucje']}): {len(rows)} datuma, glomazni {len(bulky)}, granje "
                  f"{len(branches)}; {'; '.join(notes) or 'bez pomaka'}")
            for p in problems:
                print(f"   PROBLEM {p}")
            if problems:
                ok = False
                continue
            old = data["zone"].get(str(zone), {})
            zones[str(zone)] = {
                "jls": cal["jls"], "podrucje": f"{cal['podrucje']} – {describe(cal['rules'])}", "ulice": cal["ulice"],
                "napomena": napomena,
                "raw": {**old.get("raw", {}),
                        str(year): podaci.month_lines([(d, c, d in flag) for d, c in out.items()])},
            }
    problems = glyph_check(samples)
    for p in problems:
        print(f"   PROBLEM {p}")
    print(f"Provjera znamenki: {len(samples)} znakova, problema {len(problems)}")
    if not ok or problems:
        sys.exit("Ništa nije upisano.")
    data["zone"].update(zones)
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
