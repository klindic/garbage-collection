"""Komunalno Basilija d.o.o.: Šolta, five groups of settlements (one weekday each).

    python3 -m izvori.komunalno_basilija [--year 2026]

The page "Odvoz komunalnog otpada" links the year PDF "Raspored pražnjenja spremnika i prikupljanja
reciklabilnog otpada". Page 1 is a table weekday -> settlements for mixed waste (once a week, all year)
followed by the holiday exceptions ("pražnjenje za mjesta …, umjesto u četvrtak biti će u petak
02.01.2026.g."), which are applied as moved dates; the PDF also says that holidays from 1.6. to 31.8. are
normal collection days. Page 2 has two tables (January-June, July-December) with the dates of the
recyclables bags (paper, plastic and glass are collected together) per group, read with pdfplumber.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalno-basilija"
SITE = "https://www.komunalno-basilija.hr"
PAGE = SITE + "/odvoz-komunalnog-otpada/"
DANI = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK", "SUBOTA", "NEDJELJA"]
DANI_U = {"ponedjeljak": 0, "utorak": 1, "srijedu": 2, "srijeda": 2, "četvrtak": 3, "petak": 4, "subotu": 5}
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
KEY = ["pon", "uto", "sri", "čet", "pet"]
RECIKLABILNI = "PKS"
PROVIDER = {
    "davatelj": "Komunalno Basilija d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Šolta"],
    "nazivi": {"P": "Plastika (žuta vrećica)", "K": "Papir (plava vrećica)", "S": "Staklo (smeđa/siva vrećica)"},
    "napomene": [
        "Spremnik za miješani otpad postavlja se do 6 sati na dogovorenu lokaciju; vrećice s reciklabilnim "
        "otpadom (papir, plastika, staklo) odlažu se pored spremnika istog jutra prema rasporedu.",
        "Reciklabilni otpad odvozi se jednom mjesečno od 1. listopada do 1. lipnja, a dva puta mjesečno ljeti.",
        "Pomaci zbog blagdana preuzeti su iz rasporeda; od 1.6. do 31.8. odvoz je i na praznike.",
        "Od 1.6. do 30.9. mogu se ugovoriti dodatni odvozi miješanog otpada: 021/654-331, info@komunalno-basilija.hr.",
        "Glomazni otpad i biootpad: na poziv 021/718-888.",
        "Deponij Borovik: ponedjeljak – subota 8 – 14 sati.",
    ],
}


def naslov(name):
    return " ".join(w.capitalize() for w in name.split())


def find_pdf(html, year):
    links = re.findall(rf'href="(https?://[^"]*RASPORED[^"]*{year}[^"]*\.pdf)"', html, re.I)
    if not links:
        sys.exit(f"Na {PAGE} nema rasporeda za {year}.")
    return links[-1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    url = find_pdf(fetch(PAGE).decode("utf-8", "replace"), year)
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raspored.pdf"
        fetch(url, path)
        with pdfplumber.open(path) as pdf:
            text = " ".join(" ".join(p.extract_text() or "" for p in pdf.pages).split())
            tabs = [t for p in pdf.pages for t in p.extract_tables()]
    if f"U {year}. GODINI" not in text:
        sys.exit(f"{url} nije raspored za {year}.")

    # mixed waste: weekday -> settlements
    groups = {}  # weekday -> [settlements]
    for row in tabs[0]:
        cells = [c for c in row if c]
        if len(cells) == 2 and cells[0].strip() in DANI:
            groups[DANI.index(cells[0].strip())] = [naslov(n) for n in cells[1].split(",") if n.strip()]
    if sorted(groups) != [0, 1, 2, 3, 4]:
        sys.exit(f"Tablica miješanog otpada: dani {sorted(groups)}")

    # holiday exceptions
    moves = {}  # original date -> new date
    for hol, places, old_day, new_day, new in re.findall(
            r"(\d\d\.\d\d\.\d{4})\.g\. ?[–-] [^I]*?Iznimno, zbog \w+, pražnjenje za mjesta (.+?), umjesto u (\w+),? "
            r"biti će u (\w+) (\d\d\.\d\d\.\d{4})\.g\.", text):
        h, n = (date(*reversed([int(x) for x in s.split(".")])) for s in (hol, new))
        if h.weekday() != DANI_U[old_day] or n.weekday() != DANI_U[new_day]:
            problems.append(f"iznimka {hol}: dani se ne slažu ({old_day} -> {new_day} {new})")
        elif not places.startswith(groups[h.weekday()][0][:5]):
            problems.append(f"iznimka {hol}: mjesta {places!r} nisu skupina za {DANI[h.weekday()].lower()}")
        moves[h] = n
    summer = re.search(r"Na praznike koji spadaju od (\d\d)\.(\d\d)\.-(\d\d)\.(\d\d)\.\d{4}\.g\. odvoz smeća će se "
                       r"vršiti po redovnom rasporedu", text)
    if not moves or not summer:
        problems.append("nema iznimki zbog blagdana ili napomene o praznicima ljeti")
    else:
        summer = (date(year, int(summer.group(2)), int(summer.group(1))),
                  date(year, int(summer.group(4)), int(summer.group(3))))
        for h in pravila.blagdani(year):
            if h.weekday() < 5 and not summer[0] <= h <= summer[1] and h not in moves:
                print(f"PAŽNJA: za blagdan {h:%d.%m.} nema iznimke, odvoz ostaje po redovnom rasporedu")

    # recyclables: two half-year tables DAN | MJESTO | 6 months
    rec = {d: [] for d in groups}
    halves = 0
    for t in tabs:
        head = next((r for r in t if r and r[0] == "DAN" and r[1] == "MJESTO"), None)
        if not head:
            continue
        months = [MONTHS.index(h.strip().lower()) + 1 for h in head[2:]]
        halves += 1
        for row in t:
            day = (row[0] or "").strip()
            if day not in DANI:
                continue
            wd = DANI.index(day)
            if [naslov(n) for n in row[1].split("\n")] != groups.get(wd):
                problems.append(f"reciklabilni {day}: mjesta {row[1]!r} ne odgovaraju miješanom otpadu")
            for m, cell in zip(months, row[2:]):
                got = [(int(d), int(mm)) for d, mm in re.findall(r"(\d\d)\.(\d\d)\.?", cell or "")]
                want = 2 if 6 <= m <= 9 else 1
                if len(got) != want:
                    problems.append(f"reciklabilni {day} {m}. mjesec: {len(got)} datuma, očekivano {want}")
                for d, mm in got:
                    if mm != m:
                        problems.append(f"reciklabilni {day}: {d}.{mm}. u stupcu {m}. mjeseca")
                        continue
                    rec[wd].append(date(year, m, d))
    if halves != 2:
        problems.append(f"{halves} tablica reciklabilnog otpada, očekivane 2")

    data = {**PROVIDER, "zone": {}}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    for z, wd in enumerate(sorted(groups), 1):
        dates = {moves.get(d, d): ["M", d in moves] for d in pravila.tjedno(year, KEY[wd])}
        if len(rec[wd]) != len(set(rec[wd])):
            problems.append(f"{DANI[wd]}: reciklabilni datum dvaput")
        for d in rec[wd]:
            if d.weekday() != wd:
                problems.append(f"{DANI[wd]}: reciklabilni {d:%d.%m.} je drugi dan u tjednu")
            entry = dates.setdefault(d, ["", False])
            entry[0] += RECIKLABILNI
        for d, (codes, moved) in dates.items():
            if d.weekday() != wd and not moved:
                problems.append(f"{DANI[wd]}: {d:%d.%m.} nije {DANI[wd].lower()}")
        per_month = Counter(d.month for d, (c, _) in dates.items() if "M" in c)
        if sorted(set(per_month.values())) not in ([4, 5], [4], [5]) or len(per_month) != 12:
            problems.append(f"{DANI[wd]}: miješani po mjesecima {dict(per_month)}")
        rows = [(d, c, mv) for d, (c, mv) in dates.items()]
        places = groups[wd]
        data["zone"][str(z)] = {
            "jls": "Šolta",
            "podrucje": f"{DANI[wd].capitalize()} – {', '.join(places)}",
            "ulice": places,
            "raw": {**old.get(str(z), {}).get("raw", {}), str(year): podaci.month_lines(rows)},
        }
        moved = [f"{d:%d.%m.}" for d, _, mv in sorted(rows) if mv]
        print(f"Zona {z} ({DANI[wd].lower()}: {', '.join(places)}): {len(rows)} odvoza, reciklabilni "
              f"{len(rec[wd])}, pomaknuto: {' '.join(moved) or '-'}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
