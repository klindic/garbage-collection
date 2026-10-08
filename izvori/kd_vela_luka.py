"""Komunalne djelatnosti d.o.o. Vela Luka: Općina Vela Luka (individual bins, shared containers and port, coves).

    python3 -m izvori.kd_vela_luka [--year 2026]

The page "Raspored odvoza otpada" gives the rules as text, read here with regular expressions: winter
(1.1.-31.5. and 1.10.-31.12.) mixed waste on Monday, Wednesday and Friday, in the coves on Tuesday; summer
(1.6.-30.9.) shared containers and the port every day, individual bins Monday, Wednesday and Friday, the
coves Tuesday, Thursday and Saturday; all year paper on the first Tuesday and plastic on the first Thursday
of the month at the user's address. Holidays: the company announces each shift in a news post ("odvoz ...
umjesto u petak 1. svibnja 2026. vršiti u subotu 2. svibnja"); the posts are read through the WordPress
REST API and applied as moved dates. Holidays without a notice keep the regular date (printed and noted).
"""
import argparse
import calendar
import html
import json
import re
import sys
from collections import Counter
from datetime import date

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch, plain

SLUG = "kd-vela-luka"
SITE = "https://komunalne-djelatnosti.hr"
PAGE = SITE + "/raspored-odvoza-otpada/"
POSTS = SITE + "/wp-json/wp/v2/posts?search=umjesto&per_page=100&after={after}&_fields=id,date,link,content"
MONTHS = {"siječnja": 1, "veljače": 2, "ožujka": 3, "travnja": 4, "svibnja": 5, "lipnja": 6, "srpnja": 7,
          "kolovoza": 8, "rujna": 9, "listopada": 10, "studenoga": 11, "studenog": 11, "prosinca": 12}
DAY_WORDS = {"ponedjeljkom": "pon", "utorkom": "uto", "srijedom": "sri", "četvrtkom": "čet", "petkom": "pet",
             "subotom": "sub", "nedjeljom": "ned", "utorak": "uto", "četvrtak": "čet"}
NOTICE_TYPES = {"miješanogkomunalnogotpada": "M", "otpadneplastike": "P", "otpadnogpapira": "K"}
PROVIDER = {
    "davatelj": "Komunalne djelatnosti d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Vela Luka"],
    "nazivi": {"K": "Papir (plavi spremnik)", "P": "Plastika (žuti spremnik)"},
}
NAPOMENE = [
    "Papir (prvi utorak u mjesecu) i plastika (prvi četvrtak u mjesecu) odvoze se s obračunskog mjesta korisnika "
    "koji imaju plave i žute spremnike; spremnici na zelenim otocima prazne se ponedjeljkom, srijedom i petkom.",
    "Ostali reciklabilni otpad odlaže se na zelenim otocima i lokacijama za odvojeno prikupljanje (pražnjenje "
    "ponedjeljkom, srijedom i petkom).",
    "Na lučkom području odvoz je od 07.00 do 08.00 sati.",
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


def days(text):
    """'ponedjeljkom, srijedom i petkom' -> 'pon sri pet'; 'svaki dan' stays."""
    text = text.strip().lower()
    if text == "svaki dan":
        return text
    words = re.split(r",\s*|\s+i\s+", text)
    if not words or any(w not in DAY_WORDS for w in words):
        raise ValueError(text)
    return " ".join(DAY_WORDS[w] for w in words)


def page_rules(problems):
    """{zone key: {code: [(rule, ranges)]}} from the page text."""
    page = fetch(PAGE).decode("utf-8", "replace")
    paras = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)).split())
             for p in re.findall(r"<p[ >].*?</p>", page, re.S)]
    sections, cur = {}, None
    head = re.compile(r"Od (\d{1,2})\.\s*(\w+) do (\d{1,2})\.\s*(\w+)(?: [–-] (\d{1,2})\.\s*(\w+) do (\d{1,2})\.\s*(\w+))?")
    for p in paras:
        m = head.fullmatch(p)
        if m:
            g = [x for x in m.groups() if x]
            if any(not x.isdigit() and x not in MONTHS for x in g):
                problems.append(f"nepoznat naslov razdoblja: {p!r}")
                continue
            cur = tuple((f"{int(g[i]):02}.{MONTHS[g[i + 1]]:02}.", f"{int(g[i + 2]):02}.{MONTHS[g[i + 3]]:02}.")
                        for i in range(0, len(g), 4))
            sections[cur] = []
        elif cur:
            sections[cur].append(p)
    want = {(("01.01.", "31.05."), ("01.10.", "31.12.")): "zima", (("01.06.", "30.09."),): "ljeto",
            (("01.01.", "31.12."),): "godina"}
    if set(sections) != set(want):
        problems.append(f"razdoblja na stranici: {list(sections)}")
        return None
    text = {want[k]: " ".join(v) for k, v in sections.items()}
    ranges = {want[k]: list(k) for k in sections}

    def find(season, pattern):
        m = re.search(pattern, text[season])
        if not m:
            problems.append(f"{season}: nema rečenice {pattern!r}")
            return None
        try:
            return days(m.group(1))
        except ValueError as e:
            problems.append(f"{season}: nepoznati dani {e}")
            return None

    both = r"\(na zajedničkim spremnicima i kod korisnika koji posjeduju individualne spremnike\): (.+?)\."
    r = {
        "zima_svi": find("zima", r"^Miješani komunalni otpad " + both),
        "zima_luka": find("zima", r"Na lučkom području se odvoz obavlja (.+?) od 07"),
        "zima_uvale": find("zima", r"Uvale: miješani komunalni otpad " + both),
        "ljeto_zajed": find("ljeto", r"Miješani komunalni otpad na zajedničkim spremnicima: (.+?)\."),
        "ljeto_luka": find("ljeto", r"Na lučkom području se odvoz obavlja (.+?) od 07"),
        "ljeto_indiv": find("ljeto", r"Miješani komunalni otpad kod korisnika koji posjeduju individualne spremnike: (.+?)\."),
        "ljeto_uvale": find("ljeto", r"Uvale: miješani komunalni otpad " + both),
        "papir": find("godina", r"Svaki prvi (\w+) u mjesecu: otpadni papir"),
        "plastika": find("godina", r"Svaki prvi (\w+) u mjesecu: otpadna plastika"),
    }
    if None in r.values():
        return None
    if r["zima_luka"] != r["zima_svi"] or r["ljeto_luka"] != r["ljeto_zajed"]:
        problems.append(f"lučko područje ima drukčije dane od zajedničkih spremnika: {r}")
    print("Pravila sa stranice: " + "; ".join(f"{k} {v}" for k, v in r.items()))
    zima, ljeto, god = ranges["zima"], ranges["ljeto"], ranges["godina"]
    recycling = {"K": [("1. " + r["papir"], god)], "P": [("1. " + r["plastika"], god)]}
    return {
        "indiv": {"M": [(r["zima_svi"], zima), (r["ljeto_indiv"], ljeto)], **recycling},
        "zajed": {"M": [(r["zima_svi"], zima), (r["ljeto_zajed"], ljeto)]},
        "uvale": {"M": [(r["zima_uvale"], zima), (r["ljeto_uvale"], ljeto)], **recycling},
    }


def notices(year, problems):
    """[(code, coves only, old date, new date, link)] from the company's news posts."""
    pat = re.compile(r"odvoz(" + "|".join(NOTICE_TYPES) + r")(izuvala)?(?:ćese)?umjestou[a-zčćšžđ]+?"
                     r"(\d{1,2})\.([a-zčćšžđ]+)(\d{4})\.g\.?,?(?:ćese)?vršitiu[a-zčćšžđ]+?(\d{1,2})\.([a-zčćšžđ]+)(\d{4})")
    out = []
    for p in json.loads(fetch(POSTS.format(after=f"{year - 1}-10-01T00:00:00"))):
        text = re.sub(r"\s+", "", plain(p["content"]["rendered"]).lower())
        for typ, coves, d1, m1, y1, d2, m2, y2 in pat.findall(text):
            if m1 not in MONTHS or m2 not in MONTHS:
                problems.append(f"nepoznat mjesec u obavijesti {p['link']}")
                continue
            old, new = date(int(y1), MONTHS[m1], int(d1)), date(int(y2), MONTHS[m2], int(d2))
            if old.year == year or new.year == year:
                out.append((NOTICE_TYPES[typ], bool(coves), old, new, p["link"]))
        if "umjesto" in text and "vršiti" in text and not pat.search(text):
            problems.append(f"obavijest nije pročitana: {p['link']}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    rules = page_rules(problems)
    moves = notices(year, problems)
    if problems or not rules:
        for p in problems:
            print("   PROBLEM", p)
        sys.exit("Ništa nije upisano.")
    zones_def = [
        ("indiv", "Vela Luka – korisnici s individualnim spremnicima",
         ["Vela Luka (individualni spremnici)"], None),
        ("zajed", "Vela Luka – zajednički spremnici i lučko područje",
         ["Vela Luka (zajednički spremnici)", "Lučko područje"],
         "Ljeti (1.6. – 30.9.) zajednički spremnici i lučko područje prazne se svaki dan."),
        ("uvale", "Uvale Općine Vela Luka",
         ["Uvale (Vela Luka)"],
         "Uvale: zimi utorkom, ljeti (1.6. – 30.9.) utorkom, četvrtkom i subotom (zajednički i individualni spremnici)."),
    ]
    used = set()
    zones = {}
    hol = [h for h in pravila.blagdani(year) if h.weekday() < 6]
    for z, (key, podrucje, ulice, note) in enumerate(zones_def, 1):
        dates = collect(year, rules[key], problems, f"zona {z}")
        moved = set()
        for code, coves, old, new, link in moves:
            if coves and key != "uvale" or code not in dates.get(old, ""):
                continue
            dates[old] = dates[old].replace(code, "")
            if code in dates.get(new, ""):
                problems.append(f"zona {z}: pomak {old} -> {new} na dan koji već ima {code}")
            dates[new] = dates.get(new, "") + code
            moved.add(new)
            used.add((code, old, new))
            print(f"Zona {z}: {code} {old:%d.%m.} -> {new:%d.%m.} ({link})")
        rows = sorted((d, c, d in moved) for d, c in dates.items() if c)
        check(f"zona {z}", rows, rules[key], year, problems)
        for h in hol:
            if h in dates and dates[h] and h not in moved:
                print(f"Zona {z}: blagdan {h:%d.%m.} ({dates[h]}) bez obavijesti – zadržan redovni odvoz")
        zones[str(z)] = {"jls": "Vela Luka", "podrucje": podrucje, "ulice": ulice}
        if note:
            zones[str(z)]["napomena"] = note
        zones[str(z)]["raw"] = {str(year): podaci.month_lines(rows)}
        print(f"Zona {z} ({podrucje}): " + ", ".join(f"{c} {n}" for c, n in sorted(
            Counter(c for _, cs, _ in rows for c in cs).items())))
    for code, coves, old, new, link in moves:
        if (code, old, new) not in used:
            print(f"Obavijest {code} {old} -> {new} ne odgovara ni jednom upisanom datumu ({link})")
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    applied = sorted({(old, new) for _, _, old, new, _ in moves if any(u[1:] == (old, new) for u in used)})
    holiday = ("Blagdani: pomaci se objavljuju kao obavijesti na komunalne-djelatnosti.hr; upisani su objavljeni "
               "pomaci (" + ", ".join(f"{o:%d.%m.} → {n:%d.%m.}" for o, n in applied) + "), a za blagdane bez "
               "obavijesti zadržani su redovni dani.")
    podaci.save(SLUG, {**PROVIDER, "napomene": NAPOMENE + [holiday], "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
