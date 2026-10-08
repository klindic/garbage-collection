"""Komunalno Sućuraj d.o.o.: Općina Sućuraj (Sućuraj and Bogomolje), summer 2026 and winter 2026/2027.

    python3 -m izvori.komunalno_sucuraj [--year 2026]

The schedules are image leaflets, transcribed by hand below: the page "Raspored odvoza" shows the winter
leaflets (1.10.-31.5.: mixed waste Monday, Wednesday and Friday in the whole municipality; plastic on the
last Tuesday and paper on the last Thursday of the month, with dates), and the WordPress post "Obavijest o
ljetnom rasporedu … 2026" has one summer leaflet each for Sućuraj and Bogomolje (1.6.-30.9.: mixed waste
daily or on three weekdays per part of the village / group of coves). The script finds the images on the
site, checks their sha256 against the transcribed versions and stops ("slika se promijenila") when an image
changes or a new one appears. Summer recyclables are given only as "every odd/even Wednesday" or "every
other Tuesday/Thursday" with dates "to be published", so they are left out. No holiday rule is published:
the weekday dates are kept; the listed recyclables dates are used as they are. Dates outside the written
window (1.6.YYYY-31.5.YYYY+1) are kept from podaci/<slug>.json.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalno-sucuraj"
SITE = "https://komunalnosucuraj.hr"
PAGE = SITE + "/raspored-odvoza/"
POSTS = SITE + "/wp-json/wp/v2/posts?search=ljetnom%20rasporedu&per_page=20&_fields=id,date,link,title,content"
YEAR = 2026  # season the transcription below belongs to (summer YEAR, winter YEAR/YEAR+1)
IMAGES = {  # file name: sha256 of the transcribed image
    "raspored-zima-MKO.jpg": "756d4d8ae99ecb82df3c4dffc915939c2c716d3476325c04bc0aa788c0eff51e",
    "raspored-reciklati-zima.png": "d58c7a7f4ed36c9b5eaa5f7b9a2f556bd3f90ee6b36b8b1591b44a2369c5cb3e",
    "SUCURAJ-OBAVIJEST-O-LJETNOM-RASPOREDU-ODVOZA-MJESOVITOG-KOMUNALNOG-OTPADA.png":
        "5d6dae126624d6c690334b8c1ac046d2b0aa0632f68e4c3e3f4ba10e1eed423e",
    "BOGOMOLJE-OBAVIJEST-O-LJETNOM-RASPOREDU-ODVOZA-MJESOVITOG-KOMUNALNOG-OTPADA-.png":
        "c575958f0c50512c64fe35a708b6e223b5f64f3ce33ef5611f43f758acc4f930",
}
# raspored-zima-MKO.jpg: "( 01.10. – 31.05. ) … cijele općine Sućuraj: PONEDJELJAK SRIJEDA PETAK"
WINTER = ((10, 1), (5, 31), ["pon", "sri", "pet"])
# raspored-reciklati-zima.png: "ŽUTI SPREMNIK (PLASTIKA) – ZADNJI UTORAK U MJESECU",
# "PLAVI SPREMNIK (PAPIR, KARTON) – ZADNJI ČETVRTAK U MJESECU" and the two date columns
RECYCLING = {
    "P": ("uto", "27.10. 24.11. 29.12. 26.01. 23.02. 30.03. 27.04. 25.05."),
    "K": ("čet", "29.10. 26.11. 31.12. 28.01. 25.02. 25.03. 29.04 27.05"),
}
SUMMER = ((6, 1), (9, 30))
ZONES = [  # (podrucje, settlement, summer weekdays, streets / coves as on the summer leaflets)
    ("Sućuraj – centar (ljeti svakodnevno pon – sub)", "Sućuraj", ["pon", "uto", "sri", "čet", "pet", "sub"],
     ["Gustrina (Pijaca)", "Česminica", "Put Pomurvice", "Trajektna ulica", "Riva", "Centar", "Put Lučice",
      "Put Polja", "Mačak", "Blace"]),
    ("Sućuraj – Bilina, Zabrig (ljeti uto, čet, sub)", "Sućuraj", ["uto", "čet", "sub"], ["Bilina", "Zabrig"]),
    ("Bogomolje – sjeverne uvale i mjesto (ljeti pon, sri, pet)", "Bogomolje", ["pon", "sri", "pet"],
     ["Solotiša", "Zavala", "Stara", "V. Gačice", "Male Gačice", "Bristova", "V. Pogorila", "Mala Pogorila",
      "Račevina", "Jerkov Dvor", "Srid Sela", "Podglavica", "Glava"]),
    ("Bogomolje – južne uvale (ljeti uto, čet, sub)", "Bogomolje", ["uto", "čet", "sub"],
     ["Smrska", "Donji Pelinovik", "Gornji Pelinovik", "Smokvina", "Duboka", "Kožija", "Selca", "Zaglav"]),
]
PROVIDER = {
    "davatelj": "Komunalno Sućuraj d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Sućuraj"],
    "napomene": [
        "U registru davatelja za Sućuraj navedena je Općina Sućuraj; uslugu sada pruža Komunalno Sućuraj d.o.o. "
        "(OIB 23957325654), društvo u vlasništvu općine.",
        "Zimski raspored (1.10. – 31.5.): miješani otpad ponedjeljkom, srijedom i petkom na području cijele "
        "općine; plastika (žuti spremnik) zadnji utorak, papir i karton (plavi spremnik) zadnji četvrtak u mjesecu.",
        "Ljetni raspored (1.6. – 30.9.) razlikuje dijelove Sućurja i uvale Bogomolja. Ljeti se reciklabilni "
        "otpad odvozi svaku drugu srijedu (Sućuraj) odnosno svaki drugi utorak i četvrtak (Bogomolje), ali "
        "datumi nisu objavljeni pa ljetni odvoz reciklabilnog otpada nije upisan.",
        "Upisano je razdoblje od 1.6.2026. (ljetni raspored 2026. i zimski 2026./2027.); raniji raspored nije objavljen.",
        "Spremnike iznijeti večer prije ili najkasnije do 7 sati (zimi), ljeti do 5 sati (Sućuraj) odnosno 6 sati "
        "(Bogomolje) na dan odvoza.",
        "Pomaci zbog blagdana nisu objavljeni.",
        "Kontakt: 021/717-738, komunalnosucuraj@gmail.com.",
    ],
}


def base_name(url):
    """'…/raspored-zima-MKO-688x1024.jpg' -> 'raspored-zima-MKO.jpg' (WordPress size variant -> original)."""
    return re.sub(r"-\d+x\d+(?=\.\w+$)", "", url.rsplit("/", 1)[1])


def images(problems):
    """{file name: original URL} of the schedule images on the page and in the summer post."""
    page = fetch(PAGE).decode("utf-8", "replace")
    found = {}
    for url in re.findall(r'src="(https?://[^"]+/wp-content/uploads/[^"]+\.(?:jpe?g|png))"', page):
        if "placeholder" not in url:
            found[base_name(url)] = re.sub(r"-\d+x\d+(?=\.\w+$)", "", url)
    posts = [p for p in json.loads(fetch(POSTS)) if str(YEAR) in p["title"]["rendered"]]
    if not posts:
        problems.append(f"nema objave o ljetnom rasporedu {YEAR}")
    for p in posts:
        for url in re.findall(r'src="(https?://[^"]+/wp-content/uploads/[^"]+\.(?:jpe?g|png))"', p["content"]["rendered"]):
            found[base_name(url)] = re.sub(r"-\d+x\d+(?=\.\w+$)", "", url)
    for name in sorted(set(found) - set(IMAGES)):
        problems.append(f"nova slika na stranici: {found[name]} – raspored treba ponovno prepisati")
    for name in sorted(set(IMAGES) - set(found)):
        problems.append(f"slika {name} više nije na stranici – raspored se promijenio")
    return {n: u for n, u in found.items() if n in IMAGES}


def last_weekday_rows(code, day, text, start, problems):
    """'27.10. 24.11. …' (the season from `start`) -> [(date, code, moved)]; each must be the last `day` of its month."""
    rows, months = [], Counter()
    for d, m in re.findall(r"(\d\d)\.(\d\d)\.?", text):
        y = start.year if int(m) >= start.month else start.year + 1
        dt = date(y, int(m), int(d))
        months[(y, int(m))] += 1
        last = max(x for x in pravila.tjedno(y, day) if x.month == dt.month)
        if dt == last:
            rows.append((dt, code, False))
        else:
            monday = dt - timedelta(days=dt.weekday())
            if any(monday <= h <= monday + timedelta(days=6) for h in pravila.blagdani(y)):
                rows.append((dt, code, True))
            else:
                problems.append(f"{code} {dt:%d.%m.%Y}: nije zadnji {day} u mjesecu")
    if len(months) != 8 or max(months.values()) != 1:
        problems.append(f"{code}: datumi po mjesecima {dict(months)}")
    return rows


def merge(old_zone, rows, start, end):
    """raw by year: the new rows plus the old dates outside start..end."""
    keep = [r for r in podaci.iter_dates(old_zone) if not start <= r[0] <= end] if old_zone else []
    years = {}
    for r in keep + rows:
        years.setdefault(r[0].year, []).append(r)
    return {str(y): podaci.month_lines(v) for y, v in sorted(years.items())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Prepisani su samo rasporedi za {YEAR}./{YEAR + 1}.; za {year}. treba prepisati nove slike.")
    problems = []
    for name, url in images(problems).items():
        digest = hashlib.sha256(fetch(url)).hexdigest()
        if digest != IMAGES[name]:
            problems.append(f"slika se promijenila, raspored treba ponovno prepisati: {url} (sha256 {digest})")
    if problems:
        for p in problems:
            print("PROBLEM", p)
        sys.exit("Ništa nije upisano.")

    s_start, s_end = date(year, *SUMMER[0]), date(year, *SUMMER[1])
    w_start, w_end = date(year, *WINTER[0]), date(year + 1, *WINTER[1])
    winter_days = [d for y in (year, year + 1) for k in WINTER[2] for d in pravila.tjedno(y, k) if w_start <= d <= w_end]
    recycling = [r for code, (day, text) in RECYCLING.items() for r in last_weekday_rows(code, day, text, w_start, problems)]
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, (podrucje, place, summer_days, streets) in enumerate(ZONES, 1):
        summer = [d for k in summer_days for d in pravila.tjedno(year, k) if s_start <= d <= s_end]
        dates = {d: "M" for d in summer + winter_days}
        for d, code, moved in recycling:
            dates[d] = dates.get(d, "") + code + ("!" if moved else "")
        rows = [(d, c.replace("!", ""), "!" in c) for d, c in dates.items()]
        per_month = Counter((d.year, d.month) for d in summer + winter_days)
        want = 4 * len(summer_days) - 1
        for (y, m), n in sorted(per_month.items()):
            expect = want if s_start <= date(y, m, 1) <= s_end else 4 * len(WINTER[2]) - 1
            if not expect <= n <= expect + 4:
                problems.append(f"zona {z} {m}/{y}: {n} odvoza miješanog otpada")
        data["zone"][str(z)] = {
            "jls": "Sućuraj",
            "podrucje": podrucje,
            "ulice": [place] + streets,
            "raw": merge(old.get(str(z)), rows, s_start, w_end),
        }
        print(f"Zona {z} ({podrucje}): {len(rows)} odvoza od {s_start:%d.%m.%Y} do {w_end:%d.%m.%Y}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
