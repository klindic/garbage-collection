"""Biograd na Moru and Polača: Bošana d.o.o. (bosana.hr), one year PDF per municipality.

    python3 -m izvori.bosana [--year 2026]

The page "Raspored odvoza" links odvoza-otpada-<year>.pdf (Biograd) and odvoza-otpada-polaca-<year>.pdf.
Page 1 of each gives rules per area and season (winter 01.01.-30.04. and 01.11.-31.12., summer
01.05.-31.10.): mixed waste weekdays, "first and third Tuesday", "every second Friday - see the calendar".
Page 2 is a year calendar with one circle per day (green mixed, yellow plastic, blue paper, orange
biowaste, brown glass, red Sunday/holiday, a split circle for two types) but without areas. The rules
were written into AREAS/GROUPS below by hand; the script checks that every rule line is still in the PDF
text. pdfminer cannot read page 2, so it is rendered with pdftoppm and the circle around each day number
(pdftotext -bbox) is sampled; every day must sit in its weekday column. A rule date is kept only when the
calendar shows that type on it; "every second ..." rules take the calendar's days of that weekday.
Holidays: the published rule is applied (in winter mixed waste goes to the next day, marked as moved;
other types are not moved); in summer no rule is published and holidays the calendar shows red have no
collection. Calendar dates that no area's rule explains are printed and listed in napomene.
"""
import argparse
import calendar
import html
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import LONG, MONTHS, weekday_rows

SLUG = "bosana"
SITE = "https://www.bosana.hr"
PAGE = SITE + "/usluge/čistoća/raspored-odvoza"
DPI = 200
# rendered circle colours -> code ("-" red Sunday/holiday, "." white, no collection)
PALETTE = {(57, 170, 52): "M", (255, 237, 0): "P", (54, 169, 225): "K", (51, 169, 225): "K",
           (249, 178, 50): "B", (164, 137, 122): "S", (231, 50, 41): "-", (227, 0, 15): "-",
           (255, 255, 255): "."}
WINTER, SUMMER = (1, 2, 3, 4, 11, 12), (5, 6, 7, 8, 9, 10)
# Rules: ("w", days) every week; ("n", (1, 3), day) n-th weekday of the month; ("c", day) the calendar's
# days of that weekday ("svaki drugi ... prema kalendaru"); ("zrs",) last working Saturday of the month.
GROUPS = {  # recyclables and biowaste groups of page 1: {code: (winter rule, summer rule)}
    "biograd A": {"P": (("n", (1, 3), "uto"), ("c", "pon")), "K": (("n", (1, 3), "čet"), ("c", "sri")),
                  "B": (("w", "pon"), ("w", "pet")), "S": (("zrs",), ("n", (4,), "ned"))},
    "biograd B": {"P": (("n", (2, 4), "uto"), ("c", "uto")), "K": (("n", (2, 4), "čet"), ("c", "čet")),
                  "B": (("w", "pon"), ("w", "sub")), "S": (("zrs",), ("n", (4,), "ned"))},
    "biograd -": {"B": (("w", "pon"), None), "S": (("zrs",), ("n", (4,), "ned"))},
    "polača 1": {"P": (("c", "pet"), ("c", "pon")), "K": (("c", "pet"), ("c", "sri")),
                 "B": (("w", "uto"), ("w", "uto")), "S": (("n", (2,), "sub"), ("n", (2,), "sub"))},
    "polača 2": {"P": (("c", "pet"), ("c", "uto")), "K": (("c", "pet"), ("c", "čet")),
                 "B": (("w", "uto"), ("w", "uto")), "S": (("n", (2,), "sub"), ("n", (2,), "sub"))},
}
NO_RECYCLING = "Područje nije navedeno u rasporedu plastike, papira i ljetnog biootpada (pitati Bošanu)."
# zone: (JLS, areas, mixed waste winter days, summer days, group, note)
AREAS = {
    "1": ("Biograd na Moru", "Poluotok, Jaz, Vruljine, Kožina, Bošana, Zagrebačka ulica", "pon", "pon čet",
          "biograd A", None),
    "2": ("Biograd na Moru", "Ulica dr. Franje Tuđmana", "pon", "pon čet", "biograd B", None),
    "3": ("Biograd na Moru", "Bračka ulica, Paška ulica, Lastovska ulica", "pon", "sri sub", "biograd -",
          NO_RECYCLING),
    "4": ("Biograd na Moru", "Kosa istok, Kosa zapad", "sri", "sri sub", "biograd A", None),
    "5": ("Biograd na Moru", "Meterize, Granda", "sri", "sri sub", "biograd B", None),
    "6": ("Biograd na Moru", "Dubrovačka ulica", "sri", "sri sub", "biograd -", NO_RECYCLING),
    "7": ("Biograd na Moru", "Jankolovica", "sri", "uto pet", "biograd A", None),
    "8": ("Biograd na Moru", "Rust, Tuče, Centar, Kumenat", "pet", "uto pet", "biograd B", None),
    "9": ("Biograd na Moru", "Novo naselje", None, None, "biograd A",
          "Naselje nije navedeno u rasporedu miješanog komunalnog otpada (pitati Bošanu)."),
    "10": ("Polača", "Polača (osim predjela Rasti), Donja Jagodnja, Gornja Jagodnja", "sri", "sri pet",
           "polača 1", None),
    "11": ("Polača", "Kakma, Polača - Rasti", "čet", "pon čet", "polača 2", None),
}
SEASONS = "od 01.01.-30.04.{y}. i od 01.11.-31.12.{y}."
# lines of page 1 the rules above were written from (compared without spaces and without the lost "ij")
TEXT = {
    "Biograd na Moru": [
        "Raspored odvoza komunalnog otpada " + SEASONS,
        "Ponedjeljak - Poluotok, Jaz, Vruljine, Kožina, Bošana, Zagrebačka ulica, Ulica dr. Franje Tuđmana, "
        "Bračka ulica, Paška ulica i Lastovska ulica",
        "Srijeda - Kosa Istok, Kosa zapad, Meterize, Granda, Dubrovačka ulica i Jankolovica",
        "Petak - Rust, Tuče, Centar i Kumenat",
        "Raspored odvoza komunalnog otpada od 01.05.- 31.10.{y}.",
        "Ponedjeljak i četvrtak - Poluotok, Jaz, Vruljine, Kožina, Bošana, Zagrebačka ulica i Ulica dr. Franje "
        "Tuđmana",
        "Utorak i petak - Jankolovica, Rust, Tuče, Centar i Kumenat",
        "Srijeda i subota - Kosa zapad, Kosa istok, Granda, Meterize, Dubrovačka ulica, Bračka ulica, Paška "
        "ulica i Lastovska ulica",
        "Prvi i treći utorak - Poluotok, Jaz, Vruljine, Kožina, Bošana, Kosa istok, Kosa zapad, Novo naselje, "
        "Jankolovica i Zagrebačka ulica",
        "Drugi i četvrti utorak - Centar, Rust, Tuče, Meterize, Granda, Kumenat i Ulica dr. Franje Tuđmana",
        "Dva puta mjesečno prema rasporedu: Ponedjeljak - Poluotok, Jaz, Vruljine, Kožina, Bošana, Kosa istok, "
        "Kosa zapad, Novo naselje, Jankolovica i Zagrebačka ulica",
        "Utorak - Centar, Rust, Tuče, Meterize, Granda, Kumenat i Ulica dr. Franje Tuđmana",
        "Prvi i treći četvrtak - Poluotok, Jaz, Vruljine, Kožina, Bošana, Kosa istok, Kosa zapad, Novo naselje, "
        "Jankolovica i Zagrebačka ulica",
        "Drugi i četvrti četvrtak - Centar, Rust, Tuče, Meterize, Granda, Kumenat i Ulica dr. Franje Tuđmana",
        "Svaku drugu srijedu - Poluotok, Vruljine, Jaz, Bošana, Kožina, Kosa istok, Kosa zapad, Novo naselje, "
        "Jankolovica i Zagrebačka ulica",
        "Svaki drugi četvrtak - Centar, Rust, Tuče, Meterize, Granda, Kumenat i Ulica dr. Franje Tuđmana",
        "Raspored odvoza biootpada " + SEASONS + " Svaki ponedjeljak",
        "Petak - Poluotok, Vruljine, Jaz, Bošana, Kožina, Kosa istok, Kosa zapad, Novo naselje, Jankolovica i "
        "Zagrebačka ulica",
        "Subota - Centar, Rust, Tuče, Meterize, Granda, Kumenat i Ulica dr. Franje Tuđmana",
        "Raspored odvoza stakla " + SEASONS + " Zadnja radna subota u mjesecu",
        "Raspored odvoza stakla od 01.05.-31.10.{y}. Četvrta nedjelja u mjesecu",
        "usluga će se vršiti narednog dana isključivo za mještani komunalni otpad",
    ],
    "Polača": [
        "Raspored odvoza komunalnog otpada " + SEASONS,
        "Srijeda - Polača (osim predio Rasti), Donja Jagodnja, Gornja Jagodnja",
        "Četvrtak - Kakma i Polača - Rasti",
        "Raspored odvoza komunalnog otpada od 01.05.- 31.10.{y}.",
        "Ponedjeljak i četvrtak - Kakma i Polača - Rasti",
        "Srijeda i petak - Polača (osim predio Rasti), Donja Jagodnja, Gornja Jagodnja",
        "Raspored odvoza plastične ambalaže " + SEASONS + " Svaki drugi petak - prema kalendaru",
        "Svaki drugi ponedjeljak - prema kalendaru: Polača (osim predio Rasti), Donja Jagodnja, Gornja Jagodnja",
        "Svaki drugi utorak - prema kalendaru: Kakma i Polača - Rasti",
        "Raspored odvoza papira, kartona i tetrapak ambalaže " + SEASONS + " Svaki drugi petak - prema kalendaru",
        "Svaka druga srijeda - prema kalendaru: Polača (osim predio Rasti), Donja Jagodnja, Gornja Jagodnja",
        "Svaki drugi četvrtak - prema kalendaru: Kakma i Polača - Rasti",
        "Raspored odvoza biootpada od 01.01.-31.12.{y}. Svaki utorak",
        "Druga subota u mjesecu",
        "usluga će se vršiti narednog dana isključivo za mještani komunalni otpad",
    ],
}
FILES = {"Biograd na Moru": "odvoza-otpada-{y}.pdf", "Polača": "odvoza-otpada-polaca-{y}.pdf"}
# calendar dates (code) that no area's rule explains, or rule dates the calendar leaves empty (checked
# each run: a new mismatch stops the script, these are only reported)
KNOWN = {"Biograd na Moru": {"2026-01-29 K", "2026-12-29 P", "2026-12-01 P"}}
NAMES = {"P": "plastike", "K": "papira", "M": "miješanog otpada", "B": "biootpada", "S": "stakla"}
PROVIDER = {
    "davatelj": "Bošana d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zadarska",
    "jls": ["Biograd na Moru", "Polača"],
    "nazivi": {"P": "Plastična ambalaža", "K": "Papir, karton i tetrapak"},
}
NAPOMENE = [
    "Zimski raspored vrijedi od 01.01. do 30.04. i od 01.11. do 31.12., ljetni od 01.05. do 31.10.",
    "Zimi se za vrijeme državnih praznika i blagdana miješani komunalni otpad odvozi idući dan (označeno "
    "kao pomaknuto); ostale vrste se ne pomiču. Ljeti pravilo za blagdane nije objavljeno: blagdani koje "
    "kalendar označava crveno nemaju odvoza.",
    "Kalendar u PDF-u prikazuje sve odvoze grada/općine bez podjele po područjima; područja su prema "
    "pravilima s prve stranice.",
    "Polača: u evidenciji (IRDJU 2024) kao davatelj usluge naveden je Komunalno društvo Polača d.o.o., ali "
    "raspored odvoza za Polaču objavljuje Bošana d.o.o. (bosana.hr).",
    "Reciklažno dvorište Vilišnica b.b. (Industrijska zona, Put Vilišnice b.b., Biograd na Moru): "
    "01.01.-30.04. i 01.10.-31.12. pon-pet 07:30-18:30, sub 07:30-16:30; 01.05.-30.09. pon-pet "
    "07:30-20:30, sub 07:30-16:30; nedjeljom i blagdanom zatvoreno.",
    "Kontakt: +385 23 384 363, cistoca@bosana.hr.",
]


def norm(text):
    """Text without spaces and without "ij" (the PDF font loses the ij glyph: "Sr eda", "S ečanj")."""
    return re.sub(r"\s+|ij", "", text)


def words(pdf, page):
    out = subprocess.run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"],
                         capture_output=True, text=True, check=True).stdout
    return [dict(x0=float(a), top=float(b), x1=float(c), bottom=float(d), text=html.unescape(t))
            for a, b, c, d, t in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" '
                                            r'yMax="([\d.]+)">([^<]*)</word>', out)]


def classify(px):
    best = min(PALETTE, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, px)))
    return PALETTE[best] if sum((a - b) ** 2 for a, b in zip(best, px)) < 900 else None


def read_calendar(pdf, year, tmp):
    """({date: set of codes}, problems) from the colour calendar on page 2."""
    ws = words(pdf, 2)
    # a heading word set 2-3 pt lower makes weekday_rows find the same row twice
    rows = sorted({(round(g[0]["x0"]), round(g[0]["top"]) // 10): g for g in weekday_rows(ws)}.values(),
                  key=lambda g: (round(g[0]["top"]) // 10, g[0]["x0"]))
    if len(rows) != 12:
        return {}, [f"kalendar: {len(rows)} redaka s danima u tjednu, očekivano 12"]
    problems = []
    for m, g in enumerate(rows, 1):
        title = "".join(w["text"] for w in ws if 0 < g[0]["top"] - w["bottom"] < 25 and not w["text"].isdigit()
                        and g[0]["x0"] - 10 <= (w["x0"] + w["x1"]) / 2 <= g[-1]["x1"] + 10)
        if norm(title.lower()) != norm(MONTHS[m - 1].lower()):
            problems.append(f"kalendar: blok {m} ima naslov {title!r}")
    subprocess.run(["pdftoppm", "-f", "2", "-l", "2", "-r", str(DPI), "-png", "-singlefile", str(pdf),
                    str(Path(tmp) / "cal")], check=True)
    img = np.asarray(Image.open(Path(tmp) / "cal.png").convert("RGB")).astype(int)
    s = DPI / 72
    found, seen = {}, Counter()
    for w in ws:
        if not w["text"].isdigit():
            continue
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        inside = [(m, g) for m, g in enumerate(rows, 1)
                  if g[0]["bottom"] <= w["top"] and g[0]["x0"] - 10 <= cx <= g[-1]["x1"] + 10]
        if not inside:
            continue
        m, g = max(inside, key=lambda mg: mg[1][0]["top"])
        if not 1 <= int(w["text"]) <= calendar.monthrange(year, m)[1]:
            problems.append(f"kalendar {m}: nemoguć dan {w['text']}")
            continue
        d = date(year, m, int(w["text"]))
        seen[d] += 1
        col = min(range(7), key=lambda i: abs((g[i]["x0"] + g[i]["x1"]) / 2 - cx))
        if col != d.weekday():
            problems.append(f"kalendar {d}: u stupcu {LONG[col]}, a to je {LONG[d.weekday()]}")
        votes = Counter()
        for k in range(72):  # ring inside the circle (radius 8.3 pt), around the digits
            a = 2 * np.pi * k / 72
            for r in (5.5, 6.5, 7.3):
                votes[classify(tuple(img[int((cy + r * np.sin(a)) * s), int((cx + r * np.cos(a)) * s)]))] += 1
        total = sum(votes.values())
        if votes[None] > 0.4 * total:
            problems.append(f"kalendar {d}: nepoznata boja kružića")
        votes.pop(None, None)
        codes = {c for c, v in votes.items() if v > 0.12 * total}
        if len(codes) > 1 and codes & {".", "-"}:
            problems.append(f"kalendar {d}: nejasan kružić {sorted(codes)}")
        found[d] = codes - {"."}
    for m in range(1, 13):
        for day in range(1, calendar.monthrange(year, m)[1] + 1):
            if seen[date(year, m, day)] != 1:
                problems.append(f"kalendar: {date(year, m, day)} se pojavljuje {seen[date(year, m, day)]} puta")
    return found, problems


def candidates(rule, year, months):
    """(dates, from the calendar?) for one rule in the given months."""
    hol = set(pravila.blagdani(year))
    if rule[0] == "w":
        out = [d for day in rule[1].split() for d in pravila.tjedno(year, day)]
    elif rule[0] == "n":
        out = [d for n in rule[1] for d in pravila.mjesecno(year, rule[2], n)]
    elif rule[0] == "c":
        out = pravila.tjedno(year, rule[1])
    else:  # last Saturday of the month that is not a holiday
        out = [max(d for d in pravila.tjedno(year, "sub") if d.month == m and d not in hol) for m in range(1, 13)]
    return sorted(d for d in out if d.month in months), rule[0] == "c"


def zone_dates(year, cal, mixed, group, used, notes, problems):
    """[(date, codes, moved)] of one zone; marks the calendar entries it uses in `used`."""
    hol = set(pravila.blagdani(year))
    rules = {"M": (("w", mixed[0]), ("w", mixed[1])) if mixed[0] else (None, None), **GROUPS[group]}
    rows = {}
    for code, seasons in rules.items():
        for rule, months, winter in ((seasons[0], WINTER, True), (seasons[1], SUMMER, False)):
            if rule is None:
                continue
            dates, from_cal = candidates(rule, year, months)
            for d in dates:
                shown = cal[d]
                if code == "M" and winter and d in hol:  # published rule: next day
                    (new, _), = pravila.primijeni_blagdane([d], "sljedeci", year)
                    rows.setdefault(new, [set(), True])[0].add(code)
                    notes.add(f"{d:%d.%m.} blagdan: miješani otpad {new:%d.%m.}"
                              + (" (kalendar ga crta na sam blagdan)" if code in shown else ""))
                    used.add((d, code))
                elif code in shown:
                    rows.setdefault(d, [set(), False])[0].add(code)
                    used.add((d, code))
                elif ("-" in shown or d in hol) and not from_cal:
                    notes.add(f"{d:%d.%m.} blagdan: nema odvoza {NAMES[code]}")
                elif not from_cal and "-" not in shown and d not in hol:
                    problems.append(f"{d} {code}")
    return [(d, "".join(c), moved) for d, (c, moved) in rows.items()]


def days(text):
    """'pon čet' -> 'ponedjeljak i četvrtak'."""
    return " i ".join(podaci.DAYS[pravila.DANI[d]] for d in text.split())


def check_counts(z, rows, mixed, problems):
    for m in range(1, 13):
        days = [codes for d, codes, _ in rows if d.month == m]
        n = sum("M" in c for c in days)
        want = len((mixed[0] if m in WINTER else mixed[1]).split()) * 4 if mixed[0] else 0
        if not want - 2 <= n <= want + 5 or (want and n < 3):
            problems.append(f"zona {z} {m:02d}: {n} odvoza miješanog otpada")
        if not days:
            problems.append(f"zona {z} {m:02d}: nema odvoza")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    links = set(re.findall(r'href="([^"]+\.pdf)"', fetch(PAGE).decode("utf-8", "replace")))
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": list(NAPOMENE), "zone": {}}
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        for jls, name in FILES.items():
            name = name.format(y=year)
            url = next((l for l in links if l.endswith("/" + name)), None)
            if not url:
                sys.exit(f"Na {PAGE} nema {name}")
            pdf = Path(tmp) / name
            fetch(url if url.startswith("http") else SITE + url, pdf)
            text = norm(subprocess.run(["pdftotext", "-f", "1", "-l", "1", str(pdf), "-"],
                                       capture_output=True, text=True, check=True).stdout)
            for line in TEXT[jls]:
                if norm(line.format(y=year)) not in text:
                    problems.append(f"{jls}: u PDF-u više nema retka {line.format(y=year)!r} (pravila provjeriti)")
            cal, cal_problems = read_calendar(pdf, year, tmp)
            problems += [f"{jls}: {p}" for p in cal_problems]
            if cal_problems:
                continue
            used, notes, odd = set(), set(), []
            for z, (zjls, areas, w, s, group, note) in AREAS.items():
                if zjls != jls:
                    continue
                rows = zone_dates(year, cal, (w, s), group, used, notes, odd)
                check_counts(z, rows, (w, s), problems)
                desc = (f"Zimi {days(w)}, ljeti {days(s)} – " if w else "") + areas
                data["zone"][z] = {
                    "jls": jls, "podrucje": desc, "ulice": [a.strip() for a in areas.split(",")],
                    **({"napomena": note} if note else {}),
                    "raw": {**old["zone"].get(z, {}).get("raw", {}), str(year): podaci.month_lines(rows)},
                }
                print(f"{jls} zona {z} ({areas}): {len(rows)} dana odvoza")
            odd = set(odd) | {f"{d} {c}" for d, codes in cal.items() for c in codes - {"-"} if (d, c) not in used}
            for n in sorted(notes, key=lambda t: t[3:5] + t[:2]):
                print(f"   {jls}: {n}")
            known = KNOWN.get(jls, set())
            for x in sorted(odd - known):
                problems.append(f"{jls}: kalendar i pravila se ne slažu za {x}")
            if odd & known:
                text = ", ".join(f"{NAMES[x[-1]]} {date.fromisoformat(x[:10]):%d.%m.}" for x in sorted(odd & known))
                print(f"   {jls}: poznata neslaganja kalendara i pravila (nije upisano): {text}")
                data["napomene"].append(f"{jls}: kalendar i pravila se ne slažu za odvoz {text} (u kalendaru je "
                                        "odvoz bez navedenog područja ili ga nema na dan iz pravila); ti datumi "
                                        "nisu upisani.")
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
