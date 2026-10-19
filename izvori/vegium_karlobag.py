"""Karlobag: Vegium d.o.o. Karlobag (vegium.hr), standing winter and summer rules for mixed waste.

    python3 -m izvori.vegium_karlobag [--year 2026]

The page "Odvoz komunalnog otpada Karlobag" has two HTML tables, "Zimski raspored od 01.10.-31.05." and
"Ljetni raspored od 01.06.-30.09.": one column per weekday (Pon - Sub), the settlements collected that day in
the cells ("Karlobag cijeli" is the whole town, "Karlobag centar" the centre only). The rules have no
year, so they are applied to the requested year. Settlements with the same days in both seasons form a
zone. Only mixed waste is published; nothing is said about holidays, so the computed days are kept.
"""
import argparse
import html as htmllib
import re
import sys
from datetime import date, timedelta

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "vegium-karlobag"
SITE = "https://www.vegium.hr"
PAGE = SITE + "/odvoz_komunalnog_otpada_karlobag.php"
DAYS = ["Pon", "Uto", "Sri", "Čet", "Pet", "Sub"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
FIXES = {"Kučišta": "Kućišta", "Ledenik Kućišta": "Ledenik, Kućišta"}
TOWN = {"Karlobag cijeli": ["Karlobag (centar)", "Karlobag (izvan centra)"], "Karlobag centar": ["Karlobag (centar)"]}
PROVIDER = {
    "davatelj": "Vegium d.o.o. Karlobag",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Ličko-senjska",
    "jls": ["Karlobag"],
}
NAPOMENE = [
    "Objavljen je samo raspored odvoza miješanog komunalnog otpada (zimski od 1.10. do 31.5., ljetni od 1.6. do 30.9.); "
    "raspored nema godinu pa vrijedi dok se ne promijeni.",
    "Pomaci zbog blagdana nisu objavljeni; datumi su izračunati po danima u tjednu.",
    "Krupni (glomazni) otpad: vidi stranicu \"Odvoz krupnog otpada\" na vegium.hr. Vegium: 053 694 017, "
    "vegium.karlobag@gmail.com.",
]


def text(fragment):
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def read_tables(page, problems):
    """{'zima'|'ljeto': (first (month, day), last (month, day), {place: {weekday}})}."""
    out = {}
    for m in re.finditer(r'imCellStyleTitle_\d+">(.*?)</div>(.*?</table>)', page, re.S):
        title, table = text(m.group(1)), m.group(2)
        t = re.match(r"(Zimski|Ljetni) raspored od (\d{2})\.(\d{2})\.\s*-\s*(\d{2})\.(\d{2})\.", title)
        if not t:
            problems.append(f"nepoznat naslov tablice: {title!r}")
            continue
        rows = [[text(td) for td in re.findall(r"<td.*?</td>", tr, re.S)] for tr in re.findall(r"<tr.*?</tr>", table, re.S)]
        if not rows or rows[0] != DAYS:
            problems.append(f"{title}: zaglavlje {rows[:1]}")
            continue
        days = {}
        for row in rows[1:]:
            for wd, cell in enumerate(row):
                for a, b in FIXES.items():
                    cell = cell.replace(a, b)
                if cell in ("", "/"):
                    continue
                for place in TOWN.get(cell, [p.strip() for p in cell.split(",")]):
                    days.setdefault(place, set()).add(wd)
        season = "zima" if t.group(1) == "Zimski" else "ljeto"
        out[season] = ((int(t.group(3)), int(t.group(2))), (int(t.group(5)), int(t.group(4))), days)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    tables = read_tables(fetch(PAGE).decode("utf-8", "replace"), problems)
    if set(tables) != {"zima", "ljeto"}:
        problems.append(f"tablice: {sorted(tables)}")
    else:
        (w1, w2, winter), (s1, s2, summer) = tables["zima"], tables["ljeto"]
        if (w1, w2, s1, s2) != ((10, 1), (5, 31), (6, 1), (9, 30)):
            problems.append(f"razdoblja se promijenila: zima {w1}–{w2}, ljeto {s1}–{s2}")
        if set(winter) != set(summer):
            problems.append(f"naselja se razlikuju: {sorted(set(winter) ^ set(summer))}")
    if problems:
        for p in problems:
            print(f"   PROBLEM {p}")
        sys.exit("Ništa nije upisano.")

    groups = {}
    for place in winter:  # keep the order of the tables
        groups.setdefault((tuple(sorted(winter[place])), tuple(sorted(summer[place]))), []).append(place)
    zones = {}
    for (wd_w, wd_s), places in groups.items():
        rows, d = [], date(year, 1, 1)
        while d.year == year:
            summer_day = (6, 1) <= (d.month, d.day) <= (9, 30)
            if d.weekday() in (wd_s if summer_day else wd_w):
                rows.append((d, "M", False))
            d += timedelta(days=1)
        for mo in range(1, 13):
            n = sum(1 for r in rows if r[0].month == mo)
            per_week = len(wd_s if 6 <= mo <= 9 else wd_w)
            if not 4 * per_week <= n <= 5 * per_week + 1:
                problems.append(f"{places[0]}: {n} odvoza u {mo}. mjesecu")
        names = lambda wds: ", ".join(DAN[w] for w in wds[:-1]) + (" i " if len(wds) > 1 else "") + DAN[wds[-1]]
        when = f"zimi {names(wd_w)}, ljeti {names(wd_s)}"
        zones[str(len(zones) + 1)] = {
            "jls": "Karlobag", "podrucje": f"{', '.join(places)} – {when}", "ulice": places,
            "raw": {str(year): podaci.month_lines(rows)}}
        print(f"Zona {len(zones)}: {', '.join(places)} – {when}")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "napomene": NAPOMENE, "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years of zones that did not change
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
