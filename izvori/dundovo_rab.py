"""Rab: Dundovo d.o.o. (dundovo.hr), six areas, one PDF with four tables.

    python3 -m izvori.dundovo_rab [--year 2026]

The page "Raspored odvoza otpada" links the year's PDF (Word tables with a text layer). Words are put
in columns by the x position of the area headings (the glass table has its own column order):
- mixed waste (MKO) and biowaste: weekday(s) per area and period (once a week, mixed waste twice a week
  from 16.6. to 14.9.);
- plastic (with metal and tetrapak) and paper: explicit dates per month, "svaki <dan>" in summer
  (weekly, "(do 28.9)" ends it); a date in brackets is the replacement of a holiday (or, when the bracketed
  date is the holiday itself, the date before it is the replacement);
- glass: explicit dates under the rules ("1. četvrtak u mjesecu"; "navedeni datumi su ujedno i dani odvoza").
The holiday rule under the first table (1.1., 1.5., 25.12. a day earlier; 6.1., 6.4., 18.11. a day later)
is applied to the weekday rules; other holidays are normal collection days. A date off its area's weekday
must be within a week of a holiday (moved), except the dates listed in SUSPECT, which are kept as printed
and get a note.
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

SLUG = "dundovo-rab"
SITE = "https://www.dundovo.hr"
PAGE = SITE + "/raspored-odvoza-otpada"
AREAS = {"PALIT": "Palit, Rab, PC Mali Palit", "BARBAT": "Barbat", "MUNDANIJE": "Mundanije",
         "SUPETARSKA": "Supetarska Draga", "KAMPOR": "Kampor", "BANJOL": "Banjol"}
ULICE = {"PALIT": ["Palit", "Rab", "PC Mali Palit"], "SUPETARSKA": ["Supetarska Draga"]}
DAYS = {"ponedjeljak": 0, "utorak": 1, "srijeda": 2, "srijedu": 2, "četvrtak": 3, "petak": 4, "subota": 5}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
TITLES = [("MIJEŠANOG", "MB"), ("PLASTIČNOG", "P"), ("PAPIRA", "K"), ("STAKLENOG", "S")]
# dates printed off the area's weekday with no holiday near: kept as printed, with a note
SUSPECT = {("BANJOL", "P", (12, 12)): "Plastika 12.12. (subota) prepisana je kako je objavljena; vjerojatno je "
                                       "riječ o petku 11.12. – provjeriti kod davatelja."}
PROVIDER = {
    "davatelj": "Dundovo d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Primorsko-goranska",
    "jls": ["Rab"],
    "nazivi": {"P": "Plastika, metal i tetrapak", "K": "Papir i karton"},
}
NAPOMENE = [
    "Spremnike pripremite za pražnjenje najkasnije do 06:00 sati, odnosno od 22:00 sata prethodne večeri.",
    "Ako spremnik iz objektivnih razloga ne bude ispražnjen prema rasporedu, usluga se u pravilu vrši sljedećeg dana.",
    "Od 16.6. do 14.9. miješani komunalni otpad odvozi se dvaput tjedno, a plastika i papir svaki tjedan "
    "(od 1.1. do 30.4. te od 5.10. do 31.12. svaka dva tjedna).",
    "Općina Lopar ima drugog davatelja (Lopar Vrutak d.o.o.).",
]


def centre(w):
    return (w["x0"] + w["x1"]) / 2


def lines(words):
    """Words grouped by line (top within 3 pt), each line sorted by x."""
    out = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(out[-1][0]["top"] - w["top"]) < 3:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in out]


def area_centres(words):
    """{area: x centre} from the heading words of one table."""
    out = {}
    for w in words:
        if w["text"] in AREAS and w["text"] not in out:
            out[w["text"]] = centre(w)
    return dict(sorted(out.items(), key=lambda kv: kv[1]))  # left to right


def column(w, centres):
    return min(centres, key=lambda a: abs(centres[a] - centre(w)))


def cell_dates(text, year, holidays):
    """'6.1./(7.1.)/20.1.' -> [(7.1., True), (20.1., False)]; '30.4.(1.5.)' -> [(30.4., True)]."""
    out = []
    for m in re.finditer(r"(\()?(\d{1,2})\.(\d{1,2})\.?\)?", text):
        d = date(year, int(m.group(3)), int(m.group(2)))
        if m.group(1):  # bracketed: the holiday itself, or the replacement of the date before it
            if d in holidays:
                if out:
                    out[-1] = (out[-1][0], True)
            elif out and out[-1][0] in holidays:
                out[-1] = (d, True)
            else:
                raise ValueError(f"zagrada bez blagdana: {text!r}")
        else:
            out.append((d, False))
    return out


def weekly(year, month, wd, until=None):
    d = date(year, month, 1)
    out = []
    while d.month == month:
        if d.weekday() == wd and (until is None or d <= until):
            out.append(d)
        d += timedelta(days=1)
    return out


def read(pdf, year, problems):
    """({area: [(date, codes, moved)]}, holiday rule {holiday: new date}, [notes])."""
    page = pdf.pages[0]
    words = page.extract_words()
    text = " ".join((page.extract_text() or "").split())
    if f"01.01.{year}. - 31.12.{year}." not in text:
        problems.append(f"raspored nije za {year}. godinu")
    hol = set(pravila.blagdani(year))
    rule = {}
    for d, m, how in re.findall(r"(\d{1,2})\.(\d{1,2})\. [^;]*?odvoz se vrši dan (ranije|kasnije)", text):
        h = date(year, int(m), int(d))
        if h not in hol:
            problems.append(f"pravilo za {h} – nije blagdan")
        rule[h] = h + timedelta(days=-1 if how == "ranije" else 1)
    if len(rule) != 6:
        problems.append(f"pravilo za blagdane: {len(rule)} blagdana, očekivano 6")
    tops = {}
    for code_word, kind in TITLES:
        t = [w["top"] for w in words if w["text"] == code_word and w["x0"] > 150]
        if not t:
            problems.append(f"nema tablice {code_word}")
            return {}, rule
        tops[kind] = t[0]
    bands = dict(zip(tops, list(tops.values())[1:] + [page.height]))
    rows = defaultdict(list)

    # mixed waste and biowaste: weekday(s) per period and area
    band = [w for w in words if tops["MB"] < w["top"] < bands["MB"]]
    centres = area_centres(band)
    sub = sorted((w for w in band if w["text"] in ("MKO", "BIO")), key=lambda w: w["x0"])
    if list(centres) != list(AREAS) or len(sub) != 12:
        problems.append(f"tablica miješanog otpada: stupci {list(centres)}, {len(sub)} podstupaca")
        return {}, rule
    labels = [(w["top"], re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", w["text"])) for w in band if w["x0"] < 60]
    labels = [(t, date(int(m.group(3)), int(m.group(2)), int(m.group(1)))) for t, m in labels if m]
    # (start label top, end label top, first day, last day)
    periods = [(labels[i][0], labels[i + 1][0], labels[i][1], labels[i + 1][1]) for i in range(0, len(labels) - 1, 2)]
    if not periods or periods[0][2] != date(year, 1, 1) or periods[-1][3] != date(year, 12, 31) or \
            any(b + timedelta(days=1) != c for (_, _, _, b), (_, _, c, _) in zip(periods, periods[1:])):
        problems.append(f"razdoblja miješanog otpada: {[p[2:] for p in periods]}")
    for i, (top, _, a, b) in enumerate(periods):
        top = periods[i - 1][1] + 2 if i else top - 6  # below the previous period's end label
        end_top = periods[i + 1][0] - 2 if i + 1 < len(periods) else bands["MB"]
        cells = defaultdict(set)
        for w in band:
            if top <= w["top"] < end_top and w["text"].lower() in DAYS and w["text"][0].isupper():  # not the notes
                s = min(range(12), key=lambda j: abs(centre(sub[j]) - centre(w)))
                cells[(list(AREAS)[s // 2], sub[s]["text"])].add(DAYS[w["text"].lower()])
        for area in AREAS:
            for kind, code in (("MKO", "M"), ("BIO", "B")):
                days = cells.get((area, kind))
                if not days:
                    problems.append(f"{area} {kind} {a}–{b}: nema dana")
                    continue
                d = a
                while d <= b:
                    if d.weekday() in days:
                        rows[area].append((rule.get(d, d), code, d in rule))
                    d += timedelta(days=1)
        print(f"Razdoblje {a:%d.%m.}–{b:%d.%m.}: " + ", ".join(
            f"{area} M {'+'.join(DAN[x][:3] for x in sorted(cells[(area, 'MKO')]))}"
            f"/B {'+'.join(DAN[x][:3] for x in sorted(cells[(area, 'BIO')]))}" for area in AREAS))

    # plastic and paper: one row per month
    for kind in ("P", "K"):
        band = [w for w in words if tops[kind] < w["top"] < bands[kind]]
        centres = area_centres(band)
        if list(centres) != list(AREAS):
            problems.append(f"tablica {kind}: stupci {list(centres)}")
            continue
        seen = set()
        for line in lines(band):
            month = line[0]["text"].lower()
            if month not in MONTHS or line[0]["x0"] > 60:
                continue
            mo = MONTHS.index(month) + 1
            seen.add(mo)
            cells = defaultdict(list)
            for w in line[1:]:
                cells[column(w, centres)].append(w["text"])
            for area in AREAS:
                cell = " ".join(cells.get(area, []))
                m = re.match(r"svak[ia] (\w+?)(?:\(do (\d{1,2})\.(\d{1,2})\.?\))?$", cell)
                try:
                    if m and m.group(1) in DAYS:
                        until = m.group(2) and date(year, int(m.group(3)), int(m.group(2)))
                        found = [(rule.get(d, d), d in rule) for d in weekly(year, mo, DAYS[m.group(1)], until)]
                    else:
                        found = cell_dates(cell, year, hol)
                except ValueError as e:
                    problems.append(f"{area} {kind} {month}: {e}")
                    continue
                if not found:
                    problems.append(f"{area} {kind} {month}: nema datuma ({cell!r})")
                rows[area] += [(d, kind, moved) for d, moved in found]
        if seen != set(range(1, 13)):
            problems.append(f"tablica {kind}: mjeseci {sorted(seen)}")

    # glass: every date in the table, by column
    band = [w for w in words if tops["S"] < w["top"] < bands["S"] and not w["text"].startswith("*")]
    band = [w for w in band if w["top"] < min([x["top"] for x in words if x["text"] == "navedeni"] or [9999])]
    centres = area_centres(band)
    if set(centres) != set(AREAS):
        problems.append(f"tablica stakla: stupci {list(centres)}")
    else:
        for w in band:
            if re.search(r"\d{1,2}\.\d{1,2}", w["text"]) and w["x0"] > 60:
                rows[column(w, centres)] += [(d, "S", m) for d, m in cell_dates(w["text"].strip("()"), year, hol)]
    return rows, rule


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    html = fetch(PAGE).decode("utf-8", "replace")
    m = re.search(rf'href="([^"]*Odvoz_{year}[^"]*\.pdf)"', html, re.I)
    if not m:
        sys.exit(f"Na {PAGE} nema rasporeda za {year}.")
    url = m.group(1) if m.group(1).startswith("http") else SITE + "/" + m.group(1).lstrip("/")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raspored.pdf"
        fetch(url, path)
        rows, rule = read(pdfplumber.open(path), year, problems)
    if not rows:
        for p in problems:
            print(f"   PROBLEM {p}")
        sys.exit("Ništa nije upisano.")

    hol = pravila.blagdani(year)
    zones = {}
    for i, area in enumerate(AREAS, 1):
        merged, notes = {}, []
        regular = {c: Counter(d.weekday() for d, cc, m in rows[area] if c in cc and not m).most_common(1)[0][0]
                   for c in "MBPKS"}
        for d, codes, moved in rows[area]:
            if d.year != year:
                continue
            for c in codes:
                key = (area, c, (d.month, d.day))
                near = any(abs((d - h).days) <= 7 for h in hol)
                if c != "M" and d.weekday() != regular[c] and not moved:
                    if key in SUSPECT:
                        notes.append(SUSPECT[key])
                        print(f"{AREAS[area]}: {SUSPECT[key]}")
                    elif near:
                        moved = True
                    elif not (c == "S" and 6 <= d.month <= 9):  # summer glass runs on other days
                        problems.append(f"{area} {c} {d} ({DAN[d.weekday()]}) nije {DAN[regular[c]]}, a nema blagdana")
                old = merged.get(d, ("", False))
                if c in old[0]:
                    problems.append(f"{area}: {d} {c} dvaput")
                merged[d] = (old[0] + c, old[1] or moved)
        counts = Counter((d.month, c) for d, (cc, _) in merged.items() for c in cc)
        for mo in range(1, 13):  # once a week (twice for mixed waste), every 2 weeks off season, glass monthly
            summer = 7 <= mo <= 8
            for c, lo, hi in (("M", 8 if summer else 3, 10), ("B", 3, 5), ("P", 4 if summer else 1, 5),
                              ("K", 4 if summer else 1, 5), ("S", 1, 3)):
                if not lo <= counts[(mo, c)] <= hi:
                    problems.append(f"{area}: {counts[(mo, c)]}× {c} u {mo}. mjesecu")
        zone = {"jls": "Rab", "podrucje": AREAS[area], "ulice": ULICE.get(area, [AREAS[area]])}
        if notes:
            zone["napomena"] = " ".join(dict.fromkeys(notes))
        zone["raw"] = {str(year): podaci.month_lines([(d, c, m) for d, (c, m) in merged.items()])}
        zones[str(i)] = zone
        print(f"Zona {i} {AREAS[area]}: " + ", ".join(f"{c} {sum(n for (_, x), n in counts.items() if x == c)}"
                                                       for c in "MBPKS"))
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "napomene": NAPOMENE + [
        "Blagdani (pravilo iz rasporeda): " + ", ".join(
            f"{h:%d.%m.} → {n:%d.%m.}" for h, n in sorted(rule.items())) +
        "; na ostale blagdane odvozi se prema rasporedu. Zamjenski datumi u tablicama označeni su kao pomaknuti.",
        f"Izvor: {url}",
    ], "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("podrucje") == zone["podrucje"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
