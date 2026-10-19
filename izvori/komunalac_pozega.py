"""Komunalac Požega d.o.o.: Požega, Kutjevo, Pleternica, Brestovac, Čaglin, Jakšić, Kaptol, Velika (search calendar).

    python3 -m izvori.komunalac_pozega [--year 2026]

The calendar on komunalac-pozega.com.hr: the home page holds the names of all streets and settlements
(the autocomplete list, about 1080); the search form (POST) answers with a refresh to ?s=1&it=ID, and
?s=1&it=ID&vrs=N shows the days of one waste type (1 mixed, 3 bio, 4 bulky, 5 paper and plastic in one
bin, 6 glass) from tomorrow to the end of the year as coloured day cells (class kvadbojaN; the page draws
the calendar twice, the second copy with kvadboja0, and both copies must agree). Locations of one JLS
with the same dates form a zone. Responses are cached in $ODVOZ_CACHE (ids for 30 days, calendars for
20 hours) and requests are at least 0.5 s apart.

Holiday moves are built into the dates (the provider's notice: a weekday holiday moves the round to the
Saturday before or after, e.g. 18.11. -> 21.11.); a date off a location's usual weekday is marked as
moved when a holiday falls within a week of it, otherwise it is reported. The site only shows the rest
of the year, so on every run the earlier months of each location are taken from the previous
podaci/komunalac-pozega.json (merge by date) and the napomene say so.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import podaci
from pravila import blagdani

SLUG = "komunalac-pozega"
SITE = "https://komunalac-pozega.com.hr"
UA = {"User-Agent": "Mozilla/5.0 (odvoz-otpada; +https://klindic.github.io/garbage-collection/)"}
CACHE = Path(os.environ.get("ODVOZ_CACHE", Path(tempfile.gettempdir()) / "odvoz-cache")) / SLUG
VRS = {1: "M", 3: "B", 4: "G", 5: "PK", 6: "S"}
NAMES = {1: "miješani otpad", 3: "biootpad", 4: "glomazni otpad", 5: "papir i plastiku", 6: "staklo"}
MONTHS = ["Siječanj", "Veljača", "Ožujak", "Travanj", "Svibanj", "Lipanj", "Srpanj", "Kolovoz", "Rujan",
          "Listopad", "Studeni", "Prosinac"]
JLS_ORDER = ["Požega", "Pleternica", "Kutjevo", "Brestovac", "Čaglin", "Jakšić", "Kaptol", "Velika"]
# settlements per JLS, from the provider's 2026 notice (glass and paper/plastic tables, mobile recycling
# yard Čaglin) and the mixed waste plan ("GRAD / OPĆINA - NASELJA"); Čečavac, Čečavački Vučjak, Jeminovac,
# Koprivna, Oblakovac and Šnjegavić (Brestovac) are in neither, so they come from the settlement register.
# Street names carry their settlement in brackets.
NASELJA = {
    "Požega": "Požega, Alaginci, Bankovci, Crkveni Vrhovci, Ćosine Laze, Dervišaga, Donji Emovci, Drškovci, "
              "Emovački Lug, Golobrdci, Gornji Emovci, Gradski Vrhovci, Komušina, Krivaj, Kunovci, Laze Prnjavor, "
              "Marindvor, Mihaljevci, Nova Lipa, Novi Bankovci, Novi Mihaljevci, Novi Štitnjak, Novo Selo, Seoci, "
              "Stara Lipa, Šeovci, Škrabutnik, Štitnjak, Turnić, Ugarci, Vasine Laze, Vidovci",
    "Pleternica": "Pleternica, Ašikovci, Bilice, Blacko, Bresnica, Brodski Drenovac, Buk, Bučje, Bzenica, "
                  "Ćosinac, Drenovac, Frkljevci, Gradac, Kadanovci, Kalinić, Komorica, Koprivnica, Kuzmica, "
                  "Lakušija, Novoselci, Poloje, Ratkovica, Resnik, Sesvete, Srednje Selo, Sulkovci, Svilna, "
                  "Trapari, Vesela, Viškovci, Zagrađe, Zarilac",
    "Kutjevo": "Kutjevo, Bektež, Bjeliševac, Ciglenik, Ferovac, Grabarje, Gradište, Hrnjevac, Kula, Lukač, "
               "Mitrovac, Ovčare, Poreč, Šumanovci, Tominovac, Venje, Vetovo",
    "Brestovac": "Brestovac, Bolomače, Boričevci, Brđani, Busnovi, Daranovci, Deževci, Dolac, Donji Gučani, "
                 "Gornji Gučani, Ivandol, Jaguplije, Kamenska, Kamenski Vučjak, Kujnik, Mijači, Novo Zvečevo, "
                 "Nurkovac, Orljavac, Pasikovci, Pavlovci, Perenci, Rasna, Sažije, Skenderovci, Sloboština, "
                 "Striježevica, Vilić Selo, Zakorenje, Završje, Čečavac, Čečavački Vučjak, Jeminovac, Koprivna, "
                 "Oblakovac, Šnjegavić",
    "Čaglin": "Čaglin, Djedina Rijeka, Duboka, Ivanovci, Kneževac, Latinovac, Migalovci, Milanlug, Mokreš, "
              "Nova Lipovica, Nova Ljeskovica, Novi Zdenkovac, Ruševo, Sapna, Sovski Dol, Stara Ljeskovica, "
              "Stari Zdenkovac, Vlatkovac, Vukojevica",
    "Jakšić": "Jakšić, Bertelovci, Cerovac, Eminovci, Granje, Radnovac, Rajsavac, Svetinja, Tekić, Treštanovci",
    "Kaptol": "Kaptol, Alilovci, Bešinci, Češljakovci, Doljanovci, Golo Brdo, Komarovci, Novi Bešinci, "
              "Podgorje, Ramanovci",
    "Velika": "Velika, Antunovac, Biškupci, Bratuljevci, Doljanci, Draga, Gornji Vrhovci, Kantrovci, Lučinci, "
              "Milanovac, Milivojevci, Oljasi, Poljanska, Potočani, Radovanci, Stražeman, Toranj, Trenkovo, Trnovac",
}
JLS_OF = {n.strip().lower(): jls for jls, names in NASELJA.items() for n in names.split(",")}
PROVIDER = {
    "davatelj": "Komunalac Požega d.o.o.",
    "web": "https://www.komunalac-pozega.hr",
    "izvor": SITE + "/",
    "zupanija": "Požeško-slavonska",
    "jls": JLS_ORDER,
}
NAPOMENE = [
    "Kalendar na komunalac-pozega.com.hr prikazuje odvoze od sutrašnjeg dana do kraja godine; raniji "
    "mjeseci u ovom rasporedu potječu iz ranijih preuzimanja (od {first}).",
    "Pomaci zbog blagdana već su upisani u datume (odvoz se seli na subotu prije ili poslije blagdana).",
    "Papir i plastika odvoze se zajedno (plavo-žuti spremnik), jednom mjesečno.",
    "Glomazni otpad: datumi iz kalendara; izvan Grada Požege potrebu odvoza prijavite na (034) 440 997 "
    "najkasnije dan uoči odvoza do 13 sati.",
    "Reciklažna dvorišta: Požega (Industrijska 25c), Pleternica (Ante Starčevića 29), Jakšić "
    "(Kolodvorska 215a), Velika (Strossmayerova 20D), Kaptol (Novi Bešinci 2B), Kutjevo (Kamenjača 15).",
]

_last = [0.0]
stats = Counter()


def get(url, body=None, max_age=20 * 3600, tries=5):
    """GET (or POST a form) with a disk cache, at least 0.5 s between requests and retries with backoff."""
    key = hashlib.sha1(f"{url}\n{body or ''}".encode()).hexdigest()
    path = CACHE / key[:2] / key
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        stats["cache"] += 1
        return path.read_bytes()
    data = urllib.parse.urlencode(body).encode() if body else None
    for attempt in range(tries):
        time.sleep(max(0.0, _last[0] + 0.5 - time.time()))
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=90) as r:
                out = r.read()
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if (isinstance(e, urllib.error.HTTPError) and e.code < 500) or attempt == tries - 1:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(2 ** (attempt + 1))
    stats["mreža"] += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".tmp").write_bytes(out)
    path.with_suffix(".tmp").replace(path)
    return out


def names():
    """All location names from the autocomplete list on the home page."""
    page = get(SITE + "/").decode("utf-8", "replace")
    return json.loads(re.search(r"var countries = (\[.*?\]);", page, re.S).group(1))


def location_id(name):
    """The search form answers with <meta http-equiv=Refresh content=0;url=...?s=1&it=ID>."""
    body = {"main": "", "trazim": name, "pucacina": "Vidi", "pucaj": "1", "nassel": "0"}
    page = get(SITE, body, max_age=30 * 86400).decode("utf-8", "replace")
    m = re.search(r"Refresh content=0;url=[^>]*[?&]it=(\d+)", page)
    return int(m.group(1)) if m else None


CELL = re.compile(r'<div class="kvadrat kvadratsir kvadratvis (\w+)">\s*(?:<div style="[^"]*"></div>)?\s*'
                  r'<div class="kvadrat2[^"]*">\s*<div[^>]*>(\d+)</div>')


def calendar(page, vrs):
    """(dates of the first copy, of the second copy, first day shown, problems) for one waste type.

    Every month block must list each day from its first shown day to the month's end."""
    copies, problems, seen, shown = [set(), set()], [], Counter(), []
    if re.search(r"Za ovu ulicu još nisu unijeti datumi odvoza", page):
        return set(), set(), None, []  # the type is not (yet) scheduled for this location
    parts = re.split(r">\s*(" + "|".join(MONTHS) + r") (\d{4})\.\s*</div>", page)
    for month, year, block in zip(parts[1::3], parts[2::3], parts[3::3]):
        y, m = int(year), MONTHS.index(month) + 1
        k = seen[(y, m)]
        seen[(y, m)] += 1
        cells = CELL.findall(block)
        days = [int(d) for _, d in cells]
        last = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)).day
        if k > 1 or not days or days != list(range(days[0], last + 1)):
            problems.append(f"{month} {year} (kopija {k + 1}): dani {days[:2]}…{days[-2:]}")
            continue
        shown.append(date(y, m, days[0]))
        copies[k] |= {date(y, m, int(d)) for c, d in cells if c in (f"kvadboja{vrs}", "kvadboja0")}
    if set(seen.values()) != {2}:
        problems.append(f"kalendar nije prikazan dvaput: {dict(seen)}")
    return copies[0], copies[1], min(shown, default=None), problems


def where(name):
    """(JLS, name as 'Ulica (Naselje)' or 'Naselje') for 'Ulica (Naselje)', 'Naselje: Ulica' or 'Naselje'."""
    m = re.match(r"(.+?) \(([^()]+)\)$", name)
    street, place = m.groups() if m else (None, None)
    if not m and (m := re.match(r"([^:]+): (.+)$", name)):
        place, street = m.groups()
    if not m:
        place = re.split(r" - ", name)[0]
    return JLS_OF.get(place.strip().lower()), f"{street} ({place})" if street else name


def regular(dates_, hol, problems, label):
    """[(date, moved)]: dates off the usual weekday(s) are moved if a holiday is within 7 days."""
    days = Counter(d.weekday() for d in dates_)
    usual = {w for w, n in days.items() if n >= max(2, len(dates_) // 5)}
    out = []
    for d in sorted(dates_):
        off = bool(usual) and d.weekday() not in usual
        if off and not any(abs((d - h).days) <= 7 for h in hol):
            problems.append(f"{label}: {d:%d.%m.} nije uobičajeni dan ({', '.join(podaci.DAYS[w] for w in sorted(usual))})")
        out.append((d, off))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    hol = blagdani(year) + blagdani(year + 1)
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    # past dates per location from the previous file
    past = {}
    for zone in old["zone"].values():
        rows = list(podaci.iter_dates(zone, year))
        for u in zone.get("ulice", []):
            past[u] = rows

    problems, skipped, by_id = [], [], {}
    all_names = names()
    print(f"Lokacija u tražilici: {len(all_names)}")
    for i, name in enumerate(all_names, 1):
        jls, label = where(name)
        it = location_id(name)
        if it is None:
            problems.append(f"{name}: tražilica nije vratila lokaciju")
            continue
        if (label, jls) not in by_id.get(it, []):
            by_id.setdefault(it, []).append((label, jls))
        if i % 100 == 0:
            print(f"  {i}/{len(all_names)} imena, mreža {stats['mreža']}, cache {stats['cache']}", flush=True)

    windows, locations = set(), []
    for n, (it, names_) in enumerate(sorted(by_id.items()), 1):
        jlss = {j for _, j in names_ if j}
        if len(jlss) > 1:
            problems.append(f"it={it}: imena iz više JLS {names_}")
            continue
        names_ = [(nm, next(iter(jlss), None)) for nm, _ in names_]
        rows, missing = defaultdict(str), []
        for vrs, codes in VRS.items():
            page = get(f"{SITE}/?s=1&it={it}&vrs={vrs}").decode("utf-8", "replace")
            first, second, shown, probs = calendar(page, vrs)
            if shown is None and not probs:
                missing.append(vrs)
            else:
                windows.add(shown)
            problems += [f"it={it} vrs={vrs}: {p}" for p in probs]
            if first != second:
                problems.append(f"it={it} vrs={vrs}: dvije kopije kalendara se razlikuju")
            for d in first:
                rows[d] += codes
        if n % 100 == 0:
            print(f"  {n}/{len(by_id)} lokacija, mreža {stats['mreža']}, cache {stats['cache']}", flush=True)
        locations.append((it, names_, dict(rows), tuple(missing)))
    if len(windows) != 1:
        problems.append(f"kalendari počinju različitim danima: {sorted(windows, key=str)}")
    start = min(windows, key=lambda d: d or date.max) or date(year + 1, 1, 1)
    print(f"Prozor kalendara: od {start:%d.%m.%Y.}, lokacija (it) {len(locations)}, izostavljeno {len(skipped)}")

    # merge with the past months and group identical schedules per JLS
    groups = defaultdict(list)
    for it, names_, rows, missing in locations:
        mine = {d: c for d, c in rows.items() if d.year == year}
        if not mine:
            skipped.append(f"{names_[0][0]} (bez odvoza u {year}.)")
            continue
        if names_[0][1] is None:
            skipped.append(f"{names_[0][0]} (naselje nije u popisima naselja davatelja, JLS nepoznata)")
            continue
        older = {}
        for nm, _ in names_:
            older.update({d: (c, mv) for d, c, mv in past.get(nm, []) if d < start})
        moved = {}  # mixed and bio rounds off their usual weekday
        for code in "MB":
            for d, off in regular([d for d, c in mine.items() if code in c], hol, problems, f"{names_[0][0]} {code}"):
                moved[d] = moved.get(d, False) or off
        labeled = [(d, c, mv) for d, (c, mv) in older.items()]
        labeled += [(d, "".join(x for x in podaci.ORDER if x in c), moved.get(d, False)) for d, c in mine.items()]
        key = (names_[0][1], tuple(sorted(labeled)), missing)
        groups[key] += [nm for nm, _ in names_]

    for p in skipped:
        print(f"  izostavljeno: {p}")
    zones, total = {}, Counter()
    def day_of(rows):
        return podaci.regular_day([r for r in rows if "M" in r[1]]) or "bez miješanog"

    order = sorted(groups, key=lambda k: (JLS_ORDER.index(k[0]), podaci.DAYS.index(day_of(k[1]))
                                          if day_of(k[1]) in podaci.DAYS else 9, sorted(groups[k])[0]))
    for n, key in enumerate(order, 1):
        jls, rows, missing = key
        ulice = sorted(set(groups[key]), key=str.lower)
        day = day_of(rows)
        # whole months inside the window: mixed waste 1-10 times (every other week up to twice a week)
        months = Counter(d.month for d, c, _ in rows if "M" in c and d >= start)
        for m in range(start.month + (start.day > 1), 13):
            if not 1 <= months[m] <= 10 and day != "bez miješanog":
                problems.append(f"zona {n} ({jls}): {months[m]} odvoza miješanog u {m}. mjesecu")
        short = [u.split(" (")[0] for u in ulice]
        zones[str(n)] = {
            "jls": jls,
            "podrucje": f"{jls}, {day}: " + ", ".join(short[:4]) + (", …" if len(short) > 4 else ""),
            "ulice": ulice,
        }
        if set(missing) - {4}:  # bulky waste outside the town of Požega is collected on request
            zones[str(n)]["napomena"] = ("Kalendar davatelja za ove lokacije nema datuma za: "
                                         + ", ".join(NAMES[v] for v in missing if v != 4) + ".")
        zones[str(n)]["raw"] = {str(year): podaci.month_lines(list(rows))}
        total.update(c for _, codes, _ in rows for c in codes)
        print(f"Zona {n} ({jls}, {day}): lokacija {len(ulice)}, odvoza {len(rows)}")
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    first = min((d for k in groups for d, _, _ in k[1]), default=start)
    print(f"Prozor: od {start:%d.%m.%Y.}; raniji datumi iz prethodnog upisa od {first:%d.%m.%Y.}")
    data = {**PROVIDER, "napomene": [NAPOMENE[0].format(first=f"{first:%d.%m.%Y.}")] + NAPOMENE[1:], "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona; {dict(total)}); "
          f"HTTP: mreža {stats['mreža']}, cache {stats['cache']}")


if __name__ == "__main__":
    main()
