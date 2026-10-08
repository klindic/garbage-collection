"""EKO d.o.o. (Blato): Općina Blato, mixed waste by area (mostly shared 1100 l containers), off-season and summer.

    python3 -m izvori.eko_blato [--year 2026]

The page "Prikupljanje otpada" lists, for two seasons ("Od 1. siječnja do 31. lipnja, te od 24. rujna do 31.
prosinca" and "Od 1. srpnja do 23. rujna"), the places and companies served on each weekday. The script reads
the paragraphs "Dan: place, place, ...", keeps the settlements and parts of Blato (companies are recognised by
name and skipped; an unknown name stops the script), and turns the weekdays on which each area appears into
weekly rules per season ("Blato komplet" covers every part of Blato, "osim Vele Strane" leaves Vela Strana out).
"31. lipnja" on the page is read as 30 June. Paper bins at home have no published schedule. Holidays: in the
summer season collection runs on holidays as scheduled (as the page says); for the rest of the year nothing is
published, so the regular dates are kept and a note says so.
"""
import argparse
import calendar
import html
import re
import sys
from collections import Counter, defaultdict
from datetime import date

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "eko-blato"
SITE = "https://eko-blato.hr"
PAGE = SITE + "/pr-ot"
MONTHS = {"siječnja": 1, "veljače": 2, "ožujka": 3, "travnja": 4, "svibnja": 5, "lipnja": 6, "srpnja": 7,
          "kolovoza": 8, "rujna": 9, "listopada": 10, "studenoga": 11, "studenog": 11, "prosinca": 12}
DAY_NAMES = {"Ponedjeljak": "pon", "Utorak": "uto", "Srijeda": "sri", "Četvrtak": "čet", "Petak": "pet",
             "Subota": "sub", "Nedjelja": "ned"}
BLATO = ["centar", "uzi", "ostalo", "vela"]  # parts of Blato covered by "Blato komplet"
PLACES = [  # (regex on a normalised name, zone keys)
    (r"(centar blata|blato centar)", ["centar", "uzi"]),
    (r"blato uži centar", ["uzi"]),
    (r"blato komplet osim vele strane", ["centar", "uzi", "ostalo"]),
    (r"blato komplet", BLATO),
    (r"vela strana", ["vela"]),
    (r"(prigradica|črnja luka|prigradica črnja luka)", ["prigradica"]),
    (r"(gršćica|prižba|prišćapac)", ["grscica"]),
    (r"vinačac", ["vinacac"]),
    (r"(karbuni|zaglav|karbuni zaglav)", ["karbuni"]),
    (r"(potirna|nova|garma nova)", ["potirna"]),
    (r"(žukova|naplovac)", ["zukova"]),
    (r"(popovratak|kurija)", ["popovratak"]),
]
COMPANY = re.compile(r"d\.d|d\.o\.o|obrt|pekar|uljara|konzum|hep|velpro|commerce|bura 1|dom za|groblje|autotrans|"
                     r"sajeta|dunja|globo|trikop|s\.t\.p|dubljević|pinbor|radež|vinarija|ljekarna|ekonomija|"
                     r"markota|mariner|babić|pena|karbon|ana$")
ZONES = [  # key, podrucje, places for search
    ("centar", "Blato – centar", ["Blato – centar"]),
    ("uzi", "Blato – uži centar", ["Blato – uži centar"]),
    ("vela", "Blato – Vela Strana", ["Vela Strana"]),
    ("ostalo", "Blato – ostali dijelovi naselja", ["Blato"]),
    ("potirna", "Potirna i Nova (Garma)", ["Potirna", "Nova", "Garma"]),
    ("karbuni", "Karbuni i Zaglav", ["Karbuni", "Zaglav"]),
    ("prigradica", "Prigradica i Črnja Luka", ["Prigradica", "Črnja Luka"]),
    ("zukova", "Žukova i Naplovac", ["Žukova", "Naplovac"]),
    ("popovratak", "Popovratak i Kurija", ["Popovratak", "Kurija"]),
    ("grscica", "Gršćica, Prižba i Prišćapac", ["Gršćica", "Prižba", "Prišćapac"]),
    ("vinacac", "Vinačac", ["Vinačac"]),
]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "EKO d.o.o. (Blato)",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Blato"],
    "napomene": [
        "Miješani komunalni otpad prikuplja se uglavnom iz zajedničkih spremnika od 1100 litara; upisani su dani "
        "pražnjenja spremnika na pojedinom području.",
        "U predsezoni i posezoni učestalost odvoza prilagođava se popunjenosti smještajnih kapaciteta "
        "(odvoz se pojačava ili smanjuje po potrebi).",
        "Ljeti se otpad odvozi i blagdanima prema rasporedu; za ostatak godine pravilo za blagdane nije objavljeno, "
        "pa su upisani redovni dani.",
        "Papir i karton (vlastiti spremnici) nemaju objavljen raspored; ambalaža na zelenim otocima.",
        "Glomazni i ostali otpad: reciklažno dvorište Krtinja (pon/sri/pet 7–15, uto/čet 11–19), bez naknade.",
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


def day_month(d, mname, problems):
    """'31', 'lipnja' -> '30.06.' (a day past the month end, like '31. lipnja', becomes the last day)."""
    m = MONTHS.get(mname)
    if not m:
        problems.append(f"nepoznat mjesec {mname!r}")
        return "01.01."
    last = calendar.monthrange(2026, m)[1] if m != 2 else 28
    if int(d) > last:
        print(f"Napomena: '{d}. {mname}' ne postoji, čitam kao {last}.{m:02}.")
    return f"{min(int(d), last):02}.{m:02}."


def names(text):
    """'centar Blata, Euro Karbon - Konzum, ...' -> normalised names (dashes and dots as spaces)."""
    out = []
    for part in re.split(r",\s*|(?<!Sv)\.\s+(?=[A-ZČĆŠŽĐ])", text.strip().rstrip(".")):
        part = re.sub(r"\s*[–-]\s*", " ", part).strip().lower()
        if part:
            out.append(" ".join(part.split()))
    return out


def page_rules(problems):
    """({zone key: [(weekdays, ranges)]}, season ranges) from the page."""
    page = fetch(PAGE).decode("utf-8", "replace")
    paras = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)).split())
             for p in re.findall(r"<p[ >].*?</p>", page, re.S)]
    season, seasons = None, {}
    head = re.compile(r"Od (\d{1,2})\. (\w+) do (\d{1,2})\. (\w+)(?:, te od (\d{1,2})\. (\w+) do (\d{1,2})\. (\w+))?\s*\.?")
    for p in paras:
        m = head.fullmatch(p)
        if m:
            g = [x for x in m.groups() if x]
            season = tuple((day_month(g[i], g[i + 1], problems), day_month(g[i + 2], g[i + 3], problems))
                           for i in range(0, len(g), 4))
            seasons[season] = defaultdict(set)
            continue
        m = re.fullmatch(r"(\w+):\s*(.+)", p)
        if season and m and m.group(1) in DAY_NAMES:
            day = DAY_NAMES[m.group(1)]
            for n in names(m.group(2)):
                hit = next((keys for pat, keys in PLACES if re.fullmatch(pat, n)), None)
                if hit:
                    for k in hit:
                        seasons[season][k].add(day)
                elif not COMPANY.search(n):
                    problems.append(f"nepoznat naziv na stranici: {n!r} ({m.group(1)})")
        elif season and p.startswith("Napomena"):
            season = None
    if len(seasons) != 2:
        problems.append(f"na stranici {len(seasons)} razdoblja umjesto 2")
    days_in = sum((hi - lo).days + 1 for s in seasons for lo, hi in spans(2026, s))
    if days_in != 365:
        problems.append(f"razdoblja ne pokrivaju godinu točno jednom: {list(seasons)} ({days_in} dana)")
    rules = {k: [(" ".join(sorted(ds[k], key=DANI.index)), list(s)) for s, ds in seasons.items() if ds.get(k)]
             for k, *_ in ZONES}
    for k, items in rules.items():
        if len(items) != len(seasons):
            problems.append(f"područje {k} nema dane u svim razdobljima: {items}")
    return rules, list(seasons)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    rules, seasons = page_rules(problems)
    if problems:
        for p in problems:
            print("   PROBLEM", p)
        sys.exit("Ništa nije upisano.")
    summer = next(s for s in seasons if len(s) == 1)
    print("Razdoblja: " + "; ".join(" i ".join(f"{a}–{b}" for a, b in s) for s in seasons))
    print("Pretpostavka: izvan ljeta blagdani bez pomaka (pravilo nije objavljeno).")
    zones = {}
    for z, (key, podrucje, places) in enumerate(ZONES, 1):
        rules_z = {"M": rules[key]}
        dates = collect(year, rules_z, problems, f"zona {z}")
        rows = sorted((d, c, False) for d, c in dates.items())
        check(f"zona {z}", rows, rules_z, year, problems)
        desc = "; ".join(("ljeto " if tuple(s) == summer else "ostatak godine ")
                         + ", ".join(DAN[DANI.index(k)] for k in r.split()) for r, s in rules[key])
        zones[str(z)] = {"jls": "Blato", "podrucje": podrucje, "ulice": places,
                         "napomena": f"Miješani otpad – {desc} (ljeto {summer[0][0]} – {summer[0][1]}).",
                         "raw": {str(year): podaci.month_lines(rows)}}
        print(f"Zona {z} ({podrucje}): {desc}; M {len(rows)}")
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
