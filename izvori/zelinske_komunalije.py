"""Zelinske komunalije d.o.o.: Grad Sveti Ivan Zelina, zones = settlements with the same weekday rules.

    python3 -m izvori.zelinske_komunalije [--year 2026]

The menu "Komunalni otpad" on zelkom.hr links three Joomla articles with rules, not dates: mixed waste
every week on a weekday per settlement (Monday to Thursday, "Raspored odvoza miješanog komunalnog otpada"),
plastic (yellow bag) on the 1st or 2nd and paper (blue bag) on the 3rd or 4th Monday, Tuesday or Thursday of
the month per list of settlements. Settlements with the same three rules form a zone; the year's dates come
from pravila.py. Holiday rule from the mixed waste page: Friday is reserved for collections that fall on a
public holiday, so such a collection moves to the Friday of the same week (marked as moved). Biowaste is
collected on Fridays on request only (a note, no dates).
"""
import argparse
import html
import re
import sys
from collections import Counter, defaultdict
from datetime import timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "zelinske-komunalije"
SITE = "https://www.zelkom.hr"
MENU = SITE + "/informacije/komunalni-otpad"
PAGES = {"M": "raspored-odvoza-mijesanog", "P": "raspored-odvoza-korisnog-otpada-plastika",
         "K": "raspored-odvoza-korisnog-otpada-papir", "B": "raspored-odvoza-biootpada"}
WEEKDAY = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "ČETVRTAK": 3, "PETAK": 4}
KEY = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Zelinske komunalije d.o.o.",
    "web": SITE,
    "zupanija": "Zagrebačka",
    "jls": ["Sveti Ivan Zelina"],
    "nazivi": {"P": "Plastika i metal (žuta vreća)", "K": "Papir (plava vreća)"},
}
NAPOMENE = [
    "Plastika (žuta vreća) i papir (plava vreća) moraju biti izneseni do 7 sati.",
    "Srijedom se po narudžbi odvoze veće količine folije i najlona te uredskog papira i kartona.",
    "Biootpad se odvozi petkom uz prethodnu najavu na 01 2040 750; datumi nisu u rasporedu.",
    "Zelinske komunalije d.o.o., Katarine Krizmanić 1, Sveti Ivan Zelina; 01 2040 758.",
]


def lines_of(page):
    """Text lines of the Joomla article body."""
    i = page.find('itemprop="articleBody"')
    body = page[i:page.find('class="pager', i) if 'class="pager' in page[i:] else len(page)]
    body = re.sub(r"<br\s*/?>|</p>|</td>|</li>|</h\d>", "\n", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body)).replace("\xa0", " ")
    return [" ".join(l.split()) for l in text.splitlines() if l.strip()]


def canon(name):
    """One spelling per settlement: 'Drenova Donja' -> 'Donja Drenova', 'Sv. Helena' -> 'Sveta Helena'."""
    name = " ".join(name.replace("Sv. ", "Sveta ").split()).strip(" ,.")
    m = re.fullmatch(r"(\w+) (Donj[aei]|Gornj[aei])", name)
    if m and m.group(1) not in ("Orešje",):  # the provider writes Orešje Donje/Gornje the same way everywhere
        name = f"{m.group(2)} {m.group(1)}"
    return name[0].upper() + name[1:].replace(" brijeg", " Brijeg")


def mixed_rules(lines, problems):
    """({settlement: weekday}, holiday rule text) from the mixed waste article."""
    out, day, friday = {}, None, []
    for line in lines:
        if line in WEEKDAY:
            day = WEEKDAY[line]
            continue
        if day is None:
            continue
        if day == 4:
            friday.append(line)
            continue
        for n in line.split(","):
            if n.strip():
                if canon(n) in out:
                    problems.append(f"miješani: {canon(n)} dvaput")
                out[canon(n)] = day
    rule = " ".join(friday)
    if not re.search(r"blagdana.*prebacuje na petak", rule):
        problems.append(f"pravilo za blagdane (petak) se promijenilo: {rule!r}")
    if sorted(set(out.values())) != [0, 1, 2, 3]:
        problems.append(f"miješani: dani {sorted(set(out.values()))}")
    return out, rule


def monthly_rules(lines, word, problems):
    """{settlement: (n, weekday)} from '1. PONEDJELJAK U MJESECU - Plastika' + settlement line."""
    out = {}
    for i, line in enumerate(lines[:-1]):
        m = re.fullmatch(rf"(\d)\. (PONEDJELJAK|UTORAK|SRIJEDA|ČETVRTAK|PETAK) U MJESECU\s*[-–]\s*{word}", line)
        if not m:
            heading = re.match(r"[\d., ]+(PONEDJELJAK|UTORAK|SRIJEDA|ČETVRTAK|PETAK) U MJESECU", line)
            if heading and "narudžbi" not in lines[i + 1]:  # Wednesdays: larger amounts on order only
                problems.append(f"{word}: nepoznat redak {line!r}")
            continue
        for n in lines[i + 1].split(","):
            if n.strip():
                if canon(n) in out:
                    problems.append(f"{word}: {canon(n)} dvaput")
                out[canon(n)] = (int(m.group(1)), WEEKDAY[m.group(2)])
    if len(out) < 20:
        problems.append(f"{word}: samo {len(out)} naselja")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []

    menu = fetch(MENU).decode("utf-8", "replace")
    urls = {}
    for code, part in PAGES.items():
        found = sorted(set(re.findall(rf'href="(/informacije/komunalni-otpad/{part}[^"]*)"', menu)))
        if not found:
            sys.exit(f"U izborniku {MENU} nema stranice {part}")
        urls[code] = SITE + found[-1]
    pages = {code: lines_of(fetch(url).decode("utf-8", "replace")) for code, url in urls.items()}
    mko, holiday_text = mixed_rules(pages["M"], problems)
    plastic = monthly_rules(pages["P"], "Plastika", problems)
    paper = monthly_rules(pages["K"], "Papir", problems)
    if not any("PETKOM" in l and "najavu" in l for l in pages["B"]):
        problems.append(f"biootpad: tekst se promijenio: {pages['B'][:3]}")
    for n in sorted(set(plastic) | set(paper)):
        if n not in mko:
            problems.append(f"{n}: nema dana miješanog otpada")
    for n in sorted(set(plastic) ^ set(paper)):
        problems.append(f"{n}: samo u rasporedu {'plastike' if n in plastic else 'papira'}")
    no_recycling = sorted(n for n in mko if n not in plastic)

    hol = set(pravila.blagdani(year))
    print("Pravilo za blagdane: odvoz na blagdan (pon–čet) prebacuje se na petak istog tjedna; primijenjeno na "
          "miješani otpad, plastiku i papir.")
    groups = defaultdict(list)
    for n, wd in mko.items():
        groups[(wd, plastic.get(n), paper.get(n))].append(n)

    zones, totals = {}, Counter()
    for i, ((wd, p, k), names) in enumerate(sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or (9, 9))), 1):
        rows = [(d, "M") for d in pravila.tjedno(year, KEY[wd])]
        for code, rule in (("P", p), ("K", k)):
            if rule:
                rows += [(d, code) for d in pravila.mjesecno(year, KEY[rule[1]], rule[0])]
        merged = {}
        for d, code in rows:
            new = d + timedelta(days=4 - d.weekday()) if d in hol else d
            if new in hol:
                problems.append(f"zona {i}: petak {new} je i sam blagdan")
            codes, moved = merged.get(new, ("", False))
            if code in codes:
                problems.append(f"zona {i}: {code} dvaput {new}")
            merged[new] = (codes + code, moved or new != d)
        got = Counter(c for codes, _ in merged.values() for c in codes)
        if not 52 <= got["M"] <= 53 or got["P"] != (12 if p else 0) or got["K"] != (12 if k else 0):
            problems.append(f"zona {i}: {dict(got)}")
        totals.update(got)
        desc = f"miješani svaki {DAN[wd]}".replace("svaki srijeda", "svaku srijedu")
        if p:
            desc += f", plastika {p[0]}. i papir {k[0]}. {DAN[p[1]]} u mjesecu" if k and k[1] == p[1] else \
                f", plastika {p[0]}. {DAN[p[1]]}, papir {k[0]}. {DAN[k[1]]} u mjesecu"
        zone = {"jls": "Sveti Ivan Zelina",
                "podrucje": f"{', '.join(names[:3])}{' …' if len(names) > 3 else ''} – {desc}",
                "ulice": names}
        if not p:
            zone["napomena"] = "Naselje nije navedeno u rasporedu odvoza plastike i papira; provjerite kod " \
                               "Zelinskih komunalija (01 2040 758)."
        zone["raw"] = {str(year): podaci.month_lines([(d, c, m) for d, (c, m) in merged.items()])}
        zones[str(i)] = zone

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    moved = sorted(h for h in hol if h.year == year and h.weekday() < 4)
    data = {**PROVIDER, "izvor": urls["M"], "napomene": NAPOMENE + [
        "Blagdani: petak je rezerviran za odvoze koji padaju na državni ili vjerski blagdan (pravilo sa "
        "stranice rasporeda miješanog otpada); takvi odvozi prebačeni su na petak istog tjedna i označeni kao "
        f"pomaknuti ({', '.join(f'{h:%d.%m.}' for h in moved)}). Isto je primijenjeno na plastiku i papir.",
        "Izvori: " + ", ".join(urls[c] for c in "MPKB") + ".",
    ], "zone": zones}
    if no_recycling:
        print(f"Bez rasporeda plastike i papira: {', '.join(no_recycling)}")
    out = podaci.PODACI / f"{SLUG}.json"
    if out.exists():  # keep other years of zones that did not change
        old = podaci.load(out)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    print(f"Zona: {len(zones)}; odvoza {year} (zbroj po zonama): "
          + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {out.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
