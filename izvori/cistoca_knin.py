"""Knin: Čistoća i zelenilo d.o.o. Knin (ciz.hr), one zone per weekday route (Monday to Saturday).

    python3 -m izvori.cistoca_knin [--year 2026]

The page "Raspored odvoza komunalnog otpada" (plain HTML; the WordPress REST content is empty) links one
year calendar image per route (1.-PON-<year>.jpg ... 6.-SUB-<year>.jpg): a street list ("ULICE: ...") and
a 12-month grid where every collection day is a coloured cell, green = mixed waste (MKO), yellow =
plastic, cyan = paper (a cell split green/yellow is both). The images are only pictures, so the street
lists and the dates were transcribed into ROUTES below, together with each image's sha256 (a changed
image stops the script): mixed waste is weekly on the route's day except the holiday shifts drawn in
the image (removed holiday, added day, marked as moved), the Saturday route and plastic/paper are lists
of dates. On every run the cells of the image are read again by pixel colour on its fixed grid and must
match the transcription exactly; days of other months must have no colour.
"""
import argparse
import hashlib
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

SLUG = "cistoca-knin"
SITE = "https://ciz.hr"
PAGE = SITE + "/raspored-odvoza-komunalnog-otpada/"
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
KEYS = {"PON": 0, "UTO": 1, "SRI": 2, "CET": 3, "PET": 4, "SUB": 5}
# Per image (transcribed by hand, checked against the pixels on every run): sha256; mixed waste either as
# changes to the weekly rule ("-MM-DD" holiday without collection, "+MM-DD" moved collection) or, for the
# Saturday route, the full list; plastic dates; paper dates; streets.
ROUTES = {
    "1.-PON-2026.jpg": (
        "4fc1e80b9f021138ba99e745059d74e9274462ce2184ac83d75bae5bfdcf52d3",
        "-04-06 -06-22 +04-07 +06-23",
        "01-08 02-05 03-05 04-09 05-07 06-03 07-02 07-30 09-03 10-01 10-29 12-03",
        "01-09 02-06 03-06 04-10 05-08 06-05 07-03 08-07 09-04 10-09 11-06 12-04",
        "Tomislavova, Getaldićeva, Vukovarska, Hrvatskih generala, Cotina, Škabrnjska, Suronjina, Alkarska, "
        "Matijaševa, Paližina, Kijevska, Sigetski prolaz, Kliški prolaz, Krbavska, Meštrovićeva, Kačićeva, "
        "Bribirskih knezova, Šuškova, Gotovčeva, Braće Radića, Ujevićeva, Domagojeva, Branimirova, Velebitska, "
        "Jelenina, Nelipićeva, Marulića trg, Krešimirova, Prolaz Petra Zrinskog, Šimunovićeva, Gundulićeva, "
        "Dokozina, Kosorova, Gajeva, Janjevačka, Zoranićeva, Dubravka Horvatića, Frane Bulića, Medačka, "
        "Preradovićeva, Anića glavica, Blajburška, Kranjčevićeva, Fra Andrije Marića, Domobranska, Kovačićeva, "
        "Stankovačka, Bunjevački put, Lišanska, Hercegovački put"),
    "2.-UTO-2026.jpg": (
        "4e018aef0f0b50eb75dff8fbf546af32a096fe544c1126743e2694b1511ab68b",
        "-01-06 +01-07",
        "01-15 02-12 03-12 04-16 05-14 06-11 07-09 08-13 09-10 10-08 11-05 12-10",
        "01-16 02-13 03-13 04-10 05-15 06-12 07-10 08-14 09-11 10-16 11-13 12-11",
        "Golubić novo naselje, Bubonje, Joška Čačića Bega, Dobrilina, Strmica, Tavelićeva, Badurinina, "
        "Domovinskog rata, Stošićeva, Tuđmanova, Grabovčeva, Drniška, Zvonimirova, Cesarića obala, 7. gardijska, "
        "Pavlinovićeva, Franjevački trg, Zadarska, Trg Ante Starčevića, Katićev prolaz, Zagrebačka, Višeslavova, "
        "Dionisija Novakovića, Dmitrovići, Zelenbabe, Zupbčići, Svačićeva, Martići, Jelačićeva, Gunjačine skale, "
        "Rašule, Nonkovići, Miljevići, Bajići"),
    "3.-SRI-2026.jpg": (
        "a4e1c305afeb6d1b2e50de50f49f6a6dbb618d48fe2c0934bbd4b33bc1383a63",
        "-08-05 -11-18 +08-06 +11-19",
        "01-22 02-19 03-19 04-23 05-21 06-18 07-16 08-20 09-17 10-15 11-12 12-17",
        "01-23 02-20 03-20 04-17 05-22 06-19 07-17 08-21 09-18 10-23 11-20 12-18",
        "Kupreška, Jovićeva, Arambašićeva, Poljička, Ikičina, Put Krčića, Sisačka, Ninska, Prominski put, "
        "Biogradska, Imotska, Sinjska cesta, Solinska, Dinarska, Splitska, Oćestovo, Vrančićeva, Vitezovićeva, "
        "4. gardijska, Boškovićeva, Krambergerova, Trpimirova, Ružičkina, Tvrtkova, Visovačka (Ljubač), Kašićeva, "
        "72. bojne Vojne policije, Mohorovićeva, Gospina, Uskočka, Rendićeva, Montijeva, Lornjina, Vlahe Bukovca, "
        "Teslina, Novakova, Hektorovića put, Katićeva, Fra Grge Martića, Stari put, Defilipsova, Vrlička, "
        "P. Jos. Karimovića grabe, Šibenska (Konj), Vrgoračka, Sv. Ante - Potkonje"),
    "4.-CET-2026-scaled.jpg": (
        "92ef5333ea862f04f168099e573bdc5aac41002bbe06848e30a39873753b048a",
        "-01-01 -06-04 +01-02 +06-05",
        "01-29 02-26 03-26 04-30 05-28 06-25 07-23 08-27 09-24 10-22 11-26 12-31",
        "01-30 02-27 03-27 04-24 05-29 06-26 07-24 08-28 09-25 10-30 11-27 12-24",
        "Trg Oluje, Zlatovićeva, Bornina, Resičina, Marka Čačića, Milanovićeva, Kninske bojne, 142. brigada, "
        "Vugdelijin put, Begovićeva, Slavka Krvavice, Gospodnetićev put, Jerkovićev put, Kuduzova, Carevjev put, "
        "Poljakov put, Fra Mije Kotaraša, Fra Petra Kneževića"),
    "5.-PET-2026-scaled.jpg": (
        "193809b1ef507e22c49f96bfb780d83aff4d65d278f300ed1beff65f31d330bf",
        "-05-01 -12-25 +04-30 +12-24 +12-31",
        "01-29 02-26 03-26 04-30 05-28 06-25 07-23 08-27 09-24 10-22 11-26 12-31",
        "01-30 02-27 03-27 04-24 05-29 06-26 07-24 08-28 09-25 10-30 11-27 12-24",
        "Hrvatski vitezovi, Đerekova, Ivana Brlić Mažuranić, Lašvanski put, Frane Šimunovića, Paradžikova, "
        "Ante Anića, Kljakovićeva, Bušićeva, Galovićeva, Hatzova, Vojnovićeva, Šenoina, Zajčeva, Kapitul, "
        "Šimićeva, Šeperova, Raškajeva, Marovićeva, Stepinčeva, Gojsaličina, Mihanovićeva, Viteza Jovana Sinobada, "
        "Matoševa, Lisinskog, Berislavićeva, Držićeva, Tijardovićeva"),
    "6.-SUB-2026-scaled.jpg": (
        "bb001c7d7354193aa3f6d3d2979d014f94c49d0544214385fb21b6a7a27b89ce",
        "01-03 01-17 01-31 02-14 02-28 03-14 03-28 04-11 04-25 05-09 05-23 06-06 06-20 07-04 07-18 08-01 08-22 "
        "09-05 09-19 10-03 10-17 10-31 11-14 11-28 12-05 12-19",
        "01-13 02-10 03-10 04-14 05-12 06-09 07-14 08-11 09-08 10-13 11-10 12-08",
        "01-20 02-17 03-17 04-21 05-19 06-16 07-21 08-18 09-22 10-20 11-17 12-22",
        "Stari Golubić"),
}
# fixed grid of the 1241 px wide page (the -scaled images are the same page at 1810 px): centre of the
# Monday cell in the first week row of each month column and row, column and row pitch
GRID_X, GRID_Y, PX, PY = (82, 355.5, 623, 890.5), (654.7, 869.3, 1082.3), 33.3, 24.5
PROVIDER = {
    "davatelj": "Čistoća i zelenilo d.o.o. Knin",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Knin"],
    "nazivi": {"P": "Plastika"},
    "napomene": [
        "Spremnici za pražnjenje moraju biti na javnoj površini do 6:00 sati na dan pražnjenja.",
        "Ako spremnik nije ispražnjen do 13:00 sati na dan pražnjenja, nazovite 022/668-163.",
        "Pomaci zbog blagdana ucrtani su u kalendare (označeni kao pomaknuti).",
    ],
    "bioNapomena": "Raspored Čistoće i zelenila ne navodi odvoz biootpada.",
}


def pixel_class(p):
    r, g, b = p
    if min(p) > 225:
        return "."
    if g > 200 and 90 < r < 200 and b < 150:
        return "M"
    if r > 200 and g > 200 and b < 200:
        return "P"
    if g > 200 and b > 200 and r < 170:
        return "K"
    return "?"


def read_image(path, year):
    """({date: codes}, problems): colour of the left and right part of every day cell."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    s = a.shape[1] / 1241
    found, problems = {}, []
    for m in range(1, 13):
        monday = date(year, m, 1) - timedelta(days=date(year, m, 1).weekday())
        for k in range(6):
            for col in range(7):
                d = monday + timedelta(days=7 * k + col)
                cx, cy = (GRID_X[(m - 1) % 4] + col * PX) * s, (GRID_Y[(m - 1) // 4] + k * PY) * s
                halves = []
                for x0, x1 in ((-0.46, -0.15), (0.15, 0.46)):
                    reg = a[int(cy - 0.4 * PY * s):int(cy + 0.4 * PY * s),
                            int(cx + x0 * PX * s):int(cx + x1 * PX * s)].reshape(-1, 3)
                    votes = Counter(pixel_class(tuple(p)) for p in reg)
                    votes.pop("?", None)  # digits
                    top = votes.most_common(1)[0] if votes else ("?", 0)
                    halves.append(top[0] if top[1] > 0.5 * len(reg) else "?")
                codes = "".join(sorted(set(halves) - {"."}))
                if "?" in codes:
                    problems.append(f"slika: {d:%d.%m.} nepoznata boja ćelije")
                elif d.month != m and codes:
                    problems.append(f"slika: {d:%d.%m.} obojen u mjesecu {m}")
                elif d.month == m and codes:
                    found[d] = codes
    return found, problems


def transcribed(year, wd, mixed, plastic, paper):
    """{date: codes} from a ROUTES entry, and the set of moved dates."""
    def dates(text):
        return [date(year, *map(int, x.lstrip("+-").split("-"))) for x in text.split()]

    out, moved = {}, set()
    if wd == 5:
        for d in dates(mixed):
            out[d] = "M"
    else:
        drop = set(dates(" ".join(x for x in mixed.split() if x.startswith("-"))))
        add = dates(" ".join(x for x in mixed.split() if x.startswith("+")))
        for d in pravila.tjedno(year, list(pravila.DANI)[wd]):
            if d not in drop:
                out[d] = "M"
        for d in add:
            out[d] = "M"
            moved.add(d)
    for code, text in (("P", plastic), ("K", paper)):
        for d in dates(text):
            out[d] = "".join(sorted(out.get(d, "") + code))
    return out, moved


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = fetch(PAGE).decode("utf-8", "replace")
    links = sorted(set(re.findall(rf'https?://[^"\s]+/\d\.-(?:PON|UTO|SRI|CET|PET|SUB)-{year}(?:-scaled)?\.jpg',
                                  page)))
    if len(links) != 6:
        sys.exit(f"Na {PAGE} nije nađeno 6 kalendara za {year}: {links}")
    problems = []
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "zone": {}}
    with tempfile.TemporaryDirectory() as tmp:
        for url in links:
            name = url.rsplit("/", 1)[1]
            wd = KEYS[re.search(r"-(PON|UTO|SRI|CET|PET|SUB)-", name).group(1)]
            img = Path(tmp) / name
            fetch(url, img)
            sha = hashlib.sha256(img.read_bytes()).hexdigest()
            if name not in ROUTES or ROUTES[name][0] != sha:
                problems.append(f"slika se promijenila ili nije provjerena: {name} (sha256 {sha}); prepišite "
                                "ulice i datume u ROUTES")
                continue
            _, mixed, plastic, paper, streets = ROUTES[name]
            want, moved = transcribed(year, wd, mixed, plastic, paper)
            got, img_problems = read_image(img, year)
            problems += [f"{name}: {p}" for p in img_problems]
            for d in sorted(set(want) | set(got)):
                if want.get(d, "") != got.get(d, ""):
                    problems.append(f"{name}: {d:%d.%m.} prepisano {want.get(d) or '-'}, na slici {got.get(d) or '-'}")
            # checks: moved mixed waste near a holiday; plastic and paper on their usual day, or moved
            for d in moved:
                if not any(h for h in hol if 0 < abs((h - d).days) <= 6 and h.weekday() == wd):
                    problems.append(f"{name}: pomak na {d} bez blagdana na {DAN[wd]}")
            rows = []
            for code in "PK":
                ds = sorted(d for d, c in want.items() if code in c)
                usual = Counter(d.weekday() for d in ds).most_common(1)[0][0]
                for d in ds:
                    shift = d.weekday() != usual
                    if shift and not any(h.weekday() == usual and abs((h - d).days) <= 6 for h in hol):
                        problems.append(f"{name}: {code} {d} nije {DAN[usual]}, a blagdana nema")
                    if shift:
                        moved.add(d)
                if len(ds) != 12 or any(not 21 <= (b - a).days <= 49 for a, b in zip(ds, ds[1:])):
                    problems.append(f"{name}: {code} nije otprilike jednom mjesečno: {ds}")
            n = Counter(c for c in "".join(want.values()))
            if not (52 <= n["M"] <= 53 if wd < 5 else 24 <= n["M"] <= 27):
                problems.append(f"{name}: miješani {n['M']} puta")
            for d, codes in want.items():
                if d in hol or d.weekday() == 6:
                    problems.append(f"{name}: odvoz na blagdan ili nedjelju {d}")
                if "M" in codes and d not in moved and wd < 5 and d.weekday() != wd:
                    problems.append(f"{name}: {d} nije {DAN[wd]}")
                rows.append((d, codes, d in moved))
            names = [s.strip() for s in streets.split(",")]
            label = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
            day_text = "subotom otprilike svaka dva tjedna" if wd == 5 else f"{DAN[wd]} svaki tjedan"
            key = str(len(data["zone"]) + 1)
            prev = old["zone"].get(key, {})
            data["zone"][key] = {
                "jls": "Knin",
                "podrucje": f"{DAN[wd].capitalize()} – {label}",
                "ulice": names,
                "napomena": f"Miješani otpad {day_text}; plastika i papir jednom mjesečno prema kalendaru.",
                "raw": {**{k: v for k, v in prev.get("raw", {}).items() if k != str(year)},
                        str(year): podaci.month_lines(rows)},
            }
            print(f"{name}: {len(names)} ulica, {dict(n)}, pomaknuto: "
                  f"{', '.join(f'{d:%d.%m.}' for d in sorted(moved)) or '-'}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
