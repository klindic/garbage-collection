"""Vir: Čisti otok d.o.o. (cistiotok.hr), six zones of streets, weekly rules with a summer season for mixed waste.

    python3 -m izvori.cisti_otok_vir [--year 2026]

The page "Raspored rada po zonama – interaktivna karta" gives the rule of every zone in one line
("ZONA 1 - Ponedjeljak (PA+PL+MKO) / 15.06 - 15.09: Ponedjeljak i Četvrtak (MKO)"): mixed waste (MKO),
paper (PA) and plastic (PL) once a week on the zone's day, and from 15.06. to 15.09. mixed waste twice a
week on the two summer days instead, while paper and plastic stay on the zone's day all year. The street
lists come from the site's event calendar (WordPress REST, post type mec-events): one event per zone and
waste type, titled like the rule line, with the streets one per line; the mixed-waste and paper/plastic
events of a zone must list the same streets and agree with the page. Paper and plastic go out on the same
day and are written as "PK". No holiday rule is published, so the dates stay as computed.
"""
import argparse
import html
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cisti-otok-vir"
SITE = "https://cistiotok.hr"
PAGE = SITE + "/raspored-rada-po-zonama-interaktivna-karta/"
EVENTS = SITE + "/wp-json/wp/v2/mec-events?per_page=100&_fields=id,modified,title,content"
DAYS = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
INSTR = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
DAY = "|".join(d.capitalize() for d in DAYS)
RULE = re.compile(rf"ZONA (\d) - ({DAY}) \(PA\+PL\+MKO\) / (\d\d)\.(\d\d) - (\d\d)\.(\d\d): ({DAY}) i ({DAY}) \(MKO\)")
TITLE = re.compile(rf"(MKO|PA \+ PL) – ZONA (\d) – ({DAY})(?: \((\d\d)\.(\d\d) – (\d\d)\.(\d\d): ({DAY}) i ({DAY})\))?")
SEASON_TEXT = "U ljetnim mjesecima od 15.06. – 15.09."
PROVIDER = {
    "davatelj": "Čisti otok d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zadarska",
    "jls": ["Vir"],
    "nazivi": {"P": "Plastika (PL)", "K": "Papir (PA)"},
}
NAPOMENE = [
    "Miješani komunalni otpad (MKO) te papir i plastika (PA+PL) odvoze se jednom tjedno na dan zone; od 15.06. do "
    "15.09. MKO se odvozi dvaput tjedno (zone 1 i 2 ponedjeljkom i četvrtkom, zone 3 i 4 utorkom i petkom, "
    "zone 5 i 6 srijedom i subotom), a papir i plastika i dalje jednom tjedno na dan zone.",
    "Glomazni otpad: na zahtjev e-poštom (vidi cistiotok.hr).",
    "Pomaci odvoza zbog blagdana nisu objavljeni; datumi su izračunati iz pravila.",
]


def plain(fragment):
    t = html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").replace("–", "-")
    return " ".join(t.split())


def nice(name):
    """'BANA JOSIPA JELAČIĆA' -> 'Bana Josipa Jelačića'; Roman numerals and mixed-case parts stay as they are."""
    return re.sub(r"[A-ZČĆŠŽĐ]{2,}", lambda m: m.group(0) if re.fullmatch(r"[IVXL]+", m.group(0))
                  else m.group(0).capitalize(), name)


def streets(content):
    items = [html.unescape(re.sub(r"<[^>]+>", "", x)).replace("\xa0", " ").strip()
             for x in re.split(r"<br\s*/?>|</?p>", content)]
    return [" ".join(x.split()) for x in items if x.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    page = plain(fetch(PAGE).decode("utf-8", "replace"))
    if SEASON_TEXT.replace("–", "-") not in page:
        problems.append(f"na stranici nema rečenice {SEASON_TEXT!r}")
    rules = {}
    for z, day, d0, m0, d1, m1, s1, s2 in RULE.findall(page):
        rules[z] = (DAYS.index(day.lower()), ((int(m0), int(d0)), (int(m1), int(d1))),
                    [DAYS.index(s1.lower()), DAYS.index(s2.lower())])
    if sorted(rules) != [str(i) for i in range(1, 7)]:
        problems.append(f"pravila zona na stranici: {sorted(rules)}")
    events = json.loads(fetch(EVENTS))
    lists = {}
    for e in events:
        title = html.unescape(e["title"]["rendered"])
        m = TITLE.fullmatch(" ".join(title.split()))
        if not m:
            problems.append(f"događaj {e['id']}: ne razumijem naslov {title!r}")
            continue
        kind, z, day = m.group(1), m.group(2), DAYS.index(m.group(3).lower())
        if z not in rules or rules[z][0] != day:
            problems.append(f"događaj {title!r} ne odgovara pravilu na stranici")
        if kind == "MKO":
            season = ((int(m.group(5)), int(m.group(4))), (int(m.group(7)), int(m.group(6))))
            summer = [DAYS.index(m.group(8).lower()), DAYS.index(m.group(9).lower())]
            if z in rules and (season, summer) != rules[z][1:]:
                problems.append(f"događaj {title!r}: ljetni raspored ne odgovara stranici")
        if (kind, z) in lists:
            problems.append(f"zona {z}: dva događaja {kind}")
        lists[kind, z] = streets(e["content"]["rendered"])
    names = Counter()
    for z in rules:
        a, b = lists.get(("MKO", z)), lists.get(("PA + PL", z))
        if not a or a != b:
            problems.append(f"zona {z}: popisi ulica za MKO i PA+PL nisu jednaki")
        names.update(a or [])
    for n, c in names.items():
        if c > 1:
            problems.append(f"ulica {n} je u {c} zone")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": NAPOMENE, "zone": {}}
    for z in sorted(rules):
        day, ((m0, d0), (m1, d1)), summer = rules[z]
        s0, s1 = date(year, m0, d0), date(year, m1, d1)
        rows, d = [], date(year, 1, 1)
        while d.year == year:
            codes = ""
            if d.weekday() in (summer if s0 <= d <= s1 else [day]):
                codes += "M"
            if d.weekday() == day:
                codes += "PK"
            if codes:
                rows.append((d, codes, False))
            d += timedelta(days=1)
        per = Counter((c, d.month) for d, codes, _ in rows for c in codes)
        for (c, m), n in per.items():
            if not (4 <= n <= 5 if c != "M" or m in (1, 2, 3, 4, 5, 10, 11, 12) else 4 <= n <= 10):
                problems.append(f"zona {z}: {c} {n} puta u mjesecu {m}")
        ulice = [nice(s) for s in lists.get(("MKO", z), [])]
        season = f"{d0}.{m0}.–{d1}.{m1}."
        zone = {
            "jls": "Vir",
            "podrucje": f"Zona {z} – {DAYS[day]} (ljeti {season} MKO {INSTR[summer[0]]} i {INSTR[summer[1]]}) – "
                        + ", ".join(ulice[:3]) + " …",
            "ulice": ulice,
            "napomena": f"MKO {INSTR[day]}, ljeti ({season}) {INSTR[summer[0]]} i {INSTR[summer[1]]}; papir i "
                        f"plastika {INSTR[day]} cijele godine.",
        }
        prev = old["zone"].get(z, {})
        keep = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("ulice") == ulice else {}
        zone["raw"] = {**keep, str(year): podaci.month_lines(rows)}
        data["zone"][z] = zone
        print(f"Zona {z}: {len(ulice)} ulica, {dict(Counter(c for _, codes, _ in rows for c in codes))}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
