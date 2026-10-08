"""Erdut: Čvorkovac d.o.o. (cvorkovac.hr), Dalj I., Dalj II., Planina, Bijelo Brdo, Aljmaš and Erdut.

    python3 -m izvori.cvorkovac [--year 2026]

The page "Odvoz otpada" links the year PDF (raspored_odvoza_<year>.pdf), a 3-page colour scan without a text
layer: page 1 is a table month × (kind, area) of explicit dates (green mixed waste every two weeks, with one
extra round in June, July and August for "Dalj i Aljmaš" and "BB, Planina i Erdut"; brown biowaste every two
weeks; blue paper and yellow plastic once a month), page 2 has the holiday table ("nema odvoza" → "zamjenski
odvoz"), glass, bulky waste, textile and e-waste dates and the recycling yards, page 3 the weekdays per area.
The dates were transcribed by hand into COLUMNS and are kept with the PDF's sha256 (a changed PDF stops the
script). Dates from the holiday table are marked as moved; on the other public holidays the provider collects
as scheduled.
"""
import argparse
import hashlib
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cvorkovac"
SITE = "https://www.cvorkovac.hr"
PAGE = SITE + "/index.php/usluge/odvoz-otpada"
# year -> transcription of the PDF: column -> (kind, weekday, every n weeks (0 = monthly), dates; "!" = moved)
SCHEDULES = {2026: {
    "sha256": "fda4890155a72c2e3d8b1427df6c3c52116e00c8df1241d49b3e2d4259b952ff",
    "columns": {
        "M_DALJ1_PLANINA": ("M", 0, 2, "01-05 01-19 02-02 02-16 03-02 03-16 03-30 04-09! 04-27 05-11 05-25 06-08 06-22 "
                                       "07-06 07-20 08-03 08-17 08-31 09-14 09-28 10-12 10-26 11-09 11-23 12-07 12-21"),
        "M_DALJ2": ("M", 1, 2, "01-02! 01-20 02-03 02-17 03-03 03-17 03-31 04-14 04-28 05-12 05-26 06-09 06-23 07-07 "
                               "07-21 08-04 08-18 09-01 09-15 09-29 10-13 10-27 11-10 11-24 12-08 12-22"),
        "M_BB": ("M", 2, 2, "01-09! 01-21 02-04 02-18 03-04 03-18 04-01 04-15 04-29 05-13 05-27 06-10 06-24 07-08 07-22 "
                            "08-05 08-19 09-02 09-16 09-30 10-14 10-28 11-11 11-25 12-09 12-23"),
        "M_ALJMAS_ERDUT": ("M", 3, 2, "01-08 01-22 02-05 02-19 03-05 03-19 04-02 04-16 04-30 05-14 05-28 06-11 06-25 "
                                      "07-09 07-23 08-06 08-20 09-03 09-17 10-01 10-15 10-29 11-12 11-26 12-10 12-24"),
        "B_DALJ1_PLANINA": ("B", 0, 2, "01-12 01-26 02-09 02-23 03-09 03-23 04-03! 04-20 05-04 05-18 06-01 06-15 06-29 "
                                       "07-13 07-27 08-10 08-24 09-07 09-21 10-05 10-19 11-02 11-16 11-30 12-14 12-28"),
        "B_DALJ2_ERDUT": ("B", 1, 2, "01-13 01-27 02-10 02-24 03-10 03-24 04-07 04-21 05-05 05-19 06-02 06-16 06-30 "
                                     "07-14 07-28 08-11 08-25 09-08 09-22 10-06 10-20 11-03 11-17 12-01 12-15 12-29"),
        "B_ALJMAS_BB": ("B", 2, 2, "01-14 01-28 02-11 02-25 03-11 03-25 04-08 04-22 05-06 05-20 06-03 06-17 07-01 "
                                   "07-15 07-29 08-12 08-26 09-09 09-23 10-07 10-21 11-04 11-13! 12-02 12-16 12-30"),
        "K_DALJ_ERDUT_PLANINA": ("K", 3, 0, "01-29 02-26 03-26 04-23 05-21 06-18 07-30 08-27 09-24 10-22 11-19 12-17"),
        "K_ALJMAS_BB": ("K", 2, 0, "01-14 02-11 03-11 04-08 05-06 06-03 07-01 08-12 09-09 10-07 11-04 12-02"),
        "P_DALJ_ERDUT_PLANINA": ("P", 4, 0, "01-30 02-27 03-27 04-24 05-22 06-19 07-31 08-28 09-25 10-23 11-20 12-18"),
        "P_ALJMAS_BB": ("P", 4, 0, "01-16 02-13 03-13 04-10 05-08 06-05 07-03 08-14 09-11 10-09 11-06 12-04"),
        # extra summer mixed-waste rounds and the other kinds, by the two groups of page 2
        "M_DALJ_ALJMAS": ("M", None, None, "06-29 07-16 08-11"),
        "M_BB_PLANINA_ERDUT": ("M", None, None, "06-30 07-17 08-10"),
        "S_DALJ_ALJMAS": ("S", None, None, "03-05"), "S_BB_ERDUT_PLANINA": ("S", None, None, "03-06"),
        "G_DALJ_ALJMAS": ("G", None, None, "05-15"), "G_BB_ERDUT_PLANINA": ("G", None, None, "05-29"),
        "T_DALJ_ALJMAS": ("T", None, None, "08-20"), "T_BB_ERDUT_PLANINA": ("T", None, None, "08-21"),
    },
    "moves": {"01-06": "01-02", "01-07": "01-09", "04-06": "04-03", "04-13": "04-09", "11-18": "11-13"},
    "ewaste": ("17.9.", "18.9."),
}}
# zone: (settlement, columns)
ZONES = {
    "1": ("Dalj I.", ["M_DALJ1_PLANINA", "B_DALJ1_PLANINA", "K_DALJ_ERDUT_PLANINA", "P_DALJ_ERDUT_PLANINA",
                      "M_DALJ_ALJMAS", "S_DALJ_ALJMAS", "G_DALJ_ALJMAS", "T_DALJ_ALJMAS"]),
    "2": ("Dalj II.", ["M_DALJ2", "B_DALJ2_ERDUT", "K_DALJ_ERDUT_PLANINA", "P_DALJ_ERDUT_PLANINA",
                       "M_DALJ_ALJMAS", "S_DALJ_ALJMAS", "G_DALJ_ALJMAS", "T_DALJ_ALJMAS"]),
    "3": ("Planina", ["M_DALJ1_PLANINA", "B_DALJ1_PLANINA", "K_DALJ_ERDUT_PLANINA", "P_DALJ_ERDUT_PLANINA",
                      "M_BB_PLANINA_ERDUT", "S_BB_ERDUT_PLANINA", "G_BB_ERDUT_PLANINA", "T_BB_ERDUT_PLANINA"]),
    "4": ("Bijelo Brdo", ["M_BB", "B_ALJMAS_BB", "K_ALJMAS_BB", "P_ALJMAS_BB",
                          "M_BB_PLANINA_ERDUT", "S_BB_ERDUT_PLANINA", "G_BB_ERDUT_PLANINA", "T_BB_ERDUT_PLANINA"]),
    "5": ("Aljmaš", ["M_ALJMAS_ERDUT", "B_ALJMAS_BB", "K_ALJMAS_BB", "P_ALJMAS_BB",
                     "M_DALJ_ALJMAS", "S_DALJ_ALJMAS", "G_DALJ_ALJMAS", "T_DALJ_ALJMAS"]),
    "6": ("Erdut", ["M_ALJMAS_ERDUT", "B_DALJ2_ERDUT", "K_DALJ_ERDUT_PLANINA", "P_DALJ_ERDUT_PLANINA",
                    "M_BB_PLANINA_ERDUT", "S_BB_ERDUT_PLANINA", "G_BB_ERDUT_PLANINA", "T_BB_ERDUT_PLANINA"]),
}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
INS = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
KIND = {"M": "miješani", "B": "biootpad", "K": "papir", "P": "plastika"}
PROVIDER = {
    "davatelj": "Čvorkovac d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Osječko-baranjska",
    "jls": ["Erdut"],
    "nazivi": {"M": "Miješani komunalni otpad (zelena kanta)", "P": "Plastika (žuta kanta)",
               "K": "Papir i karton (plava kanta)"},
}
NAPOMENE = [
    "Miješani komunalni otpad (zelena kanta) odvozi se svaka dva tjedna, a u lipnju, srpnju i kolovozu još jednom "
    "dodatno; biootpad (smeđa kanta) svaka dva tjedna; papir (plava kanta) i plastika (žuta kanta) jednom mjesečno. "
    "Spremnike treba pripremiti do 6 sati.",
    "Podjela Dalja na Dalj I. i Dalj II. nije objavljena u rasporedu; provjerite kod Čvorkovca (031/590-272).",
    "Reciklažno dvorište Studenac, Pašnjak livadica bb, Dalj: od 1.11. do 31.3. utorak–subota 8–16 sati; od 1.4. do "
    "31.10. utorak, četvrtak, petak i subota 8–16, srijeda 10–18 sati; ponedjeljkom zatvoreno.",
    "Mobilno reciklažno dvorište: drugi petak u mjesecu Bijelo Brdo (kod Doma kulture), treći petak Aljmaš, četvrti "
    "petak Erdut (kod Doma kulture).",
    "Čvorkovac d.o.o., Bana Josipa Jelačića 12, Dalj: 031/590-272, blagajna 031/591-556.",
]


def dates(text, year):
    return [(date(year, *map(int, t.rstrip("!").split("-"))), t.endswith("!")) for t in text.split()]


def check(cols, moves, year):
    """Rule problems of the transcription: weekday, two-week or monthly rhythm, holiday moves."""
    problems, used = [], set()
    for name, (kind, wd, weeks, text) in cols.items():
        rows = dates(text, year)
        if [d for d, _ in rows] != sorted(d for d, _ in rows):
            problems.append(f"{name}: datumi nisu poredani")
        if wd is None:
            problems += [f"{name}: {d} je {DAN[d.weekday()]}" for d, _ in rows if d.weekday() > 4]
            continue
        due = []
        for d, moved in rows:
            old = next((o for o, n in moves.items() if n == d and o.weekday() == wd), None) if moved else d
            if moved and old is None:
                problems.append(f"{name}: {d} označen kao premješten, a nije u tablici zamjenskih termina")
                continue
            if moved:
                used.add(old)
            if old.weekday() != wd:
                problems.append(f"{name}: {d} je {DAN[d.weekday()]}, očekivano {DAN[wd]}")
            if old in moves and not moved:
                problems.append(f"{name}: {d} je u tablici „nema odvoza”")
            due.append(old)
        if weeks:
            problems += [f"{name}: razmak {a} – {b}" for a, b in zip(due, due[1:]) if (b - a).days != 7 * weeks]
            months = Counter(d.month for d in due)
            problems += [f"{name}: {k} odvoza u {m}. mjesecu" for m, k in months.items() if not 2 <= k <= 3]
        elif sorted(d.month for d in due) != list(range(1, 13)):
            problems.append(f"{name}: nije jednom mjesečno")
    if set(moves) - used:
        problems.append(f"zamjenski termini koji se ne koriste: {sorted(set(moves) - used)}")
    for o, n in moves.items():
        if abs((n - o).days) > 7 or n.weekday() > 4:
            problems.append(f"zamjenski odvoz {o} → {n} nije vjerojatan")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    if year not in SCHEDULES:
        sys.exit(f"Za {year}. nema prepisanog rasporeda u skripti (SCHEDULES).")
    sched = SCHEDULES[year]
    page = fetch(PAGE).decode("utf-8", "replace")
    links = sorted(set(re.findall(rf'href="([^"]*raspored_odvoza_{year}\.pdf)"', page)))
    if len(links) != 1:
        sys.exit(f"Na {PAGE} nije pronađen jedan PDF rasporeda za {year}: {links}")
    url = links[0] if links[0].startswith("http") else SITE + "/" + links[0].lstrip("/")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "raspored.pdf"
        fetch(url, pdf)
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if sha != sched["sha256"]:
        sys.exit(f"slika se promijenila, prepisati ponovno: {url} (sha256 {sha})")
    cols = sched["columns"]
    moves = {date(year, *map(int, o.split("-"))): date(year, *map(int, n.split("-")))
             for o, n in sched["moves"].items()}
    problems = check(cols, moves, year)
    hol = set(pravila.blagdani(year))
    on_holiday = sorted({d for _, _, _, text in cols.values() for d, _ in dates(text, year) if d in hol})
    if on_holiday:
        print("Odvoz na blagdan prema rasporedu:", ", ".join(f"{d:%d.%m.}" for d in on_holiday))
    data = {**PROVIDER, "napomene": list(NAPOMENE), "zone": {}}
    data["napomene"].insert(1, "Nema odvoza " + ", ".join(f"{o.day}.{o.month}." for o in sorted(moves)) +
                            "; zamjenski odvozi (" + ", ".join(f"{n.day}.{n.month}." for _, n in sorted(moves.items()))
                            + ") označeni su kao premješteni." +
                            (" Na blagdane " + ", ".join(f"{d.day}.{d.month}." for d in on_holiday) +
                             " odvoz je prema rasporedu." if on_holiday else ""))
    data["napomene"].insert(2, f"Elektronički otpad odvozi se s kućnog praga {sched['ewaste'][0]} (Dalj, Aljmaš) i "
                               f"{sched['ewaste'][1]} (Bijelo Brdo, Erdut, Planina).")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    for z, (name, columns) in ZONES.items():
        merged = {}
        for col in columns:
            kind = cols[col][0]
            for d, moved in dates(cols[col][3], year):
                codes, mv = merged.get(d, ("", False))
                merged[d] = (codes + kind, mv or moved)
        rows = [(d, codes, mv) for d, (codes, mv) in merged.items()]
        days = {}
        for col in columns[:4]:
            days.setdefault(cols[col][1], []).append(KIND[cols[col][0]])
        desc = ", ".join(f"{', '.join(k[:-1]) + ' i ' * (len(k) > 1) + k[-1]} {INS[wd]}" for wd, k in days.items())
        prev = old.get(z, {}).get("raw", {})
        data["zone"][z] = {
            "jls": "Erdut",
            "podrucje": f"{name} – {desc}",
            "ulice": ["Dalj", name] if name.startswith("Dalj") else [name],
            "raw": {**{y: v for y, v in prev.items() if y != str(year)}, str(year): podaci.month_lines(rows)},
        }
        n = Counter(c for _, codes, _ in rows for c in codes)
        print(f"{name}: {dict(n)}, premješteno: {', '.join(f'{d:%d.%m.}' for d, _, mv in sorted(rows) if mv) or '-'}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
