"""Buzet, Lanišće: PARK d.o.o. za komunalne djelatnosti Buzet (park.hr), rules from the year's plan.

    python3 -m izvori.park_buzet [--year 2026]

The plan "PLAN ODVOZA OTPADA ZA YYYY" (found through the WordPress media API; Word tables with a text
layer) has two pages. Page 2 is the plan for recyclables in Grad Buzet: seven groups of settlements with
rules such as "prvi ponedjeljak u mjesecu" for the blue (paper) and the yellow bin (plastic, metal,
tetrapak) and "drugi i četvrti petak u mjesecu" for the brown bin (biowaste, in some groups only in the
listed settlements, or except the listed ones). Every group (split where biowaste differs) becomes a zone.
Page 1 gives the mixed-waste day for users without the metered shared containers (otpadomjer): in Buzet
per local committee (MO), which do not match the recyclables' groups and whose settlements the plan
does not list, so these days are only a note; Lanišće has mixed waste every second Thursday from the
date printed in the plan and becomes its own zone. Holidays: the plan says the collection moves to the
first following working day (or a notice is published); Monday to Friday is taken as working days.
"""
import argparse
import json
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "park-buzet"
SITE = "https://park.hr"
MEDIA = SITE + "/wp-json/wp/v2/media?search=plan%20odvoza&per_page=50&_fields=id,date,source_url"
ORD = {"PRVI": 1, "PRVA": 1, "DRUGI": 2, "DRUGA": 2, "TREĆI": 3, "TREĆA": 3, "ČETVRTI": 4, "ČETVRTA": 4}
DAYS = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "ČETVRTAK": 3, "PETAK": 4}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "PARK d.o.o. za komunalne djelatnosti Buzet",
    "web": SITE,
    "zupanija": "Istarska",
    "jls": ["Buzet", "Lanišće"],
    "nazivi": {"P": "Plastična i metalna ambalaža, tetrapak (žuti spremnik)", "K": "Papir i karton (plavi spremnik)"},
    "bioNapomena": "Biootpad (smeđi spremnik) samo u naseljima navedenima u planu.",
}
NAPOMENE = [
    "Spremnici za reciklabilni otpad prazne se jednom mjesečno; na dan odvoza moraju biti na javnoj površini do 7:00.",
    "Reciklažno dvorište Griža: ponedjeljak – petak 08:00 – 15:00, subota 08:00 – 13:00.",
    "Glomazni i zeleni otpad: akcije prema obavijestima (proljeće i jesen), planovi na park.hr.",
]


def nth(year, month, wd, n):
    days = [date(year, month, d) for d in range(1, 32) if _ok(year, month, d) and date(year, month, d).weekday() == wd]
    return days[n - 1] if len(days) >= n else None


def _ok(y, m, d):
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def next_working(d, hol):
    """The holiday rule: the first following working day (Monday to Friday, not a holiday)."""
    if d not in hol:
        return d, False
    while d in hol or d.weekday() >= 5:
        d += timedelta(days=1)
    return d, True


def rule(text):
    """'DRUGI I ČETVRTI PETAK U MJESECU' -> ([2, 4], 4); None if not understood."""
    m = re.fullmatch(r"((?:\w+)(?: I \w+)*) (\w+) U MJESECU", " ".join(text.upper().split()))
    if not m:
        return None
    ords = m.group(1).split(" I ")
    if not all(o in ORD for o in ords) or m.group(2) not in DAYS:
        return None
    return [ORD[o] for o in ords], DAYS[m.group(2)]


def names(text):
    """Settlement list -> names ('ulice: A. C. Tonića' -> 'A. C. Tonića'; 'Mali i Veli Mlun' stays)."""
    out = []
    for part in text.split(","):
        part = re.sub(r"^ulice:\s*", "", " ".join(part.split())).strip(" .")
        if part:
            out.append(part)
    return out


def read_groups(page, problems):
    """[(settlements, blue rule, yellow rule, bio rule text)] from page 2."""
    tables = page.find_tables()
    big = max(tables, key=lambda t: len(t.rows))
    rows = big.extract()
    groups = []
    for r in rows:
        cells = [" ".join((c or "").split()) for c in r]
        if cells[0] == "NASELJA" or not cells[0]:  # heading rows, or the rest of the biowaste cell
            extra = " ".join(c for c in cells[3:] if c and c != "-")
            if groups and not cells[0] and extra:
                groups[-1][3] = (groups[-1][3] + " " + extra).strip()
            continue
        bio = " ".join(c for c in cells[3:] if c and c != "-")
        groups.append([cells[0], cells[1], cells[2], bio])
    # the settlement cells are cut in the big table: take the full text from the text boxes on the left
    boxes = sorted((t for t in tables if t is not big and t.bbox[0] < 100), key=lambda t: t.bbox[1])
    texts = [" ".join(" ".join(c or "" for c in r) for r in t.extract()) for t in boxes]
    texts = [" ".join(t.split()) for t in texts if t.strip() and not t.startswith("PLAVI")]
    if len(texts) != len(groups):
        problems.append(f"str. 2: {len(groups)} skupina u tablici, {len(texts)} popisa naselja")
        return []
    for g, t in zip(groups, texts):
        if not t.startswith(g[0].split(",")[0]) or len(t) < len(g[0]) - 5:
            problems.append(f"str. 2: popis {t[:40]!r} ne odgovara retku {g[0][:40]!r}")
        g[0] = t
    return groups


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    media = json.loads(fetch(MEDIA))
    urls = [m["source_url"] for m in sorted(media, key=lambda m: m["date"], reverse=True)
            if re.search(rf"PLAN-ODVOZA[-_](OTPADA[-_])?(ZA[-_])?{year}\.pdf$", m["source_url"], re.I)]
    if not urls:
        sys.exit(f"Nema plana odvoza za {year} na {SITE}")
    url = urls[0]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plan.pdf"
        fetch(url, path)
        pdf = pdfplumber.open(path)
        p1, p2 = pdf.pages[:2]
        text1 = " ".join((p1.extract_text() or "").split())
        text2 = " ".join((p2.extract_text() or "").split())
        groups = read_groups(p2, problems)
        mko = []
        for t in p1.extract_tables():
            for r in t:
                cells = [" ".join((c or "").split()) for c in r if c and c.strip()]
                if cells and cells[0].rstrip(":") in DAYS or cells and cells[0].startswith("SVAKI DRUGI"):
                    mko.append([cells[0].rstrip(":"), " ".join(cells[1:])])
                elif cells and mko:
                    mko[-1][1] += " " + " ".join(cells)
    if f"ZA {year}.g." not in text1 or f"ZA {year}.g." not in text2:
        problems.append(f"plan nije za {year}. godinu")
    if "prvog slijedećeg radnog dana" not in text2:
        problems.append("nema pravila za blagdane (prvi sljedeći radni dan)")
    hol = set(pravila.blagdani(year))

    zones, moved_all = {}, set()

    def add_zone(jls, podrucje, ulice, rows, napomena=None):
        merged = {}
        for d, code in rows:
            d2, moved = next_working(d, hol)
            if moved:
                moved_all.add((d, d2))
            c, m = merged.get(d2, ("", False))
            if code in c:
                problems.append(f"{podrucje}: {d2} {code} dvaput")
            merged[d2] = (c + code, m or moved)
        zone = {"jls": jls, "podrucje": podrucje, "ulice": ulice}
        if napomena:
            zone["napomena"] = napomena
        zone["raw"] = {str(year): podaci.month_lines([(d, c, m) for d, (c, m) in merged.items()])}
        zones[str(len(zones) + 1)] = zone

    mko_note = ("Miješani otpad (korisnici bez otpadomjera) odvozi se jednom tjedno prema mjesnom odboru – vidi "
                "napomene; korisnici otpadomjera odlažu ga u zajedničke spremnike.")
    for settl, blue, yellow, bio in groups:
        rb, ry = rule(blue), rule(yellow)
        if not rb or not ry:
            problems.append(f"{settl[:30]}: pravilo nije prepoznato: {blue!r} / {yellow!r}")
            continue
        all_names = names(settl)
        rows = [(nth(year, mo, rb[1], n), "K") for mo in range(1, 13) for n in rb[0]]
        rows += [(nth(year, mo, ry[1], n), "P") for mo in range(1, 13) for n in ry[0]]
        if any(d is None for d, _ in rows):
            problems.append(f"{settl[:30]}: n-ti dan ne postoji")
            continue
        parts = [(all_names, None)]
        if bio:
            m = re.fullmatch(r"(.+? U MJESECU)\s*(?:\((osim naselja|u naseljima):\s*(.+)\))?", bio)
            rbio = rule(m.group(1)) if m else None
            if not rbio:
                problems.append(f"{settl[:30]}: pravilo za biootpad nije prepoznato: {bio!r}")
                continue
            listed = names(m.group(3)) if m.group(3) else []
            unknown = [n for n in listed if n not in all_names]
            if unknown:
                problems.append(f"{settl[:30]}: naselja za biootpad nisu u skupini: {unknown}")
            with_bio = [n for n in all_names if (n in listed) == (m.group(2) == "u naseljima")] if listed else all_names
            parts = [(with_bio, rbio), ([n for n in all_names if n not in with_bio], None)]
        for ulice, rbio in parts:
            if not ulice:
                continue
            zrows = list(rows)
            if rbio:
                zrows += [(nth(year, mo, rbio[1], n), "B") for mo in range(1, 13) for n in rbio[0]]
            short = lambda t: " ".join(t.lower().split()).replace(" u mjesecu", "")
            desc = (f"papir {short(blue)}, plastika {short(yellow)}" +
                    (f", biootpad {short(m.group(1))}" if rbio else "") + " u mjesecu")
            add_zone("Buzet", f"{', '.join(ulice[:3])}{' …' if len(ulice) > 3 else ''} – {desc}", ulice, zrows, mko_note)

    # Lanišće: mixed waste every second Thursday from the first printed date
    lan = next((t for d, t in mko if d.startswith("SVAKI DRUGI")), None)
    if not lan:
        problems.append("str. 1: nema rasporeda za Općinu Lanišće")
    else:
        first = [date(int(y), int(m), int(d)) for d, m, y in re.findall(r"(\d{2})\.(\d{2})\.(\d{4})", lan)]
        if len(first) < 2 or any((b - a).days != 14 for a, b in zip(first, first[1:])) or first[0].weekday() != 3:
            problems.append(f"Lanišće: početni datumi {first} nisu svaki drugi četvrtak")
        else:
            rows, d = [], first[0]
            while d.year == year:
                rows.append((d, "M"))
                d += timedelta(days=14)
            ulice = names(re.split(r"\(", lan)[0])
            add_zone("Lanišće", "Općina Lanišće – miješani otpad svaki drugi četvrtak", ulice, rows,
                     "Za Lanišće plan ne predviđa odvoz reciklabilnog otpada ni biootpada.")
            if first[0].year != year:
                problems.append(f"Lanišće: prvi datum {first[0]} nije u {year}.")

    for i, z in zones.items():
        rows = list(podaci.iter_dates(z, year))
        for code in "PKBM":
            n = sum(code in c for _, c, _ in rows)
            expect = {"P": (12, 12), "K": (12, 12), "B": (24, 24), "M": (26, 27)}[code]
            if n and not expect[0] <= n <= expect[1]:
                problems.append(f"zona {i}: {n} odvoza {code}")
        print(f"Zona {i} {z['jls']}: {len(z['ulice'])} naselja – {z['podrucje']}")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    for d, d2 in sorted(moved_all):
        print(f"Blagdan {d:%d.%m.} → {d2:%d.%m.} (prvi sljedeći radni dan, pon – pet)")
    days = "; ".join(f"{d.lower()}: {t}" for d, t in mko if not d.startswith("SVAKI"))
    data = {**PROVIDER, "izvor": url, "napomene": NAPOMENE + [
        "Miješani komunalni otpad u Gradu Buzetu (spremnici 60 – 360 l, samo za korisnike koji ne koriste "
        f"otpadomjer), jednom tjedno: {days}. Plan ne navodi naselja mjesnih odbora pa ti dani nisu u kalendaru.",
        "Blagdani: prema planu odvoz se obavlja prvog sljedećeg radnog dana (ili prema obavijesti na oglasnim "
        "pločama, društvenim mrežama i park.hr); kao radni dani uzeti su ponedjeljak – petak (" +
        ", ".join(f"{d:%d.%m.} → {d2:%d.%m.}" for d, d2 in sorted(moved_all)) + ").",
    ], "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years of zones that did not change
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
