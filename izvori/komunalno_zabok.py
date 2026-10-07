"""Zabok and Bedekovčina: Komunalno-Zabok d.o.o. (komunalno-zabok.hr), one zone per weekday route.

    python3 -m izvori.komunalno_zabok [--year 2026]

The page "Odvoz otpada" (read through the WordPress REST API) lists the streets and settlements of every
weekday route for Grad Zabok and Općina Bedekovčina. The year calendar image linked on the same page
(KALENDAR-ODVOZA-OTPADA-<year>_page-0002.jpg) colours whole weeks: black = mixed waste, yellow = plastic
and tetrapak, blue = paper and cardboard, in a fixed four-week cycle by ISO week (odd weeks mixed, week
2 mod 4 plastic, week 0 mod 4 paper); every route is collected on its weekday in every week. The dates
are computed from that rule (pravila.py) and every weekday cell of the image is checked against it by
pixel colour on the image's fixed grid (weekends and days of other months must be white). Biowaste
(brown bin, on request) is collected every Friday. Holidays are only printed red inside the coloured
weeks and no shift rule is published, so the dates stay as computed and a note says so. The sha256 of
both images (calendar and the sheet with notes) is kept below; a changed image stops the script.
"""
import argparse
import hashlib
import html
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalno-zabok"
SITE = "https://www.komunalno-zabok.hr"
PAGE = SITE + "/odvoz-otpada/"
REST = SITE + "/wp-json/wp/v2/pages?slug=odvoz-otpada&_fields=id,link,modified,content"
IMAGES = {  # checked by hand: file name -> sha256
    "KALENDAR-ODVOZA-OTPADA-2026_page-0002.jpg": "a87bbd8c6bf07d5fda01fed731975b146d48923e480297fab976e2117e73c5da",
    "KALENDAR-ODVOZA-OTPADA-2026_page-0001.jpg": "6df77b7fbaebd7db7a8a4de6e403f1f4ebf024fa3db3b172131c14f5bcc387b5",
}
CYCLE = {1: "M", 2: "P", 3: "M", 0: "K"}  # ISO week mod 4 -> bin (the image's black/yellow/black/blue weeks)
FILL = {"M": "crno", "P": "žuto", "K": "plavo", None: "bijelo"}
# fixed grid of the 1241x1754 calendar: centre of the Monday cell in the first row of each month column
# and row, column and row pitch (px)
GRID_X, GRID_Y, PX, PY = (125, 500.5, 875), (328.5, 680, 1032, 1383.5), 44.75, 46
JLS = {"grad zabok": "Zabok", "općina bedekovčina": "Bedekovčina"}
DAYS = {"ponedjeljak": 0, "utorak": 1, "srijeda": 2, "četvrtak": 3, "petak": 4, "subota": 5}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
KEY = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
PROVIDER = {
    "davatelj": "Komunalno-Zabok d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Krapinsko-zagorska",
    "jls": ["Zabok", "Bedekovčina"],
    "nazivi": {"P": "Plastika i tetrapak", "M": "Miješani komunalni otpad (zelena posuda)"},
    "bioNapomena": "Biootpad (smeđa posuda) odvozi se svaki petak; smeđu posudu dobiva tko ju zatraži "
                   "(tko ne kompostira kod kuće).",
    "napomene": [
        "Na kalendaru su obojeni cijeli tjedni: crno miješani komunalni otpad, žuto plastika i tetrapak, plavo "
        "papir i karton; odvozi se na dan iz rasporeda ulica u tom tjednu.",
        "Pomaci zbog blagdana nisu objavljeni: na kalendaru su blagdani samo otisnuti crveno{extra}, pa su datumi "
        "ostavljeni prema rasporedu. Za odvoz na blagdan provjerite s Komunalno-Zabok, 049/226-630.",
        "Odvoz od 7 do 15 sati (1.1.–31.5. i 1.11.–31.12.) odnosno od 6 do 14 sati (1.6.–31.10.); ponedjeljkom "
        "i petkom od 5 do 13 sati.",
        "Kontejner za glomazni otpad: jedanput godišnje kontejner od 5 m³ (komunalno-zabok@kr.t-com.hr, "
        "049 492 357, 492 358, 226 630).",
        "Mobilno reciklažno dvorište (Grad Zabok) svaki petak na drugoj lokaciji; otpad se uz najavu može dovesti "
        "u Komunalno dvorište, K. Š. Gjalskog 31, Zabok. Reciklažno dvorište Bedekovčina, S. Radića 35e: utorak "
        "i četvrtak 13–20, subota 9–14 sati.",
    ],
}


def routes(content, problems):
    """[(jls, weekday, [streets])] from the page tables, in page order."""
    out = []
    for table in re.findall(r"<table.*?</table>", content, re.S):
        text = html.unescape(re.sub(r"<[^>]+>", " ", table.split("</td>", 1)[0])).lower()
        jls = next((v for k, v in JLS.items() if k in " ".join(text.split())), None)
        if not jls:
            problems.append(f"tablica bez grada/općine: {' '.join(text.split())[:80]!r}")
            continue
        for kind, val in re.findall(r"<(strong|li)>(.*?)</\1>", table, re.S):
            val = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", val)).split())
            if kind == "strong" and val.lower() in DAYS:
                out.append((jls, DAYS[val.lower()], []))
            elif kind == "li" and val:
                if not out or out[-1][0] != jls:
                    problems.append(f"{jls}: ulica bez dana: {val!r}")
                    continue
                out[-1][2].append(clean(val))
    return out


def clean(name):
    """'Gajeva ul. –' -> 'Gajeva ul.', 'M. GUpca' -> 'M. Gupca'."""
    name = re.sub(r"[\s,–-]+$", "", name).strip()
    return name.replace("GUpca", "Gupca")


def cycle_dates(year, wd):
    """{date: bin} for one weekday route, counted from the first week of each bin (CYCLE)."""
    out = {}
    for code, n in (("M", 2), ("P", 4), ("K", 4)):
        first = next(d for d in pravila.tjedno(year, KEY[wd]) if CYCLE[d.isocalendar()[1] % 4] == code)
        out.update({d: code for d in pravila.svaki_n_tjedan(year, KEY[wd], n, first)})
    return out


def check_image(path, year):
    """Problems where a weekday cell's colour differs from the four-week cycle, and the red weekdays."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    problems, reds = [], set()
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    red = (r - g > 30) & (r - b > 20) & (r > 90)

    def fill(cx, cy):
        votes = []
        for dx in (-16, 16):
            for dy in (-16, 16):
                p = np.median(a[int(cy + dy) - 2:int(cy + dy) + 3, int(cx + dx) - 2:int(cx + dx) + 3].reshape(-1, 3),
                              axis=0)
                votes.append("M" if p.max() < 70 else "K" if p[2] > 180 and p[0] < 80 else
                             "P" if p[0] > 200 and p[1] > 200 and p[2] < 90 else None if p.min() > 215 else "?")
        return Counter(votes).most_common(1)[0][0]

    for m in range(1, 13):
        first = date(year, m, 1)
        monday = first - timedelta(days=first.weekday())
        last = date(year + m // 12, m % 12 + 1, 1) - timedelta(days=1)
        d = monday
        while d <= last + timedelta(days=6 - last.weekday()):
            k, col = (d - monday).days // 7, d.weekday()
            cx, cy = GRID_X[(m - 1) % 3] + col * PX, GRID_Y[(m - 1) // 3] + k * PY
            want = CYCLE[d.isocalendar()[1] % 4] if d.month == m and col < 5 else None
            got = fill(cx, cy)
            if got != want:
                problems.append(f"slika: {d:%d.%m.} ({m}. mjesec) je {FILL.get(got, got)}, očekivano {FILL[want]}")
            if d.month == m and col < 5 and red[int(cy - 12):int(cy + 12), int(cx - 14):int(cx + 14)].sum() > 10:
                reds.add(d)
            d += timedelta(days=1)
    return problems, reds


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    page = json.loads(fetch(REST))[0]
    content = page["content"]["rendered"]
    imgs = sorted(set(re.findall(rf'https?://[^"\s]+/KALENDAR-ODVOZA-OTPADA-{year}_page-\d+\.jpg', content)))
    if not imgs:
        sys.exit(f"Nema kalendara za {year} na {PAGE}")
    with tempfile.TemporaryDirectory() as tmp:
        for url in imgs:
            name = url.rsplit("/", 1)[1]
            path = Path(tmp) / name
            fetch(url, path)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if IMAGES.get(name) != sha:
                problems.append(f"slika se promijenila ili nije provjerena: {name} (sha256 {sha})")
        cal = Path(tmp) / f"KALENDAR-ODVOZA-OTPADA-{year}_page-0002.jpg"
        if not cal.exists():
            sys.exit(f"Nema stranice kalendara (_page-0002) među slikama: {imgs}")
        img_problems, reds = check_image(cal, year)
    problems += img_problems
    hol = {d for d in pravila.blagdani(year) if d.weekday() < 5}
    for d in sorted(hol - reds):
        problems.append(f"blagdan {d} nije crven na kalendaru")
    extra = sorted(reds - hol)
    if extra:
        print("Crveno na kalendaru, a nije blagdan:", ", ".join(f"{d:%d.%m.}" for d in extra))
    extra_text = (f" (crveno je otisnut i {', '.join(f'{DAN[d.weekday()]} {d.day}.{d.month}.' for d in extra)}, "
                  "koji nije blagdan)") if extra else ""
    data = {**PROVIDER, "napomene": [n.format(extra=extra_text) for n in PROVIDER["napomene"]], "zone": {}}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    found = routes(content, problems)
    if sorted((j, wd) for j, wd, _ in found) != sorted(set((j, wd) for j, wd, _ in found)):
        problems.append("isti dan dvaput za isti grad/općinu")
    for jls, wd, streets in found:
        if not streets:
            problems.append(f"{jls} {DAN[wd]}: nema ulica")
            continue
        dates = cycle_dates(year, wd)
        rows = {}
        for d, code in dates.items():
            rows[d] = rows.get(d, "") + code
        for d in pravila.tjedno(year, "pet"):
            rows[d] = rows.get(d, "") + "B"
        n = Counter(c for codes in rows.values() for c in codes)
        for code, lo, hi in (("M", 26, 27), ("P", 13, 14), ("K", 13, 14), ("B", 52, 53)):
            if not lo <= n[code] <= hi:
                problems.append(f"{jls} {DAN[wd]}: {code} {n[code]} puta")
        for d, codes in rows.items():
            if "M" in codes or "P" in codes or "K" in codes:
                if d.weekday() != wd or CYCLE[d.isocalendar()[1] % 4] not in codes:
                    problems.append(f"{jls} {DAN[wd]}: {d} ne odgovara ciklusu")
        key = str(len(data["zone"]) + 1)
        prev = old["zone"].get(key, {})
        label = ", ".join(streets[:3]) + (" …" if len(streets) > 3 else "")
        data["zone"][key] = {
            "jls": jls,
            "podrucje": f"{DAN[wd].capitalize()} – {label}",
            "ulice": streets,
            "raw": {**({k: v for k, v in prev.get("raw", {}).items() if k != str(year)}
                       if prev.get("jls") == jls else {}),
                    str(year): podaci.month_lines([(d, c, False) for d, c in rows.items()])},
        }
        print(f"{jls}, {DAN[wd]}: {len(streets)} ulica/naselja, {dict(n)}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
