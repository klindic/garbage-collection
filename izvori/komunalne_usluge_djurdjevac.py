"""Đurđevac: Komunalne usluge Đurđevac d.o.o. (komunalneusluge.hr), five groups of streets and settlements.

    python3 -m izvori.komunalne_usluge_djurdjevac [--year 2026]

The page "Raspored odvoza otpada" links the year PDF (Raspored-odvoza-otpada-za-<year>.-godinu.pdf): a Word
export without a text layer, two pages per group, each page one 200 dpi JPG. The first page of a group has its
streets and January–March, the second April–December and the legend. A date cell is split diagonally:
green/brown = mixed waste and biowaste, blue/yellow = plastic and paper, grey = bulky waste (on registration).
The dates are kept in this script (GROUPS, read from the cell colours and checked by eye) together with the
PDF's sha256; every run extracts the images (pdfimages), samples every day cell on the fixed grid and stops if
any cell disagrees with GROUPS. Holiday shifts are built into the calendar (and confirmed by the company's
notices for 6.1., 6.4., 1.5., 4.6. and 5.8.): a date off the group's weekday, in a week whose collection day
is a holiday, is marked as moved.
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
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalne-usluge-djurdjevac"
SITE = "https://komunalneusluge.hr"
PAGE = SITE + "/gospodarenje-otpadom/odvoz-otpada/"
PDFS = {2026: "5965574d824d4f3d5e0dc4878ff5ee2b7824c924b5a9ed87abea58e1b505b5b3"}  # year -> sha256 of the PDF
# group: (weekday, streets as printed, dates by month: day + codes, "!" = moved because of a holiday)
GROUPS = {2026: [
    (0, "1. svibnja, Andrije Hebranga, Antuna Radića, Augusta Šenoe, Bregovita, Donji Brvci, Đure Basaričeka, "
        "Grgura Karlovčana, Istarska, Ivana Đuriševića, Kudrinka, Ljudevita Gaja, Miroslava Krleže, Radnička cesta, "
        "Starogradska, Stjepana Radića, Trg svetog Jurja, Ulica mladosti",
     ["5PK 12MB 19PK 26MB", "2PK 9MB 16PK 23MB", "2PK 9MB 16PK 17G 23MB 30PK", "7MB! 13PK 20MB 27PK",
      "4MB 11PK 18MB 25PK", "1MB 8PK 15MB 23PK! 29MB", "6PK 13MB 20PK 27MB", "3PK 10MB 17PK 24MB 31PK",
      "7MB 14PK 21MB 28PK", "5MB 12PK 19MB 26PK", "2MB 9PK 16MB 23PK 30MB", "7PK 14MB 21PK 28MB"]),
    (1, "Stiska, pojedini dijelovi prigradskih naselja",
     ["7PK! 13MB 20PK 27MB", "3PK 10MB 17PK 24MB", "3PK 10MB 17PK 18G 24MB 31PK", "7MB 14PK 21MB 28PK",
      "5MB 12PK 19MB 26PK", "2MB 9PK 16MB 23PK 30MB", "7PK 14MB 21PK 28MB", "4PK 11MB 18PK 25MB",
      "1PK 8MB 15PK 22MB 29PK", "6MB 13PK 20MB 27PK", "3MB 10PK 17MB 24PK", "1MB 8PK 15MB 22PK 29MB"]),
    (2, "Matije Gupca, Grkinska, Mare Matočec, Pešćenica, Petra Preradovića, Petra Zrinskog, Šandora Brauna, "
        "Tratinska, Budrovac, Čepelovac, Sirova Katalena, Suha Katalena",
     ["7PK 14MB 21PK 28MB", "4PK 11MB 18PK 25MB", "4PK 11MB 17G 18PK 25MB", "1PK 8MB 15PK 22MB 29PK",
      "6MB 13PK 20MB 27PK", "3MB 10PK 17MB 24PK", "1MB 8PK 15MB 22PK 29MB", "4PK! 12MB 19PK 26MB",
      "2PK 9MB 16PK 23MB 30PK", "7MB 14PK 21MB 28PK", "4MB 11PK 17MB! 25PK", "2MB 9PK 16MB 23PK 30MB"]),
    (3, "Severovačka, Ivana Gundulića, Kralja Tomislava, Međašna, Grkine, Mičetinac, Severovci, Sveta Ana",
     ["8PK 15MB 22PK 29MB", "5PK 12MB 19PK 26MB", "5PK 12MB 17G 18G 19PK 26MB", "2PK 9MB 16PK 23MB 30PK",
      "7MB 14PK 21MB 28PK", "3MB! 11PK 18MB 25PK", "2MB 9PK 16MB 23PK 30MB", "6PK 13MB 20PK 27MB",
      "3PK 10MB 17PK 24MB", "1PK 8MB 15PK 22MB 29PK", "5MB 12PK 19MB 26PK", "3MB 10PK 17MB 24PK 31MB"]),
    (4, "Antuna Gustava Matoša, Bana Josipa Jelačića, Bjelovarska, Ciglenska, Doktora Ivana Kranjčeva, Eugena "
        "Kumičića, Ferde Rusana, Grada Vukovara, Ivana Gorana Kovačića, Ivana Mažuranića, Kneza Branimira, "
        "Kolodvorska, Kralja Petra Krešimira IV, Kralja Zvonimira, Mihovila Pavleka Miškine, Pavla Radića, "
        "Ruđera Boškovića, Škurdijeva, Tina Ujevića, Trg hrvatske mladosti, Vinogradska, Vladimira Nazora",
     ["2MB 9PK 16MB 23PK 30MB", "6PK 13MB 20PK 27MB", "6PK 13MB 17G 20PK 27MB", "3PK 10MB 17PK 24MB 30PK!",
      "8MB 15PK 22MB 29PK", "5MB 12PK 19MB 26PK", "3MB 10PK 17MB 24PK 31MB", "7PK 14MB 21PK 28MB",
      "4PK 11MB 18PK 25MB", "2PK 9MB 16PK 23MB 30PK", "6MB 13PK 20MB 27PK", "4MB 11PK 18MB 23PK! 31MB!"]),
]}
# fixed grid of the 1214x1699 page images: centre x of the Monday column of each month block (by block row)
# and centre y of the first week row; column pitch and row pitch for months with 5 and 6 week rows (px)
GRID = {"q1": ([(112, 472, 831)], [1311.5]),
        "q2": ([(107.5, 466.5, 825.5), (112.5, 472, 830.5), (107.5, 466.5, 825.5)], [239.5, 622.7, 1006])}
PX, PY5, PY6 = 45.8, 46.8, 37.5
FAMILY = {"MB": "GR", "PK": "BY", "G": "S"}  # cell code -> colour classes (green, brown, blue, yellow, grey)
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Komunalne usluge Đurđevac d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Koprivničko-križevačka",
    "jls": ["Đurđevac"],
    "nazivi": {"P": "Plastika", "K": "Papir"},
    "bioNapomena": "Biootpad se odvozi isti dan kad i miješani komunalni otpad.",
    "napomene": [
        "Miješani komunalni otpad i biootpad odvoze se zajedno svaki drugi tjedan, a plastika i papir zajedno u "
        "tjednima između, na dan skupine ulica iz godišnjeg kalendara. Posude treba iznijeti večer prije odvoza.",
        "Raspored vrijedi samo za korisnike s vlastitim posudama za otpad.",
        "Pomaci zbog blagdana ugrađeni su u kalendar (označeni kao premješteni); obavijesti o promjenama "
        "objavljuju se na komunalneusluge.hr.",
        "Glomazni otpad odvozi se prema prijavi (048/625-138, gospodarenje-otpadom@komunalneusluge.hr) na dane "
        "označene sivo u kalendaru; besplatno do 2 m³.",
        "Staklo se odlaže u zelene spremnike na javnim lokacijama (popis je u PDF-u rasporeda).",
        "Od 1. lipnja do 30. rujna otpad se odvozi od 5 sati ujutro (obavijest za 2026.).",
        "Komunalne usluge Đurđevac: 048/625 138, info@komunalneusluge.hr.",
    ],
}


def colour_classes(a):
    """Per pixel: G green, R brown, B blue, Y yellow, S grey, '.' anything else (background, digits)."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    out = np.full(r.shape, ".", dtype="<U1")
    out[(r > 190) & (g > 170) & (b < 110)] = "Y"
    out[(b > 150) & (r < 120) & (g > 100)] = "B"
    out[(g > 100) & (g > r + 40) & (g > b + 50)] = "G"
    out[(r > 80) & (r < 170) & (g < 110) & (b < 80) & (r > g + 25) & (r > b + 50)] = "R"
    out[(a.max(-1) - a.min(-1) < 16) & (a.min(-1) > 140) & (a.max(-1) < 205)] = "S"
    return out


def read_cells(path, months, grid, year):
    """({date: "MB" | "PK" | "G" | ""}, problems) for every day cell of the months on one page image."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    if a.shape[:2] != (1699, 1214):
        return {}, [f"{path.name}: neočekivana veličina {a.shape[1]}x{a.shape[0]}"]
    cls = colour_classes(a)
    xs, ys = GRID[grid]
    found, problems = {}, []
    for i, m in enumerate(months):
        x0, y0 = xs[i // 3][i % 3], ys[i // 3]
        first, ndays = date(year, m, 1), calendar.monthrange(year, m)[1]
        py = PY5 if (first.weekday() + ndays + 6) // 7 == 5 else PY6
        for day in range(1, ndays + 1):
            d = date(year, m, day)
            cx, cy = round(x0 + d.weekday() * PX), round(y0 + (first.weekday() + day - 1) // 7 * py)
            cell = cls[cy - 12:cy + 13, cx - 18:cx + 19]
            n = Counter(cell.ravel().tolist())
            share = {k: n[k] / cell.size for k in "GRBYS"}
            total = sum(share.values())
            if total < 0.12:
                found[d] = ""
                continue
            code = max(FAMILY, key=lambda c: sum(share[k] for k in FAMILY[c]))
            if total - sum(share[k] for k in FAMILY[code]) > 0.15 or \
                    code != "G" and min(share[k] for k in FAMILY[code]) < 0.02:
                problems.append(f"{d}: nejasna boja ćelije {({k: round(v, 2) for k, v in share.items() if v})}")
            found[d] = code
    return found, problems


def parse(months, year):
    """[(date, codes, moved)] from the month strings of GROUPS."""
    rows = []
    for m, text in enumerate(months, 1):
        for tok in text.split():
            day, codes = re.fullmatch(r"(\d+)([A-Z]+!?)", tok).groups()
            rows.append((date(year, m, int(day)), codes.rstrip("!"), codes.endswith("!")))
    return rows


def check_group(n, wd, rows, year):
    """Rule problems of one group: weekday, holiday moves and the alternating MB / PK weeks."""
    problems = []
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))
    regular = []
    for d, codes, moved in rows:
        if codes == "G":
            if d.weekday() > 4 or moved:
                problems.append(f"skupina {n}: glomazni {d} nije radni dan")
            continue
        due = d - timedelta(days=d.weekday() - wd)  # the group's day in that week
        if moved != (d != due):
            problems.append(f"skupina {n}: {d} ({DAN[d.weekday()]}) {'je' if moved else 'nije'} označen kao "
                            f"premješten, a dan skupine je {DAN[wd]}")
        if moved and due not in hol:
            problems.append(f"skupina {n}: {d} premješten, a {due} nije blagdan")
        if not moved and d in hol:
            problems.append(f"skupina {n}: odvoz na blagdan {d}")
        regular.append((due, codes))
    regular.sort()
    for (a, ca), (b, cb) in zip(regular, regular[1:]):
        if (b - a).days != 7 or ca == cb:
            problems.append(f"skupina {n}: {a} {ca} pa {b} {cb} (očekivano izmjenično svaki tjedan)")
    per_month = Counter((d.month, c) for d, c, _ in rows if c != "G")
    for (m, c), k in per_month.items():
        if not 1 <= k <= 3:
            problems.append(f"skupina {n}: {k}× {c} u mjesecu {m}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    if year not in GROUPS:
        sys.exit(f"Za {year}. nema prepisanog rasporeda u skripti (GROUPS).")
    page = fetch(PAGE).decode("utf-8", "replace")
    links = [u for u in re.findall(r'href="([^"]+\.pdf)"', page) if "aspored" in u and str(year) in u]
    if len(links) != 1:
        sys.exit(f"Na {PAGE} nije pronađen jedan PDF rasporeda za {year}: {links}")
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "raspored.pdf"
        fetch(links[0].replace("http://", "https://"), pdf)
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if sha != PDFS[year]:
            sys.exit(f"slika se promijenila, prepisati ponovno: {links[0]} (sha256 {sha})")
        subprocess.run(["pdfimages", "-j", str(pdf), str(Path(tmp) / "img")], check=True)
        images = sorted(Path(tmp).glob("img-*.jpg"))
        if len(images) != 2 * len(GROUPS[year]):
            sys.exit(f"PDF ima {len(images)} slika, očekivano {2 * len(GROUPS[year])}.")
        cells = []
        for g in range(len(GROUPS[year])):
            got = {}
            for path, months, grid in ((images[2 * g], [1, 2, 3], "q1"), (images[2 * g + 1], range(4, 13), "q2")):
                found, probs = read_cells(path, months, grid, year)
                got.update(found)
                problems += [f"skupina {g + 1}: {p}" for p in probs]
            cells.append(got)
    data = {**PROVIDER, "zone": {}}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    for g, (wd, streets, months) in enumerate(GROUPS[year]):
        n = g + 1
        rows = parse(months, year)
        mine = {d: codes for d, codes, _ in rows}
        for d, code in cells[g].items():
            if mine.get(d, "") != code:
                problems.append(f"skupina {n}: {d} u skripti {mine.get(d) or '-'}, na slici {code or '-'}")
        problems += check_group(n, wd, rows, year)
        names = [s.strip() for s in streets.split(",")]
        ulice = [s for s in names if not s.startswith("pojedini")]
        zone = {
            "jls": "Đurđevac",
            "podrucje": f"{DAN[wd].capitalize()} – " + ", ".join(names[:4]) + (" …" if len(names) > 4 else ""),
            "opis": streets,
            "ulice": ulice,
        }
        if len(ulice) < len(names):
            zone["napomena"] = "Raspored vrijedi i za pojedine dijelove prigradskih naselja (u PDF-u nisu navedeni)."
        prev = old.get(str(n), {}).get("raw", {})
        zone["raw"] = {**{y: v for y, v in prev.items() if y != str(year)}, str(year): podaci.month_lines(rows)}
        data["zone"][str(n)] = zone
        cnt = Counter(c for _, codes, _ in rows for c in codes)
        moved = [f"{d:%d.%m.}" for d, _, mv in rows if mv]
        print(f"Skupina {n} ({DAN[wd]}): {len(ulice)} ulica/naselja, {dict(cnt)}, premješteno: {', '.join(moved) or '-'}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
