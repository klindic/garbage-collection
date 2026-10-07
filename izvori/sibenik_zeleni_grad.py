"""Šibenik and Bilice: Zeleni grad Šibenik d.o.o. (zeleni-grad.hr), rules per street and settlement.

    python3 -m izvori.sibenik_zeleni_grad [--year 2026]

The page "Za korisnike" holds two tables: mixed waste per street (street, settlement, local board
"MO / GC", weekdays such as "Ponedjeljak Četvrtak") and useful waste per settlement or quarter
("Drugi utorak u mjesecu": the yellow plastic bin and the blue paper bin on the same day). A street
gets the useful-waste day of its settlement (outside the city) or of its local board (in Šibenik).
Streets with the same pair of rules form a zone. The company publishes no holiday shifts, so the
dates follow the rules on holidays too. Streets served only by underground containers have no
household day and are left out (listed in the notes).
"""
import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "sibenik-zeleni-grad"
SITE = "https://zeleni-grad.hr"
PAGE = SITE + "/za-korisnike/"
PAGE_API = SITE + "/wp-json/wp/v2/pages?slug=za-korisnike&_fields=id,modified"
DAYS = {"ponedjeljak": "pon", "utorak": "uto", "srijeda": "sri", "četvrtak": "čet", "petak": "pet",
        "subota": "sub", "nedjelja": "ned"}
DAN = list(DAYS)
NTH = {"prvi": 1, "prva": 1, "drugi": 2, "druga": 2, "treći": 3, "treća": 3, "četvrti": 4, "četvrta": 4}
UNDERGROUND = "podzemni spremnici"
# rows of the mixed-waste table without a day, known and left out on purpose
NO_DAY = {("OTOK KAKANJ", "Kaprije"): "otočić Kakanj (Kaprije): dan odvoza nije naveden"}
# useful-waste table: spelling differences to the mixed-waste table
ALIAS = {"Dailo Biranj": "Danilo Biranj", "Dubrava": "Dubrava Kod Šibenika"}
# settlements of the mixed-waste table that the useful-waste table does not list (zone note instead)
NO_USEFUL = {"Čvrljevo", "Radonić", "Podine"}
# useful-waste areas without streets of their own in the mixed-waste table (named in the notes)
EXTRA = {"Škopinac", "Rokići"}
SMALL = {"i", "u", "na", "kod", "od", "do", "iz", "uz", "nad", "pod", "za", "niz", "s", "sa", "kroz", "prema"}
MONTHS = {"siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja", "kolovoza",
          "rujna", "listopada", "studenoga", "studenog", "prosinca"}
PROVIDER = {
    "davatelj": "Zeleni grad Šibenik d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Šibenik", "Bilice"],
    "nazivi": {"P": "Plastika", "K": "Papir i karton"},
}
NAPOMENE = [
    "Korisni otpad: žuti spremnik (plastika) i plavi spremnik (papir i karton) odvoze se isti dan, "
    "jednom mjesečno.",
    "Zeleni grad ne objavljuje pomake zbog blagdana: datumi su izračunati prema pravilima i za blagdane "
    "(odvoz kao inače).",
    "Glomazni otpad: jednom godišnje do 3 m³ s kućnog praga besplatno, na poziv 022/332-325.",
    "Reciklažno dvorište Bikarac, Đelalija 12A, Donje Polje, 7–17 h.",
]


def cells(fragment):
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).replace("\xa0", " ").split())
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", fragment, re.S)]


def tables(page):
    """[(header, rows)] of the wpDataTables on the page."""
    out = []
    for t in re.findall(r"<table[^>]*wpDataTable.*?</table>", page, re.S):
        head = cells((re.search(r"<thead.*?</thead>", t, re.S) or [""])[0])
        body = (re.search(r"<tbody.*?</tbody>", t, re.S) or [""])[0]
        out.append((head, [cells(r) for r in re.findall(r"<tr[^>]*>.*?</tr>", body, re.S)]))
    return out


def naslov(name):
    """'ULICA 113. ŠIBENSKE BRIGADE HV-A' -> 'Ulica 113. Šibenske Brigade HV-a'."""
    name = re.sub(r"\.(?=[^\W\d_])", ". ", name)
    words = name.split()
    out = []
    for i, w in enumerate(words):
        if w == "HV-A":
            out.append("HV-a")
        elif re.fullmatch(r"[IVX]+\.?", w) and (w != "I" or i == len(words) - 1):
            out.append(w)
        elif i and (w.lower() in SMALL or w.lower() in MONTHS or w.lower() in ("ulica", "cesta")):
            out.append(w.lower())
        else:
            out.append(re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), w.lower()))
    return " ".join(out)


def mko_rule(text):
    """'Ponedjeljak Četvrtak' -> (('pon', 'čet'), underground?); None if not understood."""
    t = " ".join(text.lower().split())
    under = UNDERGROUND in t
    t = t.replace(UNDERGROUND, "").strip()
    if t == "svaki dan":
        return tuple(DAYS.values()), under
    words = t.split()
    if all(w in DAYS for w in words) and len(set(words)) == len(words):
        return tuple(DAYS[w] for w in words), under
    return None


def useful_rule(text):
    """'Drugi utorak u mjesecu' -> (2, 'uto'); None if not understood."""
    m = re.fullmatch(r"(\w+) (\w+) u mjesecu", " ".join(text.lower().split()))
    if m and m.group(1) in NTH and m.group(2) in DAYS:
        return NTH[m.group(1)], DAYS[m.group(2)]
    return None


def mko_text(days):
    if len(days) == 7:
        return "svaki dan"
    names = [DAN[list(DAYS.values()).index(d)] for d in days]
    return (" i ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else names[0]).capitalize()


def useful_text(rule):
    n, d = rule
    return f"{n}. {DAN[list(DAYS.values()).index(d)]} u mjesecu"


def board(mo):
    """'Baldekin  II  Šibenik' -> 'Baldekin II'; the area name used by the useful-waste table is 'Baldekin'."""
    mo = " ".join(mo.split())
    return re.sub(r"\s*Šibenik$", "", mo)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []

    page = fetch(PAGE).decode("utf-8", "replace")
    try:
        modified = json.loads(fetch(PAGE_API))[0]["modified"][:10]
    except (ValueError, IndexError, KeyError, RuntimeError):
        modified = None
    mko = useful = None
    for head, rows in tables(page):
        if head[:1] == ["Ulica"] and "Dan odvoza" in head:
            mko = [dict(zip(head, r)) for r in rows]
        elif head[:1] == ["Naselje / četvrt"] and "Dan odvoza" in head:
            useful = [dict(zip(head, r)) for r in rows]
    if not mko or not useful:
        sys.exit(f"Na {PAGE} nema obje tablice (MKO po ulicama, korisni otpad po naseljima).")
    print(f"Tablica MKO: {len(mko)} redaka, korisni otpad: {len(useful)} redaka")

    # useful waste per area
    areas = {}
    for r in useful:
        name = ALIAS.get(r["Naselje / četvrt"], r["Naselje / četvrt"])
        rule = useful_rule(r["Dan odvoza"])
        if rule is None:
            problems.append(f"korisni otpad, {name}: nepoznato pravilo {r['Dan odvoza']!r}")
            continue
        if name in areas and areas[name] != rule:
            problems.append(f"korisni otpad, {name}: dva različita pravila")
        areas[name] = rule
    used = set()
    lower = {name.lower(): name for name in areas}
    underground_areas = set()

    # mixed waste per street
    places = []  # (jls, area for people, street for search, mko days, useful rule)
    left_out = Counter()
    for r in mko:
        street, settlement, mo = r["Ulica"], r["Naselje"], board(r["MO / GC"])
        rule = mko_rule(r["Dan odvoza"])
        if not r["Dan odvoza"].strip():
            if (street, settlement) in NO_DAY:
                left_out["bez dana"] += 1
                continue
            problems.append(f"MKO {street} ({settlement}): nema dana odvoza")
            continue
        if rule is None:
            problems.append(f"MKO {street} ({settlement}): nepoznato pravilo {r['Dan odvoza']!r}")
            continue
        days, under = rule
        if settlement == "Šibenik":
            area = re.sub(r"\s+[IVX]+$", "", mo)
            shown = mo
        else:
            area = settlement
            shown = settlement.replace(" Kod ", " kod ")
        if not days:  # underground containers only: no household collection day
            left_out[shown] += 1
            underground_areas.add(area)
            continue
        own = naslov(street)
        key = lower.get(own.lower())
        if key:  # a hamlet that the useful-waste table names on its own (Rakovo selo, Tromilja...)
            used.add(key)
            if areas[key] != areas.get(area):
                problems.append(f"{own}: pravilo za korisni otpad razlikuje se od {area}")
        if area in areas:
            used.add(area)
            ur = areas[area]
        elif settlement in NO_USEFUL:
            ur = None
        else:
            problems.append(f"MKO {street} ({settlement}, {mo}): nema pravila za korisni otpad")
            continue
        jls = "Bilice" if settlement == "Bilice" else "Šibenik"
        places.append((jls, shown, own if settlement == "Šibenik" else f"{own} ({shown})", days,
                       ur, settlement, under))
    for name in sorted(set(areas) - used - EXTRA - underground_areas):
        problems.append(f"korisni otpad: {name} se ne može povezati s ulicama iz tablice MKO")

    # zones: same municipality and the same two rules
    groups = defaultdict(list)
    for p in places:
        groups[(p[0], p[3], p[4])].append(p)
    order = sorted(groups, key=lambda k: (k[0] != "Šibenik", sorted({p[1] for p in groups[k]})[0], k))
    names, named = Counter(), Counter()
    for k in order:
        names.update({p[2] for p in groups[k]})
        named.update({(p[2], p[1]) for p in groups[k]})
    zones = {}
    totals = Counter()
    for i, k in enumerate(order, 1):
        jls, days, ur = k
        ps = groups[k]
        shown = sorted({p[1] for p in ps})
        ulice = sorted({p[2] if names[p[2]] == 1 else f"{p[2]} ({p[1]})" if named[p[2], p[1]] == 1
                        else f"{p[2]} ({p[1]}; {mko_text(days).lower()})" for p in ps}, key=str.lower)
        rows = [(d, "M") for dd in days for d in pravila.tjedno(year, dd)]
        if ur:
            rows += [(d, "KP") for d in pravila.mjesecno(year, ur[1], ur[0])]
        merged = defaultdict(str)
        kept = pravila.primijeni_blagdane([d for d, _ in rows], "isti", year)  # no holiday shifts
        for (d, moved), (_, codes) in zip(kept, rows):
            merged[d] += codes
        for d, codes in merged.items():
            if "M" in codes and d.weekday() not in [pravila.DANI[x] for x in days]:
                problems.append(f"zona {i}: {d} nije dan miješanog otpada")
            if "K" in codes and (not ur or d.weekday() != pravila.DANI[ur[1]]):
                problems.append(f"zona {i}: {d} nije dan korisnog otpada")
        m = sum("M" in c for c in merged.values())
        if not 52 * len(days) <= m <= 53 * len(days) + 1:
            problems.append(f"zona {i}: {m} odvoza miješanog otpada")
        if ur and sum("K" in c for c in merged.values()) != 12:
            problems.append(f"zona {i}: korisni otpad nije 12 puta")
        for c in "".join(merged.values()):
            totals[c] += 1
        sched = mko_text(days) + ("; papir i plastika " + useful_text(ur) if ur else "")
        zone = {
            "jls": jls,
            "podrucje": ", ".join(shown[:3]) + (" …" if len(shown) > 3 else "") + " – " + sched,
            "opis": ", ".join(shown),
            "ulice": ulice,
        }
        under = sorted({p[2] for p in ps if p[6]})
        notes = []
        if under:
            notes.append("Dio ulice ima podzemne spremnike: " + ", ".join(under) + ".")
        if not ur:
            where = sorted({p[5] for p in ps})
            notes.append(f"Dan odvoza korisnog otpada za {', '.join(where)} nije naveden u rasporedu "
                         "Zelenog grada; provjerite na 022/332-325.")
        if notes:
            zone["napomena"] = " ".join(notes)
        zone["raw"] = {str(year): podaci.month_lines([(d, c, False) for d, c in merged.items()])}
        zones[str(i)] = zone
    dup = Counter(u for z in zones.values() for u in z["ulice"])
    for u, n in dup.items():
        if n > 1:
            problems.append(f"{u!r} je u {n} zone")

    print(f"Ulica/zaselaka u zonama: {len(places)}, zona: {len(zones)}, "
          f"izostavljeno: {sum(left_out.values())} ({dict(left_out)})")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")

    under_only = {k: v for k, v in left_out.items() if k != "bez dana"}
    extra = sorted((EXTRA | underground_areas - used) & set(areas))
    data = {**PROVIDER, "napomene": NAPOMENE + [
        f"Raspored nema godinu; stranica {PAGE} zadnji put je izmijenjena "
        f"{date.fromisoformat(modified):%d.%m.%Y.}" if modified else
        f"Raspored nema godinu (stranica {PAGE}).",
        f"Bez dana odvoza za kućanstva (samo podzemni spremnici) je {sum(under_only.values())} ulica: " +
        ", ".join(f"{k} {v}" for k, v in sorted(under_only.items())) + "; nisu u ovom rasporedu.",
        *[f"Korisni otpad {name}: {useful_text(areas[name])} (" +
          ("ulice imaju samo podzemne spremnike za miješani otpad" if name in underground_areas
           else "u tablici ulica nije posebno označen") + ")." for name in extra],
        *[f"Izostavljeno: {v}." for v in NO_DAY.values()],
    ], "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years of zones that did not change
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("podrucje") == zone["podrucje"] and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    print(f"Odvoza {year} (zbroj po zonama): " + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
