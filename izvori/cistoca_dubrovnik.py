"""Dubrovnik, Dubrovačko primorje, Župa dubrovačka: Čistoća d.o.o. Dubrovnik, settlements with weekday collection.

    python3 -m izvori.cistoca_dubrovnik [--year 2026]

The page "Odvojeno prikupljanje i zbrinjavanje otpada" links the "Program odvoza komunalnog otpada": since
August 2026 the scanned PDF "Izmjene odvoza miješanog komunalnog otpada za 2026. godinu od 1. kolovoza
2026." (pages turned 90°), before that "Odvoz miješanog komunalnog otpada za 2026. godinu". Both are tables
of 132 rows (settlement, street, collection in season, out of season, note) without a usable text layer, so
the rows are transcribed below and both PDFs' sha256 are pinned. Mixed waste is mostly left at shared
collection points (Odluka o mjestima primopredaje), and paper, plastic and glass go to public containers,
so only the rows with a weekday rule are written (rural settlements, the Elafiti islands, Mandrač, Postranje,
Dubrovačko primorje); rows collected every day or every working day (the town, Mokošica, Cavtat, Obod,
Zvekovica, Župa "u naselju") and roadside collection points ("odlagalište uz cestu / magistralu") are only
described in napomene. Season: Grad Dubrovnik 1.5.-31.10., the other municipalities 1.6.-30.10. (31.10.
is in neither period there and is left out when the two rules differ). The company's notices are applied:
the Elafiti seasonal schedule started on 24.6.2026, Mali Zaton and Štikovica have Tuesday and Saturday from
1.8.2026 (also in the amended table). No holiday rule is published, so holidays keep their dates (noted).
"""
import argparse
import hashlib
import html
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cistoca-dubrovnik"
SITE = "https://cistocadubrovnik.hr"
PAGE = SITE + "/usluge/odvojeno-prikupljanje-i-zbrinjavanje-otpada/"
POSTS = SITE + "/wp-json/wp/v2/posts?per_page=50&after={after}&search=odvoz&_fields=id,date,link,title"
YEAR = 2026
PROGRAMS = [  # (valid from, file, sha256); the January one is no longer linked from the page
    (date(2026, 1, 1), SITE + "/wp-content/uploads/2026/01/Odvoz-mijesanog-komunalnog-otpada-za-2026.-godinu.pdf",
     "c041f08a0439ff5d4342520f1258203e0783993b14e4965b522159ddc20d2a19"),
    (date(2026, 8, 1), SITE + "/wp-content/uploads/2026/07/Izmjena-odvoza-mijesanog-komunalnog-otpada.pdf",
     "384ddf216714f5b0a8dfee646372da3202a24dc94be8c584dd07cef90dc4dc4e"),
]
KNOWN_NOTICES = {"obavijest-za-stanovnike-elafita", "obavijest-korisnicima-u-naseljima-mali-zaton-i-stikovica"}
# season (first, last day) and off-season (last day before, first day after) as the programme defines them
SEASON = {"Dubrovnik": (date(YEAR, 5, 1), date(YEAR, 10, 31)), "Župa dubrovačka": (date(YEAR, 6, 1), date(YEAR, 10, 30)),
          "Dubrovačko primorje": (date(YEAR, 6, 1), date(YEAR, 10, 30))}
OFF = {"Dubrovnik": (date(YEAR, 4, 30), date(YEAR, 11, 1)), "Župa dubrovačka": (date(YEAR, 5, 30), date(YEAR, 11, 1)),
       "Dubrovačko primorje": (date(YEAR, 5, 30), date(YEAR, 11, 1))}
D = {"pon": 0, "uto": 1, "sri": 2, "čet": 3, "pet": 4, "sub": 5, "ned": 6, "svaki dan": None}
# Transcribed rows with a weekday rule, grouped by identical rules: (jls, [(row no., settlement / place)],
# season days, off-season days, changes {from date: (season, off-season)}, note). Both programs agree on
# every row except Štikovica and Mali Zaton (Tue, Thu, Sat until 31.7.; Tue, Sat from 1.8.).
GROUPS = [
    ("Dubrovnik", [(76, "Bosanka")], "pon pet", "pon pet", {}, None),
    ("Dubrovnik", [(77, "Brsečine"), (90, "Orašac"), (99, "Trsteno (uz Jadransku cestu)")],
     "pon čet sub", "pon čet", {}, None),
    ("Dubrovnik", [(78, "Čajkovica")], "pon sri pet", "pon sri pet", {}, None),
    ("Dubrovnik", [(81, "Dubravica"), (82, "Gromača"), (83, "Kliševo"), (87, "Ljubač"), (88, "Mravinjac"),
                   (89, "Mrčevo"), (92, "Riđica")], "pet", "čet", {}, None),
    ("Dubrovnik", [(84, "Knežica")], "uto čet pet", "uto pet", {}, None),
    ("Dubrovnik", [(85, "Komolac"), (96, "Šumet (u naselju)")], "uto pet", "uto pet", {},
     "Šumet: odlagalište uz cestu prazni se utorkom, četvrtkom i subotom."),
    ("Dubrovnik", [(98, "Tor")], "uto čet pet", "uto čet pet", {}, None),
    ("Dubrovnik", [(95, "Štikovica"), (101, "Mali Zaton")], "uto čet sub", "uto čet sub",
     {date(2026, 8, 1): ("uto sub", "uto sub")},
     "Od 1.8.2026. odvoz s kućnih adresa (vlastiti spremnici) utorkom i subotom (obavijest od 29.7.2026. i "
     "izmjena programa); do 31.7. utorkom, četvrtkom i subotom."),
    ("Dubrovnik", [(103, "Koločep"), (104, "Lopud"), (105, "Šipan")], "pon čet sub", "pon", {},
     "Elafiti: sezonski raspored (ponedjeljak, četvrtak, subota) u 2026. počeo je tek 24.6. (obavijest od "
     "24.6.2026.); izvan sezone samo ponedjeljkom."),
    ("Dubrovnik", [(47, "Lapad – Mandrač")], "svaki dan", "pon pet", {}, None),
    ("Župa dubrovačka", [(117, "Postranje")], "pon sri sub", "pon sri sub", {}, None),
    ("Dubrovačko primorje", [(120, "Banići"), (126, "Slađenovići"), (127, "Slano")], "pon čet sub", "pon čet", {},
     None),
    ("Dubrovačko primorje", [(121, "Čepikuće"), (123, "Imotica"), (124, "Lisac"), (125, "Majkovi"),
                             (129, "Štedrica"), (130, "Topolo"), (131, "Trnovica"), (132, "Ošlje")],
     "pet", "čet", {}, None),
    ("Dubrovačko primorje", [(122, "Doli"), (128, "Smokvina")], "čet", "čet", {}, None),
]
ELAFITI_SEASON_FROM = date(2026, 6, 24)  # notice "Obavijest za stanovnike Elafita"
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Čistoća d.o.o. Dubrovnik",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Dubrovnik", "Župa dubrovačka", "Dubrovačko primorje"],
    "napomene": [
        "Miješani komunalni otpad većinom se odlaže u spremnike na zajedničkim mjestima primopredaje; raspored "
        "pokazuje dane odvoza za naselje. Otpad se odlaže dan uoči odvoza od 19:00 do 3:00 sata (u povijesnoj "
        "jezgri od 1.5. do 31.10. od 18:00 do 8:30, od 1.11. do 30.4. od 20:00 do 8:30).",
        "Uključena su samo naselja s odvozom određenim danima u tjednu. Ulice i naselja s odvozom svaki dan ili "
        "svaki radni dan (Grad Dubrovnik: Gruž, Pile–Kono, Ploče, Lapad, Montovjerna, stara gradska jezgra, "
        "Mokošica, Čajkovići, Donje Obuljeno, Lozica, Prijevor, Rožat, Sustjepan, Vrbica, Veliki Zaton; Župa "
        "dubrovačka: Brgat, Čibača, Kupari, Mandaljena, Mlini, Plat, Soline, Srebreno; Konavle: Cavtat, Obod, "
        "Zvekovica) te odlagališta uz cestu i magistralu nisu upisani kao kalendar.",
        "Sezona: Grad Dubrovnik od 1. svibnja do 31. listopada; ostale općine od 1. lipnja do 30. listopada. "
        "Iz Općine Dubrovačko primorje u sezoni se po potrebi odvozi i nedjeljom.",
        "Papir, plastika i staklo odlažu se u javne spremnike (zeleni otoci); raspored njihova pražnjenja nije "
        "objavljen po danima.",
        "Pomaci zbog blagdana nisu objavljeni; datumi nisu pomaknuti.",
        "Glomazni otpad: Program prikupljanja glomaznog otpada za 2026. na stranici Čistoće; besplatni telefon "
        "0800 606 707.",
    ],
}


def days(text):
    """'pon čet sub' -> {0, 3, 5}; 'svaki dan' -> all seven days."""
    return set(range(7)) if text == "svaki dan" else {D[t] for t in text.split()}


def describe(text):
    return "svaki dan" if text == "svaki dan" else ", ".join(DAN[D[t]] for t in text.split())


def group_dates(jls, places, season, off, changes, year, problems):
    """[(date, 'M', False)] for one group from its season / off-season rules."""
    start, end = SEASON[jls]
    off_until, off_from = OFF[jls]
    if any(n in (103, 104, 105) for n, _ in places):  # Elafiti: the 2026 season began on 24.6.
        start, off_until = ELAFITI_SEASON_FROM, ELAFITI_SEASON_FROM - timedelta(days=1)
    rows, gap, d = [], [], date(year, 1, 1)  # gap: days in neither period where the two rules differ
    while d.year == year:
        s, o = season, off
        for since, (s2, o2) in sorted(changes.items()):
            if d >= since:
                s, o = s2, o2
        a, b = d.weekday() in days(s), d.weekday() in days(o)
        if start <= d <= end:
            want = a
        elif d <= off_until or d >= off_from:
            want = b
        else:
            want = a and b
            if a != b:
                gap.append(d)
        if want:
            rows.append((d, "M", False))
        d += timedelta(days=1)
    for g in gap:
        print(f"   {places[0][1]}…: {g:%d.%m.} nije ni u sezoni ni izvan sezone; izostavljeno")
    cnt = Counter(x.month for x, _, _ in rows)
    lo = min(len(days(r)) for r in [season, off] + [x for c in changes.values() for x in c]) * 4 - 1
    if any(cnt[m] < max(lo, 3) for m in range(1, 13)):
        problems.append(f"{places[0][1]}: premalo odvoza u nekom mjesecu {dict(cnt)}")
    return rows, gap


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Program za {year} nije prepisan. Ništa nije upisano.")
    problems = []
    page = html.unescape(fetch(PAGE).decode("utf-8", "replace"))
    m = re.search(r'href="([^"]+\.pdf)"[^>]*>\s*Program odvoza komunalnog otpada', page)
    current = m.group(1) if m else None
    if current != PROGRAMS[-1][1]:
        problems.append(f"na stranici je drugi program odvoza: {current}; prepisati ponovno")
    for since, url, sha in PROGRAMS:
        try:
            digest = hashlib.sha256(fetch(url)).hexdigest()
        except RuntimeError as e:
            print(f"UPOZORENJE: program od {since:%d.%m.} više nije dostupan ({e}); koristi se prijepis")
            continue
        if digest != sha:
            problems.append(f"slika se promijenila, prepisati ponovno: {url} (sha256 {digest})")
    posts = json.loads(fetch(POSTS.format(after=f"{year}-01-01T00:00:00")))
    slugs = {p["link"].rstrip("/").rsplit("/", 1)[1] for p in posts}
    for missing in KNOWN_NOTICES - slugs:
        problems.append(f"obavijest {missing} nije pronađena")
    newest = max((p["date"] for p in posts if p["link"].rstrip("/").rsplit("/", 1)[1] in KNOWN_NOTICES), default="")
    for p in posts:
        title = html.unescape(p["title"]["rendered"])
        if p["date"] > newest and re.search(r"komunaln|miješan|raspored", title, re.I) and "glomazn" not in title.lower():
            print(f"UPOZORENJE: novija obavijest, provjerite: {p['date'][:10]} {title} {p['link']}")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    hol = set(pravila.blagdani(year))
    data = {**PROVIDER, "zone": {}}
    for z, (jls, places, season, off, changes, note) in enumerate(GROUPS, 1):
        rows, gap = group_dates(jls, places, season, off, changes, year, problems)
        names = [p for _, p in places]
        start, end = SEASON[jls]
        if any(n in (103, 104, 105) for n, _ in places):
            start = ELAFITI_SEASON_FROM
        rule = (f"svaki tjedan: {describe(season)}" if season == off else
                f"u sezoni ({start:%d.%m.}–{end:%d.%m.}): {describe(season)}; izvan sezone: {describe(off)}")
        if changes:
            rule = "od 1.8.2026.: " + describe(next(iter(changes.values()))[0]) + "; prije: " + describe(season)
        notes = [n for n in (note, f"Redni broj u programu: {', '.join(str(n) for n, _ in places)}.") if n]
        if gap:
            notes.append(", ".join(f"{g:%d.%m.}" for g in gap) + " nije obuhvaćen ni sezonom ni izvan sezone, "
                         "pa nije upisan.")
        on_hol = [d for d, *_ in rows if d in hol]
        if on_hol:
            notes.append("Odvozi na blagdane (" + ", ".join(f"{d:%d.%m.}" for d in on_hol) + ") upisani su bez "
                         "pomaka; pomak nije objavljen.")
        data["zone"][str(z)] = {
            "jls": jls, "podrucje": f"{', '.join(names)} – {rule}", "ulice": names, "napomena": " ".join(notes),
            "raw": {**old.get(str(z), {}).get("raw", {}), str(year): podaci.month_lines(rows)},
        }
        print(f"Zona {z} ({jls}, {', '.join(names)[:45]}): M {len(rows)}, na blagdan {len(on_hol)}")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
