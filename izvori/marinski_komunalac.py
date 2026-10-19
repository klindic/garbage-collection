"""Marinski komunalac d.o.o.: Marina, Primorski Dolac, Prgomet and Lećevica (one year PDF per municipality).

    python3 -m izvori.marinski_komunalac [--year 2026]

The home page links "Preuzmi raspored (YYYY.g.)" PDFs, one per municipality. Each is a text PDF with ruled
tables (pdfplumber): settlement groups x 12 month columns, every cell a list of DD.MM. dates. Marina has
separate plastic, paper and glass rows per group and a second table with the weekdays of mixed waste
(X marks, plus a footnote adding Saturdays in summer for some settlements); the other three municipalities
list mixed waste dates per group and one row of "razvrstani reciklabilni otpad" dates for the whole
municipality. Zones are the settlements that share both schedules. Holiday shifts are built into the
listed dates (a date off the row's usual weekday is accepted only in a week with a public holiday and is
marked as moved); for Marina's weekly mixed waste no holiday rule is published, so those dates are kept.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "marinski-komunalac"
SITE = "https://marinskikomunalac.hr"
PAGE = SITE + "/"
JLS = {"MARINA": "Marina", "PRIMORSKI-DOLAC": "Primorski Dolac", "PRGOMET": "Prgomet", "LECEVICA": "Lećevica"}
TYPES = {"PLASTIKA": "P", "PAPIR": "K", "STAKLO": "S"}
RECIKLABILNI = "PKS"  # "razvrstani reciklabilni otpad" of the three smaller municipalities
WEEKDAYS = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK", "SUBOTA"]
DAN = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
PROVIDER = {
    "davatelj": "Marinski komunalac d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": list(JLS.values()),
    "napomene": [
        "Općina Marina: plastika, papir i staklo odvoze se odvojeno prema datumima iz rasporeda; miješani "
        "otpad prema danima u tjednu.",
        "Općine Primorski Dolac, Prgomet i Lećevica: raspored navodi samo \"razvrstani reciklabilni otpad\" "
        "(jedan datum mjesečno ili svaka dva tjedna); u tablici je upisan kao plastika, papir i staklo istog dana.",
        "Pomaci zbog blagdana ugrađeni su u objavljene datume. Za tjedni odvoz miješanog otpada u Općini Marina "
        "pomaci zbog blagdana nisu objavljeni.",
        "Glomazni otpad: na poziv od 1. siječnja do 31. svibnja i od 1. listopada do 31. prosinca, jednom "
        "godišnje do 3 m³ besplatno (021/889-184, marinski.komunalac@gmail.com, Ante Rudana 47, Marina).",
        "Prijave problema pri odvozu: 091 621 3507.",
    ],
}


def naslov(name):
    """'SEVID NA MORU' -> 'Sevid na Moru'."""
    return " ".join(w.lower() if w in ("NA", "I") else w.capitalize() for w in name.split())


def names(cell):
    return [naslov(n) for n in (cell or "").replace("\n", " ").split(",") if n.strip()]


def pdf_links(html, year):
    """{JLS: pdf url} from the home page's "Preuzmi raspored" links."""
    out = {}
    for url in re.findall(r'href="(https?://[^"]+\.pdf)"', html):
        m = re.search(rf"RASPORED-ODVOZA-OTPADA-ZA-{year}\.-?GODINU-OPCINA-([A-Z-]+?)(?:-\d+)?\.pdf$", url)
        if m and m.group(1) in JLS:
            out[JLS[m.group(1)]] = url
    return {j: out[j] for j in JLS.values() if j in out}


def cell_dates(cell, month, year, where, problems):
    out = []
    for d, m in re.findall(r"(\d{1,2})\.(\d{1,2})\.?", cell or ""):
        if int(m) != month:
            problems.append(f"{where}: {d}.{m}. u stupcu {month}. mjeseca")
            continue
        out.append(date(year, month, int(d)))
    return out


def tables(pdf):
    """[(kind, rows)] in reading order; kind "M" (mixed) or "R" (sorted recyclables) from the heading above."""
    out = []
    for page in pdf.pages:
        text = " ".join((page.extract_text() or "").split())
        kinds = re.findall(r"RASPORED (?:ODVOZA|PRIKUPLJANJA) (MIJEŠANOG|RAZVRSTANOG)", text)
        found = page.extract_tables()
        if len(kinds) != len(found):
            raise ValueError(f"{len(found)} tablica, a naslova {len(kinds)}")
        out += [("M" if k == "MIJEŠANOG" else "R", t) for k, t in zip(kinds, found)]
    return out


def mark_moved(rows, where, problems):
    """[date] of one table row -> [(date, moved)]: off the usual weekday only in a week with a holiday."""
    usual = Counter(d.weekday() for d in rows).most_common(1)[0][0]
    hol = set(pravila.blagdani(rows[0].year)) | set(pravila.blagdani(rows[0].year + 1))
    out = []
    for d in rows:
        if d.weekday() == usual:
            out.append((d, False))
            continue
        monday = d - timedelta(days=d.weekday())
        if any(monday <= h <= monday + timedelta(days=6) for h in hol):
            out.append((d, True))
        else:
            problems.append(f"{where}: {d:%d.%m.} je {DAN[d.weekday()]}, a red je {DAN[usual]}")
    return out


def month_rows(row, year, where, problems):
    """The last 12 cells of a table row (Jan..Dec) -> sorted dates; every month needs 1-3 dates."""
    cells = row[-12:]
    dates = []
    for m, cell in enumerate(cells, 1):
        got = cell_dates(cell, m, year, where, problems)
        if not 1 <= len(got) <= 3:
            problems.append(f"{where}: {len(got)} datuma u {m}. mjesecu")
        dates += got
    if len(dates) != len(set(dates)):
        problems.append(f"{where}: datum naveden dvaput")
    return sorted(set(dates))


def marina(tabs, year, problems):
    """Marina's tables -> ([(settlements, {date: [codes, moved]})] of recyclables, {settlement: weekday numbers})."""
    rec_groups, weekdays = [], {}
    for kind, rows in tabs:
        if kind == "R":
            group = None
            for row in rows:
                if row[0]:
                    group = (names(row[0]), defaultdict(lambda: ["", False]))
                    rec_groups.append(group)
                code = TYPES.get((row[1] or "").strip())
                if group is None or code is None or len(row) != 14:
                    problems.append(f"Marina: neočekivan redak {row[:2]}")
                    continue
                where = f"Marina {', '.join(group[0])} {row[1]}"
                for d, moved in mark_moved(month_rows(row, year, where, problems), where, problems):
                    group[1][d][0] += code
                    group[1][d][1] |= moved
        else:
            head = [(h or "").strip() for h in rows[0]]
            if head[1:] != WEEKDAYS[:len(head) - 1]:
                problems.append(f"Marina: zaglavlje dana {head}")
                continue
            for row in rows[1:]:
                days = tuple(i for i, c in enumerate(row[1:]) if (c or "").strip() == "X")
                weekdays[naslov(row[0])] = days
    return rec_groups, weekdays


def opis(dates):
    """'miješani pon i čet' / 'miješani svaki drugi uto' from the mixed waste dates of a zone."""
    mixed = sorted(d for d, (c, mv) in dates.items() if "M" in c and not mv)
    if not mixed:
        return "bez rasporeda miješanog otpada"
    days = Counter(d.weekday() for d in mixed)
    top = max(days.values())
    usual = sorted(w for w, n in days.items() if n >= 0.8 * top)
    extra = sorted(w for w, n in days.items() if 8 <= n < 0.8 * top)
    every = "svaki drugi " if top < 40 else ""
    return (f"miješani {every}" + " i ".join(DAN[w] for w in usual)
            + (", ljeti i " + " i ".join(DAN[w] for w in extra) if extra else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    links = pdf_links(fetch(PAGE).decode("utf-8", "replace"), year)
    if set(links) != set(JLS.values()):
        sys.exit(f"Na {PAGE} nema svih rasporeda za {year} (nađeno: {sorted(links)})")
    problems, zones = [], []  # zones: (jls, settlements, {date: [codes, moved]}, napomena)
    with tempfile.TemporaryDirectory() as tmp:
        for jls, url in links.items():
            path = Path(tmp) / f"{jls}.pdf"
            fetch(url, path)
            with pdfplumber.open(path) as pdf:
                text = " ".join(" ".join((p.extract_text() or "") for p in pdf.pages).split())
                if not re.search(rf"(?:ZA|U) {year}\. GODIN", text):
                    problems.append(f"{jls}: PDF nije za {year}.")
                    continue
                try:
                    tabs = tables(pdf)
                except ValueError as e:
                    problems.append(f"{jls}: {e}")
                    continue
            if jls == "Marina":
                rec_groups, weekdays = marina(tabs, year, problems)
                m = re.search(r"od (\d\d)\.(\d\d)\. do (\d\d)\.(\d\d)\. u naseljima (.+?) miješani komunalni "
                              r"otpad će se odvoziti i subotom", text)
                summer = m and (date(year, int(m.group(2)), int(m.group(1))), date(year, int(m.group(4)), int(m.group(3))),
                                {naslov(n) for n in re.split(r",| I ", m.group(5).upper())})
                if not summer:
                    problems.append("Marina: nema napomene o subotama ljeti")
                listed = {n for g, _ in rec_groups for n in g}
                for n in sorted(set(weekdays) - listed):
                    problems.append(f"Marina: {n} ima dane miješanog otpada, ali nije u rasporedu reciklabilnog")
                for settlements, rec in rec_groups:
                    by_days = {}
                    for n in settlements:
                        by_days.setdefault(weekdays.get(n), []).append(n)
                    for days, group in by_days.items():
                        dates = defaultdict(lambda: ["", False], {d: list(v) for d, v in rec.items()})
                        note = ""
                        if days is None:
                            note = "Dan odvoza miješanog otpada za ovo naselje nije naveden u rasporedu."
                        else:
                            mixed = [d for i in days for d in pravila.tjedno(year, DAN[i])]
                            if summer and set(group) <= summer[2]:
                                mixed += [d for d in pravila.tjedno(year, "sub") if summer[0] <= d <= summer[1]]
                            for d in mixed:
                                dates[d][0] += "M"
                        zones.append((jls, group, dates, note))
                continue
            rec_rows = [r for k, rows in tabs if k == "R" for r in rows if r[0] and r[0] != "NASELJA"]
            mixed_rows = [r for k, rows in tabs if k == "M" for r in rows[1:]]
            if len(rec_rows) != 1 or not mixed_rows or any(len(r) != 13 for r in rec_rows + mixed_rows):
                problems.append(f"{jls}: {len(rec_rows)} redaka reciklabilnog, {len(mixed_rows)} miješanog")
                continue
            where = f"{jls} reciklabilni"
            rec = mark_moved(month_rows(rec_rows[0], year, where, problems), where, problems)
            for row in mixed_rows:
                group = names(row[0])
                where = f"{jls} {', '.join(group)} miješani"
                dates = defaultdict(lambda: ["", False])
                for d, moved in mark_moved(month_rows(row, year, where, problems), where, problems):
                    dates[d][0] += "M"
                    dates[d][1] |= moved
                for d, moved in rec:
                    dates[d][0] += RECIKLABILNI
                    dates[d][1] |= moved
                zones.append((jls, group, dates, ""))

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for i, (jls, group, dates, note) in enumerate(zones, 1):
        for d, (codes, _) in dates.items():
            if len(set(codes)) != len(codes):
                problems.append(f"{jls} {', '.join(group)} {d:%d.%m.}: ista vrsta dvaput ({codes})")
        months = Counter(d.month for d, (c, _) in dates.items() if "M" in c)
        if months and (len(months) != 12 or min(months.values()) < 2):
            problems.append(f"{jls} {', '.join(group)}: miješani po mjesecima {dict(months)}")
        rows = [(d, codes, moved) for d, (codes, moved) in dates.items()]
        zone = {"jls": jls, "podrucje": f"{', '.join(group)} ({opis(dates)})", "ulice": group}
        if note:
            zone["napomena"] = note
        zone["raw"] = {**old.get(str(i), {}).get("raw", {}), str(year): podaci.month_lines(rows)}
        data["zone"][str(i)] = zone
        moved = [f"{d:%d.%m.}" for d, _, mv in sorted(rows) if mv]
        print(f"Zona {i} ({jls}: {zone['podrucje']}): {len(rows)} odvoza"
              + (f", pomaknuto zbog blagdana: {' '.join(moved)}" if moved else ""))
    for p in problems:
        print("PROBLEM", p)
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
