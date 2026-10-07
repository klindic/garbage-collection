"""Vukovar: Komunalac d.o.o. Vukovar (komunalac-vu.hr), one HTML page with a table per zone.

    python3 -m izvori.vukovar_komunalac [--year 2026]

The year's page has five tables (Zona 1-5); each has two or three street groups, and each group a row:
mixed waste weekday ("Ponedjeljkom") | glomazni | papir i karton | plastika i metal | biootpad |
staklena ambalaža, the last five as hand-typed date lists like "01.,15. i 29.01., 12. i 26.02.,12., i26.03".
Every street group becomes a zone. The parser reads days that wait for the next "dd.mm" month, and
refuses leftover text; each list must then be a regular 14- or 28-day series on one weekday (dates near
a public holiday may be off), with a plausible number of dates. No holiday shifts are published:
collection runs on holidays.
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
PAGE = SITE + "/nase-usluge/poslovni-centar-za-odrzivo-gospodarenje-otpadom/raspored-odvoza-otpada-za-{year}-godinu/"
HEAD = ["Komunalni otpad", "Glomazni otpad", "Papir i karton", "Plastika i metal", "Biootpad", "Staklena ambalaža"]
CODES = ["M", "G", "K", "P", "B", "S"]
DAYS = {"Ponedjeljkom": "pon", "Utorkom": "uto", "Srijedom": "sri", "Četvrtkom": "čet", "Petkom": "pet"}
# code: (min, max dates a year, days between collections or None)
EXPECT = {"G": (3, 6, None), "K": (12, 14, 28), "P": (12, 14, 28), "B": (24, 28, 14), "S": (1, 4, None)}
TOKEN = re.compile(r"(\d{1,2})\.(\d{1,2})(?!\d)\.?|(\d{1,2})\.")
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Vukovar",
    "web": SITE,
    "zupanija": "Vukovarsko-srijemska",
    "jls": ["Vukovar"],
    "nazivi": {"P": "Plastika i metal", "S": "Staklena ambalaža"},
    "napomene": [
        "Ulice s istim imenom postoje u Vukovaru, Sotinu i Lipovači; pazite na naselje.",
        "Raspored ne navodi pomicanje odvoza zbog blagdana.",
    ],
}

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


def series(code, ds, hol):
    """Count, one weekday, regular steps; a date within a week of a public holiday may be off."""
    lo, hi, step = EXPECT[code]
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
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
