"""Kijevo: Komunalno društvo Kijevo d.o.o. (pages on kijevo.hr), whole municipality, marked dates of a pocket calendar.

    python3 -m izvori.kd_kijevo [--year 2026]

The municipality's page "Zakoni, Odluke, pravilnici i cjenici" links "Kalendar odvoza komunalnog otpada_<year>",
a scanned pocket calendar (one JPEG in a PDF, no text) where the collection days are marked with a green
highlighter. The marked dates were transcribed into DATES below together with the PDF's sha256 (a changed
file stops the script). On every run the green marks are found again by pixel colour and placed in the
calendar grid (fixed positions of the 12 month blocks, weekday columns and week rows of this scan); they
must match the transcription exactly. One mark sits on a misprinted cell (June, fifth row, under Thursday,
printed "29": it is either 29.6. or 2.7.) and is left out. The calendar does not name the waste type; the
document is the calendar of "komunalni otpad", taken as mixed waste. Holiday moves are part of the marks.
"""
import argparse
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

SLUG = "kd-kijevo"
PAGE = "https://kijevo.hr/?page_id=7745"
YEAR = 2026
SHA256 = "afe78bcfe53c05b4807553fad263d2e136ca57e30349bde3eacefba4afcf1bc6"
DATES = ("01-15 01-29 02-12 02-26 03-12 03-26 04-09 04-23 05-07 05-21 06-04 06-18 07-09 07-23 08-06 08-20 09-03 "
         "09-17 10-01 10-15 10-29 11-12 11-26 12-10 12-24")
AMBIGUOUS = {(6, 4, 3): "lipanj, 5. redak, četvrtak (otisnuto '29' u pogrešnom retku: 29.6. ili 2.7.)"}
# grid of the 2480x3507 scan: Monday column x per block row and column, header y per block row, pitches
MON_X = [(303, 984, 1660), (286, 968, 1644), (284, 964, 1641), (278, 959, 1636)]
HEAD_Y = (682, 1404, 2054, 2783)
PX, PY, ROW0 = 86.3, 76.5, 80
PROVIDER = {
    "davatelj": "Komunalno društvo Kijevo d.o.o.",
    "web": "https://kijevo.hr/?page_id=8229",
    "izvor": PAGE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Kijevo"],
    "nazivi": {"M": "Komunalni (miješani) otpad"},
    "napomene": [
        "Datumi su zeleno označeni dani u kalendaru odvoza komunalnog otpada; vrsta otpada na kalendaru nije navedena.",
        "Jedna oznaka u lipnju stoji na pogrešno otisnutom polju (5. redak, četvrtak, otisnuto '29') pa nije jasno je li "
        "riječ o 29.6. ili 2.7.; taj datum nije uključen – provjerite kod davatelja.",
        "Pomaci zbog blagdana ugrađeni su u označene datume (npr. odvoz je označen i na Tijelovo 4.6.).",
    ],
}


def green_marks(path):
    """[(y, x, size)] centres of green highlighter blobs (8 px blocks, 8-connected)."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    m = (g > 170) & (g - r > 40) & (g - b > 80)
    k = 8
    h, w = m.shape[0] // k, m.shape[1] // k
    d = m[:h * k, :w * k].reshape(h, k, w, k).mean(axis=(1, 3)) > 0.2
    seen = np.zeros_like(d)
    out = []
    for y, x in zip(*np.nonzero(d)):
        if seen[y, x]:
            continue
        stack, pts = [(y, x)], []
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            pts.append((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and d[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        out.append((np.mean([p[0] for p in pts]) * k, np.mean([p[1] for p in pts]) * k, len(pts)))
    return out


def cell(y, x):
    """(month, week row, weekday, residual) of a point, or None outside the grid."""
    row = min(range(4), key=lambda i: abs(y - (HEAD_Y[i] + ROW0 + 2.5 * PY)))
    col = min(range(3), key=lambda j: abs(x - (MON_X[row][j] + 3 * PX)))
    fx, fy = (x - MON_X[row][col]) / PX, (y - HEAD_Y[row] - ROW0) / PY
    wd, wk = round(fx), round(fy)
    if not (0 <= wd <= 6 and 0 <= wk <= 5):
        return None
    return row * 3 + col + 1, wk, wd, max(abs(fx - wd), abs(fy - wk))


def date_at(year, month, week, wd):
    first = date(year, month, 1)
    x = first - timedelta(days=first.weekday()) + timedelta(days=7 * week + wd)
    return x if x.month == month else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Prepisani kalendar vrijedi samo za {YEAR}.")
    problems = []
    page = fetch(PAGE).decode("utf-8", "replace")
    links = sorted(set(re.findall(r'href="([^"]+\.pdf)"[^>]*>\s*Kalendar odvoza komunalnog otpada_?%d' % year, page)))
    if len(links) != 1:
        sys.exit(f"Na {PAGE} nije nađen (jedan) kalendar odvoza za {year}: {links}")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "kalendar.pdf"
        fetch(links[0], pdf)
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if sha != SHA256:
            sys.exit(f"slika se promijenila: {links[0]} (sha256 {sha}); prepisati označene datume u DATES, "
                     "provjeriti mrežu (MON_X, HEAD_Y) i upisati novi sha256")
        subprocess.run(["pdfimages", "-j", str(pdf), str(Path(tmp) / "k")], check=True)
        imgs = sorted(Path(tmp).glob("k-*"))
        if len(imgs) != 1:
            sys.exit(f"u PDF-u je {len(imgs)} slika, očekivana je jedna")
        size = Image.open(imgs[0]).size
        marks = green_marks(imgs[0])
    if size != (2480, 3507):
        problems.append(f"neočekivana veličina skena {size}")
    want = {date(year, *map(int, x.split("-"))) for x in DATES.split()}
    got, ambiguous = set(), []
    for y, x, n in marks:
        if n < 30:
            if n >= 5:
                print(f"Mala zelena mrlja ({n} blokova) na {x:.0f},{y:.0f} – zanemarena")
            continue
        c = cell(y, x)
        if c is None or c[3] > 0.4:
            problems.append(f"zelena oznaka na {x:.0f},{y:.0f} nije u polju kalendara ({c})")
            continue
        month, wk, wd, _ = c
        if (month, wk, wd) in AMBIGUOUS:
            ambiguous.append(AMBIGUOUS[month, wk, wd])
            continue
        d = date_at(year, month, wk, wd)
        if d is None:
            problems.append(f"zelena oznaka u mjesecu {month}, redak {wk + 1}, stupac {wd + 1}: tog dana nema")
        elif d in got:
            problems.append(f"{d} označen dvaput")
        else:
            got.add(d)
    if len(ambiguous) != len(AMBIGUOUS):
        problems.append(f"nejasna oznaka nije nađena kako je opisano: {ambiguous}")
    for d in sorted(want ^ got):
        problems.append(f"{d:%d.%m.}: prepisano {'da' if d in want else 'ne'}, na slici {'da' if d in got else 'ne'}")
    ds = sorted(want)
    gaps = Counter((b - a).days for a, b in zip(ds, ds[1:]))
    print(f"Oznaka na slici: {len(got)} + {len(ambiguous)} nejasna; razmaci (dana): {dict(gaps)}")
    for x in ds:
        if x.weekday() != 3:
            problems.append(f"{x} nije četvrtak")
    per = Counter(x.month for x in ds)
    if set(per) != set(range(1, 13)) or max(per.values()) > 3:
        problems.append(f"broj odvoza po mjesecima: {dict(per)}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    data = {**PROVIDER, "zone": {"1": {
        "jls": "Kijevo",
        "podrucje": "Općina Kijevo (cijela općina) – četvrtkom, uglavnom svaka dva tjedna",
        "ulice": ["Kijevo"],
        "napomena": "Datumi prema zeleno označenim danima u kalendaru za " + str(year) + ".",
        "raw": {str(year): podaci.month_lines([(x, "M", False) for x in ds])},
    }}}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} (1 zona, {len(ds)} datuma)")


if __name__ == "__main__":
    main()
