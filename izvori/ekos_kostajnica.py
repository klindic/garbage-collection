"""Hrvatska Kostajnica: Ekos Hrvatska Kostajnica d.o.o. (ekos-hk.hr), two zones (Gornji grad, Donji grad).

    python3 -m izvori.ekos_kostajnica [--year 2026]

The page "Otpad" links one calendar image (Kalendar-otpada<year>-scaled.jpg, 2560x1811): twelve month grids
where the background of a day number gives the collection (green = mixed waste Gornji grad, light grey =
mixed waste Donji grad, olive = biowaste, yellow + cyan halves = paper and plastic, for the whole town).
The script finds the weekday header rows ("p u s č p s n", brown letters) and the digit rows under them and
samples the colour around every day number; every day must have a digit in its cell, empty slots must be
blank and only one fill colour (or the yellow/cyan pair) may appear. The image's sha256 is pinned, so a new
calendar stops the script until the reading has been checked again. Holidays are printed red and the shifts
are built into the colours (e.g. Easter Monday moves the week by one day); a date off its type's usual
weekday in a week with a public holiday is marked as moved.
"""
import argparse
import calendar
import hashlib
import html
import re
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

SLUG = "ekos-kostajnica"
SITE = "https://ekos-hk.hr"
PAGE = SITE + "/otpad/"
IMAGE = {"year": 2026, "sha256": "ab2c0fd6a748a34b94107ab26f620e1d410de710e0c929542c78bb8bbd02e202"}
# fill colour -> code; G/D are mixed waste of Gornji / Donji grad, B biowaste, Y paper, C plastic
FILLS = {"G": "Gornji grad", "D": "Donji grad", "B": "biootpad", "Y": "papir", "C": "plastika"}
WEEKDAY = {"G": {0}, "D": {1}, "B": {2}, "YC": {3, 4}}  # usual weekday of each fill
ZONES = [("G", "Gornji grad"), ("D", "Donji grad")]
PROVIDER = {
    "davatelj": "Ekos Hrvatska Kostajnica d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Sisačko-moslavačka",
    "jls": ["Hrvatska Kostajnica"],
    "nazivi": {"P": "Plastika", "K": "Papir"},
    "napomene": [
        "Miješani komunalni otpad: Gornji grad ponedjeljkom, Donji grad utorkom (svaki tjedan). Granica između "
        "Gornjeg i Donjeg grada nije objavljena uz kalendar; provjerite kod davatelja (044 851 700).",
        "Biootpad, papir i plastika odvoze se za cijeli grad na iste datume (biootpad srijedom, svaki drugi tjedan, "
        "ljeti češće).",
        "Papir i plastika: kalendar označava dva uzastopna dana na kraju mjeseca (oba dana i papir i plastika) i "
        "ne navodi koji dan vrijedi za koji dio grada, pa su upisana oba dana za obje zone.",
        "Blagdani: pomaci su ugrađeni u kalendar (npr. 6.4. Uskrsni ponedjeljak: odvozi tog tjedna dan kasnije) "
        "i označeni su kao pomaknuti.",
        "Ekos Hrvatska Kostajnica d.o.o., Ratka Djetelića 2, tel. 044 851 700, info@ekos-hk.hr.",
    ],
}


def runs(mask, gap=3):
    """[(start, end)] of the True runs in a 1-d mask, joining gaps up to `gap`."""
    out = []
    for x in np.where(mask)[0]:
        if out and x - out[-1][1] <= gap:
            out[-1][1] = x
        else:
            out.append([x, x])
    return [tuple(r) for r in out]


def read_calendar(path, year):
    """({date: fills like 'G' or 'YC'}, problems) from the calendar image."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    ink = (r + g + b < 250) | ((r > 180) & (g < 80) & (b < 80))
    brown = (abs(r - 100) < 40) & (abs(g - 65) < 30) & (b < 60)
    cls = {"G": (r < 90) & (g > 200) & (b < 90), "D": (abs(r - g) < 12) & (abs(g - b) < 12) & (r > 180) & (r < 228),
           "B": (abs(r - 125) < 30) & (abs(g - 125) < 30) & (b < 50), "Y": (r > 220) & (g > 220) & (b < 80),
           "C": (r < 80) & (g > 200) & (b > 200)}
    heads = []  # (bottom of the weekday header row, 28 column centres)
    for s, e in runs(brown.sum(1) > 20):
        xs = [(x0 + x1) // 2 for x0, x1 in runs(brown[s:e + 1].any(0), gap=6) if x1 - x0 > 8]
        if 12 <= e - s <= 30 and len(xs) == 28:
            heads.append((e, xs))
    if len(heads) != 3:
        return {}, [f"našao {len(heads)} redova s danima u tjednu, očekivano 3"]
    found, problems = {}, []
    for bi, (hy, xs) in enumerate(heads):
        stop = heads[bi + 1][0] - 90 if bi < 2 else hy + 330
        for mi in range(4):
            month, cols = bi * 4 + mi + 1, xs[mi * 7:mi * 7 + 7]
            ys = [(s + e) // 2 + hy + 8 for s, e in runs(ink[hy + 8:stop, cols[0] - 25:cols[6] + 25].sum(1) > 2, 4)
                  if e - s >= 10]
            first, days = date(year, month, 1).weekday(), calendar.monthrange(year, month)[1]
            if len(ys) != (first + days + 6) // 7:
                problems.append(f"mjesec {month}: {len(ys)} redova brojeva, očekivano {(first + days + 6) // 7}")
                continue
            for slot in range(len(ys) * 7):
                row, col = divmod(slot, 7)
                cell = np.s_[ys[row] - 16:ys[row] + 17, cols[col] - 24:cols[col] + 25]
                n = {k: int(v[cell].sum()) for k, v in cls.items()}
                n["D"] = n["D"] if n["D"] > 150 else 0  # digit edges are grey too
                fills = "".join(k for k in "GDBYC" if n[k] > 60)
                day = slot - first + 1
                if not 1 <= day <= days:
                    if ink[cell].sum() > 5 or fills:
                        problems.append(f"mjesec {month}: nešto je u praznom polju {slot}")
                    continue
                d = date(year, month, day)
                if ink[cell].sum() < 30:
                    problems.append(f"{d:%d.%m.}: nema broja dana u polju (mreža ne odgovara)")
                if fills not in ("", "G", "D", "B", "YC"):
                    problems.append(f"{d:%d.%m.}: nejasna boja polja {n}")
                elif fills:
                    found[d] = fills
    return found, problems


def with_moves(found, year, problems):
    """[(date, fills, moved)]; off the usual weekday is moved only in a week with a public holiday."""
    hol = set(blagdani(year))
    rows = []
    for d, f in sorted(found.items()):
        moved = d.weekday() not in WEEKDAY[f]
        monday = d - timedelta(days=d.weekday())
        if moved and not any(monday + timedelta(days=i) in hol for i in range(6)):
            problems.append(f"{d:%d.%m.} {FILLS[f[0]]}: neočekivan dan u tjednu, a u tom tjednu nema blagdana")
        if d in hol or d.weekday() == 6:
            problems.append(f"{d:%d.%m.}: odvoz na blagdan ili nedjelju")
        rows.append((d, f, moved))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = fetch(PAGE).decode("utf-8", "replace")
    links = re.findall(rf'(https?://[^"\'\s]*Kalendar-otpada{year}(?:-scaled)?\.jpe?g)', page)
    if not links or year != IMAGE["year"]:
        sys.exit(f"Nema provjerenog kalendara za {year} na {PAGE}. Ništa nije upisano.")
    url = html.unescape(sorted(links, key=lambda u: "-scaled" not in u)[0])
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "kalendar.jpg"
        fetch(url, img)
        sha = hashlib.sha256(img.read_bytes()).hexdigest()
        if sha != IMAGE["sha256"]:
            sys.exit(f"slika se promijenila, prepisati ponovno: {url} (sha256 {sha}). Ništa nije upisano.")
        found, problems = read_calendar(img, year)
    rows = with_moves(found, year, problems)
    cnt = Counter((d.month, f) for d, f, _ in rows)
    for month in range(1, 13):
        for f, lo, hi in (("G", 4, 5), ("D", 4, 5), ("B", 1, 5), ("YC", 2, 2)):
            if not lo <= cnt[month, f] <= hi:
                problems.append(f"mjesec {month}: {cnt[month, f]}x {FILLS[f[0]]}, očekivano {lo}-{hi}")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, (fill, name) in enumerate(ZONES, 1):
        mine = [(d, {"G": "M", "D": "M", "B": "B", "YC": "PK"}[f], mv) for d, f, mv in rows if f in (fill, "B", "YC")]
        if len({d for d, *_ in mine}) != len(mine):
            problems.append(f"zona {z}: datum dvaput")
        data["zone"][str(z)] = {
            "jls": "Hrvatska Kostajnica",
            "podrucje": f"{name} – miješani otpad {'ponedjeljkom' if fill == 'G' else 'utorkom'}",
            "ulice": [name],
            "napomena": "Popis ulica " + ("Gornjeg" if fill == "G" else "Donjeg") + " grada nije objavljen.",
            "raw": {**old.get(str(z), {}).get("raw", {}), str(year): podaci.month_lines(mine)},
        }
        total = Counter(c for _, codes, _ in mine for c in codes)
        print(f"Zona {z} ({name}): " + ", ".join(f"{c} {total[c]}" for c in "MBPK")
              + f", pomaknuto {sum(1 for *_, mv in mine if mv)}")
    for d, f, mv in rows:
        if mv:
            print(f"   pomaknuto: {d:%d.%m.} ({podaci.DAYS[d.weekday()]}) {FILLS[f[0]]}")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(data['zone'])} zone)")


if __name__ == "__main__":
    main()
