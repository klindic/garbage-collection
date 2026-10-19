"""Zlatar, Budinščina, Hrašćina, Konjščina, Lobor, Mače, Novi Golubovec, Zlatar Bistrica: Komunalac Konjščina d.o.o.

    python3 -m izvori.komunalac_konjscina [--year 2026]

The page "Kalendari odvoza po općinama" links four scanned calendars (Canon scans, the OCR layer is useless):
Zlatar, Konjščina/Zlatar Bistrica, Hrašćina/Budinščina, Lobor/Mače/Novi Golubovec. Page 2 is a year grid where
collection days are outlined: black square mixed waste, yellow square "korisni otpad", blue square paper and
a green circle biowaste. The grid is found from the grey weekday header boxes; every day cell must show its
digit in the right ink (black, red for Sundays and holidays, grey for the neighbouring months), and the mark
of a cell is read from the outline colour at its left and right edges. The rule of each calendar (mixed on the
1st, 3rd and 5th weekday of the month, korisni otpad on the 2nd, paper on the 4th, biowaste every Monday) is
checked against every mark: a mark off the rule must be a holiday shift (a rule date on a public holiday
within 8 days without its own mark), and is then flagged as moved, or be listed in ACCEPTED below. The sha256
of each PDF is pinned, so a new calendar stops the script until it has been checked again.
"""
import argparse
import calendar
import hashlib
import html as htmlmod
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import numpy as np
from PIL import Image

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import DANI, blagdani

SLUG = "komunalac-konjscina"
SITE = "https://www.komunalac.net"
PAGE = SITE + "/kalendari-odvoza-po-opcinama/"
DAN = {"pon": "ponedjeljak", "uto": "utorak", "sri": "srijeda", "čet": "četvrtak", "pet": "petak"}
# calendar file -> JLS (in the order of the link text), weekday of the round, biowaste, sha256 of the checked PDF
CALENDARS = {
    "hrascina": (["Hrašćina", "Budinščina"], "uto", False,
                 "9aa9ee79293aa81cadfedf6b3cc121f76d5ab42eb15a85c3f19f95d3cdccb293"),
    "konjscina": (["Konjščina", "Zlatar Bistrica"], "sri", True,
                  "ab0732f5319f0d968f4cdc32b0d2231226c0afe440003fe2b1cdadda6314fa34"),
    "lobor": (["Lobor", "Mače", "Novi Golubovec"], "pet", False,
              "f6f56409db1e4003d08447d63980d40e9ba7b862055487c3d4ca6228b2e93b8c"),
    "zlatar": (["Zlatar"], "čet", True,
               "1b26d2dcb49d07ea682e71dc82719120eb7e393ef12a0f9509e1545b035aaed0"),
}
NTH = {"M": (1, 3, 5), "P": (2,), "K": (4,)}  # n-th weekday of the month
# dates where the scan legitimately differs from the rule ("!" = moved because of a holiday)
ACCEPTED = {
    # Zlatar, January 2026: New Year's Day is the 1st Thursday, the whole month moves one week on
    "zlatar": {date(2026, 1, 8): "M!", date(2026, 1, 15): "P", date(2026, 1, 22): "M", date(2026, 1, 29): "K"},
}
PROVIDER = {
    "davatelj": "Komunalac Konjščina d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Krapinsko-zagorska",
    "jls": ["Zlatar", "Budinščina", "Hrašćina", "Konjščina", "Lobor", "Mače", "Novi Golubovec", "Zlatar Bistrica"],
    "nazivi": {"P": "Korisni otpad"},
    "bioNapomena": "Biootpad se odvozi u Gradu Zlataru te općinama Konjščina i Zlatar Bistrica.",
    "napomene": [
        "Posude i/ili vreće s otpadom iznijeti na dan odvoza, najkasnije do 6:30 h.",
        "Pomaknuti datumi su odvozi premješteni zbog blagdana, kako su označeni u kalendaru.",
        "Glomazni otpad: besplatan odvoz uz kupon za 2026. (dostaviti do 13. 2. 2026.), termin javlja Komunalac.",
        "Mobilno reciklažno dvorište: raspored rada na stranici kalendara.",
        "Informacije: 049 465 120, www.komunalac.net. Komunalac Konjščina zadržava pravo izmjene rasporeda.",
    ],
}
PITCH, BLOCK, ROW = 79.45, 7 * 79.45 + 7.8, 40.0  # column pitch, month block step, week row pitch (300 dpi)
MARK = {"k": 75, "y": 75, "b": 75, "g": 40}       # edge pixels needed for a mark
UNCLEAR = {"k": 50, "y": 50, "b": 50, "g": 20}    # more than this but less than MARK: unclear
CODE = {"k": "M", "y": "P", "b": "K", "g": "B"}


def masks(png):
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mean, sat = a.mean(2), a.max(2) - a.min(2)
    return {
        "hdr": (mean > 85) & (mean < 195) & (b - r > 3) & (sat < 90),
        "y": (r > 180) & (g > 160) & (b < 150) & (r - b > 70),
        "b": (b > 170) & (b - r > 50) & (g - r > 20),
        "g": (g > 120) & (g - b > 40) & (g - r > 15) & (r < 190),
        "k": a.max(2) < 90,
        "red": (r > 170) & (g < 110) & (b < 120),
    }


def header_rows(hdr):
    """[(y centre, x0, pitch, block step)] for the 3 rows of month blocks, fitted to the weekday boxes."""
    ys, groups, out = np.where(hdr.sum(1) > 900)[0], [], []
    for y in ys:
        if groups and y - groups[-1][-1] <= 3:
            groups[-1].append(y)
        else:
            groups.append([y])
    for grp in (g for g in groups if len(g) >= 15):
        col = hdr[grp[0]:grp[-1] + 1].mean(0)
        segs = []
        for x in np.where(col > 0.35)[0]:
            if segs and x - segs[-1][1] <= 5:
                segs[-1][1] = x
            else:
                segs.append([x, x])
        centres = [(s[0] + s[1]) / 2 for s in segs if 45 < s[1] - s[0] < 90]
        grid = lambda x0: [x0 + c * PITCH + b * BLOCK for b in range(4) for c in range(7)]
        x0 = max(np.arange(100, 320, 0.5), key=lambda x0: sum(min(abs(p - c) for p in grid(x0)) < 6 for c in centres))
        rows, xs = [], []
        for c in centres:
            i = min(range(28), key=lambda i: abs(grid(x0)[i] - c))
            if abs(grid(x0)[i] - c) < 6:
                rows.append([1, i % 7, i // 7])
                xs.append(c)
        if len(xs) < 20:
            continue
        sol = np.linalg.lstsq(np.array(rows, float), np.array(xs), rcond=None)[0]
        out.append(((grp[0] + grp[-1]) / 2, *sol))
    return out


def read_scan(png, year):
    """({date: set of codes}, problems) from the year grid on page 2."""
    m = masks(png)
    hol = set(blagdani(year))
    rows = header_rows(m["hdr"])
    if len(rows) != 3:
        return {}, [f"našao {len(rows)} redova mjeseci, očekivano 3"]
    count = lambda k, x0, x1, y0, y1: int(m[k][int(y0):int(y1), int(x0):int(x1)].sum())
    found, problems = defaultdict(set), []
    for i, (yc, x0, pitch, step) in enumerate(rows):
        for b in range(4):
            month = i * 4 + b + 1
            start = date(year, month, 1) - timedelta(days=1)
            start -= timedelta(days=start.weekday())  # the first row always starts in the previous month
            for k in range(42):
                d = start + timedelta(days=k)
                cx, cy = x0 + k % 7 * pitch + b * step, yc + (k // 7 + 1) * ROW
                ink, red = count("k", cx - 22, cx + 22, cy - 12, cy + 12), count("red", cx - 22, cx + 22, cy - 12, cy + 12)
                inside = d.month == month
                want = "red" if inside and (d.weekday() == 6 or d in hol) else "k" if inside else "grey"
                if not {"k": ink > 25 and red < 15, "red": red > 25 and ink < 15, "grey": ink < 15 and red < 15}[want]:
                    problems.append(f"{d:%d.%m.} (blok {month}): broj dana nije gdje treba (mreža ne odgovara)")
                    continue
                for c in "kybg":
                    n = count(c, cx - 39, cx - 30, cy - 10, cy + 10) + count(c, cx + 30, cx + 39, cy - 10, cy + 10)
                    if n > MARK[c] and inside:
                        found[d].add(CODE[c])
                    elif n > MARK[c] and d.year == year:
                        problems.append(f"{d:%d.%m.}: oznaka {CODE[c]} na danu susjednog mjeseca (blok {month})")
                    elif n > UNCLEAR[c]:
                        problems.append(f"{d:%d.%m.}: nejasna oznaka {CODE[c]} ({n} piksela)")
    return found, problems


def rule_dates(years, dan, bio):
    """{date: set of codes} from the calendar's rule for the given years."""
    out = defaultdict(set)
    for y in years:
        for month in range(1, 13):
            days = [date(y, month, x) for x in range(1, calendar.monthrange(y, month)[1] + 1)]
            same = [d for d in days if d.weekday() == DANI[dan]]
            for code, ns in NTH.items():
                for n in ns:
                    if n <= len(same):
                        out[same[n - 1]].add(code)
            if bio:
                for d in days:
                    if d.weekday() == 0:
                        out[d].add("B")
    return out


def check(found, year, dan, bio, accepted):
    """[(date, codes, moved)] from the scan marks, and problems where the marks do not follow the rule."""
    rule = rule_dates([year - 1, year, year + 1], dan, bio)
    for d, codes in accepted.items():
        rule[d] = set(codes) - {"!"}
    hol = set(blagdani(year - 1)) | set(blagdani(year)) | set(blagdani(year + 1))
    extra = [(d, c) for d, cs in found.items() for c in cs if c not in rule.get(d, ())]
    missing = {(d, c) for d, cs in rule.items() if d.year == year or d in hol for c in cs
               if c not in found.get(d, ())}
    moved, problems, notes = set(), [], []
    for d, c in sorted(extra):
        src = min((m for m in missing if m[1] == c and m[0] in hol and abs((m[0] - d).days) <= 8),
                  key=lambda m: abs((m[0] - d).days), default=None)
        if src:
            missing.discard(src)
            moved.add((d, c))
            notes.append(f"{c} {src[0]:%d.%m.%Y.} (blagdan) -> {d:%d.%m.}")
        else:
            problems.append(f"{d:%d.%m.}: oznaka {c} nije po pravilu, a nema blagdana u blizini")
    for d, c in sorted(missing):
        if d.year != year:
            continue
        if d in hol:
            notes.append(f"{c} {d:%d.%m.} (blagdan) bez odvoza")
        else:
            problems.append(f"{d:%d.%m.}: prema pravilu {c}, a u kalendaru nema oznake")
    for d, codes in accepted.items():
        if "!" in codes:
            moved |= {(d, c) for c in codes if c != "!"}
    rows = [(d, "".join(sorted(cs)), any((d, c) in moved for c in cs)) for d, cs in sorted(found.items())]
    per_month = defaultdict(lambda: defaultdict(int))
    for d, cs in found.items():
        for c in cs:
            per_month[d.month][c] += 1
    limits = {"M": (2, 3), "P": (1, 1), "K": (1, 1), "B": (4, 5) if bio else (0, 0)}
    for month in range(1, 13):
        for c, (lo, hi) in limits.items():
            if not lo <= per_month[month][c] <= hi:
                problems.append(f"mjesec {month}: {per_month[month][c]} odvoza {c}, očekivano {lo}-{hi}")
    return rows, problems, notes


def links(page, year):
    """{calendar file stem: url} from the anchors "kalendar YYYY. ..." on the page."""
    out = {}
    for href, text in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', page, re.S | re.I):
        text = " ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        if text.lower().startswith(f"kalendar {year}") and "/Kalendari/" in href:
            out[Path(href).stem.lower()] = urljoin(PAGE, href)
    return out


def describe(dan, bio):
    d = DAN[dan]
    text = f"miješani 1., 3. i 5. {d} u mjesecu, korisni otpad 2. {d}, papir 4. {d}"
    return text + (", biootpad svaki ponedjeljak" if bio else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    found_links = links(fetch(PAGE).decode("utf-8", "replace"), year)
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    ok, zone = True, 0
    with tempfile.TemporaryDirectory() as tmp:
        for stem, (jls, dan, bio, sha) in CALENDARS.items():
            name = " / ".join(jls)
            url = found_links.get(stem)
            if not url:
                print(f"{name}: nema poveznice 'kalendar {year}.' na {PAGE}")
                ok = False
                zone += len(jls)
                continue
            pdf = Path(tmp) / f"{stem}.pdf"
            fetch(url, pdf)
            got = hashlib.sha256(pdf.read_bytes()).hexdigest()
            if got != sha:
                print(f"{name}: slika se promijenila, prepisati ponovno ({url}, sha256 {got}); "
                      "provjeriti pravila i ACCEPTED pa upisati novi sha256")
                ok = False
                zone += len(jls)
                continue
            subprocess.run(["pdfimages", "-png", "-f", "2", "-l", "2", str(pdf), str(Path(tmp) / stem)], check=True)
            found, problems = read_scan(Path(tmp) / f"{stem}-000.png", year)
            rows, more, notes = check(found, year, dan, bio, ACCEPTED.get(stem, {}))
            problems += more
            print(f"{name}: {len(rows)} dana odvoza; {'; '.join(notes) or 'bez pomaka'}")
            if stem in ACCEPTED:
                print("   iznimke prepisane sa skena: "
                      + ", ".join(f"{d:%d.%m.} {c}" for d, c in sorted(ACCEPTED[stem].items())))
            for p in problems:
                print(f"   PROBLEM {p}")
            if problems:
                ok = False
                zone += len(jls)
                continue
            for j in jls:
                zone += 1
                old = data["zone"].get(str(zone), {})
                kind = "Grad" if j == "Zlatar" else "Općina"
                data["zone"][str(zone)] = {
                    "jls": j, "podrucje": f"{kind} {j} – {describe(dan, bio)}",
                    "ulice": [f"{j} ({'cijeli grad' if kind == 'Grad' else 'cijela općina'})"],
                    "raw": {**old.get("raw", {}), str(year): podaci.month_lines(rows)},
                }
    if not ok:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
