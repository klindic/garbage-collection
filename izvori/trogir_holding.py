"""Trogir: Trogir Holding d.o.o. (tgholding.hr), three areas with a 2026 colour calendar (Čiovo, Travarica, Drvenik Veli).

    python3 -m izvori.trogir_holding [--year 2026]

The page "Gospodarenje otpadom" shows one PNG year calendar per area: Čiovo/Žedno/Arbanija/Mastrinka and
Travarica/Divulje/Plano (green = mixed waste, yellow = plastic, blue = paper and cardboard, red = no
collection) and Drvenik Veli (mixed waste only). Weeks start on Sunday. The script finds the twelve white
month grids (6 x 7 cells), samples the colour of every day cell and checks the grid: every day of the
month must have its digits in its cell and days of other months must be uncoloured. Each image's sha256 is
pinned, so a new calendar stops the script until it is checked again. The result is checked against each
area's rule (weekdays of mixed waste, plastic and paper alternating every week). Holidays: the calendars
show collection on public holidays except the cells printed red (1.1. on Čiovo, 25.12. in Travarica),
which are left out. The mainland (town centre, other streets) has no 2026 calendar (only posts from 2019),
so it is not included; nor are the public "zeleni otoci" containers.
"""
import argparse
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

SLUG = "trogir-holding"
SITE = "https://tgholding.hr"
PAGE = SITE + "/gospodarenje-otpadom/"
YEAR = 2026
# name in the image URL -> zone; M/PK: weekdays (0 = Monday) and months; sha256 of the checked image
AREAS = [
    {"file": "ciovo_zedno", "sha256": "4e838e860c2efb6c35459d5613c55b41290e0eb2d76ff8a00c27003cee16c96c",
     "podrucje": "Čiovo, Žedno, Arbanija, Mastrinka", "ulice": ["Čiovo", "Žedno", "Arbanija", "Mastrinka"],
     "M": {0, 3}, "PK": 5, "summer": None},
    {"file": "travarica_divulje", "sha256": "70ad568bc0d0d6ae202448069185fa5f230955f0f3ce9eef496d74014a52f665",
     "podrucje": "Travarica, Divulje, Plano", "ulice": ["Travarica", "Divulje", "Plano"],
     "M": {1, 4}, "PK": 2, "summer": None},
    {"file": f"Kalendar-odvoza-Drvenik-Veliki-{YEAR}",
     "sha256": "7d7cb3fc1a98a3164e9b2fc4699670e2db60b18eca8e51ad3a580c9d9394a916",
     "podrucje": "Drvenik Veli", "ulice": ["Drvenik Veli"], "M": {0}, "PK": None, "summer": (4, range(5, 10))},
]
DAN = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
CODES = {"G": "M", "Y": "P", "B": "K", "R": None}
PROVIDER = {
    "davatelj": "Trogir Holding d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Trogir"],
    "napomene": [
        "Uključena su područja s kalendarom odvoza za 2026.: Čiovo (Žedno, Arbanija, Mastrinka), Travarica/Divulje/"
        "Plano i Drvenik Veli. Za kopneni dio grada (centar i ostale ulice) i Drvenik Mali kalendar za 2026. nije "
        "objavljen (postoje samo objave iz 2019.), pa nisu uključeni.",
        "Kantu treba postaviti do 6:00 sati na vidljivo mjesto uz ulicu (kolni prilaz); otpad se preuzima od 6 do 13 h.",
        "Blagdani: prema kalendaru odvoz se obavlja i na blagdane, osim dana označenih crveno (1.1. na Čiovu, "
        "25.12. u Travarici), kada odvoza nema.",
        "Zeleni otoci (javni spremnici za staklo, plastiku i papir) prazne se prema posebnom rasporedu na "
        "stranici Trogir Holdinga; nisu uključeni.",
        "Glomazni otpad odvozi se na zahtjev; mobilno reciklažno dvorište prema rasporedu na stranici.",
        "Trogir Holding d.o.o., Put Mulina 2, Trogir, tel. 021 798 567.",
    ],
}


def runs(mask, gap=2, minlen=1):
    out = []
    for x in np.where(mask)[0]:
        if out and x - out[-1][1] <= gap:
            out[-1][1] = x
        else:
            out.append([x, x])
    return [(a, b) for a, b in out if b - a + 1 >= minlen]


def read_calendar(path, year):
    """({date: 'G'|'Y'|'B'|'R'}, problems) from one calendar image."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    cls = {"G": (r < 40) & (abs(g - 176) < 25) & (abs(b - 80) < 30), "Y": (r > 235) & (g > 225) & (b < 60),
           "B": (r < 40) & (abs(g - 176) < 25) & (b > 220), "R": (r > 220) & (g < 60) & (b < 60)}
    cell = (r >= 247) & (g >= 247) & (b >= 247) | cls["G"] | cls["Y"] | cls["B"] | cls["R"]
    ink = r + g + b < 450
    height = a.shape[0]
    cols = runs(cell[height // 4:].sum(0) > height // 6, gap=3, minlen=60)
    if len(cols) != 4:
        return {}, [f"našao {len(cols)} stupaca mjeseci, očekivano 4"]
    found, problems = {}, []
    for ci, (x0, x1) in enumerate(cols):
        grids = [(p, q) for p, q in runs(cell[:, x0:x1 + 1].mean(1) > 0.5, gap=4, minlen=60)
                 if 0.6 < (q - p) / (x1 - x0) < 0.78]  # 6 x 7 cells
        if len(grids) != 3:
            problems.append(f"stupac {ci + 1}: našao {len(grids)} mreža mjeseci, očekivano 3")
            continue
        for ri, (y0, y1) in enumerate(grids):
            month, cw, rh = ri * 4 + ci + 1, (x1 - x0 + 1) / 7, (y1 - y0 + 1) / 6
            first = date(year, month, 1)
            start = first - timedelta(days=(first.weekday() + 1) % 7)  # weeks start on Sunday
            for k in range(42):
                row, col = divmod(k, 7)
                cx, cy = int(x0 + (col + 0.5) * cw), int(y0 + (row + 0.5) * rh)
                box = np.s_[cy - int(rh * 0.3):cy + int(rh * 0.3) + 1, cx - int(cw * 0.3):cx + int(cw * 0.3) + 1]
                fills = "".join(c for c, m in cls.items() if m[box].mean() > 0.3)
                d = start + timedelta(days=k)
                if d.month != month:
                    if fills:
                        problems.append(f"{d:%d.%m.} (dan drugog mjeseca u mreži za mjesec {month}) je obojen")
                    continue
                if ink[box].mean() < 0.02:
                    problems.append(f"{d:%d.%m.}: nema broja dana u polju (mreža ne odgovara)")
                if len(fills) > 1:
                    problems.append(f"{d:%d.%m.}: više boja {fills}")
                elif fills:
                    found[d] = fills
    return found, problems


def check(area, found, year, problems):
    """[(date, codes, moved)]; every date on the area's weekdays and every expected collection present."""
    hol, name = set(blagdani(year)), area["podrucje"]
    rows, pk = [], []
    for d, f in sorted(found.items()):
        if f == "R":
            if d not in hol:
                problems.append(f"{name} {d:%d.%m.}: crveno polje, a nije blagdan")
            continue
        code = CODES[f]
        want = area["M"] | ({area["summer"][0]} if area["summer"] and d.month in area["summer"][1] else set())
        if (code == "M" and d.weekday() not in want) or (code != "M" and d.weekday() != area["PK"]):
            problems.append(f"{name} {d:%d.%m.} {code}: neočekivan dan u tjednu")
        rows.append((d, code, False))
        if code != "M":
            pk.append((d, code))
    # every mixed-waste weekday of the year is green (or red on a holiday)
    d = date(year, 1, 1)
    while d.year == year:
        want = area["M"] | ({area["summer"][0]} if area["summer"] and d.month in area["summer"][1] else set())
        if d.weekday() in want and d not in found:
            problems.append(f"{name} {d:%d.%m.}: nema odvoza miješanog otpada")
        if area["PK"] is not None and d.weekday() == area["PK"] and d not in found:
            problems.append(f"{name} {d:%d.%m.}: nema odvoza plastike ni papira")
        d += timedelta(days=1)
    if area["PK"] is None and pk:
        problems.append(f"{name}: plastika/papir u kalendaru bez legende za njih")
    for (a, ca), (b, cb) in zip(pk, pk[1:]):  # plastic and paper take turns every week
        if (b - a).days != 7 or ca == cb:
            problems.append(f"{name}: {a:%d.%m.} {ca} -> {b:%d.%m.} {cb} nije izmjenično svaki tjedan")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Kalendari za {year} nisu provjereni. Ništa nije upisano.")
    page = html.unescape(fetch(PAGE).decode("utf-8", "replace"))
    problems, data = [], {**PROVIDER, "zone": {}}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    with tempfile.TemporaryDirectory() as tmp:
        for z, area in enumerate(AREAS, 1):
            m = re.search(rf'(https?://[^"\s]*/{re.escape(area["file"])})(?:-\d+x\d+)?\.png', page)
            if not m:
                problems.append(f"{area['podrucje']}: kalendar {area['file']}.png nije na stranici")
                continue
            url = m.group(1) + ".png"  # full size, not the resized copy shown on the page
            img = Path(tmp) / f"{z}.png"
            fetch(url, img)
            sha = hashlib.sha256(img.read_bytes()).hexdigest()
            if sha != area["sha256"]:
                problems.append(f"slika se promijenila, prepisati ponovno: {url} (sha256 {sha})")
                continue
            found, p = read_calendar(img, year)
            problems += p
            rows = check(area, found, year, problems)
            cnt = Counter(c for _, c, _ in rows)
            rule = "miješani " + " i ".join(DAN[d] for d in sorted(area["M"]))
            if area["summer"]:
                rule += f" (od svibnja do rujna i {DAN[area['summer'][0]]})"
            if area["PK"] is not None:
                rule += f", plastika i papir {DAN[area['PK']]} naizmjence"
            data["zone"][str(z)] = {
                "jls": "Trogir", "podrucje": f"{area['podrucje']} – {rule}", "ulice": area["ulice"],
                "raw": {**old.get(str(z), {}).get("raw", {}), str(year): podaci.month_lines(rows)},
            }
            red = [d for d, f in found.items() if f == "R"]
            print(f"Zona {z} ({area['podrucje']}): M {cnt['M']}, P {cnt['P']}, K {cnt['K']}; bez odvoza (crveno): "
                  + (", ".join(f"{d:%d.%m.}" for d in red) or "-"))
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(data['zone'])} zone)")


if __name__ == "__main__":
    main()
