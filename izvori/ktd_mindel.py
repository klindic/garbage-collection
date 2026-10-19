"""KTD Mindel d.o.o.: Općina Lumbarda, two groups of settlements, one colour-coded year calendar (JPG) each.

    python3 -m izvori.ktd_mindel [--year 2026]

The page "Kalendar odvoza otpada" shows two small JPG calendars exported from Excel (12 month grids,
cell colour = waste type: green mixed waste, blue paper and cardboard, yellow plastic; two cells after a
Monday holiday are split diagonally, with a lighter green half for the moved mixed waste). Each image is identified by its sha256 (a changed image stops the
script: "slika se promijenila"); the colours are sampled with Pillow/numpy on the fixed grid measured
for that image, in the lower-left and upper-right corner of every cell (so the day number is not hit and
split cells give both types). Cells outside the month must be white and every colour must be known.
The Sunday column of the right-hand months is cut off in both images; all visible Sundays are white, so
those Sundays are taken as days without collection. Holiday shifts are drawn into the calendar: a date
off the type's usual weekday of the season is accepted only in a week with a public holiday and is
marked as moved.
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

SLUG = "ktd-mindel"
SITE = "https://ktd-mindel.hr/develop"
PAGE = SITE + "/kalendar-odvoza-otpada/"
YEAR = 2026
IMAGES = [  # (file name, sha256, size, Monday column centres, first week row centres, pitch x, y, zone)
    ("2026-odvoz-smeca-lumbarda-1-2.jpg", "286b2eeaa99b18770b1f8fe7d7bd58af7d45cb529deb030f1c8131e6688deeae",
     (569, 720), (11, 219, 427), (238.4, 368.6, 498.5, 628.5), 26.2, 15.15,
     ("Postrana, M. Postrana, Kosovo, Žabjak, Šerić, Pleće-Lučica, Koludrt",
      ["Postrana", "M. Postrana", "Kosovo", "Žabjak", "Šerić", "Pleće-Lučica", "Koludrt"])),
    ("2026-odvoz-smeca-lumbarda-2.jpg", "7770c813aaf5937aa73c28516271c5ed7dc4dc8e7566e8a975f56b7fa9c76dd7",
     (575, 720), (9.5, 217.5, 425.3), (241.6, 371.6, 501.6, 631.6), 26.15, 15.2,
     ("Soline, Sv. Antun, U. Račišće, Javič, Tatinja, M. Glavica, V. Glavica",
      ["Soline", "Sv. Antun", "U. Račišće", "Javič", "Tatinja", "M. Glavica", "V. Glavica"])),
]
PALETTE = {(255, 255, 255): "", (10, 169, 81): "M", (14, 170, 225): "K", (248, 252, 17): "P",
           (105, 205, 0): "M"}  # lighter green: the mixed-waste half of the two diagonally split cells
TOLERANCE = 70
PROVIDER = {
    "davatelj": "KTD Mindel d.o.o.",
    "web": SITE + "/",
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Lumbarda"],
    "nazivi": {"P": "Plastika"},
    "napomene": [
        "Raspored je preuzet iz kalendara u boji (zeleno miješani komunalni otpad, plavo papir i karton, žuto "
        "plastika); ljeti se miješani otpad odvozi češće.",
        "Pomaci zbog blagdana ucrtani su u kalendar (blagdani su označeni crveno, bez odvoza).",
        "Nedjelje u ožujku, lipnju, rujnu i prosincu odrezane su na slici; uzeto je da tih dana nema odvoza "
        "kao ni ostalih nedjelja.",
    ],
}


def sample(px, x0, x1, y0, y1):
    return np.median(px[int(y0):int(y1) + 1, int(x0):int(x1) + 1].reshape(-1, 3), axis=0)


def code_of(col, where, problems):
    dist = {k: np.abs(col - k).max() for k in PALETTE}
    key = min(dist, key=dist.get)
    if dist[key] > TOLERANCE:
        problems.append(f"{where}: nepoznata boja {tuple(int(v) for v in col)}")
        return None
    return PALETTE[key]


def read_grid(img, geometry, year, problems):
    """{date: codes} from the 12 month grids (3 per row, 4 rows)."""
    xs, ys, pitch_x, pitch_y = geometry
    px = np.asarray(img.convert("RGB")).astype(int)
    width = px.shape[1]
    out, sundays = {}, Counter()
    for m in range(1, 13):
        first, days = calendar.monthrange(year, m)
        for r in range(6):
            for c in range(7):
                cx, cy = xs[(m - 1) % 3] + c * pitch_x, ys[(m - 1) // 3] + r * pitch_y
                day = r * 7 + c - first + 1
                where = f"{m}. mjesec, redak {r + 1}, stupac {c + 1}"
                if cx + 11 >= width:
                    continue  # the cut-off Sunday column
                ll = code_of(sample(px, cx - 10, cx - 6, cy + 2, cy + 5), where, problems)
                ur = code_of(sample(px, cx + 6, cx + 10, cy - 5, cy - 2), where, problems)
                if ll is None or ur is None:
                    continue
                codes = "".join(sorted(set(ll + ur), key="MKP".index))
                if not 1 <= day <= days:
                    if codes:
                        problems.append(f"{where}: obojena ćelija izvan mjeseca")
                    continue
                d = date(year, m, day)
                if c == 6:
                    sundays[bool(codes)] += 1
                if codes:
                    out[d] = codes
    if sundays[True]:
        problems.append(f"{sundays[True]} obojenih nedjelja – pretpostavka o odrezanim nedjeljama ne vrijedi")
    return out


def mark_moved(cells, year, problems, zone):
    """[(date, codes, moved)]: a type off its usual weekday of the season must be in a week with a holiday."""
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))
    season = lambda d: "ljeto" if 6 <= d.month <= 9 else "zima"
    usual = defaultdict(Counter)
    for d, codes in cells.items():
        for c in codes:
            usual[(c, season(d))][d.weekday()] += 1
    rows = []
    for d, codes in sorted(cells.items()):
        off = [c for c in codes if usual[(c, season(d))][d.weekday()] < 0.25 * max(usual[(c, season(d))].values())]
        monday = d - timedelta(days=d.weekday())
        if off and not any(monday <= h <= monday + timedelta(days=6) for h in hol):
            problems.append(f"zona {zone} {d:%d.%m.} {''.join(off)}: neuobičajen dan bez blagdana u tom tjednu")
        rows.append((d, codes, bool(off)))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Mreže i boje provjereni su samo za kalendare {YEAR}.")
    page = fetch(PAGE).decode("utf-8", "replace")
    links = {u.rsplit("/", 1)[1]: u for u in re.findall(r'(https?://ktd-mindel\.hr/develop/wp-content/uploads/[^"\s]+\.jpe?g)', page)}
    problems, zones = [], {}
    for z, (name, sha, size, xs, ys, pitch_x, pitch_y, (podrucje, streets)) in enumerate(IMAGES, 1):
        if name not in links:
            sys.exit(f"slika se promijenila: na {PAGE} više nema {name} (nove slike: "
                     f"{sorted(n for n in links if 'odvoz' in n)})")
        body = fetch(links[name])
        digest = hashlib.sha256(body).hexdigest()
        img = Image.open(io.BytesIO(body))
        if digest != sha or img.size != size:
            sys.exit(f"slika se promijenila, kalendar treba ponovno provjeriti: {links[name]} (sha256 {digest})")
        cells = read_grid(img, (xs, ys, pitch_x, pitch_y), year, problems)
        rows = mark_moved(cells, year, problems, z)
        for m in range(1, 13):
            mixed = sum(1 for d, c, _ in rows if d.month == m and "M" in c)
            other = sum(1 for d, c, _ in rows if d.month == m and set(c) & set("KP"))
            if not (7 <= mixed <= 15 and 3 <= other <= 6):
                problems.append(f"zona {z} {m}. mjesec: miješani {mixed}, papir/plastika {other}")
        zones[str(z)] = (podrucje, streets, rows)
        split = [f"{d:%d.%m.} {c}" for d, c, _ in rows if len(c) > 1]
        moved = [f"{d:%d.%m.}" for d, _, mv in rows if mv]
        print(f"Zona {z} ({podrucje}): {len(rows)} odvoza; dvije vrste u ćeliji: {', '.join(split) or '-'}; "
              f"pomaknuto: {' '.join(moved) or '-'}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, (podrucje, streets, rows) in zones.items():
        data["zone"][z] = {"jls": "Lumbarda", "podrucje": podrucje, "ulice": streets,
                           "raw": {**old.get(z, {}).get("raw", {}), str(year): podaci.month_lines(rows)}}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
