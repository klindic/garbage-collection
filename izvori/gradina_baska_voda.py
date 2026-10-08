"""Gradina – Baška Voda d.o.o.: Općina Baška Voda (Baška Voda, Promajna, Bratuš i Krvavica, Bast i Topići).

    python3 -m izvori.gradina_baska_voda [--year 2026]

The page "Rasporedi odvoza" links one page per place; each has three HTML tables headed by their date ranges:
winter (15.10.-30.4., by street group where the place has several), pre/post-season (1.5.-31.5. and
16.9.-14.10.) and summer (1.6.-15.9.), with mixed waste, paper, plastic and glass as weekdays ("PON – SRI –
PET", "SVAKI DAN", "PRVA I TREĆA SRI U MJESECU"). The script reads the tables and the street lists ("ULICE A:
..."); "SVAKI DAN" in winter means Monday to Saturday ("nedjeljom ... se ne radi"). Two 2026 notices are
applied: mixed waste every day except Sunday in all places from 24.9. to 15.10.2026 and glass every Wednesday
until the end of September (post of 23.9.2026), and Corpus Christi (4.6.2026) collected as usual (post of
1.6.2026). Holidays: "Blagdanima i neradnim danima (nedjeljom) se ne odvozi otpad" – dates on public holidays
are dropped (no replacement is published), except where a notice says otherwise and, in winter, the second
day of Easter and Christmas ("nedjeljom i prvim danom blagdana se ne radi").
"""
import argparse
import calendar
import html
import re
import sys
from collections import Counter
from datetime import date, timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "gradina-baska-voda"
SITE = "https://gradina-baskavoda.hr"
PAGE = SITE + "/rasporedi-odvoza/"
PLACES = {"odvoz-baska-voda": "Baška Voda", "promajna": "Promajna", "bratus-i-krvavica": "Bratuš i Krvavica",
          "bast-i-topici": "Bast i Topići"}
TYPES = {"miješani otpad": "M", "miješani kom. otpad": "M", "papir": "K", "plastika": "P", "staklo": "S"}
DAYS = {"PON": "pon", "UTO": "uto", "SRI": "sri", "ČET": "čet", "PET": "pet", "SUB": "sub", "NED": "ned",
        "PONEDJELJAK": "pon", "UTORAK": "uto", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet", "SUBOTA": "sub"}
NTH = {"PRVA": "1.", "DRUGA": "2.", "TREĆA": "3.", "ČETVRTA": "4."}
# 2026 notices: (link, code, rule, from, to) replacing the regular rule in that window, for every zone
OVERRIDES = {2026: [
    (SITE + "/2026/09/23/raspored-odvoza-mijesanog-komunalnog-otpada-do-15-10/", "M", "pon-sub", "24.09.", "15.10."),
    (SITE + "/2026/09/23/raspored-odvoza-mijesanog-komunalnog-otpada-do-15-10/", "S", "sri", "23.09.", "30.09."),
]}
HOLIDAY_REGULAR = {2026: {date(2026, 6, 4): SITE + "/2026/06/01/obavijest-tijelovo/"}}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Gradina – Baška Voda d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Baška Voda"],
}
NAPOMENE = [
    "Zimski raspored 15.10. – 30.4., predsezona i posezona 1.5. – 31.5. i 16.9. – 14.10., ljetni 1.6. – 15.9.",
    "Blagdanima i nedjeljom se ne odvozi otpad (zimi se ne radi prvi dan blagdana); zamjenski dani nisu "
    "objavljeni, pa odvozi na blagdane nisu upisani. Iznimke se objavljuju na gradina-baskavoda.hr "
    "(npr. Tijelovo 4.6.2026. po redovnom rasporedu).",
    "Prema obavijesti od 23.9.2026. miješani otpad odvozi se svaki dan osim nedjelje u svim mjestima do 15.10.2026., "
    "a staklo do kraja rujna svake srijede; to je upisano.",
    "Dio centra Baške Vode koristi polupodzemne MOLOK spremnike.",
    "Gradina – Baška Voda d.o.o., Blato 12, Baška Voda; gradinabaskavoda@gmail.com.",
]

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


def check(label, rows, rules, year, problems):
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
                continue
            days, nth = weekdays(whole[0])
            lo, hi = (len(days) * len(nth),) * 2 if nth else (4 * len(days), 5 * len(days))
            lo -= sum(1 for h in pravila.blagdani(year) if h.month == m)
            if not lo <= n <= hi:
                problems.append(f"{label}: {c} u {m}. mjesecu {n} puta (očekivano {lo}–{hi})")


def text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def cell_rule(t, winter):
    """'PON – SRI – PET' -> 'pon sri pet'; 'SVAKI DAN' -> 'svaki dan' ('pon-sub' in winter); 'PRVA I TREĆA SRI U
    MJESECU' -> '1. 3. sri'. None if not understood."""
    t = " ".join(t.upper().split())
    if t == "SVAKI DAN":
        return "pon-sub" if winter else "svaki dan"
    if t == "SVAKI DAN OSIM NEDJELJE":
        return "pon-sub"
    m = re.fullmatch(r"(\w+) I (\w+) (\w+) U MJESECU", t)
    if m:
        if m.group(1) in NTH and m.group(2) in NTH and m.group(3) in DAYS:
            return f"{NTH[m.group(1)]} {NTH[m.group(2)]} {DAYS[m.group(3)]}"
        return None
    words = [w for w in re.split(r"\s*[–-]\s*", t) if w]
    return " ".join(DAYS[w] for w in words) if words and all(w in DAYS for w in words) else None


def read_place(slug, problems):
    """(columns, {column: {code: [(rule, ranges)]}}, {column: streets text}) of one place page."""
    page = fetch(f"{SITE}/{slug}/").decode("utf-8", "replace")
    chunks = re.split(r'<h3 class="sppb-addon-title">', page)[1:]
    cols, rules, streets = None, {}, {}
    for chunk in chunks:
        title = text(chunk[:chunk.find("</h3>")])
        ranges = [(f"{a}.{b}.", f"{c}.{d}.") for a, b, c, d in
                  re.findall(r"(\d\d)\.(\d\d)\.\s*[–-]\s*(\d\d)\.(\d\d)\.", title)]
        table = re.search(r"<table.*?</table>", chunk, re.S)
        if not ranges or not table:
            problems.append(f"{slug}: odlomak bez razdoblja ili tablice: {title!r}")
            continue
        winter = "nedjeljom" in text(chunk[:table.start()])
        rows = [[text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                for tr in re.findall(r"<tr[^>]*>.*?</tr>", table.group(0), re.S)]
        head = rows[0][1:]
        if cols is None and head != ["Sve ulice"]:
            cols = head
        for row in rows[1:]:
            code = TYPES.get(row[0].lower())
            if not code or len(row) != len(head) + 1:
                problems.append(f"{slug}: redak {row}")
                continue
            for col, value in zip(head, row[1:]):
                rule = cell_rule(value, winter)
                if not rule:
                    problems.append(f"{slug} {title}: nepoznato pravilo {value!r}")
                    continue
                for target in (cols or [col]) if col == "Sve ulice" else [col]:
                    rules.setdefault(target, {}).setdefault(code, []).append((rule, ranges))
        for p in re.findall(r"<p[^>]*>(.*?)</p>", chunk[table.end():], re.S):
            m = re.fullmatch(r"ULICE ([A-C]):\s*(.+)", text(p))
            if m:
                streets[f"Ulice {m.group(1)}"] = m.group(2).rstrip(".")
    if not cols:
        cols = ["Sve ulice"]
    return cols, rules, streets


def say(rule):
    """'pon sri pet' -> 'ponedjeljak, srijeda, petak'; 'pon-sub' -> 'ponedjeljak – subota'."""
    if rule == "pon-sub":
        return "ponedjeljak – subota"
    return ", ".join(DAN[DANI.index(k)] for k in rule.split())


def street_list(desc, place):
    """'Tri ceste; Fra A. K. Miošića; ...' -> list; 'Sve ulice osim ...' -> ['<place> (ostale ulice)']."""
    if desc.lower().startswith("sve"):
        return [f"{place} (ostale ulice)"]
    return [s.strip() for s in desc.split(";") if s.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    index = fetch(PAGE).decode("utf-8", "replace")
    found = [s for s in PLACES if f'href="{SITE}/{s}/"' in index]
    if found != list(PLACES):
        problems.append(f"na {PAGE} nisu sve stranice mjesta: {found}")
    overrides = OVERRIDES.get(year, [])
    regular = HOLIDAY_REGULAR.get(year, {})
    hol = set(pravila.blagdani(year))
    second_days = {pravila.uskrs(year) + timedelta(days=1), date(year, 12, 26)}
    winter = spans(year, [("15.10.", "30.04.")])
    zones = {}
    for slug in found:
        place = PLACES[slug]
        cols, rules, streets = read_place(slug, problems)
        for col in cols:
            r = rules.get(col, {})
            days = sum((hi - lo).days + 1 for _, rg in r.get("M", []) for lo, hi in spans(year, rg))
            if days != (366 if calendar.isleap(year) else 365) or set(r) != set("MKPS"):
                problems.append(f"{place} {col}: razdoblja ne pokrivaju godinu ({days} dana) ili nedostaje vrsta {set(r)}")
                continue
            z = str(len(zones) + 1)
            dates = collect(year, r, problems, f"zona {z}")
            for link, code, rule, a, b in overrides:
                (lo, hi), = spans(year, [(a, b)])
                for d in list(dates):
                    if lo <= d <= hi:
                        dates[d] = dates[d].replace(code, "")
                for d in rule_dates(year, rule):
                    if lo <= d <= hi and code not in dates.get(d, ""):
                        dates[d] = dates.get(d, "") + code
                r = {**r, code: r[code] + [(rule, [(a, b)])]}
            dropped = []
            for d in sorted(dates):
                if d in hol and dates[d] and d not in regular \
                        and not (d in second_days and any(lo <= d <= hi for lo, hi in winter)):
                    dropped.append(f"{d:%d.%m.} {dates[d]}")
                    dates[d] = ""
            rows = sorted((d, c, False) for d, c in dates.items() if c)
            check(f"zona {z}", rows, r, year, problems)
            name = place if col == "Sve ulice" else f"{place} – " + (f"ulice {col[-1]}" if col.startswith("Ulice") else col.capitalize())
            ulice = street_list(streets[col], place) if col in streets else [name.split(" – ")[-1] if col != "Sve ulice" else place]
            winter_m = next(rule for rule, rg in r["M"] if ("15.10.", "30.04.") in rg)
            short = ", ".join(ulice[:3]) + (" …" if len(ulice) > 3 else "")
            zones[z] = {"jls": "Baška Voda", "podrucje": f"{name}: {short}" if col in streets else name,
                        "ulice": ulice,
                        "napomena": f"Zimi (15.10. – 30.4.) miješani otpad: {say(winter_m)}; "
                                    "predsezona i ljeto prema rasporedu za cijelo mjesto.",
                        "raw": {str(year): podaci.month_lines(rows)}}
            if col in streets:
                zones[z]["opis"] = f"{col}: {streets[col]}"
            print(f"Zona {z} ({name}): " + ", ".join(f"{c} {n}" for c, n in sorted(
                Counter(c for _, cs, _ in rows for c in cs).items())) + (f"; bez odvoza (blagdan): {', '.join(dropped)}"
                                                                         if dropped else ""))
    print("Pretpostavka: blagdanom nema odvoza ni zamjene (osim objavljenih iznimaka i drugog dana Uskrsa/Božića zimi).")
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "napomene": NAPOMENE, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
