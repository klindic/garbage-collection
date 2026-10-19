"""Ivakop d.o.o. (Ivanić-Grad): Ivanić-Grad, Kloštar Ivanić, Križ; five weekday zones.

    python3 -m izvori.ivakop [--year 2026]

The page "Raspored odvoza otpada" embeds the calendar of its search widget as JSON
(<script id="ivakop-kalendar-js-before"> window.IVAKOP_KALENDAR = {events, zones, ...}): every mixed waste,
plastic and paper collection with its zone (a weekday) and a note when a holiday moved it ("Odvoz s
25.12.2026. pomaknut na 24.12.2026."), and each zone's settlements. The JSON only holds a window of
months (October to December 2026 when this was written), so dates already in podaci/ivakop.json are kept
and the window replaces only its own dates. The year PDF linked from the same page has no text layer but
draws every day as a cell filled with the zone colour (columns: mixed, plastic, paper); the day numbers are
vector glyphs, so the cells are found from their positions and each month block must match the calendar
of that month exactly (first weekday, number of days). The PDF dates are used only when they equal the JSON
on every date the JSON covers; they fill the months the JSON and the earlier file lack. Red cells are
non-working days; a zone colour on another weekday is a moved collection. A weekday zone that spans two
municipalities (Petak: Križ and Ivanić-Grad) is split into one zone per municipality.
"""
import argparse
import calendar
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "ivakop"
SITE = "https://www.ivakop.hr"
PAGE = SITE + "/raspored-odvoza-otpada/"
TYPES = {"mixed": "M", "plastic": "P", "paper": "K"}
DAYS = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak"]
# CMYK fills in the PDF -> zone colour in the JSON (the JSON colours are checked on every run)
PDF_COLOURS = {(0.0, 0.5, 1.0, 0.0): "#f7941d", (0.639, 0.0, 0.26, 0.0): "#48c2c5",
               (0.25, 0.4, 0.65, 0.0): "#c49a6c", (0.75, 0.0, 1.0, 0.0): "#39b54a", (0.0, 0.0, 0.0, 0.3): "#bcbec0"}
RED = (0.0, 1.0, 1.0, 0.0)
ZONE_COLOURS = dict(zip(DAYS, ["#f7941d", "#48c2c5", "#c49a6c", "#39b54a", "#bcbec0"]))
# Municipality of every settlement name the JSON zones use (official settlements; parts as written by Ivakop)
JLS_OF = {
    "Kloštar Ivanić": ["Kloštar Ivanić", "Vinari", "Vinogradski odvojci", "Sobočani", "Lipovec Lonjski",
                       "Šćapovec", "Bešlinec", "Čemernica Lonjska", "Donja Obreška", "Gornja Obreška", "Krišci",
                       "Predavec", "Stara Marča"],
    "Ivanić-Grad": ["Ivanić-Grad - centar", "Donji Šarampov", "Šemovec Breški", "Trebovec", "Zelina Breška",
                    "Greda Breška", "Šarampov Gornji (područje iza željezničke pruge prema Kloštru)", "Siporeks",
                    "Lonja", "Jalševec Breški", "Opatinec", "Lepšić", "Tarno", "Posavski Bregi", "Zaklepica",
                    "Topolje", "Lijevi Dubrovčak", "Prerovec", "Prečno", "Donja Poljana", "Gornja Poljana",
                    "Caginec", "Šumećani", "Prkos Ivanićki", "Graberje Ivanićko", "Grabersko brdo",
                    "Deanovečko Brdo", "Deanovec", "Derežani"],
    "Križ": ["Križ", "Novoselec", "Bunjani", "Mala Hrastilnica", "Velika Hrastilnica", "Johovec", "Širinec",
             "Okešinec", "Vezišće", "Obedišće", "Razljev", "Rečica Kriška", "Šušnjari",
             "Bunjani - Staklena ulica", "Konšćani", "Gornji Prnjarovec", "Donji Prnjarovec",
             "dio Staklene ulice (iz pravca Šumećana)"],
}
PROVIDER = {
    "davatelj": "Ivakop d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zagrebačka",
    "jls": ["Ivanić-Grad", "Kloštar Ivanić", "Križ"],
}
NAPOMENE = [
    "Miješani komunalni otpad odvozi se svaki tjedan, plastika dvaput i papir jednom mjesečno, prema danu "
    "odvoza naselja (ponedjeljak do petak).",
    "Biootpad iz obiteljskih kuća prikuplja se jednom tjedno na dan odvoza miješanog komunalnog otpada, iz "
    "stambenih zgrada četvrtkom ili petkom (prema kalendaru Ivakopa); datumi biootpada nisu posebno navedeni.",
    "Reciklažna dvorišta: Ivanić-Grad (Tarno 13 B), Kloštar Ivanić (Čemernička 34), Križ (Poduzetnička "
    "cesta); ne rade na blagdane. Ivakop: 01 2888 938, ivakop@ivakop.hr.",
]


def col(obj):
    c = obj.get("non_stroking_color")
    return tuple(round(float(v), 3) for v in c) if isinstance(c, (list, tuple)) else None


def kalendar(html):
    """The window.IVAKOP_KALENDAR object from the page."""
    m = re.search(r"<script[^>]*id=['\"]ivakop-kalendar-js-before['\"][^>]*>\s*window\.IVAKOP_KALENDAR\s*=\s*", html)
    if not m:
        sys.exit(f"Na {PAGE} nema skripte ivakop-kalendar-js-before")
    return json.JSONDecoder().raw_decode(html, m.end())[0]


def groups(values, tol):
    """Sorted values -> runs whose neighbours are at most tol apart."""
    out = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def day_numbers(page):
    """Centres (x, y) of the day numbers: digit glyphs (curves about 7 pt high) joined left to right."""
    glyphs = sorted((c for c in page.curves if c["fill"] and 6.5 <= c["height"] <= 7.6 and 1.5 <= c["width"] <= 5.2),
                    key=lambda c: c["x0"])
    cells = []
    for g in glyphs:
        cell = next((c for c in cells if abs(c[2] - g["top"]) < 1.5 and -1 < g["x0"] - c[1] < 5), None)
        if cell:
            cell[1] = max(cell[1], g["x1"])
        else:
            cells.append([g["x0"], g["x1"], g["top"], g["bottom"]])
    return [((x0 + x1) / 2, (t + b) / 2) for x0, x1, t, b in cells]


def read_pdf(path, year, colour_zone, problems, reds):
    """{(date, code): {zone weekday}} from the year PDF (pages 2-5 = quarters; mixed, plastic, paper columns).

    Dates of red (non-working day) cells are added to reds.
    """
    found = defaultdict(set)
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) < 5:
            problems.append(f"PDF ima {len(pdf.pages)} stranica")
            return found
        for q, page in enumerate(pdf.pages[1:5]):
            nums = day_numbers(page)
            rects = [(col(r), r) for r in page.rects
                     if r["fill"] and r["width"] < 30 and (col(r) in PDF_COLOURS or col(r) == RED)]
            for panel, code in enumerate("MPK"):
                pn = [n for n in nums if panel * page.width / 3 <= n[0] < (panel + 1) * page.width / 3]
                blocks = []
                for row in groups([n[1] for n in pn], 3):
                    if blocks and row[0] - blocks[-1][-1][-1] < 25:
                        blocks[-1].append(row)
                    else:
                        blocks.append([row])
                if len(blocks) != 3:
                    problems.append(f"PDF str. {q + 2}, stupac {code}: {len(blocks)} mjeseci umjesto 3")
                    continue
                for b, rows in enumerate(blocks):
                    month = 3 * q + b + 1
                    cells = [n for n in pn if rows[0][0] - 0.5 <= n[1] <= rows[-1][-1] + 0.5]
                    cols = groups([n[0] for n in cells], 6)
                    if len(cols) != 7:
                        problems.append(f"PDF {month}. mjesec {code}: {len(cols)} stupaca umjesto 7")
                        continue
                    def cell(x, y):
                        return (next(i for i, r in enumerate(rows) if r[0] <= y <= r[-1]),
                                next(i for i, c in enumerate(cols) if c[0] <= x <= c[-1]))
                    pos = sorted((cell(x, y), (x, y)) for x, y in cells)
                    first = date(year, month, 1).weekday()
                    expect = [divmod(first + d, 7) for d in range(calendar.monthrange(year, month)[1])]
                    if [p for p, _ in pos] != expect:
                        problems.append(f"PDF {month}. mjesec {code}: brojevi dana ne odgovaraju kalendaru")
                        continue
                    for day, (_, (x, y)) in enumerate(pos, 1):
                        hit = {c for c, r in rects  # rect over the cell (half cells count)
                               if r["top"] - 1 <= y <= r["bottom"] + 1
                               and min(r["x1"], x + 8) - max(r["x0"], x - 8) >= 4}
                        if RED in hit:  # non-working day: drawn over the zone column
                            reds.add(date(year, month, day))
                            continue
                        for c in hit:
                            found[(date(year, month, day), code)].add(colour_zone[PDF_COLOURS[c]])
    return found


FIX = {"Konščani": "Konšćani", "Ščapovec": "Šćapovec"}  # obvious typos in the zone texts


def split_names(description):
    """'Križ, Novoselec, Mala i Velika Hrastilnica' -> names; 'naselje Lonja' -> 'Lonja'."""
    out = []
    for part in re.split(r",\s*", description):
        part = re.sub(r"^naselje\s+", "", " ".join(part.split()))
        m = re.fullmatch(r"(\w+) i (\w+) (\w+)", part)
        out += [f"{m.group(1)} {m.group(3)}", f"{m.group(2)} {m.group(3)}"] if m else [part] if part else []
    return [FIX.get(n, n) for n in out]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []

    html = fetch(PAGE).decode("utf-8", "replace")
    kal = kalendar(html)
    zones_js = {z["id"]: z for z in kal["zones"]}
    names = {z["name"] for z in zones_js.values()}
    if names != set(DAYS):
        problems.append(f"zone u JSON-u: {sorted(names)}")
    for z in zones_js.values():
        if z.get("color", "").lower() != ZONE_COLOURS.get(z["name"]):
            problems.append(f"zona {z['name']}: boja {z.get('color')} (očekivano {ZONE_COLOURS.get(z['name'])})")
    colour_zone = {c: d for d, c in ZONE_COLOURS.items()}

    # JSON events -> {(date, code): {weekday zone}}, moved dates
    hol = {h for y in range(year - 1, year + 3) for h in pravila.blagdani(y)}
    window = defaultdict(set)
    moved, nonworking = set(), set()
    for e in kal["events"]:
        zone = zones_js.get(e["zone_id"], {}).get("name")
        d = date.fromisoformat(e["date"])
        if zone not in DAYS or e["type"] not in TYPES:
            problems.append(f"događaj {e['id']}: zona {zone!r}, vrsta {e['type']!r}")
            continue
        note = (e.get("note") or "").strip()
        m = re.fullmatch(r"Odvoz s (\d{1,2})\.(\d{1,2})\.(\d{4})\. pomaknut na (\d{1,2})\.(\d{1,2})\.(\d{4})\.", note)
        if note and not m:
            problems.append(f"događaj {e['id']}: nepoznata napomena {note!r}")
        elif m:
            old = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            new = date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
            if new != d or old not in hol or abs((new - old).days) > 6:
                problems.append(f"događaj {e['id']}: pomak {old} -> {new} (datum {d}) nije vjerojatan")
            moved.add((d, zone))
            nonworking.add(old)
        elif d.weekday() != DAYS.index(zone):
            problems.append(f"događaj {e['id']}: {d} nije {zone} a nije označen kao pomaknut")
        window[(d, TYPES[e["type"]])].add(zone)
    if not window:
        sys.exit("JSON nema nijednog odvoza.")
    first, last = min(d for d, _ in window), max(d for d, _ in window)
    print(f"JSON: {len(kal['events'])} odvoza od {first} do {last}")

    # year PDF, used only if it equals the JSON on the window
    links = sorted(set(re.findall(rf'href="([^"]*kalendar[^"]*{year}[^"]*\.pdf)"', html)))
    pdf_found, pdf_url, reds = {}, None, set()
    if not links:
        print(f"Na stranici nema PDF kalendara za {year}.")
    else:
        pdf_url = links[-1]
        pdf_problems = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kalendar.pdf"
            fetch(pdf_url, path)
            found = read_pdf(path, year, colour_zone, pdf_problems, reds)
        overlap = {k for k in set(found) | set(window) if first <= k[0] <= last and k[0].year == year}
        diff = sorted(f"{d:%d.%m.} {c} PDF {''.join(sorted(found.get((d, c), ''))) or '-'} / "
                      f"JSON {''.join(sorted(window.get((d, c), ''))) or '-'}"
                      for d, c in overlap if found.get((d, c), set()) != window.get((d, c), set()))
        if pdf_problems or diff or not overlap:
            why = pdf_problems + diff[:10] + ([] if overlap else ["JSON i PDF nemaju zajedničkih datuma"])
            print(f"PDF {pdf_url} se ne koristi: " + "; ".join(why))
        else:
            pdf_found = found
            nonworking |= reds
            print(f"PDF {pdf_url}: {sum(map(len, found.values()))} odvoza, jednak JSON-u na "
                  f"{len(overlap)} datuma/vrsta od {first} do {last}")

    # zones: weekday x municipality
    where = {n: j for j, ns in JLS_OF.items() for n in ns}
    parts = []  # (key, weekday, jls, names)
    for wd in DAYS:
        z = next((z for z in zones_js.values() if z["name"] == wd), None)
        if not z:
            continue
        names = split_names(z.get("description", ""))
        unknown = [n for n in names if n not in where]
        if unknown or not names:
            problems.append(f"zona {wd}: nepoznata naselja {unknown or names}")
            continue
        by_jls = defaultdict(list)
        for n in names:
            by_jls[where[n]].append(n)
        n_zone = DAYS.index(wd) + 1
        for i, (jls, ns) in enumerate(sorted(by_jls.items())):
            key = str(n_zone) + ("ABC"[i] if len(by_jls) > 1 else "")
            parts.append((key, wd, jls, ns))

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    zones, totals, changed = {}, Counter(), 0
    for key, wd, jls, ns in parts:
        prev = old["zone"].get(key, {})
        prev_rows = {d: (c, m) for d, c, m in podaci.iter_dates(prev)} if prev.get("jls") == jls else {}
        prev_months = {(d.year, d.month) for d in prev_rows}
        rows = {}
        for (d, code), zs in pdf_found.items():  # PDF: months the old file does not have
            if wd in zs and (d.year, d.month) not in prev_months:
                rows.setdefault(d, ["", d.weekday() != DAYS.index(wd)])[0] += code
        for d, (codes, mv) in prev_rows.items():
            if not first <= d <= last:
                rows[d] = [codes, mv]
                pdf_codes = "".join(c for c in "MPK" if wd in pdf_found.get((d, c), ()))
                if pdf_found and d.year == year and set(pdf_codes) != set(codes):
                    changed += 1
        for d in [d for d in rows if first <= d <= last]:
            del rows[d]
        for (d, code), zs in window.items():
            if wd in zs:
                rows.setdefault(d, ["", (d, wd) in moved])[0] += code
        # checks
        for d, (codes, mv) in rows.items():
            if mv and not any(abs((d - h).days) <= 6 for h in hol):
                problems.append(f"zona {key}: {d} pomaknut, a nema blagdana blizu")
            if not mv and d.weekday() != DAYS.index(wd):
                problems.append(f"zona {key}: {d} nije {wd}")
            if d.weekday() == 6:
                problems.append(f"zona {key}: nedjelja {d}")
            if len(set(codes)) != len(codes):
                problems.append(f"zona {key}: dvaput ista vrsta {d}")
        per_month = Counter((d.year, d.month, c) for d, (codes, _) in rows.items() for c in codes)
        for y, m in sorted({(d.year, d.month) for d in rows}):
            start, end = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
            outside = (y, m) in prev_months or any(d.year == y and d.month == m for d, _ in pdf_found)
            if (start < first <= end or start <= last < end) and not outside:
                continue  # month only partly in the JSON window
            n = {c: per_month[(y, m, c)] for c in "MPK"}
            if not (4 <= n["M"] <= 5 and 1 <= n["P"] <= 3 and n["K"] == 1):
                problems.append(f"zona {key} {m}/{y}: {n}")
        totals.update(c for codes, _ in rows.values() for c in codes)
        by_year = defaultdict(list)
        for d, (codes, mv) in rows.items():
            by_year[d.year].append((d, codes, mv))
        label = ", ".join(ns[:4]) + (" …" if len(ns) > 4 else "")
        zones[key] = {
            "jls": jls,
            "podrucje": f"{wd} – {label}",
            "opis": f"{wd}: " + ", ".join(ns),
            "ulice": ns,
            "raw": {str(y): podaci.month_lines(r) for y, r in sorted(by_year.items())},
        }
        other = [f"{', '.join(p[3])} ({p[2]}, zona {p[0]})" for p in parts if p[1] == wd and p[0] != key]
        if other:
            zones[key]["napomena"] = (f"Ivakopova zona \"{wd}\" obuhvaća i naselja: {'; '.join(other)}; "
                                      "datumi su isti.")
    if changed:
        print(f"Napomena: PDF se razlikuje od ranije upisanih podataka na {changed} datuma (zadržani su raniji).")

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    months = sorted({(d.year, d.month) for z in zones.values() for d, _, _ in podaci.iter_dates(z)})
    span = f"{months[0][1]}/{months[0][0]} – {months[-1][1]}/{months[-1][0]}"
    data = {**PROVIDER, "napomene": NAPOMENE + [
        f"Datumi od {first:%d.%m.%Y.} do {last:%d.%m.%Y.} iz kalendara na {PAGE} (JSON tražilice, objavljuje "
        "nekoliko mjeseci unaprijed); ranije upisani mjeseci se zadržavaju"
        + (f", a ostali mjeseci {year}. preuzeti su iz godišnjeg PDF kalendara ({pdf_url}), koji se na "
           "razdoblju JSON-a potpuno slaže s njim." if pdf_found else ".") + f" Ukupno razdoblje: {span}.",
        "Blagdani: prema kalendaru odvoz je i blagdanima, osim neradnih dana "
        + ", ".join(f"{d:%d.%m.%Y.}" for d in sorted(nonworking))
        + "; odvozi s tih dana pomaknuti su na prethodni ili idući radni dan i označeni kao pomaknuti.",
    ], "zone": zones}
    print(f"Zona: {len(zones)}; odvoza ukupno (zbroj po zonama): "
          + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
