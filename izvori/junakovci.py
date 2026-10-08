"""Semeljci: Junakovci d.o.o. Semeljci (junakovci.hr), one calendar per settlement (Semeljci, Koritna, Kešinci,
Vrbica, Mrzović).

    python3 -m izvori.junakovci [--year 2026]

The page "Kalendar odvoza otpada" shows one JPG per settlement (<year>._kalendar-<Naselje>[-n].jpg, an Excel
calendar with weeks starting on Sunday): green = mixed waste (green bin), brown = biowaste, blue = paper,
yellow = plastic, red = holiday. One kind is collected per week: green in the week of the month's 1st Tuesday,
brown in the 2nd, blue in the 3rd, yellow in the 4th, each settlement on its own weekdays; from May to
September biowaste has extra rounds. The dates are kept in this script (CALENDARS, read from the cell colours
and checked by eye) with each image's sha256; every run downloads the images, samples every day cell on the
fixed grid and stops if a cell disagrees. Holiday shifts are built into the calendars (red holiday, the
replacement day coloured) and marked as moved; the one shift without a holiday (biowaste in September) is
listed per settlement.
"""
import argparse
import calendar
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

SLUG = "junakovci"
SITE = "https://junakovci.hr"
PAGE = SITE + "/kalendar-odvoza-otpada/"
WEEK = {"M": 1, "B": 2, "K": 3, "P": 4}  # n-th week of the month (the week of the n-th Tuesday)
# settlement: image name in the URL, weekday per kind (0 = Monday)
SETTLEMENTS = {
    "Semeljci": ("Semeljci", {"M": 1, "B": 1, "K": 1, "P": 1}),
    "Koritna": ("Koritna", {"M": 3, "B": 1, "K": 3, "P": 3}),
    "Kešinci": ("Kesinci", {"M": 2, "B": 2, "K": 2, "P": 2}),
    "Vrbica": ("Vrbica", {"M": 4, "B": 2, "K": 2, "P": 2}),
    "Mrzović": ("Mrzovic", {"M": 4, "B": 2, "K": 3, "P": 3}),
}
# year -> settlement -> (image file, sha256, dates by month as day + code ("!" = moved because of a holiday),
# extra biowaste rounds, shifts without a holiday {new: regular})
CALENDARS = {2026: {
    "Semeljci": ("2026._kalendar-Semeljci.jpg", "7085f933801e53fbdc8e4631be65e3d55078a78ef4ba5c6d872b1ffc040a90b4",
                 ["9M! 13B 20K 27P", "3M 10B 17K 24P", "3M 10B 17K 24P", "7M 14B 21K 28P", "5M 12B 19K 25B 26P",
                  "2M 9B 16K 23P 30B", "7M 14B 21K 27B 28P", "4M 11B 18K 24B 25P", "1M 9B 15K 22P 29B",
                  "6M 13B 20K 27P", "3M 10B 17K 24P", "1M 8B 15K 22P"],
                 "05-25 06-30 07-27 08-24 09-29", {"09-09": "09-08"}),
    "Koritna": ("2026._kalendar-Koritna.jpg", "734adc29a20850b47f0f5631934c2ebaae037b9151ee180286bee556a16414b3",
                ["8M 13B 22K 29P", "5M 10B 19K 26P", "5M 10B 19K 26P", "9M 14B 23K 30P", "7M 12B 21K 25B 28P",
                 "5M! 9B 18K 25P 30B", "9M 14B 23K 27B 30P", "6M 11B 20K 24B 27P", "3M 9B 17K 24P 29B",
                 "8M 13B 22K 29P", "5M 10B 19K 26P", "3M 8B 17K 24P"],
                "05-25 06-30 07-27 08-24 09-29", {"09-09": "09-08"}),
    "Kešinci": ("2026._kalendar-Kesinci-n.jpg", "c907ddcdb8b8b7c5f617808fdd6f2a4da18915c42de8680330571f663cd9152c",
                ["7M 14B 21K 28P", "4M 11B 18K 25P", "4M 11B 18K 25P", "8M 15B 22K 29P", "6M 13B 20K 27P 29B",
                 "3M 10B 17K 24P 26B", "8M 15B 22K 29P 31B", "7M! 12B 19K 26P 28B", "2M 8B 16K 23P 25B",
                 "7M 14B 21K 28P", "4M 11B 20K! 25P", "2M 9B 16K 23P"],
                "05-29 06-26 07-31 08-28 09-25", {"09-08": "09-09"}),
    "Vrbica": ("2026._kalendar-Vrbica-n.jpg", "ce9f78416288b8799cc1a2f548310fe39c88dd84b85a4c7fb13cdcd57a894cbe",
               ["", "", "", "", "", "5M 10B 17K 24P 26B", "10M 15B 22K 29P 31B", "7M 12B 19K 26P 28B",
                "4M 8B 16K 23P 25B", "9M 14B 21K 28P", "6M 11B 20K! 25P", "4M 9B 16K 23P"],
               "06-26 07-31 08-28 09-25", {"09-08": "09-09"}),
    "Mrzović": ("2026._kalendar-Mrzovic-n.jpg", "16fa61ed1bb238027421ad25ed1d89273d2890565e468200e32347efb7e06e16",
                ["", "", "", "", "", "5M 10B 18K 25P 26B", "10M 15B 23K 30P 31B", "7M 12B 20K 27P 28B",
                 "4M 8B 17K 24P 25B", "9M 14B 22K 29P", "6M 11B 19K 26P", "4M 9B 17K 24P"],
                "06-26 07-31 08-28 09-25", {"09-08": "09-09"}),
}}
# fixed grid of the 818x999 images: centre x of the Sunday column of each month column, centre y of the first
# week row of each month row; column and row pitch (px)
GRID_X, GRID_Y, PX, PY = (28.5, 297.5, 566.5), (308, 493, 680, 867), 37, 22
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
ACC = ["ponedjeljak", "utorak", "srijedu", "četvrtak", "petak", "subotu", "nedjelju"]
INS = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
BIN = {"M": "zelena", "B": "smeđa", "K": "plava", "P": "žuta"}
PROVIDER = {
    "davatelj": "Junakovci d.o.o. Semeljci",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Osječko-baranjska",
    "jls": ["Semeljci"],
    "nazivi": {"M": "Miješani komunalni otpad (zelena kanta)", "P": "Plastika (žuta kanta)",
               "K": "Papir (plava kanta)"},
    "bioNapomena": "Biootpad (smeđa kanta) odvozi se jednom mjesečno, a od svibnja do rujna i dodatno.",
    "napomene": [
        "Svaki se tjedan odvozi jedna vrsta otpada: zelena kanta u tjednu prvog utorka u mjesecu, smeđa u drugom, "
        "plava u trećem, žuta u četvrtom tjednu; dan u tjednu ovisi o naselju.",
        "Blagdani su u kalendaru označeni crveno, a zamjenski dan odvoza obojen; takvi su datumi označeni kao "
        "premješteni.",
        "Uz svaki odvoz mogu se priložiti posebne vrste otpada u zasebnoj vrećici (pelene, konzerve, staklo, odjeća, "
        "obuća, manji kućanski aparati, žarulje).",
        "Krupni otpad odvozi se po pozivu (091/738-2013). Reciklažno dvorište i odlagalište „Ada” Koritna: "
        "ponedjeljak–petak od 9 do 17 sati.",
        "Junakovci d.o.o.: 031/856-311, junakovci@semeljci.hr.",
    ],
}


def colour_classes(a):
    """Per pixel: R red, P yellow, M green, K blue, B brown (both shades used in the images), '.' other."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    out = np.full(r.shape, ".", dtype="<U1")
    out[(r > 200) & (g < 70) & (b < 70)] = "R"
    out[(r > 210) & (g > 210) & (b < 90)] = "P"
    out[(r < 60) & (g > 140) & (g < 210) & (b > 50) & (b < 130)] = "M"
    out[(r < 60) & (g > 90) & (b > 170) & (b > g + 20)] = "K"
    out[(r > 120) & (r < 220) & (g > 40) & (g < 115) & (b < 100) & (r > g + 50)] = "B"
    return out


def read_cells(path, year):
    """({date: code or "R" or ""}, problems) for every day cell of one calendar image."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    if a.shape[:2] != (999, 818):
        return {}, [f"neočekivana veličina slike {a.shape[1]}x{a.shape[0]}"]
    cls = colour_classes(a)
    found, problems = {}, []
    for m in range(1, 13):
        first = (date(year, m, 1).weekday() + 1) % 7  # column of day 1, Sunday first
        for day in range(1, calendar.monthrange(year, m)[1] + 1):
            d = date(year, m, day)
            cx = round(GRID_X[(m - 1) % 3] + (d.weekday() + 1) % 7 * PX)
            cy = round(GRID_Y[(m - 1) // 3] + (first + day - 1) // 7 * PY)
            cell = cls[cy - 8:cy + 9, cx - 14:cx + 15]
            n = Counter(cell.ravel().tolist())
            n.pop(".", None)
            code, k = n.most_common(1)[0] if n else ("", 0)
            if k < 0.1 * cell.size:
                found[d] = ""
            elif k < 0.6 * cell.size or sum(n.values()) - k > 0.1 * cell.size:
                problems.append(f"{d}: nejasna boja ćelije {dict(n)}")
            else:
                found[d] = code
    return found, problems


def parse(months, year):
    rows = []
    for m, text in enumerate(months, 1):
        for tok in text.split():
            day, code, moved = re.fullmatch(r"(\d+)([MBKP])(!?)", tok).groups()
            rows.append((date(year, m, int(day)), code, moved == "!"))
    return rows


def regular_day(year, month, code, wd):
    """The settlement's day for a kind: its weekday in the week of the n-th Tuesday of the month."""
    tuesday = pravila.mjesecno(year, "uto", WEEK[code])[month - 1]
    return tuesday + timedelta(days=wd - 1)


def check(name, rows, weekdays, extra, shifts, year):
    problems = []
    hol = set(pravila.blagdani(year))
    months = sorted({d.month for d, _, _ in rows})
    if months != list(range(months[0], 13)):
        problems.append(f"{name}: mjeseci s rupom {months}")
    for m in months:
        regular = [(d, c, mv) for d, c, mv in rows if d.month == m and d not in extra]
        if sorted(c for _, c, _ in regular) != sorted(WEEK):
            problems.append(f"{name} {m}. mjesec: {[c for _, c, _ in regular]} (očekivano po jednom M, B, K, P)")
        for d, c, moved in regular:
            due = regular_day(year, m, c, weekdays[c])
            due = shifts.get(due, due)
            if moved and not (due in hol and abs((d - due).days) <= 3):
                problems.append(f"{name}: {d} označen kao premješten, a {due} nije blagdan")
            if not moved and d != due:
                problems.append(f"{name}: {c} {d} ({DAN[d.weekday()]}), očekivano {due} ({DAN[due.weekday()]})")
    for d in extra:
        code = next((c for x, c, _ in rows if x == d), None)
        if code != "B" or not 5 <= d.month <= 9 or d.weekday() > 4:
            problems.append(f"{name}: dodatni odvoz {d} nije biootpad radnim danom od svibnja do rujna")
    for d, c, _ in rows:
        if d in hol:
            problems.append(f"{name}: odvoz na blagdan {d}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    if year not in CALENDARS:
        sys.exit(f"Za {year}. nema prepisanih kalendara u skripti (CALENDARS).")
    page = fetch(PAGE).decode("utf-8", "replace")
    urls = {}
    for m in re.finditer(rf"https?://[^\"' ]+?/({year}\._kalendar-([A-Za-z]+)(?:-n)?\.jpg)", page):
        urls.setdefault(m.group(2), m.group(0))
    problems, data = [], {**PROVIDER, "zone": {}}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    hol = set(pravila.blagdani(year))
    with tempfile.TemporaryDirectory() as tmp:
        for z, (name, (key, weekdays)) in enumerate(SETTLEMENTS.items(), 1):
            fname, sha, months, extra, shifts = CALENDARS[year][name]
            url = urls.get(key)
            if not url:
                problems.append(f"{name}: na stranici nema slike kalendara za {year}")
                continue
            img = Path(tmp) / url.rsplit("/", 1)[1]
            fetch(url, img)
            got = hashlib.sha256(img.read_bytes()).hexdigest()
            if img.name != fname or got != sha:
                sys.exit(f"slika se promijenila, prepisati ponovno: {url} (sha256 {got})")
            cells, probs = read_cells(img, year)
            problems += [f"{name}: {p}" for p in probs]
            rows = parse(months, year)
            extra = {date(year, *map(int, x.split("-"))) for x in extra.split()}
            shifts = {date(year, *map(int, v.split("-"))): date(year, *map(int, k.split("-")))
                      for k, v in shifts.items()}
            mine = {d: c for d, c, _ in rows}
            for d, code in cells.items():
                if code == "R" and (d not in hol or d in mine):
                    problems.append(f"{name}: {d} crveno na slici, a nije blagdan bez odvoza")
                elif code != "R" and code != mine.get(d, ""):
                    problems.append(f"{name}: {d} u skripti {mine.get(d) or '-'}, na slici {code or '-'}")
            unmarked = [h for h in hol if h.month >= rows[0][0].month and cells.get(h) != "R"]
            if unmarked:
                print(f"{name}: blagdani koji nisu označeni crveno: {', '.join(f'{h:%d.%m.}' for h in sorted(unmarked))}")
            problems += check(name, rows, weekdays, extra, shifts, year)
            days = sorted({weekdays[c] for c in "MBKP"})
            by_day = {}
            for c in "MBKP":
                by_day.setdefault(weekdays[c], []).append(BIN[c])
            desc = ", ".join(f"{', '.join(bins[:-1]) + ' i ' * (len(bins) > 1) + bins[-1] if len(bins) < 4 else 'sve kante'} "
                             f"{INS[wd]}"
                             for wd, bins in by_day.items())
            zone = {"jls": "Semeljci", "podrucje": f"{name} – {desc}", "ulice": [name]}
            notes = [f"Biootpad {new.day}.{new.month}. ({DAN[new.weekday()]}) umjesto {reg.day}.{reg.month}. "
                     f"({DAN[reg.weekday()]}) prema kalendaru." for reg, new in sorted(shifts.items())]
            if rows[0][0].month > 1:
                notes.insert(0, f"Kalendar za naselje {name} (objavljen u kolovozu {year}.) prikazuje odvoz tek od "
                                f"{rows[0][0].month}. mjeseca; raniji mjeseci nisu objavljeni.")
            if notes:
                zone["napomena"] = " ".join(notes)
            prev = old.get(str(z), {}).get("raw", {}) if old.get(str(z), {}).get("ulice") == [name] else {}
            raw = {y: v for y, v in prev.items() if y != str(year)}
            keep = [r for r in podaci.iter_dates({"raw": {str(year): prev.get(str(year), [])}})
                    if r[0].month < rows[0][0].month]  # earlier months from a previous run
            raw[str(year)] = podaci.month_lines(keep + [(d, c, mv) for d, c, mv in rows])
            zone["raw"] = raw
            data["zone"][str(z)] = zone
            n = Counter(c for _, c, _ in rows)
            print(f"{name}: od {rows[0][0]:%d.%m.}, {dict(n)}, dani {', '.join(DAN[d] for d in days)}, "
                  f"premješteno: {', '.join(f'{d:%d.%m.}' for d, _, mv in rows if mv) or '-'}")
    # the page's note on five-week months ("Tuesday and Thursday must be part of a whole week in the month")
    for m in range(1, 13):
        first = date(year, m, 1)
        if first.weekday() in (1, 2, 3) and pravila.mjesecno(year, "uto", 1)[m - 1].day <= 3:
            print(f"Napomena: {m}. mjesec počinje u {ACC[first.weekday()]}, a kalendar ipak počinje odvoz u tom tjednu "
                  "(napomena na stranici o „cijelom tjednu” tu se ne primjenjuje).")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
