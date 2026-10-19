"""Komunalac Petrinja d.o.o.: Petrinja (one zone per weekday), Donji Kukuruzari, Majur; one bin a week.

    python3 -m izvori.komunalac_petrinja [--year 2026]

The page "Raspored odvoza otpada" holds the street finder's data in its JavaScript: `const ULICE = [{naziv,
dan}]` (street or settlement and its weekday), `WEEK_BINS = {1:"blue",2:"green",3:"yellow",4:"green"}` and
`SHIFT_EXCEPTIONS = {"planned": "actual"}` (holiday moves). Every address is emptied once a week on its
weekday; which bin goes is set by the week: getBinColor() counts whole weeks from the Monday of
`cycleStart` (29.12.2025., week 1) to the Monday of the date's week, modulo 4 (dates before the switch use
the old week-of-month rule). This script reproduces that logic in calendar dates and checks that the page
still contains the same expressions. Blue = paper, green = mixed waste, yellow = plastic and metal.
A planned date listed in SHIFT_EXCEPTIONS is moved (marked as moved) and keeps its week's bin.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalac-petrinja"
SITE = "https://www.komunalac-petrinja.hr"
PAGE = SITE + "/raspored-odvoza-otpada/"
DAYS = ["Pon", "Uto", "Sri", "Čet", "Pet"]
KEY = ["pon", "uto", "sri", "čet", "pet"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
BINS = {"blue": "K", "green": "M", "yellow": "P"}
OWN = {"Donji Kukuruzari": "Donji Kukuruzari", "Majur": "Majur"}  # entries that are municipalities of their own
FIX = {"Kralja Tomsilava, Mošćenica": "Kralja Tomislava, Mošćenica"}  # obvious typos
# expressions of the page's date logic that this script reproduces; a change means the logic must be re-read
LOGIC = [r"const cycleStart = new Date\((\d{4}), (\d{1,2}), (\d{1,2})\);",
         r"const switchDate = new Date\((\d{4}), (\d{1,2}), (\d{1,2})\);",
         r"const thisMonday = getMonday\(d\);\s*const startMonday = getMonday\(cycleStart\);",
         r"const diffWeeks = Math\.floor\(diffMs / \(1000 \* 60 \* 60 \* 24 \* 7\)\);",
         r"const cycleWeek = \(\(diffWeeks % 4\) \+ 4\) % 4 \+ 1;\s*return WEEK_BINS\[cycleWeek\];",
         r"return \(d >= switchDate\) \? getBinColorNew\(d\) : getBinColorOld\(d\);",
         r"let weekInMonth = Math\.floor\(diffDays / 7\) \+ 1;\s*if \(weekInMonth > 4\) weekInMonth = 4;",
         r"const color = getBinColor\(w\.monday\);"]
PROVIDER = {
    "davatelj": "Komunalac Petrinja d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Sisačko-moslavačka",
    "jls": ["Petrinja", "Donji Kukuruzari", "Majur"],
    "nazivi": {"M": "Miješani komunalni otpad (zelena kanta)", "K": "Papir i karton (plava kanta)",
               "P": "Miješana ambalaža – plastika i metal (žuta kanta)"},
}


def monday(d):
    return d - timedelta(days=d.weekday())


def bin_colour(planned, week_bins, cycle_start, switch):
    """getBinColor(getMonday(planned)) of the page, in calendar dates."""
    m = monday(planned)
    if m >= switch:  # getBinColorNew: whole weeks between the Mondays, cycle of 4 (1 = first week)
        return week_bins[(m - monday(cycle_start)).days // 7 % 4 + 1]
    return week_bins[min((m - m.replace(day=1)).days // 7 + 1, 4)]  # getBinColorOld: week of the month


def summer_time(year):
    """(first, last) Monday of the weeks the page's JavaScript counts one week short in Croatian local time.

    diffMs between two local midnights is one hour short of whole weeks while summer time is in force
    (last Sunday of March to last Sunday of October), and Math.floor() then drops a week.
    """
    def last_sunday(month):
        d = date(year, month + 1, 1) - timedelta(days=1)
        return d - timedelta(days=(d.weekday() + 1) % 7)
    return last_sunday(3) + timedelta(days=1), last_sunday(10) - timedelta(days=6)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []

    html = fetch(PAGE).decode("utf-8", "replace")
    found = [re.search(p, html) for p in LOGIC]
    for p, m in zip(LOGIC, found):
        if not m:
            problems.append(f"logika na stranici se promijenila, nema: {p}")
    if not all(found):
        for p in problems:
            print(f"   PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    cycle_start = date(int(found[0].group(1)), int(found[0].group(2)) + 1, int(found[0].group(3)))  # JS month 0-11
    switch = date(int(found[1].group(1)), int(found[1].group(2)) + 1, int(found[1].group(3)))
    m = re.search(r"const WEEK_BINS = \{([^}]*)\}", html)
    week_bins = {int(k): v for k, v in re.findall(r'(\d)\s*:\s*"(\w+)"', m.group(1))} if m else {}
    if sorted(week_bins) != [1, 2, 3, 4] or set(week_bins.values()) - set(BINS):
        problems.append(f"WEEK_BINS: {week_bins}")
    m = re.search(r"const SHIFT_EXCEPTIONS = \{([^}]*)\}", html)
    shifts = {date.fromisoformat(a): date.fromisoformat(b)
              for a, b in re.findall(r'"(\d{4}-\d\d-\d\d)"\s*:\s*"(\d{4}-\d\d-\d\d)"', m.group(1))} if m else None
    if shifts is None:
        problems.append("nema SHIFT_EXCEPTIONS")
        shifts = {}
    m = re.search(r"const ULICE = \[(.*?)\];", html, re.S)
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S) if m else ""
    ulice = re.findall(r'\{\s*naziv\s*:\s*"([^"]+)"\s*,\s*dan\s*:\s*"([^"]+)"\s*\}', body)
    if len(ulice) != body.count("naziv") or len(ulice) < 200:
        problems.append(f"ULICE: pročitano {len(ulice)} od {body.count('naziv')}")
    if "Počinje u ponedjeljak " + f"{cycle_start:%d.%m.%Y}." not in " ".join(re.sub(r"<[^>]+>", " ", html).split()):
        problems.append(f"tekst na stranici ne navodi početak ciklusa {cycle_start:%d.%m.%Y}.")
    hol = {h for y in (year - 1, year, year + 1) for h in pravila.blagdani(y)}
    for old, new in shifts.items():
        if old not in hol or abs((new - old).days) > 6 or new.weekday() == 6:
            problems.append(f"pomak {old} -> {new} nije vjerojatan")
    for h in pravila.blagdani(year):
        if h.weekday() < 5 and h not in shifts:
            print(f"Napomena: blagdan {h:%d.%m.%Y.} nema pomaka u SHIFT_EXCEPTIONS (odvoz prema rasporedu).")

    groups = defaultdict(list)
    for naziv, dan in ulice:
        if dan not in DAYS:
            problems.append(f"{naziv}: dan {dan!r}")
            continue
        naziv = FIX.get(naziv, " ".join(naziv.split()))
        jls = OWN.get(naziv, "Petrinja")
        if naziv not in groups[(jls, dan)]:
            groups[(jls, dan)].append(naziv)
    multi = Counter(n for (j, d), ns in groups.items() for n in ns)

    zones, totals = {}, Counter()
    order = sorted(groups, key=lambda k: (PROVIDER["jls"].index(k[0]), DAYS.index(k[1])))
    for i, (jls, dan) in enumerate(order, 1):
        wd = DAYS.index(dan)
        rows = {}
        for planned in pravila.tjedno(year, KEY[wd]):
            code = BINS[bin_colour(planned, week_bins, cycle_start, switch)]
            actual = shifts.get(planned, planned)
            if actual in rows:
                problems.append(f"zona {i}: dva odvoza {actual}")
            rows[actual] = (code, actual != planned)
        if any(d.year != year for d in rows):  # a December shift into the next year would need its own line
            problems.append(f"zona {i}: pomak izvan godine")
        got = Counter(c for c, _ in rows.values())
        per_month = Counter(d.month for d in rows)
        if not 50 <= len(rows) <= 53 or any(not 4 <= n <= 5 for n in per_month.values()):
            problems.append(f"zona {i}: {len(rows)} odvoza, po mjesecima {dict(per_month)}")
        if not (12 <= got["K"] <= 14 and 12 <= got["P"] <= 14 and 25 <= got["M"] <= 27):
            problems.append(f"zona {i}: {dict(got)}")
        for d, (_, moved) in rows.items():
            if not moved and d.weekday() != wd:
                problems.append(f"zona {i}: {d} nije {DAN[wd]}")
        totals.update(got)
        names = groups[(jls, dan)]
        zone = {"jls": jls}
        if jls == "Petrinja":
            short = [n.replace(", Petrinja", "") for n in names]
            zone["podrucje"] = f"{DAN[wd].capitalize()} – {', '.join(short[:4])} …"
        else:
            zone["podrucje"] = f"Općina {jls} – {DAN[wd]}"
            zone["napomena"] = (f"U popisu Komunalca Petrinja navedeno je samo naselje {jls} ({DAN[wd]}); za ostala "
                                f"naselja Općine {jls} provjerite dan odvoza kod Komunalca Petrinja (044 527 440).")
        shared = [n for n in names if multi[n] > 1]
        if shared:
            other = {n: [DAN[DAYS.index(d)] for (j, d), ns in groups.items() if n in ns and d != dan] for n in shared}
            zone["napomena"] = "Ulice podijeljene na više dana (provjerite svoj dio ulice): " + "; ".join(
                f"{n.replace(', Petrinja', '')} (i {', '.join(o)})" for n, o in other.items()) + "."
        zone["ulice"] = names
        zone["raw"] = {str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])}
        zones[str(i)] = zone

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    moved = "; ".join(f"{a:%d.%m.} → {b:%d.%m.}" for a, b in sorted(shifts.items()) if a.year == year)
    dst = summer_time(year)
    data = {**PROVIDER, "napomene": [
        "Svako se kućanstvo prazni jednom tjedno na svoj dan; vrsta otpada ide u četverotjednom ciklusu od "
        f"ponedjeljka {cycle_start:%d.%m.%Y.}: plava kanta (papir) → zelena (miješani otpad) → žuta (plastika i "
        "metal) → zelena.",
        f"Blagdani {year}. (pomaci s tražilice na stranici): {moved or 'nema'}; pomaknuti odvozi označeni su.",
        f"Tražilica na stranici računa tjedne u lokalnom vremenu, pa za tjedne od {dst[0]:%d.%m.} do "
        f"{dst[1] + timedelta(days=6):%d.%m.%Y.} (ljetno vrijeme) prikazuje boju kante prethodnog tjedna ciklusa; "
        "ovaj raspored slijedi objavljeni ciklus. Ako se boja razlikuje, provjerite kod Komunalca Petrinja "
        "(044 527 440).",
        "Reciklažno dvorište: pon–pet 9–16, sub 8–13. Komunalac Petrinja, Ivana Gundulića 14, 044 527 440.",
    ], "zone": zones}
    out = podaci.PODACI / f"{SLUG}.json"
    if out.exists():  # keep other years of zones that did not change
        old = podaci.load(out)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    print(f"Ciklus od {cycle_start}, WEEK_BINS {week_bins}, {len(shifts)} pomaka, {len(ulice)} zapisa ulica")
    print(f"Zona: {len(zones)}; odvoza {year} (zbroj po zonama): "
          + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {out.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
