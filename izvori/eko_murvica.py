"""Crikvenica, Vinodolska općina: Eko-Murvica d.o.o. (ekomurvica.hr), two zones per municipality.

    python3 -m izvori.eko_murvica [--year 2026]

The waste page links one calendar PDF per municipality ("Kalendar odvoza otpada ... Crikvenica / Vinodol"):
a Photoshop image in a PDF with two blocks of 12 month grids (western and eastern part). The waste type
is the fill colour of the day cell (green mixed, yellow plastic/tetrapak/cans, blue paper/cardboard/glass),
so the page is rendered at 300 dpi and every day cell is sampled on the fixed grid that hangs under the
grey weekday bars. The grid is checked by the digits: every day of the month must have ink in its cell
and every empty slot of the grid must be blank. Holidays are printed red and the shifts are built into
the colours; a date on an unusual weekday in a week with a public holiday is marked as moved. The sha256
of each PDF is pinned, so a changed calendar stops the script until the reading has been checked again.
"""
import argparse
import calendar
import hashlib
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import blagdani

SLUG = "eko-murvica"
SITE = "https://ekomurvica.hr"
PAGE = SITE + "/djelatnosti/gospodarenje-otpadom/"
# file name part -> (JLS, sha256 of the checked PDF)
FILES = {
    "Crikvenica": ("Crikvenica", "4f897c7e6e25153b1db9c00eb780b3d74b8b5295a100c62eb82cefaa5422feb6"),
    "Vinodol": ("Vinodolska općina", "b98ad1cab1db1273250b918253eae963b5566817e96b5d149ef77a3fae0201ff"),
}
# upper block = western part, lower block = eastern part (area text transcribed from the PDFs)
ZONES = {
    "Crikvenica": [
        ("Zapadni dio grada – ulice zapadno od Vinodolske ulice, Trg Stjepana Radića, Dramalj i Jadranovo",
         "Za sve ulice u Crikvenici zapadno od Vinodolske ulice, Trg Stjepana Radića i naselja Dramalj i Jadranovo",
         ["Crikvenica (zapadno od Vinodolske ulice)", "Trg Stjepana Radića", "Dramalj", "Jadranovo"]),
        ("Istočni dio grada – Školska, Vinodolska, ulice istočno od Vinodolske ulice i Selce",
         "Za ulice u Crikvenici: Školska, Vinodolska i sve ulice istočno od Vinodolske ulice i naselje Selce",
         ["Školska", "Vinodolska", "Crikvenica (istočno od Vinodolske ulice)", "Selce"]),
    ],
    "Vinodol": [
        ("Zapadni dio općine – Grižane-Belgrad (zapadni zaseoci), Tribalj i Drivenik",
         "Grižane-Belgrad (Kostelj, Blaškovići, Dolinci, Kamenjak, Marušići, Grižane centar, Belgrad, Baretići, "
         "Bašunje, Antovo), Tribalj i Drivenik",
         ["Kostelj", "Blaškovići", "Dolinci", "Kamenjak", "Marušići", "Grižane centar", "Belgrad", "Baretići",
          "Bašunje", "Antovo", "Tribalj", "Drivenik"]),
        ("Istočni dio općine – Grižane-Belgrad (istočni zaseoci) i Bribir",
         "Grižane-Belgrad (Saftići, Šarari, Barci, Franovići, Miroši, Mavrići) i Bribir",
         ["Saftići", "Šarari", "Barci", "Franovići", "Miroši", "Mavrići", "Bribir"]),
    ],
}
PROVIDER = {
    "davatelj": "Eko-Murvica d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Primorsko-goranska",
    "jls": ["Crikvenica", "Vinodolska općina"],
    "nazivi": {"P": "Plastika, tetrapak i konzerve", "K": "Papir i karton", "S": "Staklo (uz papir)"},
    "napomene": [
        "Papir, karton i staklo odvoze se istog dana (plava vreća ili kanta; staklo u čvrstoj kutiji uz papir).",
        "Pomaknuti datumi su odvozi premješteni zbog blagdana, kako su upisani u kalendaru.",
        "Krupni (glomazni) otpad: jedan besplatan odvoz godišnje do 2 m³ uz kupon, najava na 051 781 177 "
        "(pon-pet 7-14, sub 7-12) ili krupniotpad@ekomurvica.hr; u Crikvenici se od 1.5. do 30.9. ne odvozi s adrese.",
        "Mobilno reciklažno dvorište (Crikvenica) i reciklažno dvorište (Vinodolska općina): rasporedi na "
        "stranici Eko-Murvice.",
        "Dežurna služba: Vinodolska 22b, Crikvenica, 051 781 177; besplatni info telefon 0800 2999.",
    ],
}
# cell colour classes on the RGB render (legend: green, yellow, blue)
CODES = {"g": "M", "y": "P", "b": "KS"}
BARS = 24        # 2 zones x 2 rows x 6 months
FIRST_ROW = 29   # px from the bottom of the weekday bar to the centre of the first week row (300 dpi)
ROW = 38         # px between week rows
BOX = (14, 15)   # half width / half height of the sampled area in a cell
COUNTS = {"M": (7, 15), "P": (1, 3), "KS": (1, 3)}  # plausible collections per month


def classes(a):
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return {
        "g": (abs(r - 160) < 30) & (abs(g - 208) < 25) & (abs(b - 112) < 35),
        "y": (r > 220) & (g > 210) & (b < 120),
        "b": (r < 120) & (g > 160) & (b > 200),
        "ink": (r + g + b < 300) | ((r > 180) & (g < 90) & (b < 100)),
    }


def bars(a):
    """[(top, bottom, x0, x1)] of the grey weekday bars, top to bottom, left to right."""
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    grey = (abs(r - g) < 6) & (abs(g - b) < 6) & (r > 215) & (r < 238)
    ys = np.where(grey.sum(1) > 1000)[0]
    bands, out = [], []
    for y in ys:
        if bands and y - bands[-1][-1] <= 2:
            bands[-1].append(y)
        else:
            bands.append([y])
    for band in (b for b in bands if len(b) >= 20):
        col = grey[band[0]:band[-1] + 1].mean(0)
        segs = []
        for x in np.where(col > 0.2)[0]:
            if segs and x - segs[-1][1] <= 12:
                segs[-1][1] = x
            else:
                segs.append([x, x])
        out += [(band[0], band[-1], s[0], s[1]) for s in segs if 300 < s[1] - s[0] < 400]
    return out


def read_calendar(png, year):
    """[{date: code}, {date: code}] for the upper and lower block, and a list of problems."""
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    cls = classes(a)
    found = bars(a)
    if len(found) != BARS:
        return [], [f"našao {len(found)} traka s danima u tjednu, očekivano {BARS}"]
    problems, blocks = [], [{}, {}]
    for i, (top, bottom, x0, x1) in enumerate(found):
        block, month = i // 12, (i // 6 % 2) * 6 + i % 6 + 1
        first, days = date(year, month, 1).weekday(), calendar.monthrange(year, month)[1]
        for slot in range(42):
            row, col = divmod(slot, 7)
            cx, cy = int(x0 + (col + 0.5) * (x1 - x0) / 7), bottom + FIRST_ROW + row * ROW
            area = {k: v[cy - BOX[1]:cy + BOX[1] + 1, cx - BOX[0]:cx + BOX[0] + 1].mean() for k, v in cls.items()}
            day = slot - first + 1
            colours = sorted("gyb", key=lambda k: -area[k])
            if not 1 <= day <= days:
                if area["ink"] > 0.01 or area[colours[0]] > 0.02:
                    problems.append(f"blok {block + 1}, mjesec {month}: nešto je u praznom polju {slot}")
                continue
            d = date(year, month, day)
            if area["ink"] < 0.03:
                problems.append(f"blok {block + 1}, {d:%d.%m.}: nema broja dana u polju (mreža ne odgovara)")
            if area[colours[1]] > 0.02 or 0.02 < area[colours[0]] < 0.25:
                problems.append(f"blok {block + 1}, {d:%d.%m.}: nejasna boja polja {area}")
            elif area[colours[0]] >= 0.25:
                blocks[block][d] = CODES[colours[0]]
    return blocks, problems


def with_moves(found, year):
    """[(date, code, moved)]: a date on a weekday that is not regular for its type is moved when a public
    holiday falls on a regular weekday of that type in the same week; otherwise it is a problem."""
    hol = set(blagdani(year)) | set(blagdani(year + 1)) | set(blagdani(year - 1))
    by_code_year = Counter((c, d.weekday()) for d, c in found.items())
    by_code_month = Counter((c, d.month, d.weekday()) for d, c in found.items())
    per_month = Counter((c, d.month) for d, c in found.items())
    rows, problems = [], []
    for d, c in sorted(found.items()):
        def regular(wd, month=d.month):
            if per_month[c, month] >= 6:
                return by_code_month[c, month, wd] >= 3
            return by_code_year[c, wd] >= 6
        moved = False
        if not regular(d.weekday()):
            monday = d - timedelta(days=d.weekday())
            week = [monday + timedelta(days=i) for i in range(7)]
            if any(h in hol and regular(h.weekday(), h.month) for h in week):
                moved = True
            else:
                problems.append(f"{d:%d.%m.} {c}: neočekivan dan u tjednu, a u tom tjednu nema blagdana")
        rows.append((d, c, moved))
    for (c, m), n in sorted(per_month.items()):
        lo, hi = COUNTS[c]
        if not lo <= n <= hi:
            problems.append(f"mjesec {m}: {n} odvoza {c}, očekivano {lo}-{hi}")
    for c in COUNTS:
        months = {m for (cc, m) in per_month if cc == c}
        if len(months) != 12:
            problems.append(f"{c}: odvozi u {len(months)} mjeseci, očekivano 12")
    return rows, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    ok, zone = True, 0
    with tempfile.TemporaryDirectory() as tmp:
        for part, (jls, sha) in FILES.items():
            m = re.search(rf'href="([^"]*Kalendar-odvoza-otpada[-_]{year}-{part}\.pdf)"', html)
            if not m:
                print(f"{jls}: nema kalendara za {year} na {PAGE}")
                ok = False
                zone += 2
                continue
            pdf = Path(tmp) / f"{part}.pdf"
            fetch(m.group(1), pdf)
            got = hashlib.sha256(pdf.read_bytes()).hexdigest()
            if got != sha:
                print(f"{jls}: slika se promijenila, prepisati ponovno ({m.group(1)}, sha256 {got}); "
                      "provjeriti očitanje boja i upisati novi sha256 u FILES")
                ok = False
                zone += 2
                continue
            subprocess.run(["pdftoppm", "-r", "300", "-png", "-singlefile", str(pdf), str(pdf.with_suffix(""))],
                           check=True)
            blocks, problems = read_calendar(pdf.with_suffix(".png"), year)
            for p in problems:
                print(f"   PROBLEM {jls}: {p}")
            if problems:
                ok = False
                zone += 2
                continue
            for found, (podrucje, opis, ulice) in zip(blocks, ZONES[part]):
                zone += 1
                rows, problems = with_moves(found, year)
                moved = [f"{d:%d.%m.} {c}" for d, c, mv in rows if mv]
                print(f"Zona {zone} ({jls}, {podrucje.split(' – ')[0]}): {len(rows)} odvoza, "
                      f"pomaknuto: {', '.join(moved) or '-'}")
                for p in problems:
                    print(f"   PROBLEM {p}")
                if problems:
                    ok = False
                    continue
                old = data["zone"].get(str(zone), {})
                data["zone"][str(zone)] = {
                    "jls": jls, "podrucje": podrucje, "opis": opis, "ulice": ulice,
                    "raw": {**old.get("raw", {}), str(year): podaci.month_lines(rows)},
                }
    if not ok:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
