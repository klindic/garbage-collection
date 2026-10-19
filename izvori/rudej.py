"""Rudej d.o.o.: Općina Okrug (Okrug Gornji and Okrug Donji), one colour-coded year calendar for the whole municipality.

    python3 -m izvori.rudej [--year 2026]

The page "Raspored odvoza" shows the year calendar as a JPG (12 month grids of 7 x 6 day cells, cell colour =
waste type: pink mixed waste; winter blue paper+plastic+metal and grey glass+tetrapak+garden waste; summer
orange paper+tetrapak, green glass+garden waste, olive plastic+metal). The image is identified by its
sha256 (a changed image stops the script: "slika se promijenila"); the colours are sampled on the fixed
grid with Pillow/numpy (median of each cell, so the day number does not matter) and matched to the legend.
Cells outside the month must be empty and every colour must be known. Holiday shifts are drawn into the
calendar: a date off the type's usual weekday of the season is accepted only in a week with a public
holiday and is marked as moved.
"""
import argparse
import calendar
import hashlib
import io
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import numpy as np
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "rudej"
SITE = "https://rudej.hr"
PAGE = SITE + "/raspored-odvoza/"
YEAR = 2026
SHA256 = "0b35f0ce7a7e6285e6d07e03a4d7bbb1a200edf31dd5c40b5216e9d1e0d95047"  # WhatsApp-Slika-2025-11-26-u-14.42.05
SIZE = (1600, 787)
X0 = [74.5, 329, 579, 830, 1081, 1333]  # centre of the Monday column of the six month grids in a row
Y0 = [335, 569]  # centre of the first week row, upper and lower half
PX, PY = 33.65, 28.9  # cell pitch
PALETTE = {  # cell colour: (codes, season key)
    (245, 245, 245): ("", ""),
    (234, 127, 185): ("M", "M"),
    (122, 200, 235): ("PK", "zima PK"),     # Papir, plastika, metal
    (198, 205, 210): ("SZ", "zima SZ"),     # Staklo, tetrapak, zeleni otpad
    (245, 185, 110): ("K", "ljeto K"),      # Papir, tetrapak
    (66, 185, 135): ("SZ", "ljeto SZ"),     # Staklo, zeleni otpad
    (200, 208, 150): ("P", "ljeto P"),      # Plastika, metal
}
TOLERANCE = 25
PROVIDER = {
    "davatelj": "Rudej d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Okrug"],
    "napomene": [
        "Zimski period: papir, plastika i metal zajedno (plavo), staklo, tetrapak i zeleni otpad zajedno (sivo). "
        "Ljetni period: papir i tetrapak, staklo i zeleni otpad, plastika i metal (svaki svoj dan).",
        "Spremnike i vrećice iznijeti večer prije ili najkasnije do 5 sati ujutro.",
        "Glomazni otpad od 1.10. do 31.5. po otvorenom nalogu s kućnog praga ili u reciklažnom dvorištu; od 1.6. "
        "do 30.9. ne odvozi se s kućnog praga.",
        "Reciklažno dvorište: Put sv. Karla 70, Okrug Gornji, 021 263 915 (radnim danom 8 – 14, subotom 8 – 12 sati).",
        "Pomaci zbog blagdana ucrtani su u kalendar.",
        "Rudej d.o.o., Bana Jelačića 17, Okrug Gornji, 021/688-502.",
    ],
}


def read_grid(img, year, problems):
    """[(date, codes, season key)] from the 12 month grids."""
    px = np.asarray(img.convert("RGB")).astype(int)
    out = []
    for m in range(1, 13):
        first, days = calendar.monthrange(year, m)
        b, half = (m - 1) % 6, (m - 1) // 6
        for r in range(6):
            for c in range(7):
                cx, cy = X0[b] + c * PX, Y0[half] + r * PY
                col = np.median(px[int(cy - 10):int(cy + 10), int(cx - 12):int(cx + 12)].reshape(-1, 3), axis=0)
                dist = {k: np.abs(col - k).max() for k in PALETTE}
                key = min(dist, key=dist.get)
                day = r * 7 + c - first + 1
                if dist[key] > TOLERANCE:
                    problems.append(f"{m}. mjesec, redak {r + 1}, stupac {c + 1}: nepoznata boja {tuple(int(v) for v in col)}")
                elif not 1 <= day <= days:
                    if PALETTE[key][0]:
                        problems.append(f"{m}. mjesec: obojena ćelija izvan mjeseca (redak {r + 1}, stupac {c + 1})")
                elif PALETTE[key][0]:
                    out.append((date(year, m, day), *PALETTE[key]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Mreža i boje provjereni su samo za kalendar {YEAR}.")
    page = fetch(PAGE).decode("utf-8", "replace")
    urls = sorted({u for u in re.findall(r'(https://rudej\.hr/wp-content/uploads/[^"\s]+\.jpe?g)', page)
                   if not re.search(r"-\d+x\d+\.jpe?g$", u)})  # originals, not the srcset sizes
    found = None
    for url in urls:
        body = fetch(url)
        digest = hashlib.sha256(body).hexdigest()
        print(f"{url.rsplit('/', 1)[1]}: sha256 {digest[:12]}…")
        if digest == SHA256:
            found = body
    if found is None:
        sys.exit(f"slika se promijenila: na {PAGE} nema kalendara sa sha256 {SHA256[:12]}… – "
                 "provjeriti novu sliku, mrežu i boje")
    img = Image.open(io.BytesIO(found))
    if img.size != SIZE:
        sys.exit(f"slika se promijenila: veličina {img.size}")
    problems = []
    cells = read_grid(img, year, problems)

    # usual weekdays per type and season; anything else must be in a week with a public holiday
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))
    usual = defaultdict(Counter)
    for d, codes, key in cells:
        season = "ljeto" if 6 <= d.month <= 9 else "zima"
        usual[(key, season)][d.weekday()] += 1
    rows = []
    for d, codes, key in cells:
        season = "ljeto" if 6 <= d.month <= 9 else "zima"
        counts = usual[(key, season)]
        moved = counts[d.weekday()] < 0.25 * max(counts.values())
        monday = d - timedelta(days=d.weekday())
        if moved and not any(monday <= h <= monday + timedelta(days=6) for h in hol):
            problems.append(f"{d:%d.%m.} {key}: neuobičajen dan u tjednu bez blagdana u tom tjednu")
        rows.append((d, codes, moved))
    if len({d for d, _, _ in rows}) != len(rows):
        problems.append("dvije boje za isti dan")
    for m in range(1, 13):
        mixed = sum(1 for d, c, _ in rows if d.month == m and "M" in c)
        other = sum(1 for d, c, _ in rows if d.month == m and "M" not in c)
        want = (11, 15, 10, 15) if 6 <= m <= 9 else (8, 10, 2, 5)
        if not (want[0] <= mixed <= want[1] and want[2] <= other <= want[3]):
            problems.append(f"{m}. mjesec: miješani {mixed}, ostalo {other}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"].get("1", {}) if path.exists() else {}
    data = {**PROVIDER, "zone": {"1": {
        "jls": "Okrug",
        "podrucje": "Cijela općina (zimi miješani pon i pet, ljeti pon, čet i sub)",
        "ulice": ["Okrug Gornji", "Okrug Donji"],
        "raw": {**old.get("raw", {}), str(year): podaci.month_lines(rows)},
    }}}
    moved = [f"{d:%d.%m.}" for d, _, mv in sorted(rows) if mv]
    print(f"Zona 1: {len(rows)} odvoza, pomaknuto zbog blagdana: {' '.join(moved) or '-'}")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
