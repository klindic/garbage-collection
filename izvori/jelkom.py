"""JELKOM d.o.o. (Jelsa): Općina Jelsa, nine groups of settlements, winter and summer schedule.

    python3 -m izvori.jelkom [--year 2026]

JELKOM has no website; Općina Jelsa publishes the schedule as a news post ("JELKOM d.o.o. – Raspored odvoza za
2026.g. MKO i vrećice sa reciklažom", found through the WordPress REST API of jelsa.hr) with two Word files, one
per season ("ODVOZ MKO: 1. STUDENI – 30. TRAVANJ." and "ODVOZ MKO 01. SVIBANJ – 30. LISTOPAD"). Each file has a
table (settlements | weekdays of mixed waste) and two lines for the recyclables in bags (yellow, blue, green)
once a week ("IVAN DOLAC I ZAVALA: PONEDJELJAK", "SVI OSTALI: SRIJEDA"). Each season's weekdays apply only
inside its date range (31 October is in neither range and gets no date). No holiday rule is published: the
regular dates are kept and a note says so.
"""
import argparse
import calendar
import html
import json
import re
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "jelkom"
SITE = "https://jelsa.hr"
POSTS = SITE + "/wp-json/wp/v2/posts?search=jelkom%20raspored%20odvoza&per_page=20&_fields=id,date,link,title,content"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
DAYS = {"PONEDJELJAK": "pon", "UTORAK": "uto", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet",
        "SUBOTA": "sub", "NEDJELJA": "ned"}
REST = ("SVI OSTALI", "OSTALA MJESTA")
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "JELKOM d.o.o.",
    "web": SITE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Jelsa"],
    "nazivi": {"P": "Plastika, metal i tetrapak (žuta vrećica)", "K": "Papir i karton (plava vrećica)",
               "S": "Staklo (zelena vrećica)"},
}
NAPOMENE = [
    "JELKOM d.o.o. nema vlastite stranice; raspored objavljuje Općina Jelsa (jelsa.hr).",
    "Reciklabilni otpad u vrećicama (žuta: PET, metal i tetrapak; plava: papir i karton; zelena: staklo) "
    "odlaže se na mjesto predviđeno za spremnik miješanog otpada; odvoz od 9 sati. Vrećice su besplatne "
    "u lokalnom dućanu i na reciklažnom dvorištu.",
    "Ljetni raspored vrijedi 1.5. – 30.10., zimski 1.11. – 30.4.; 31.10. nije obuhvaćen ni jednim rasporedom "
    "pa za taj dan nema upisanog odvoza.",
    "Pomaci zbog blagdana nisu objavljeni; upisani su redovni dani odvoza.",
    "Zajednički spremnici s otpadomjerima (do 20 l): parkiralište Soline, Jelsa; u Staroj Zavali kraj dućana.",
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


def day_list(text, problems, where):
    """'PONEDJELJAK, SRIJEDA, PETAK' / 'UTORAK I SUBOTA' -> 'pon sri pet'."""
    words = [w for w in re.split(r"\s*,\s*|\s+I\s+", " ".join(text.split()).strip(" .")) if w]
    if not words or any(w not in DAYS for w in words):
        problems.append(f"{where}: nepoznati dani {text!r}")
        return None
    return " ".join(DAYS[w] for w in words)


def read_docx(path, problems):
    """(ranges, [(name, places in brackets, weekdays)], [(names or REST, weekday)]) from one season file."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    text = lambda part: html.unescape("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", part)))
    tables = re.findall(r"<w:tbl>.*?</w:tbl>", xml, re.S)
    paras = [" ".join(text(p).split()) for p in
             re.findall(r"<w:p[ >].*?</w:p>", re.sub(r"<w:tbl>.*?</w:tbl>", "", xml, flags=re.S), re.S)]
    paras = [p for p in paras if p]
    head = next((re.search(r"ODVOZ MKO:? (\d{1,2})\. (\w+)\s*[–-]\s*(\d{1,2})\. (\w+)", p) for p in paras
                 if p.startswith("ODVOZ MKO")), None)
    if not head or head.group(2) not in MONTHS or head.group(4) not in MONTHS or len(tables) != 1:
        problems.append(f"{path.name}: nema razdoblja ili tablice ({paras[:2]})")
        return None, [], []
    rng = [(f"{int(head.group(1)):02}.{MONTHS.index(head.group(2)) + 1:02}.",
            f"{int(head.group(3)):02}.{MONTHS.index(head.group(4)) + 1:02}.")]
    rows = []
    for tr in re.findall(r"<w:tr[ >].*?</w:tr>", tables[0], re.S):
        cells = [[" ".join(text(p).split()) for p in re.findall(r"<w:p[ >].*?</w:p>", tc, re.S)]
                 for tc in re.findall(r"<w:tc>.*?</w:tc>", tr, re.S)]
        cells = [[p for p in c if p] for c in cells]
        if len(cells) != 2 or not cells[0] or len(cells[1]) != 1:
            problems.append(f"{path.name}: redak tablice {cells}")
            continue
        name, extra = cells[0][0], " ".join(cells[0][1:])
        places = [p.strip() for p in extra.strip("()").split(",")] if extra else []
        rows.append((name, places, day_list(cells[1][0], problems, f"{path.name} {name}")))
    bags = []
    after = paras[paras.index(next(p for p in paras if p.startswith("ODVOZ RECIKLA"))) + 1:] \
        if any(p.startswith("ODVOZ RECIKLA") for p in paras) else []
    for p in after:
        m = re.fullmatch(r"-?\s*(.+?)\s*:\s*(\w+)", p)
        if not m:
            problems.append(f"{path.name}: redak reciklaže {p!r}")
            continue
        bags.append((m.group(1), day_list(m.group(2), problems, f"{path.name} {p}")))
    if not bags:
        problems.append(f"{path.name}: nema rasporeda vrećica")
    return rng, rows, bags


def label(name):
    """'JELSA SJEVER' -> 'Jelsa – sjever', 'POLJICA, GDINJ, ZASTRAŽIŠĆE' -> 'Poljica, Gdinj, Zastražišće'."""
    words = " ".join("i" if w == "I" else w.capitalize() for w in name.split())
    return re.sub(r"^Jelsa (Sjever|Jug)$", lambda m: f"Jelsa – {m.group(1).lower()}", words)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    posts = [p for p in json.loads(fetch(POSTS)) if f"{year}" in html.unescape(p["title"]["rendered"])
             and "raspored odvoza" in html.unescape(p["title"]["rendered"]).lower()]
    if not posts:
        sys.exit(f"Na jelsa.hr nema objave JELKOM-a s rasporedom za {year}.")
    post = max(posts, key=lambda p: p["date"])
    links = [u if u.startswith("http") else SITE + u
             for u in re.findall(r'href="([^"]+\.docx)"', post["content"]["rendered"])]
    print(f"Objava {post['link']} ({post['date'][:10]}): {len(links)} datoteke")
    seasons = []
    with tempfile.TemporaryDirectory() as tmp:
        for url in links:
            path = Path(tmp) / url.rsplit("/", 1)[1]
            fetch(url, path)
            rng, rows, bags = read_docx(path, problems)
            if rng:
                seasons.append((rng, rows, bags, url))
                print(f"{path.name}: razdoblje {rng[0][0]}–{rng[0][1]}, {len(rows)} redaka, vrećice {bags}")
    if len(seasons) != 2:
        problems.append(f"{len(seasons)} rasporeda umjesto 2")
    names = [[r[0] for r in s[1]] for s in seasons]
    if len(seasons) == 2 and names[0] != names[1]:
        problems.append(f"popisi naselja u dva rasporeda se razlikuju: {names}")
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    zones = {}
    for z, name in enumerate(names[0], 1):
        rules = {"M": [], "PKS": []}
        places = []
        for rng, rows, bags, _ in seasons:
            _, places, mko = next(r for r in rows if r[0] == name)
            rules["M"].append((mko, rng))
            named = {n: d for who, d in bags if who not in REST for n in [who] + who.split(" I ")}
            rest = [d for who, d in bags if who in REST]
            bag_day = named.get(name, rest[0] if rest else None)
            if not bag_day:
                problems.append(f"{name}: nema dana za vrećice")
                continue
            rules["PKS"].append((bag_day, rng))
        dates = collect(year, rules, problems, f"zona {z}")
        rows = sorted((d, c, False) for d, c in dates.items())
        check(f"zona {z}", rows, {"M": rules["M"], **{c: rules["PKS"] for c in "PKS"}}, year, problems)
        desc = "; ".join(f"{a}–{b}: " + ", ".join(DAN[DANI.index(k)] for k in r.split())
                         for r, ((a, b),) in rules["M"])
        bags_desc = "; ".join(f"{a}–{b}: " + DAN[DANI.index(r)] for r, ((a, b),) in rules["PKS"])
        base = [w.capitalize() for w in re.split(r",\s*|\s+I\s+", name) if w not in ("SJEVER", "JUG")]
        base = ["Jelsa"] if name.startswith("JELSA") else base
        zones[str(z)] = {"jls": "Jelsa", "podrucje": label(name), "ulice": base + places,
                         "napomena": f"Miješani otpad – {desc}. Vrećice s reciklažom – {bags_desc}.",
                         "raw": {str(year): podaci.month_lines(rows)}}
        print(f"Zona {z} ({label(name)}): M {desc}; vrećice {bags_desc}; "
              + ", ".join(f"{c} {n}" for c, n in sorted(Counter(c for _, cs, _ in rows for c in cs).items())))
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "izvor": post["link"], "napomene": NAPOMENE, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
