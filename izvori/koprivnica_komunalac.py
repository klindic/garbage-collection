"""Koprivnica and surroundings: GKP Komunalac d.o.o. Koprivnica (komunalac-kc.hr), one PDF per rajon.

    python3 -m izvori.koprivnica_komunalac [--year 2026]

The schedule page links one PDF per rajon (1-15 the town, 16-25 neighbouring municipalities). Page 1
is a table: a row per month, columns MIJEŠANI KOMUNALNI | BIO RAZGRADIVI | PAPIR | PLASTIKA | GLOMAZNI,
cells hold "d.m." dates that may wrap over lines; "3.1. (zamjena za 5.1.)" is a date moved because
of a holiday. The coloured header cells give each column's x range and the month cells each row's
y range, so every date is assigned by position and must belong to its row's month. The street list
page (street -> rajon) gives the zone's streets and is checked against the street list printed
at the top of each PDF.
"""
import argparse
import difflib
import html
import re
import sys
import tempfile
import time
import urllib.error
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
from izvori.sisak_gos import fetch
from pravila import blagdani

SLUG = "koprivnica-komunalac"
SITE = "https://komunalac-kc.hr"
PAGE = SITE + "/raspored-odvoza-komunalnog-otpada-i-odvojeno-prikupljenog-otpada/"
STREETS = SITE + "/popis-ulica-po-rajonima/"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ",
          "KOLOVOZ", "RUJAN", "LISTOPAD", "STUDENI", "PROSINAC"]
HEAD = {"MIJEŠANI": "M", "BIO": "B", "PAPIR": "K", "PLASTIKA": "P", "GLOMAZNI": "G"}
COUNTS = {"M": (24, 36), "B": (12, 40), "K": (12, 14), "P": (12, 26), "G": (0, 2)}  # per rajon and year
PER_MONTH = {"M": 5, "B": 5, "K": 2, "P": 3, "G": 1}
TOKEN = re.compile(r"\(\s*zamjena\s+za\s+(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*\)|(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?"
                   r"|\(\s*(" + "|".join(podaci.DAYS) + r")\s*\)", re.I)
JLS = {"23": ("Drnje", "Rajon 23 je vikend naselje uz jezero Šoderica (k.o. Drnje).")}
PROVIDER = {
    "davatelj": "GKP Komunalac d.o.o. Koprivnica",
    "web": SITE,
    "zupanija": "Koprivničko-križevačka",
    "jls": ["Koprivnica", "Peteranec", "Drnje", "Đelekovec", "Legrad", "Koprivnički Ivanec",
            "Sokolovac", "Koprivnički Bregi", "Novigrad Podravski"],
    "nazivi": {"B": "Bio razgradivi otpad", "G": "Glomazni otpad (obavezno prijaviti)"},
    "napomene": [
        "Spremnike iznijeti na javnu površinu ispred objekta najkasnije do 7:00 sati.",
        "Glomazni otpad odvozi se besplatno samo uz prijavnicu (dobivenu poštom u prosincu).",
        "Reciklažno dvorište Herešin, Hrvatske državnosti 94: pon 7-18 (zimi 7-16), uto-pet 7-15, sub 8-13.",
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


def rajon_pdfs(page, year):
    """{rajon: (pdf url, label)} for links 'RASPORED ODVOZA OTPADA ZA <year>. – RAJON N (Općina X)'."""
    found = {}
    for url, label in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', page, re.S):
        label = text(label)
        m = re.search(rf"ZA {year}\..*RAJON (\d+)", label)
        if m:
            found[m.group(1)] = (url, label)
    return found


def street_table(page):
    """[(street, rajon)] from the street list page."""
    table = page[page.find("<table"):page.find("</table>")]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = [text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(cells) == 2 and cells[1].isdigit():
            rows.append((cells[0], cells[1]))
    return rows


def nice(name):
    """'I VINODOLSKI ODVOJAK' -> 'I Vinodolski odvojak': title case, Roman numerals, generic nouns in lower case."""
    words = name.title().split()
    out = []
    for i, w in enumerate(words):
        if re.fullmatch(r"[IVX]+\.?", w.upper()) and (len(w) > 1 or i == 0 or words[i - 1].upper() in ("I", "II")):
            w = w.upper()
        elif w.lower() in ("i", "od", "do", "pod", "za", "put", "odvojak", "cesta", "ulica") and i:
            w = w.lower()
        out.append(w)
    return re.sub(r"\((\w)", lambda m: "(" + m.group(1).lower(), " ".join(out))


def cell_dates(cell, year, month):
    """[(date, moved, replaced date)] from a cell text like '3.1. (zamjena za 5.1.), 12.1., 19.1.'."""
    out, problems, pos = [], [], 0
    for m in TOKEN.finditer(cell):
        gap = cell[pos:m.start()].strip(" ,.")
        if gap:
            problems.append(f"unexpected text {gap!r} in {cell!r}")
        pos = m.end()
        try:
            if m.group(5):  # "3.1. (subota)": a weekday note, check it
                if not out or podaci.DAYS[out[-1][0].weekday()] != m.group(5).lower():
                    problems.append(f"weekday note does not fit in {cell!r}")
            elif m.group(1):
                if not out:
                    problems.append(f"'zamjena' without a date in {cell!r}")
                    continue
                out[-1] = (out[-1][0], True, date(year, int(m.group(2)), int(m.group(1))))
            else:
                out.append((date(year, int(m.group(4)), int(m.group(3))), False, None))
        except ValueError:
            problems.append(f"impossible date in {cell!r}")
    if cell[pos:].strip(" ,."):
        problems.append(f"unexpected text {cell[pos:]!r}")
    for d, _, _ in out:
        if d.month != month:
            problems.append(f"{d:%d.%m.} is in the {MONTHS[month - 1]} row")
    return out, problems


def read_pdf(path, year, rajon):
    """({code: [(date, moved, replaced)]}, header text, problems) from page 1 of a rajon PDF."""
    page = pdfplumber.open(path).pages[0]
    words = page.extract_words()
    full = " ".join((page.extract_text() or "").split())
    problems = [] if f"ZA {year}. GODINU" in full else [f"no 'ZA {year}. GODINU' on page 1"]
    if not re.search(rf"RAJON {rajon}\b", full):
        problems.append(f"the PDF is not for rajon {rajon}")
    fills = [r for r in page.rects if r["fill"] and r["width"] < page.width * 0.9]

    def cell_of(w):
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        c = [r for r in fills if r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"]]
        return min(c, key=lambda r: r["width"] * r["height"]) if c else None

    cols = {HEAD[w["text"]]: cell_of(w) for w in words if w["text"] in HEAD}
    rows = {MONTHS.index(w["text"]) + 1: cell_of(w) for w in words if w["text"] in MONTHS}
    if sorted(c for c, r in cols.items() if r) != sorted(HEAD.values()) or sorted(m for m, r in rows.items() if r) != list(range(1, 13)):
        return {}, "", problems + [f"table not found: columns {sorted(cols)}, months {sorted(rows)}"]
    top, bottom = rows[1]["top"], rows[12]["bottom"]
    header = " ".join(w["text"] for w in words if w["bottom"] < min(r["top"] for r in cols.values())
                      and w["top"] > min(x["top"] for x in words if "RAJON" in x["text"]) + 5)
    cells = {}
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        if not top - 2 <= cy <= bottom + 2 or w["x0"] < rows[1]["x1"] - 2:
            continue
        col = [c for c, r in cols.items() if r["x0"] - 1 <= cx <= r["x1"] + 1]
        row = [m for m, r in rows.items() if r["top"] - 1 <= cy <= r["bottom"] + 1]
        if len(col) != 1 or len(row) != 1:
            problems.append(f"{w['text']!r} at ({cx:.0f}, {cy:.0f}) is not in one table cell")
            continue
        cells.setdefault((row[0], col[0]), []).append(w)
    found = {c: [] for c in HEAD.values()}
    for (month, code), ws in sorted(cells.items()):
        ws.sort(key=lambda w: (round(w["top"]), w["x0"]))
        dates, probs = cell_dates(" ".join(w["text"] for w in ws), year, month)
        found[code] += dates
        problems += [f"{MONTHS[month - 1]} {code}: {p}" for p in probs]
    return found, header, problems


def check(found, hol):
    """Counts per type, one or two weekdays per column, replacements next to a public holiday."""
    problems = []
    for code, dates in found.items():
        lo, hi = COUNTS[code]
        if not lo <= len(dates) <= hi:
            problems.append(f"{len(dates)}x {code}, expected {lo}-{hi}")
        if len({d for d, _, _ in dates}) != len(dates):
            problems.append(f"{code}: a date is listed twice")
        months = Counter(d.month for d, _, _ in dates)
        if months and max(months.values()) > PER_MONTH[code]:
            problems.append(f"{code}: {max(months.values())} dates in one month")
        # monthly rounds drift a little (Easter week, Saturdays), but most dates keep one weekday
        days = Counter(d.weekday() for d, moved, _ in dates if not moved)
        if code != "G" and days and (days.most_common(1)[0][1] < sum(days.values()) * 2 / 3
                                     or len(dates) >= 20 and len(days) > 2):
            problems.append(f"{code}: regular dates on weekdays {dict(days)} (wrong column?)")
        for d, moved, replaced in dates:
            if moved and (abs((d - replaced).days) > 14 or not any(abs((replaced - h).days) <= 2 for h in hol)):
                problems.append(f"{code} {d:%d.%m.} replaces {replaced:%d.%m.}, which is not next to a holiday")
            if moved and replaced in {x for x, _, _ in dates}:
                problems.append(f"{code} {replaced:%d.%m.} is replaced but still listed")
    return problems


def matches(street, header_words):
    """Every word of the street name is (nearly) a word of the PDF's street list."""
    words = [w for w in re.findall(r"[A-ZČĆŽŠĐ]{3,}", street.upper()) if w not in ("ULICA", "TRG")]
    return all(difflib.get_close_matches(w, header_words, n=1, cutoff=0.75) for w in words)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    pdfs = rajon_pdfs(get(PAGE).decode("utf-8", "replace"), year)
    streets = street_table(get(STREETS).decode("utf-8", "replace"))
    if len(pdfs) < 20 or len(streets) < 250:
        sys.exit(f"Našao {len(pdfs)} PDF-ova za {year} i {len(streets)} ulica, očekivano 25 i ~329. Ništa nije upisano.")
    hol = blagdani(year)
    ok, zones, misses = True, {}, []
    if set(r for _, r in streets) - set(pdfs):
        print(f"PROBLEM rajoni s popisa ulica bez PDF-a: {sorted(set(r for _, r in streets) - set(pdfs))}")
        ok = False
    with tempfile.TemporaryDirectory() as tmp:
        for rajon, (url, label) in sorted(pdfs.items(), key=lambda kv: int(kv[0])):
            pdf = Path(tmp) / f"rajon{rajon}.pdf"
            get(url, pdf)
            found, header, problems = read_pdf(pdf, year, rajon)
            if found:
                problems += check(found, hol)
            mine = [s for s, r in streets if r == rajon]
            hwords = re.findall(r"[A-ZČĆŽŠĐ]{3,}", header.upper())
            misses += [f"{s} (rajon {rajon})" for s in mine if not matches(s, hwords)]
            if not mine:
                problems.append("no streets on the street list page")
            if problems:
                print(f"Rajon {rajon}: PROBLEM ({url.rsplit('/', 1)[1]})")
                for p in problems[:15]:
                    print(f"   {p}")
                ok = False
                continue
            m = re.search(r"\(Općina ([^)]+)\)", label)
            jls, note = (m.group(1), None) if m else JLS.get(rajon, ("Koprivnica", None))
            ulice = [nice(s) for s in mine]
            rows = {}
            for code, dates in found.items():
                for d, moved, _ in dates:
                    codes, mv = rows.get(d, ("", False))
                    rows[d] = (codes + code, mv or moved)
            rows = sorted((d, c, mv) for d, (c, mv) in rows.items())
            short = ", ".join(ulice[:4]) + (", …" if len(ulice) > 4 else "")
            zone = {"jls": jls, "podrucje": f"Rajon {rajon}: {short}", "opis": header, "ulice": ulice}
            if note:
                zone["napomena"] = note
            old = data["zone"].get(rajon, {})
            zone["raw"] = {**old.get("raw", {}), str(year): podaci.month_lines(rows)}
            zones[rajon] = zone
            moved = sum(1 for d, c, mv in rows if mv)
            print(f"Rajon {rajon} ({jls}): " + ", ".join(f"{c} {len(found[c])}" for c in "MBKPG")
                  + f", pomaknuto {moved}, ulica {len(ulice)}")
    # The street list page and the PDF headers are two sources for the same streets.
    print(f"Ulice s popisa koje nisu u zaglavlju PDF-a svog rajona: {len(misses)} od {len(streets)}"
          + (f": {', '.join(misses)}" if misses else ""))
    if len(misses) > len(streets) * 0.05:
        print("PROBLEM previše ulica ne odgovara PDF-ovima")
        ok = False
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} rajona)")


if __name__ == "__main__":
    main()
