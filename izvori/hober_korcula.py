"""KTD Hober d.o.o. (Korčula): Grad Korčula, six groups of settlements, from 1.2.2026.

    python3 -m izvori.hober_korcula [--year 2026]

The page "Gospodarenje otpadom" links the household schedule as one JPG ("Raspored odvoza otpada",
2026.-raspored-od-1.2.jpg): a 6 x 3 table of rules (mixed waste on two weekdays, paper on the 1st and 3rd,
plastic on the 2nd and 4th Saturday or Wednesday of the month). The table is transcribed by hand below; the
script finds the image on the page, checks its sha256 and stops ("slika se promijenila") when it changes.
The start date comes from the file name (od 1.2.); earlier dates of the year already in podaci/<slug>.json are
kept. The second image on the page is the summer schedule for restaurants (zones A and B), not households.
No holiday rule is published: the dates stay on their weekdays and a note says so.
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

SLUG = "hober-korcula"
SITE = "https://hober.hr"
PAGE = SITE + "/djelatnost/gospodarenje-otpadom/"
LINK_TEXT = "Raspored odvoza otpada"
IMAGE_SHA256 = "ce5c8cbf9bf811152b869e22e75a6005c84aa0f44c120c3709af27b5f64e5769"  # 2026.-raspored-od-1.2.jpg
ALL = [("01.01.", "31.12.")]
SAT = {"M": [("uto pet", ALL)], "K": [("1. 3. sub", ALL)], "P": [("2. 4. sub", ALL)]}
ZONES = [  # (podrucje as in the image, places for search, rules); M uto+pet / pon+čet, K and P per row
    ("Korčula: Biline, Borak, Sv. Nikola, Cvjetno naselje", ["Biline", "Borak", "Sv. Nikola", "Cvjetno naselje"],
     SAT),
    ("Korčula: Sv. Antun, Zagradac, Dominče, Ekonomija", ["Sv. Antun", "Zagradac", "Dominče", "Ekonomija"],
     {"M": [("pon čet", ALL)], "K": [("1. 3. sri", ALL)], "P": [("2. 4. sri", ALL)]}),
    ("Žrnovo: Prvo Selo, Brdo, Kampuš", ["Žrnovo", "Prvo Selo", "Brdo", "Kampuš"], SAT),
    ("Račišće, Kneže, Tri Žala, Žrnovska Banja, Medvinjak, Strečica",
     ["Račišće", "Kneže", "Tri Žala", "Žrnovska Banja", "Medvinjak", "Strečica"],
     {"M": [("uto pet", ALL)], "K": [("1. 3. sri", ALL)], "P": [("2. 4. sri", ALL)]}),
    ("Čara, Zavalatica, Pupnat, Babina, Rasoha", ["Čara", "Zavalatica", "Pupnat", "Babina", "Rasoha"],
     {"M": [("pon čet", ALL)], "K": [("1. 3. sri", ALL)], "P": [("2. 4. sri", ALL)]}),
    ("Postrana", ["Postrana"], {"M": [("uto pet", ALL)], "K": [("1. 3. sri", ALL)], "P": [("2. 4. sri", ALL)]}),
]
PROVIDER = {
    "davatelj": "KTD Hober d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Korčula"],
    "nazivi": {"K": "Papir i karton (plavi spremnik)", "P": "Plastika (žuti spremnik)"},
    "napomene": [
        "Stari grad, Biline te dio Borka, Sv. Nikole, Zagradca i Sv. Antuna djelomično koriste zajedničke "
        "spremnike s otpadomjerima (čip kartice); raspored vrijedi za individualne spremnike.",
        "Staklo i ostali otpad: Reciklažno dvorište Lokva (otvoreno u veljači 2026.); biootpad u kućnim komposterima.",
        "Raspored odvoza za ugostitelje u sezoni (zone A i B) objavljen je zasebno i nije ovdje upisan.",
        "Pomaci zbog blagdana nisu objavljeni; upisani su redovni dani odvoza.",
        "Krupni otpad: besplatan odvoz na poziv, 020/711-506 ili 099/700-5145.",
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


def collect(year, rules, start, problems, label):
    """{code: [(rule, ranges)]} -> {date: codes}, each rule inside its date ranges and from `start`."""
    out = {}
    for code, items in rules.items():
        for rule, ranges in items:
            sp = spans(year, ranges)
            for d in rule_dates(year, rule):
                if d >= start and any(lo <= d <= hi for lo, hi in sp):
                    if code in out.get(d, ""):
                        problems.append(f"{label}: {code} dvaput {d}")
                    out[d] = out.get(d, "") + code
    return out


def check(label, rows, rules, year, start, problems):
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
    hol = pravila.blagdani(year)
    for m in range(start.month if start.year == year else 1, 13):
        first, last = date(year, m, 1), date(year, m, calendar.monthrange(year, m)[1])
        for c, items in rules.items():
            n = sum(1 for d, codes, _ in rows if d.month == m and c in codes)
            whole = [r for r, rg in items if any(lo <= first and last <= hi for lo, hi in spans(year, rg))]
            if len(whole) != 1 or first < start:
                continue
            days, nth = weekdays(whole[0])
            lo, hi = (len(days) * len(nth),) * 2 if nth else (4 * len(days), 5 * len(days))
            lo -= sum(1 for h in hol if h.month == m)
            if not lo <= n <= hi:
                problems.append(f"{label}: {c} u {m}. mjesecu {n} puta (očekivano {lo}–{hi})")


def merge(old_zone, rows, start, end):
    """raw by year: the new rows plus the old dates outside start..end."""
    keep = [r for r in podaci.iter_dates(old_zone) if not start <= r[0] <= end] if old_zone else []
    years = {}
    for r in keep + rows:
        years.setdefault(r[0].year, []).append(r)
    return {str(y): podaci.month_lines(v) for y, v in sorted(years.items())}


def schedule_image():
    """(url, start date) of the household schedule image linked from the page."""
    page = fetch(PAGE).decode("utf-8", "replace")
    for href, text in re.findall(r'<a[^>]+href="([^"]+\.(?:jpe?g|png))"[^>]*>(.*?)</a>', page, re.S):
        if " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split()) == LINK_TEXT:
            m = re.search(r"(\d{4})\.-raspored-od-(\d{1,2})\.(\d{1,2})", href)
            if not m:
                sys.exit(f"Ne prepoznajem datum početka u nazivu slike: {href}")
            return href, date(int(m.group(1)), int(m.group(3)), int(m.group(2)))
    sys.exit(f"Na {PAGE} nema poveznice '{LINK_TEXT}'.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    url, valid_from = schedule_image()
    digest = hashlib.sha256(fetch(url)).hexdigest()
    if digest != IMAGE_SHA256:
        sys.exit(f"Slika se promijenila, raspored treba ponovno prepisati: {url} (sha256 {digest})")
    if year < valid_from.year:
        sys.exit(f"Raspored vrijedi od {valid_from:%d.%m.%Y}.")
    start, end = max(valid_from, date(year, 1, 1)), date(year, 12, 31)
    print(f"Slika {url}: raspored od {valid_from:%d.%m.%Y}, upisujem {start:%d.%m.%Y} – {end:%d.%m.%Y}")
    print("Pretpostavka: blagdani bez pomaka (pravilo nije objavljeno).")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    problems, zones = [], {}
    for z, (podrucje, places, rules) in enumerate(ZONES, 1):
        dates = collect(year, rules, start, problems, f"zona {z}")
        rows = sorted((d, c, False) for d, c in dates.items())
        check(f"zona {z}", rows, rules, year, start, problems)
        prev = old.get(str(z))
        zones[str(z)] = {"jls": "Korčula", "podrucje": podrucje, "ulice": places,
                         "raw": merge(prev if prev and prev.get("ulice") == places else None, rows, start, end)}
        print(f"Zona {z} ({podrucje}): " + ", ".join(f"{c} {n}" for c, n in sorted(
            Counter(c for _, cs, _ in rows for c in cs).items())))
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    napomene = PROVIDER["napomene"] + [
        f"Raspored vrijedi od {valid_from:%d.%m.%Y}.; ranije razdoblje nije objavljeno pa nije upisano."]
    podaci.save(SLUG, {**PROVIDER, "napomene": napomene, "zone": zones})
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
