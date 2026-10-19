"""Komunalno društvo GRAD d.o.o. (Supetar): Grad Supetar (Supetar in three areas, Mirca, Splitska, Škrip), 2026.

    python3 -m izvori.kd_grad_supetar [--year 2026]

The page "Gospodarenje otpadom" links two PDFs ("Raspored odvoza miješanog komunalnog otpada" and "Raspored
odvoza reciklabilnog otpada") whose tables are pictures: mixed waste on two (winter, 1.1.-21.6. and
7.9.-31.12.) or three (summer, 22.6.-6.9.) weekdays per area, paper on the first and plastic on the second
given weekday of the month (different in winter and summer), plus the street list of the three Supetar
areas. The tables are transcribed by hand below; the script finds both PDFs on the page, checks their
sha256 and stops ("slika se promijenila") when one changes. Each rule applies only on dates inside its
season. Holidays: the PDFs say there is no collection on 1.1., 5.4. (Easter) and 25.12.2026, without a
replacement day, so those dates are dropped.
"""
import argparse
import calendar
import hashlib
import html
import re
import sys
from collections import Counter
from datetime import date

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "kd-grad-supetar"
SITE = "https://www.kdgrad.hr"
PAGE = SITE + "/gospodarenje-otpadom/"
YEAR = 2026  # "Raspored je u primjeni od 01.01.2026. godine do 31.12.2026. godine"
PDFS = {  # link text on the page: sha256 of the transcribed PDF
    "Raspored odvoza miješanog komunalnog otpada":
        "bf84e92e870a2da629b9a73240aaa984d7b0b34669183275772c24bbcf686776",
    "Raspored odvoza reciklabilnog otpada": "f05a687275f97c3e4e78fa3bd796e5e12068072f315a80fe7a47ce7e133b7ea9",
}
# "NAPOMENA: Otpad se ne sakuplja za Novu godinu 01.01.2026., za Uskrs 05.04.2026. i za Božić 25.12.2026."
NO_COLLECTION = [date(2026, 1, 1), date(2026, 4, 5), date(2026, 12, 25)]
ZIMA = [("01.01.", "21.06."), ("07.09.", "31.12.")]
LJETO = [("22.06.", "06.09.")]


def rules(m_winter, m_summer, k_winter, k_summer):
    """Mixed waste weekdays and the paper weekday (paper 1st, plastic 2nd of the month) per season."""
    return {"M": [(m_winter, ZIMA), (m_summer, LJETO)],
            "K": [(f"1. {k_winter}", ZIMA), (f"1. {k_summer}", LJETO)],
            "P": [(f"2. {k_winter}", ZIMA), (f"2. {k_summer}", LJETO)]}


STAR = "Ulice označene zvjezdicom u rasporedu (promjena rasporeda u 2026.): "
ZONES = [  # (podrucje, rules, streets, note)
    ("Supetar – Dolčić", rules("sri sub", "pon sri pet", "sub", "sri"), [
        "Biokovska", "Bračka", "Busje", "Dolčić", "Frane Petrinovića 9-21 (neparni), 2-10 (parni)", "Gaja Bulata",
        "Gat Hrvatske mornarice", "Hrvatskih velikana", "Hvarska", "Ive Jakšića", "Jadranska", "Kipara Ivana Rendića",
        "Malačnica", "Mladena Vodanovića", "Muškat", "Petra Jakšića", "Polanda", "Prilaz perivoju", "Put Gaja",
        "Put Gustirne luke", "Put Kapelica", "Put križa", "Put Ozdrina", "Put Sv. Roka", "Put vrila", "Radnička",
        "Supetar", "Šoltanska", "Trg dr. Franje Tuđmana", "Vladimira Nazora", "Vrilo",
        "XII dalmatinske brigade 1-9A (neparni), 2-6 (parni)", "Žedno - Drage"],
     STAR + "Biokovska, Frane Petrinovića 9-21 i 2-10, Ive Jakšića, Muškat, Put Kapelica, "
            "XII dalmatinske brigade 1-9A i 2-6."),
    ("Supetar – Centar", rules("sri sub", "uto čet sub", "sub", "čet"), [
        "8. ožujka", "Andrije Hebranga", "Frane Petrinovića 1-7 (neparni), 2A (parni)", "Get", "Ignjata Joba",
        "Ive Vojnovića", "Kala", "Kaleta", "Marka Vuškovića", "Mirka Kustića", "Porat", "Prilaz", "Prilaz pomoraca",
        "Prolaz Feliksa Tironija", "Put Barba Maškova", "Put kule", "Put Ošega dolca", "Put Varoša", "Vlačica"],
     STAR + "Frane Petrinovića 1-7 i 2A."),
    ("Supetar – Pašike", rules("uto pet", "uto čet sub", "pet", "sub"), [
        "1. svibnja", "18. rujna", "22. lipnja", "Ante Mihanovića", "Bana Josipa Jelačića", "Branka Deškovića",
        "Don Drage Bosiljevca", "Don Nike Miličevića", "Dr. Ante Starčevića", "Dujma Hrankovića",
        "Fra Andrije Dorotića", "Hrvatskih žrtava", "Iseljenika", "Ivana Gorana Kovačića", "Ive Tijardovića",
        "Junaka Vukovara", "Kardinala Alojzija Stepinca", "Kneza Domagoja", "Kralja Petra Krešimira IV",
        "Kralja Tomislava", "Kralja Zvonimira", "Matka Dukića", "Mornarska", "Otona Postružnika", "Punta",
        "Put Jezerina", "Put Pašika", "Put Vele luke", "Put Višćica", "Ratac", "Stjepana Radića",
        "Šetalište Miljenka Smoje", "Šetalište put Banja", "Tina Ujevića", "Vojalo",
        "XII dalmatinske brigade 11-33 (neparni), 8-24 (parni)", "Zrinsko-Frankopanska"],
     STAR + "XII dalmatinske brigade 11-33 i 8-24."),
    ("Mirca", rules("uto pet", "uto čet sub", "uto", "čet"), ["Mirca (čitavo naselje)"], None),
    ("Splitska", rules("pon čet", "pon sri pet", "pon", "sri"), ["Splitska (čitavo naselje)"], None),
    ("Škrip", rules("pon čet", "pon sri pet", "pon", "sri"), ["Škrip (čitavo naselje)"], None),
]
PROVIDER = {
    "davatelj": "Komunalno društvo GRAD d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Supetar"],
    "nazivi": {"K": "Papir i karton (plavi spremnik)", "P": "Plastika (žuti spremnik)"},
    "napomene": [
        "Miješani otpad (zeleni spremnik) zimi (1.1. – 21.6. i 7.9. – 31.12.) dva puta tjedno, ljeti "
        "(22.6. – 6.9.) tri puta tjedno; papir i plastika jednom mjesečno, prema rasporedu za svako područje.",
        "Otpad se ne sakuplja 1.1.2026. (Nova godina), 5.4.2026. (Uskrs) i 25.12.2026. (Božić); zamjenski dani "
        "nisu navedeni.",
        "U mjesecima kad se mijenja raspored (lipanj, rujan) papir i plastika upisani su prema rasporedu koji "
        "vrijedi na taj dan.",
        "Glomazni otpad jednom godišnje na zahtjev korisnika. Reciklažno dvorište Žedno–Drage.",
        "Komunalno društvo GRAD d.o.o., Žedno-Drage 7, Supetar; 021/631-077.",
    ],
}

DANI = list(pravila.DANI)


def spans(year, ranges):
    """[("01.10.", "31.05."), ...] -> [(first, last)] in `year`; a range over New Year is split in two."""
    out = []
    for a, b in ranges:
        lo, hi = (date(year, *map(int, reversed(x.strip(".").split(".")))) for x in (a, b))
        out += [(lo, hi)] if lo <= hi else [(date(year, 1, 1), hi), (lo, date(year, 12, 31))]
    return out


def weekdays(rule):
    """'pon sri pet' / 'pon-sub' / 'svaki dan' / '1. 3. sub' / 'zadnji čet' -> (weekday keys, n-th list)."""
    days, nth = [], []
    for w in ("pon-ned" if rule == "svaki dan" else rule).split():
        if "-" in w:
            a, b = w.split("-")
            days += DANI[DANI.index(a):DANI.index(b) + 1]
        elif w in DANI:
            days.append(w)
        else:
            nth.append(-1 if w == "zadnji" else int(w.rstrip(".")))
    return days, nth


def rule_dates(year, rule):
    days, nth = weekdays(rule)
    if nth:
        return sorted(d for k in days for n in nth for d in pravila.mjesecno(year, k, n))
    return sorted(d for k in days for d in pravila.tjedno(year, k))


def collect(year, rules, problems, label):
    """{code: [(rule, ranges)]} -> {date: codes}, each rule only inside its date ranges."""
    out = {}
    for code, items in rules.items():
        for rule, ranges in items:
            sp = spans(year, ranges)
            for d in rule_dates(year, rule):
                if any(lo <= d <= hi for lo, hi in sp):
                    if code in out.get(d, ""):
                        problems.append(f"{label}: {code} dvaput {d}")
                    out[d] = out.get(d, "") + code
    return out


def check(label, rows, rules, year, problems, off=()):
    """Dates on a weekday of a rule in force (unless moved), none twice, plausible counts per month."""
    for d, n in Counter(d for d, _, _ in rows).items():
        if n > 1:
            problems.append(f"{label}: {d} dvaput")
    for d, codes, moved in rows:
        for c in codes:
            ok = any(DANI[d.weekday()] in weekdays(r)[0] and any(lo <= d <= hi for lo, hi in spans(year, rg))
                     for r, rg in rules[c])
            if not ok and not moved:
                problems.append(f"{label}: {c} {d} ({DANI[d.weekday()]}) nije po pravilu")
    for m in range(1, 13):
        first, last = date(year, m, 1), date(year, m, calendar.monthrange(year, m)[1])
        for c, items in rules.items():
            n = sum(1 for d, codes, _ in rows if d.month == m and c in codes)
            whole = [r for r, rg in items if any(lo <= first and last <= hi for lo, hi in spans(year, rg))]
            if len(whole) != 1:
                if n > 31:
                    problems.append(f"{label}: {c} u {m}. mjesecu {n} puta")
                continue
            days, nth = weekdays(whole[0])
            lo, hi = (len(days) * len(nth),) * 2 if nth else (4 * len(days), 5 * len(days))
            lo -= sum(1 for h in list(pravila.blagdani(year)) + list(off) if h.month == m)
            if not lo <= n <= hi:
                problems.append(f"{label}: {c} u {m}. mjesecu {n} puta (očekivano {lo}–{hi})")


def pdf_links(problems):
    """{link text: url} of the two schedule PDFs on the page."""
    page = fetch(PAGE).decode("utf-8", "replace")
    found = {}
    for href, text in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', page, re.S):
        found.setdefault(" ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split()), href)
    for name in PDFS:
        if name not in found:
            problems.append(f"na {PAGE} nema poveznice '{name}'")
    return {n: found[n] for n in PDFS if n in found}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Prepisan je samo raspored za {YEAR}.; za {year}. treba prepisati novi.")
    problems = []
    links = pdf_links(problems)
    for name, url in links.items():
        digest = hashlib.sha256(fetch(url)).hexdigest()
        if digest != PDFS[name]:
            problems.append(f"slika se promijenila, raspored treba ponovno prepisati: {url} (sha256 {digest})")
        else:
            print(f"{name}: {url} (sha256 isti kao u prijepisu)")
    if problems:
        for p in problems:
            print("   PROBLEM", p)
        sys.exit("Ništa nije upisano.")
    print("Blagdani: bez odvoza " + ", ".join(f"{d:%d.%m.}" for d in NO_COLLECTION) + " (prema napomeni u PDF-u).")
    zones = {}
    for z, (podrucje, rules_z, streets, note) in enumerate(ZONES, 1):
        dates = collect(year, rules_z, problems, f"zona {z}")
        dropped = sorted(d for d in dates if d in NO_COLLECTION)
        rows = sorted((d, c, False) for d, c in dates.items() if d not in NO_COLLECTION)
        check(f"zona {z}", rows, rules_z, year, problems, NO_COLLECTION)
        zones[str(z)] = {"jls": "Supetar", "podrucje": podrucje, "ulice": streets}
        if note:
            zones[str(z)]["napomena"] = note
        zones[str(z)]["raw"] = {str(year): podaci.month_lines(rows)}
        print(f"Zona {z} ({podrucje}): " + ", ".join(f"{c} {n}" for c, n in sorted(
            Counter(c for _, cs, _ in rows for c in cs).items()))
              + (f"; bez odvoza {', '.join(f'{d:%d.%m.}' for d in dropped)}" if dropped else ""))
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
