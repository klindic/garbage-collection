"""Bibinje: Bibinjac d.o.o. (bibinjac.hr), mixed waste by weekday and paper/plastic by date, above and below the railway.

    python3 -m izvori.bibinjac [--year 2026]

The home page has a widget "Odvoz otpada 30.09.2026. – 21.04.2027." whose data is the JavaScript object
`var podaci = {iznad: {mko: "Svaki ponedjeljak i četvrtak", opis: ..., datumi: [{tekst, datum}, ...]},
ispod: {...}}`, read here with regular expressions. Mixed waste dates are the weekdays of `mko` inside
the widget's period; paper and plastic (collected together, code PK) are the listed dates. Only this
window is online (the schedule before 30.09.2026 is no longer published), so every year the window
touches is written and dates of earlier runs outside the window are kept; --year only has to be one of
them. Holidays: the paper/plastic dates already skip holidays; no holiday rule is published for mixed
waste, so its weekday dates are kept and the holidays are listed in napomene.
"""
import argparse
import html as htmlmod
import re
import sys
from datetime import date, timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "bibinjac"
SITE = "https://www.bibinjac.hr"
PAGE = SITE + "/"
DAYS = {"ponedjeljak": 0, "utorak": 1, "srijeda": 2, "srijedu": 2, "četvrtak": 3, "petak": 4, "subota": 5,
        "subotu": 5}
ZONES = {"iznad": ("1", "Iznad pruge"), "ispod": ("2", "Ispod pruge")}
PROVIDER = {
    "davatelj": "Bibinjac d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zadarska",
    "jls": ["Bibinje"],
    "nazivi": {"P": "Plastika"},
}
NAPOMENE = [
    "Na webu je objavljen samo raspored od {start} do {end} (widget na naslovnici); raniji raspored za "
    "listopad 2025. - rujan 2026. više nije objavljen.",
    "Bibinjac ne objavljuje popis ulica iznad i ispod pruge.",
    "Papir i plastika odvoze se istoga dana (svaka druga srijeda prema datumima).",
    "Spremnike iznijeti večer prije odvoza ili najkasnije rano ujutro na dan odvoza.",
    "Općina navodi da se miješani otpad ljeti odvozi tri puta tjedno, ali ljetni raspored nije objavljen.",
    "Glomazni otpad: prema obavijesti od 07.10.2026. odvozi se utorkom i četvrtkom (na blagdan idući "
    "utvrđeni dan).",
    "Kontakt: 023/261-201, tdbibinjac@gmail.com.",
]


def parse(page):
    """{area: (mko text, opis, [(date, tekst)])} from `var podaci = {...}`."""
    m = re.search(r"var podaci = \{(.*?)\n\s*\};", page, re.S)
    if not m:
        return {}
    out = {}
    for area, mko, opis, items in re.findall(r'(\w+):\s*\{\s*mko:\s*"([^"]+)",\s*opis:\s*"([^"]+)",\s*'
                                             r'datumi:\s*\[(.*?)\]', m.group(1), re.S):
        out[area] = (mko, opis, [(date.fromisoformat(d), t) for t, d in
                                 re.findall(r'tekst:\s*"([^"]+)",\s*datum:\s*"(\d{4}-\d\d-\d\d)"', items)])
    return out


def dmy(text):
    d, m, y = map(int, re.match(r"(\d\d)\.(\d\d)\.(\d{4})", text).groups())
    return date(y, m, d)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = fetch(PAGE).decode("utf-8", "replace")
    areas = parse(page)
    text = " ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", page)).split())
    head = re.search(r"Odvoz otpada (\d\d\.\d\d\.\d{4})\. [–-] (\d\d\.\d\d\.\d{4})\.", text)
    problems = []
    if set(areas) != set(ZONES):
        sys.exit(f"Na {PAGE} nema očekivanog objekta podaci (iznad/ispod), nađeno: {sorted(areas)}")
    if not head:
        problems.append("nema naslova widgeta s razdobljem")
        start = min(d for _, _, items in areas.values() for d, _ in items)
        end = max(d for _, _, items in areas.values() for d, _ in items)
    else:
        start, end = dmy(head.group(1)), dmy(head.group(2))
    if not start.year <= year <= end.year:
        problems.append(f"razdoblje {start}-{end} ne obuhvaća {year}.")
    hol = {h for y in range(start.year, end.year + 1) for h in pravila.blagdani(y)}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    napomene = [n.format(start=f"{start:%d.%m.%Y.}", end=f"{end:%d.%m.%Y.}") for n in NAPOMENE]
    data = {**PROVIDER, "napomene": napomene, "zone": {}}
    hol_notes = []
    for area, (z, name) in ZONES.items():
        mko, opis, items = areas[area]
        days = [DAYS[w] for w in re.findall(r"\w+", mko.lower()) if w in DAYS]
        if not mko.lower().startswith("svaki") or not days:
            problems.append(f"{name}: ne razumijem '{mko}'")
            continue
        w = re.search(r"od (\d\d\.\d\d\.\d{4})\. do (\d\d\.\d\d\.\d{4})\.", opis)
        a, b = (dmy(w.group(1)), dmy(w.group(2))) if w else (start, end)
        rows = {}
        for d, t in items:
            if t != f"{d:%d.%m.'%y.}":
                problems.append(f"{name}: tekst {t} i datum {d} se ne slažu")
            if not a <= d <= b or not start <= d <= end:
                problems.append(f"{name}: {d} izvan razdoblja")
            if d in rows:
                print(f"   {name}: {d:%d.%m.%Y.} je u popisu dvaput (uzet jednom)")
            rows[d] = "PK"
        if len({d.weekday() for d in rows}) != 1:
            problems.append(f"{name}: papir i plastika nisu uvijek isti dan u tjednu")
        d = start
        while d <= end:
            if d.weekday() in days:
                rows[d] = rows.get(d, "") + "M"
                if d in hol:
                    hol_notes.append(f"{d:%d.%m.%Y.} ({name.lower()})")
            d += timedelta(days=1)
        for (y, m) in sorted({(d.year, d.month) for d in rows}):
            n = sum("M" in c for d, c in rows.items() if (d.year, d.month) == (y, m))
            full = start <= date(y, m, 1) and (date(y, m, 28) + timedelta(days=4)).replace(day=1) <= end
            if not (8 if full else 0) <= n <= 10:
                problems.append(f"{name} {y}-{m:02d}: {n} odvoza miješanog otpada")
        raw = dict(old["zone"].get(z, {}).get("raw", {}))
        kept = [x for x in podaci.iter_dates(old["zone"].get(z, {"raw": {}})) if not start <= x[0] <= end]
        for y in sorted({d.year for d in rows} | {x[0].year for x in kept}):
            raw[str(y)] = podaci.month_lines([(d, c, False) for d, c in rows.items() if d.year == y]
                                             + [x for x in kept if x[0].year == y])
        data["zone"][z] = {"jls": "Bibinje", "podrucje": f"{name} – miješani {mko.lower()}, papir i plastika "
                                                         "svaka druga srijeda",
                           "ulice": [f"Bibinje – {name.lower()}"], "raw": raw}
        print(f"zona {z} ({name}): {len(rows)} dana od {start:%d.%m.%Y.} do {end:%d.%m.%Y.}"
              + (f", zadržano iz ranijih podataka {len(kept)}" if kept else ""))
    if hol_notes:
        print(f"   blagdani na dan odvoza miješanog otpada (bez objavljenog pomaka): {', '.join(hol_notes)}")
        data["napomene"].append("Pomaci odvoza miješanog otpada zbog blagdana nisu objavljeni; upisani su dani "
                                "po rasporedu i za blagdane: " + ", ".join(hol_notes) + ".")
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
