"""Vukovar: Komunalac d.o.o. Vukovar (komunalac-vu.hr), city + Bogdanovci, Negoslavci, Tompojevci.

    python3 -m izvori.vukovar_komunalac [--year 2026]

The year's page has five tables (Zona 1-5); each has two or three street groups, and each group a row:
mixed waste weekday ("Ponedjeljkom") | glomazni | papir i karton | plastika i metal | biootpad |
staklena ambalaža, the last five as hand-typed date lists like "01.,15. i 29.01., 12. i 26.02.,12., i26.03".
Every street group becomes a zone. The parser reads days that wait for the next "dd.mm" month, and
refuses leftover text; each list must then be a regular 14- or 28-day series on one weekday (dates near
a public holiday may be off), with a plausible number of dates. No holiday shifts are published:
collection runs on holidays.

The municipalities (concessions) have their own pages, linked from the concessions page
("raspored-odvoza-otpada-<općina>"), each with one table in the same layout: settlements, headings,
one row. The mixed waste cell is a weekday or (Tompojevci) a date list every second week. These pages carry no year, so the
dates are read as the requested year and must all fall on working days (a wrong year moves Friday
lists to Saturday). Obvious typos ("30,10.", "17 i") are fixed with a printed note, and a second line
in a cell that only repeats months already listed (Bogdanovci biowaste: last year's Fridays) is dropped.
"""
import argparse
import html
import re
import sys
import time
import urllib.error
from collections import Counter
from datetime import date

import podaci
from izvori.sisak_gos import fetch
from pravila import blagdani, tjedno

SLUG = "vukovar-komunalac"
SITE = "https://www.komunalac-vu.hr"
KONCESIJE = SITE + "/nase-usluge/koncesije/"
PAGE = SITE + "/nase-usluge/poslovni-centar-za-odrzivo-gospodarenje-otpadom/raspored-odvoza-otpada-za-{year}-godinu/"
HEAD = ["Komunalni otpad", "Glomazni otpad", "Papir i karton", "Plastika i metal", "Biootpad", "Staklena ambalaža"]
CODES = ["M", "G", "K", "P", "B", "S"]
DAYS = {"Ponedjeljkom": "pon", "Utorkom": "uto", "Srijedom": "sri", "Četvrtkom": "čet", "Petkom": "pet"}
# code: (min, max dates a year, days between collections or None)
EXPECT = {"G": (3, 6, None), "K": (12, 14, 28), "P": (12, 14, 28), "B": (24, 28, 14), "S": (1, 4, None)}
# municipalities: mixed waste may be a list every 14 days; bulky waste once and glass twice a year
EXPECT_MUNI = {**EXPECT, "M": (24, 27, 14), "G": (1, 3, None)}
TOKEN = re.compile(r"(\d{1,2})\.(\d{1,2})(?!\d)\.?|(\d{1,2})\.")
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Vukovar",
    "web": SITE,
    "zupanija": "Vukovarsko-srijemska",
    "jls": ["Vukovar", "Bogdanovci", "Negoslavci", "Tompojevci"],
    "nazivi": {"P": "Plastika i metal", "S": "Staklena ambalaža"},
    "napomene": [
        "Ulice s istim imenom postoje u Vukovaru, Sotinu i Lipovači; pazite na naselje.",
        "Raspored ne navodi pomicanje odvoza zbog blagdana.",
    ],
}
MUNI = re.compile(r'href="(?:https?://[^"]*?/)?(nase-usluge/koncesije/raspored-odvoza-otpada-[a-z-]+)/?"')
MUNI_NOTE = ("Raspored općine objavljen je bez godine; datumi su čitani kao {year}. "
             "(svi padaju na radne dane).")
TYPOS = [(re.compile(r"(?<![\d.])(\d{1,2}),(\d{1,2})\."), r"\1.\2."),  # "30,10." -> "30.10."
         (re.compile(r"(?<![\d.])(\d{1,2})(?=\s+i\b)"), r"\1.")]  # "17 i 31.07." -> "17. i 31.07."

_last = [0.0]


def get(url, dest=None, tries=4):
    """fetch() with at most 2 requests per second and retries with backoff."""
    for i in range(tries):
        time.sleep(max(0.0, _last[0] + 0.5 - time.monotonic()))
        _last[0] = time.monotonic()
        try:
            return fetch(url, dest)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if i == tries - 1 or getattr(e, "code", 500) < 500:
                raise
            time.sleep(2 * 2 ** i)


def text(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def tables(page):
    """[(zone number, [[cell, ...] per row])] for the tables after 'Zona N' headings."""
    out = []
    for m in re.finditer(r"Zona\s*(\d+)\s*(?:<[^>]+>\s*)*<table(.*?)</table>", page, re.S):
        rows = [[text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
                for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(2), re.S)]
        out.append((m.group(1), [r for r in rows if any(r)]))
    return out


def dates(cell, year):
    """'01.,15. i 29.01., 12. i 26.02.,12., i26.03' -> dates; a day without a month waits for the next 'dd.mm'."""
    out, waiting, problems, pos = [], [], [], 0
    for m in TOKEN.finditer(cell):
        if cell[pos:m.start()].strip(" ,.i"):
            problems.append(f"unexpected {cell[pos:m.start()]!r}")
        pos = m.end()
        if m.group(3):
            waiting.append(int(m.group(3)))
            continue
        month = int(m.group(2))
        for day in waiting + [int(m.group(1))]:
            try:
                out.append(date(year, month, day))
            except ValueError:
                problems.append(f"impossible date {day}.{month}.")
        waiting = []
    if cell[pos:].strip(" ,.i"):
        problems.append(f"unexpected {cell[pos:]!r}")
    if waiting:
        problems.append(f"days {waiting} without a month")
    if out != sorted(set(out)):
        problems.append("dates out of order or repeated")
    return out, [f"{p} in {cell!r}" for p in problems]


def fix(cell, where):
    """Obvious typos in a date list, each one printed."""
    for rx, rep in TYPOS:
        for m in rx.finditer(cell):
            print(f"   {where}: ispravak {m.group(0)!r} -> {m.expand(rep)!r}")
        cell = rx.sub(rep, cell)
    return cell


def lines(cell, year, where):
    """A municipal cell split on line breaks; a later line whose months are all listed above is dropped."""
    parts = [fix(text(p), where) for p in re.split(r"<br\s*/?>|\n", cell)]
    parts = [p for p in parts if p]
    keep, months = [], set()
    for p in parts:
        ds, _ = dates(p, year)
        if keep and ds and {d.month for d in ds} <= months:
            print(f"   {where}: zanemaren redak {p!r} (ponavlja mjesece {sorted({d.month for d in ds})})")
            continue
        keep.append(p)
        months |= {d.month for d in ds}
    return " ".join(keep)


def muni_tables(page):
    """[(općina, [[cell html, ...] per row])] for '<h2>Općina X</h2><table>' on a municipal page."""
    out = []
    for m in re.finditer(r"<h2>\s*Općina\s+([^<]+?)\s*</h2>\s*<table(.*?)</table>", page, re.S):
        rows = [re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
                for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(2), re.S)]
        out.append((m.group(1), rows))
    return out


def settlements(cell, jls):
    """'Naselja: Bogdanovci, Petrovci i Svinjarevci' -> list; an empty cell -> [the municipality]."""
    t = re.sub(r"^Naselja:\s*", "", text(cell))
    if not t:
        print(f"   {jls}: popis naselja je prazan, uzima se naselje {jls}")
        return [jls]
    return [x.strip(" .") for x in re.split(r",|\si\s", t) if x.strip(" .")]


def series(code, ds, hol, expect=EXPECT):
    """Count, one weekday, regular steps; a date within a week of a public holiday may be off."""
    lo, hi, step = expect[code]
    problems = [] if lo <= len(ds) <= hi else [f"{len(ds)} dates, expected {lo}-{hi}"]
    near = {d for d in ds if any(abs((d - h).days) <= 7 for h in hol)}
    days = Counter(d.weekday() for d in ds if d not in near)
    if len(days) > 1:
        problems.append(f"weekdays {dict(days)}")
    if step:
        bad = [f"{a:%d.%m.}-{b:%d.%m.}" for a, b in zip(ds, ds[1:])
               if (b - a).days != step and not {a, b} & near]
        if bad:
            problems.append(f"not every {step} days: {', '.join(bad)}")
        if ds and (ds[0].timetuple().tm_yday > step + 7 or ds[-1].timetuple().tm_yday < 358 - step):
            problems.append(f"does not cover the year ({ds[0]} - {ds[-1]})")
    odd = sorted(d for d in near if days and d.weekday() != days.most_common(1)[0][0])
    return [f"{code}: {p}" for p in problems], odd


def streets(cell):
    """'..., Iločka SOTIN: Antuna Gustava Matoša, ...' -> [..., 'Iločka', 'Antuna Gustava Matoša (Sotin)', ...]."""
    parts = re.split(r"\b(SOTIN|LIPOVAČA):", cell)
    out = [s.strip(" .") for s in parts[0].split(",")]
    for place, part in zip(parts[1::2], parts[2::2]):
        out += [f"{s.strip(' .')} ({place.capitalize()})" for s in part.split(",")]
    return [s for s in out if s]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    url = PAGE.format(year=year)
    try:
        found = tables(get(url).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        sys.exit(f"{url}: HTTP {e.code}. Ništa nije upisano.")
    if [z for z, _ in found] != ["1", "2", "3", "4", "5"]:
        sys.exit(f"Tablice zona na {url}: {[z for z, _ in found]}, očekivano 1-5. Ništa nije upisano.")
    hol = blagdani(year)
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = url
    zones, problems, n = {}, [], 0
    for zona, rows in found:
        # rows come in threes: streets, column headings, dates
        if len(rows) % 3:
            problems.append(f"Zona {zona}: {len(rows)} rows")
            continue
        for street_row, head, row in zip(rows[0::3], rows[1::3], rows[2::3]):
            n += 1
            name = f"Zona {zona}, skupina {n}"
            if head != HEAD or len(street_row) != 1 or len(row) != 6 or row[0] not in DAYS:
                problems.append(f"{name}: unexpected table layout {head} / {row[:1]}")
                continue
            lists, warn = {}, []
            for code, cell in zip(CODES[1:], row[1:]):
                ds, probs = dates(cell, year)
                lists[code] = ds
                probs2, odd = series(code, ds, hol)
                problems += [f"{name} {p}" for p in probs + probs2]
                warn += [f"{code} {d:%d.%m.}" for d in odd]
            rows_ = {d: "M" for d in tjedno(year, DAYS[row[0]])}
            for code, ds in lists.items():
                for d in ds:
                    rows_[d] = rows_.get(d, "") + code
            out = sorted((d, c, False) for d, c in rows_.items())
            ulice = streets(street_row[0])
            day = podaci.DAYS[list(DAYS.values()).index(DAYS[row[0]])]
            zone = {"jls": "Vukovar",
                    "podrucje": f"Zona {zona} ({day}): " + ", ".join(ulice[:3]) + (", …" if len(ulice) > 3 else ""),
                    "opis": street_row[0], "ulice": ulice}
            old = data["zone"].get(str(n), {})
            keep = old.get("raw", {}) if old.get("opis") == zone["opis"] else {}
            zone["raw"] = {**keep, str(year): podaci.month_lines(out)}
            zones[str(n)] = zone
            cnt = Counter(c for _, codes, _ in out for c in codes)
            print(f"Zona {n} (Zona {zona}, {day}): " + ", ".join(f"{c} {cnt[c]}" for c in "MBKPSG")
                  + f", ulica {len(ulice)}" + (f"; izvan ritma uz blagdan: {', '.join(warn)}" if warn else ""))
    # municipalities: pages linked from the concessions page, in the order of the menu
    links = dict.fromkeys(MUNI.findall(get(KONCESIJE).decode("utf-8", "replace")))
    if len(links) != len(PROVIDER["jls"]) - 1:
        problems.append(f"{KONCESIJE}: {len(links)} rasporeda općina, očekivano {len(PROVIDER['jls']) - 1}")
    for path_ in links:
        murl = f"{SITE}/{path_}/"
        try:
            found_m = muni_tables(get(murl).decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            problems.append(f"{murl}: HTTP {e.code}")
            continue
        if len(found_m) != 1:
            problems.append(f"{murl}: {len(found_m)} tablica općine, očekivana 1")
            continue
        jls, rows = found_m[0]
        if len(rows) != 3 or [text(c) for c in rows[1]] != HEAD or len(rows[0]) != 1 or len(rows[2]) != 6:
            problems.append(f"Općina {jls}: unexpected table layout {[len(r) for r in rows]}")
            continue
        if jls not in PROVIDER["jls"]:
            problems.append(f"Općina {jls}: nije u popisu JLS")
            continue
        n += 1
        name = f"Općina {jls}"
        row = [lines(c, year, name) for c in rows[2]]
        rows_, warn = {}, []
        weekly = DAYS.get(row[0])
        if weekly:
            rows_ = {d: "M" for d in tjedno(year, weekly)}
        for code, cell in zip(CODES if not weekly else CODES[1:], row if not weekly else row[1:]):
            ds, probs = dates(cell, year)
            probs2, odd = series(code, ds, hol, EXPECT_MUNI)
            problems += [f"{name} {p}" for p in probs + probs2]
            problems += [f"{name} {code}: {d:%d.%m.%Y} nije radni dan" for d in ds if d.weekday() > 4]
            warn += [f"{code} {d:%d.%m.}" for d in odd]
            for d in ds:
                rows_[d] = rows_.get(d, "") + code
        out = sorted((d, c, False) for d, c in rows_.items())
        ulice = settlements(rows[0][0], jls)
        mdays = Counter(d.weekday() for d, c, _ in out if "M" in c)
        day = podaci.DAYS[mdays.most_common(1)[0][0]] if mdays else "?"
        often = "" if weekly else ", svaki drugi tjedan"
        zone = {"jls": jls, "podrucje": f"Općina {jls} ({day}{often}): " + ", ".join(ulice),
                "opis": text(rows[0][0]) or f"Općina {jls}", "ulice": ulice,
                "napomena": MUNI_NOTE.format(year=year)}
        old = data["zone"].get(str(n), {})
        keep = old.get("raw", {}) if old.get("opis") == zone["opis"] else {}
        zone["raw"] = {**keep, str(year): podaci.month_lines(out)}
        zones[str(n)] = zone
        cnt = Counter(c for _, codes, _ in out for c in codes)
        print(f"Zona {n} ({name}, {day}): " + ", ".join(f"{c} {cnt[c]}" for c in "MBKPSG")
              + f", naselja {len(ulice)}" + (f"; izvan ritma uz blagdan: {', '.join(warn)}" if warn else ""))
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
