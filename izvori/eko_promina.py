"""Promina: EKO Promina d.o.o. (eko-promina.hr), year colour calendar for two groups of settlements.

    python3 -m izvori.eko_promina [--year 2026]

The page "Prikupljanje otpada" links the leaflet "Obavijest o sakupljanju komunalnog otpada u <year>.
godini". Page 2 has a 12-month calendar (week number, P U S Č P S N) whose collection days are filled
cells: green mixed waste, yellow plastic and metal, blue paper; beside it the rules say which weekday
serves which settlements (mixed waste Monday / Thursday, plastic and paper Tuesday / Wednesday). The
calendar is read like kalendar_boje.read_page (same month blocks and weekday columns), but invisible
white leftover numbers are dropped and a misprinted day number (July 2026 shows "26" on Friday 24) is
corrected from its row neighbours and reported. Each coloured day goes to the group of its weekday; a
day on another weekday is a holiday shift (marked as moved) and goes to the group whose day was the
public holiday just before it. Holidays are therefore built into the calendar.
"""
import argparse
import calendar
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import LONG, MONTHS, colour, lookup, weekday_rows

SLUG = "eko-promina"
SITE = "https://www.eko-promina.hr"
PAGE = SITE + "/usluge/prikupljanje-otpada"
PALETTE = {(0.0, 0.508, 0.215): "M", (0.99, 0.919, 0.062): "P", (0.094, 0.293, 0.608): "K"}
BINS = {"Zeleni": "M", "Žuti": "P", "Plavi": "K"}
DAYS = {"ponedjeljkom": 0, "utorkom": 1, "srijedom": 2, "četvrtkom": 3, "petkom": 4, "subotom": 5}
PROVIDER = {
    "davatelj": "EKO Promina d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Promina"],
    "bioNapomena": "Biootpad se kompostira u kućnim komposterima (besplatni komposter za korisnike).",
}
NAPOMENE = [
    "Pomaci zbog blagdana ucrtani su u kalendar (označeni kao pomaknuti).",
    "Staklo: zeleni spremnik na javnoj površini u svakom od 11 naselja.",
    "Glomazni otpad jednom godišnje do 2 m³ na zahtjev; ostalo na mobilnom reciklažnom dvorištu.",
    "Kontakt: 022/881-046, kontakt@eko-promina.hr.",
]


def read_calendar(page, year):
    """({date: codes}, notes, problems, text of the column right of the calendar): like
    kalendar_boje.read_page, without invisible white text and with a single misprinted day number
    corrected from the other numbers of the same week row."""
    cells = [s for s in page.rects if s.get("fill") and colour(s) and 20 <= s["x1"] - s["x0"] <= 40
             and 7 <= s["bottom"] - s["top"] <= 14]

    def cell(w):
        mx, my = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        return next((s for s in cells if s["x0"] <= mx <= s["x1"] and s["top"] <= my <= s["bottom"]
                     and colour(s) != (1.0, 1.0, 1.0)), None)

    # white text is visible only on a coloured cell; elsewhere it is a leftover ("6 30 31" under January)
    words = [w for w in page.extract_words(extra_attrs=["non_stroking_color"])
             if colour({"non_stroking_color": w["non_stroking_color"]}) != (1.0, 1.0, 1.0) or cell(w)]
    months = {w["text"].upper(): w for w in words if w["text"].upper() in MONTHS}
    if len(months) != 12:
        return {}, [], [f"naslovi mjeseci {sorted(months)}"], ""
    # weekday_rows finds a row twice when its letters sit 2-3 pt apart in height
    rows = list({id(g[0]): g for g in weekday_rows(words)}.values())
    found, notes, problems, seen = {}, [], [], Counter()
    for name, h in months.items():
        month = MONTHS.index(name) + 1
        below = [g for g in rows if 0 <= g[0]["top"] - h["bottom"] < 40
                 and g[0]["x0"] - 20 <= (h["x0"] + h["x1"]) / 2 <= g[-1]["x1"] + 20]
        if len(below) != 1:
            problems.append(f"{name}: {len(below)} redaka s danima ispod naslova")
            continue
        g = below[0]
        nxt = min((m["top"] for m in months.values() if m["top"] > g[0]["bottom"]
                   and g[0]["x0"] - 20 <= (m["x0"] + m["x1"]) / 2 <= g[-1]["x1"] + 20), default=page.height)
        cx = [(w["x0"] + w["x1"]) / 2 for w in g]
        digits = [w for w in words if w["text"].isdigit() and g[0]["bottom"] < w["top"] < nxt
                  and g[0]["x0"] - 8 <= (w["x0"] + w["x1"]) / 2 <= g[-1]["x1"] + 8]
        lines = {}
        for w in digits:
            key = next((k for k in lines if abs(k - w["top"]) < 2), w["top"])
            lines.setdefault(key, []).append(w)
        for line in lines.values():
            cols = [(min(range(7), key=lambda i: abs(cx[i] - (w["x0"] + w["x1"]) / 2)), w) for w in line]
            base = Counter(int(w["text"]) - c for c, w in cols).most_common(1)[0][0]
            for c, w in cols:
                day = int(w["text"])
                if day - c != base:
                    notes.append(f"{name}: tiskana je {day}, a po redu tjedna to je {base + c}")
                    day = base + c
                if not 1 <= day <= calendar.monthrange(year, month)[1]:
                    problems.append(f"{name}: nemoguć dan {day}")
                    continue
                d = date(year, month, day)
                seen[d] += 1
                if c != d.weekday():
                    problems.append(f"{d}: u stupcu {LONG[c]}, a to je {LONG[d.weekday()]}")
                under = cell(w)
                if under is None:
                    continue
                code = lookup(colour(under), PALETTE)
                if code == "?":
                    problems.append(f"{d}: nepoznata boja {colour(under)}")
                else:
                    found[d] = code
    for m in range(1, 13):
        for day in range(1, calendar.monthrange(year, m)[1] + 1):
            if seen[date(year, m, day)] != 1:
                problems.append(f"{date(year, m, day)} je u kalendaru {seen[date(year, m, day)]} puta")
    right = max(g[-1]["x1"] for g in rows) + 5
    return found, notes, problems, " ".join(page.crop((right, 0, page.width, page.height)).extract_text().split())


def rules(text):
    """{code: {weekday: [settlements]}} from 'Zeleni spremnik ... preuzima se ponedjeljkom u naseljima: ...'."""
    out = {}
    for bin_, day, places in re.findall(r"(Zeleni|Žuti|Plavi) spremnik za [^:]*?preuzima se (\w+) u naseljima: "
                                        r"([^.]+)\.", text):
        out.setdefault(BINS[bin_], {})[DAYS[day]] = [p.strip() for p in places.split(",")]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    links = [l for l in re.findall(r'href="([^"]+\.pdf)"', html)
             if re.search(rf"sakupljanju_komunalnog_otpada_u_{year}", l, re.I)]
    if not links:
        sys.exit(f"Na {PAGE} nema obavijesti za {year}")
    url = links[0] if links[0].startswith("http") else SITE + "/" + links[0].lstrip("/")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "obavijest.pdf"
        fetch(url, path)
        cal, notes, problems, text = read_calendar(pdfplumber.open(path).pages[1], year)
    rule = rules(text)
    if set(rule) != set("MPK") or any(len(v) != 2 for v in rule.values()):
        problems.append(f"pravila po danima: {rule}")
        rule = {}
    groups = []  # zones: settlements, {code: weekday}
    for code, by_day in rule.items():
        for day, places in by_day.items():
            zone = next((z for z in groups if z[0] == places), None)
            if zone is None:
                zone = (places, {})
                groups.append(zone)
            zone[1][code] = day
    if rule and (len(groups) != 2 or any(set(z[1]) != set("MPK") for z in groups)):
        problems.append(f"skupine naselja se ne poklapaju po vrstama: {groups}")
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year - 1))
    rows = {i: {} for i in range(len(groups))}
    for d, code in sorted(cal.items()):
        owners = [i for i, (_, days) in enumerate(groups) if days.get(code) == d.weekday()]
        moved = False
        if not owners:  # holiday shift: the group whose day was a holiday in the days before
            owners = [i for i, (_, days) in enumerate(groups) for k in range(1, 4)
                      if d - timedelta(days=k) in hol and (d - timedelta(days=k)).weekday() == days.get(code)]
            moved = True
            if len(owners) != 1:
                problems.append(f"{d} {code}: nije na dan nijedne skupine i nema blagdana prije")
                continue
            print(f"   {d:%d.%m.} {code}: pomak zbog blagdana (skupina {owners[0] + 1})")
        for i in owners:
            rows[i][d] = (rows[i].get(d, ("", False))[0] + code, moved)
    for n in notes:
        print(f"   ISPRAVAK {n}")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": list(NAPOMENE), "zone": {}}
    for i, (places, days) in enumerate(groups):
        z = str(i + 1)
        for m in range(1, 13):
            n = Counter(c for d, (codes, _) in rows[i].items() if d.month == m for c in codes)
            if not 2 <= n["M"] <= 5 or not 1 <= n["P"] <= 3 or not 1 <= n["K"] <= 3:
                problems.append(f"zona {z} {year}-{m:02d}: {dict(n)}")
        desc = f"Miješani {podaci.DAYS[days['M']]}, plastika i papir {podaci.DAYS[days['P']]}"
        data["zone"][z] = {
            "jls": "Promina", "podrucje": f"{desc} – {', '.join(places)}", "ulice": places,
            "raw": {**old["zone"].get(z, {}).get("raw", {}),
                    str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows[i].items()])},
        }
        print(f"zona {z} ({', '.join(places)}): {len(rows[i])} dana")
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
