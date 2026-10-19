"""Krublić d.o.o.: Općina Smokvica (Smokvica and Blaca, Brna and Vinačac, restaurants and the hotel), month by month.

    python3 -m izvori.krublic [--year 2026]

The page "Kalendar odvoza" shows only the current month ("rujan 2026."): one block per day with the
weekday and date ("Srijeda<br>2. 9."), the group ("Smokvica Blaca", "Brna Vinačac", "Sve" for everyone,
"Restorani Hotel") and one bin icon per waste type (kanta_zelena = mixed, kanta_zuta = plastic,
kanta_plava = paper). Every block is checked (weekday, month, known group and icon). Holidays are part of
the daily list. Because the page is overwritten every month, the dates already in podaci/<slug>.json for
other months are kept and the shown month replaces its own month, so running this every month builds up
the year. The rules text on the same page ("Redovni odvoz otpada") does not match the calendar and is not
used.
"""
import argparse
import re
import sys
from collections import defaultdict
from datetime import date

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "krublic"
SITE = "https://krublic.hr"
PAGE = SITE + "/kalendar-odvoza/"
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
DAYS = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
ICONS = {"zelena": "M", "zuta": "P", "plava": "K"}
GROUPS = {"smokvica blaca": ["1"], "brna vinačac": ["2"], "restorani hotel": ["3"], "sve": ["1", "2", "3"]}
ZONES = {
    "1": ("Smokvica, Blaca", ["Smokvica", "Blaca"], ""),
    "2": ("Brna, Vinačac", ["Brna", "Vinačac"], ""),
    "3": ("Restorani i hotel", ["Restorani", "Hotel"], "Raspored za restorane i hotel na području općine."),
}
PROVIDER = {
    "davatelj": "Krublić d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Smokvica"],
    "nazivi": {"M": "Miješani otpad (zelena kanta)", "P": "Plastika (žuta kanta)", "K": "Papir (plava kanta)"},
    "napomene": [
        "Krublić na webu objavljuje samo raspored za tekući mjesec; ovdje su upisani mjeseci koji su bili "
        "objavljeni kad je skripta pokrenuta.",
        "Pomaci zbog blagdana uključeni su u mjesečni raspored.",
        "Ljeti se otpad odvozi od 6 sati ujutro.",
        "Zeleni otoci prazne se srijedom (Smokvica i Brna).",
    ],
}


def text(fragment):
    return " ".join(re.sub(r"<[^>]+>", " ", fragment.replace("<br>", " ")).split())


def blocks(page):
    """(month heading, [(day header, group, [icon colours])]) from the calendar page."""
    head = re.search(r"<h3[^>]*>\s*(\w+) (\d{4})\.\s*</h3>", page)
    out = []
    for part in re.split(r"(?=<h6[^>]*has-background[^>]*>)", page)[1:]:
        h6 = re.findall(r"<h6[^>]*>(.*?)</h6>", part, re.S)
        if len(h6) < 2:
            continue
        out.append((text(h6[0]), text(h6[1]), re.findall(r'src="[^"]*/kanta_(\w+?)_ico\.png"', part)))
    return head, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    head, days = blocks(fetch(PAGE).decode("utf-8", "replace"))
    if not head or head.group(1).lower() not in MONTHS:
        sys.exit(f"Na {PAGE} nema naslova mjeseca.")
    month, shown_year = MONTHS.index(head.group(1).lower()) + 1, int(head.group(2))
    if shown_year != year:
        sys.exit(f"Stranica prikazuje {head.group(1)} {shown_year}., a traži se {year}.")
    problems = []
    rows = {z: defaultdict(str) for z in ZONES}
    seen = set()
    for header, group, icons in days:
        m = re.fullmatch(r"(\w+) (\d{1,2})\. (\d{1,2})\.", header)
        if not m:
            problems.append(f"zaglavlje dana {header!r}")
            continue
        d = date(year, int(m.group(3)), int(m.group(2)))
        name = m.group(1).lower().replace("ponedeljak", "ponedjeljak")
        if d.month != month or DAYS[d.weekday()] != name:
            problems.append(f"{header}: nije {name} u mjesecu {head.group(1)}")
        zones = GROUPS.get(group.lower())
        codes = [ICONS.get(i) for i in icons]
        if zones is None or not codes or None in codes:
            problems.append(f"{header}: nepoznata skupina {group!r} ili ikona {icons}")
            continue
        for z in zones:
            for c in codes:
                if c in rows[z][d]:
                    problems.append(f"{header} zona {z}: {c} dvaput")
                rows[z][d] += c
        seen.add(d)
    last = (date(year + (month == 12), month % 12 + 1, 1) - date(year, month, 1)).days
    if len(seen) < 20 or max(seen, default=date(year, month, 1)).day < last - 3:
        problems.append(f"prikazano je samo {len(seen)} dana mjeseca")
    for z in ("1", "2"):
        n = sum("M" in c for c in rows[z].values())
        if not 4 <= n <= 14:
            problems.append(f"zona {z}: {n} odvoza miješanog otpada u mjesecu")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, (podrucje, streets, note) in ZONES.items():
        keep = [r for r in podaci.iter_dates(old[z]) if (r[0].year, r[0].month) != (year, month)] if z in old else []
        new = [(d, c, False) for d, c in rows[z].items()]
        years = defaultdict(list)
        for r in keep + new:
            years[r[0].year].append(r)
        zone = {"jls": "Smokvica", "podrucje": podrucje, "ulice": streets}
        if note:
            zone["napomena"] = note
        zone["raw"] = {str(y): podaci.month_lines(v) for y, v in sorted(years.items())}
        data["zone"][z] = zone
        months = sorted({(r[0].year, r[0].month) for r in keep + new})
        print(f"Zona {z} ({podrucje}): {len(new)} odvoza u {month}/{year}; mjeseci u podacima: "
              + ", ".join(f"{m}/{y}" for y, m in months))
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
