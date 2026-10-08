"""Novalja: ARBUROŽA d.o.o. (arburoza.hr), one table PDF per period of a few weeks to three months.

    python3 -m izvori.arburoza_novalja [--year 2026]

The page "Raspored prikupa otpada" links one PDF per period ("primjenjuje se od DD.MM.YYYY. do
DD.MM.YYYY."; Excel export with a text layer); the year is covered by the periods that touch it, as far as
they are published. Each PDF is a table: rows are areas (between the thin full-width rects), columns
are waste types (x of the column headings). A cell holds either weekdays ("PONEDJELJAK ČETVRTAK SUBOTA
(Centar)", "SVAKI UTORAK", "SVAKI DAN"), which repeat every week of the period, or explicit dates
("(06. i 20.) 10.", "01., 15. i 29.08.", "14.01.") with the weekday as a label. "(Centar)" limits a day to
the centre of Novalja and "(Samo Škuncini stani)" a type to Škuncini, so those areas are zones of their own.
The notes under the table are read for the patterns we know (no collection on a date, an extra
collection instead, no separate collection between two dates, notes for businesses); any other note
stops the script. Holiday moves are only what these notes and the explicit dates say.
Bulky waste (on request) and the row "Novalja gospodarstva" (businesses) are not included.
"""
import argparse
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "arburoza-novalja"
SITE = "https://arburoza.hr"
PAGE = SITE + "/raspored-prikupa-otpada/"
HEADS = {"MIJEŠANI": "M", "PAPIR": "K", "PLASTIKA": "P", "STAKLO": "S", "METAL": "L", "BIOOTPAD": "B", "GLOMAZNI": "G"}
DAYS = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "SRIJEDU": 2, "ČETVRTAK": 3, "PETAK": 4, "SUBOTA": 5,
        "SUBOTU": 5, "NEDJELJA": 6, "NEDJELJU": 6}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
ROWS = {"NOVALJA": "NOVALJA", "STARA": "STARA", "BARBATI": "BARBATI", "LUN": "LUN", "GAJAC": "GAJAC"}
ONLY_SK = "(Samo Škuncini stani)"
LABEL_X = 125  # area names left of this x (word centre), the mixed-waste column right of it
# zone: (row, part of the row, places, description)
ZONES = [
    ("NOVALJA", "centar", ["Novalja (centar)"], "Novalja – centar"),
    ("NOVALJA", "ostalo", ["Novalja (izvan centra)"], "Novalja – izvan centra"),
    ("STARA", None, ["Stara Novalja"], "Stara Novalja"),
    ("BARBATI", None, ["Barbati", "Caska", "Vidalići", "Kustići", "Zubovići", "Metajna"],
     "Barbati, Caska, Vidalići, Kustići, Zubovići i Metajna"),
    ("LUN", None, ["Lun", "Jakišnica", "Potočnica", "Stani", "Varsan"], "Lun, Jakišnica, Potočnica, Stani i Varsan"),
    ("GAJAC", "skuncini", ["Škuncini", "Škuncini stani"], "Škuncini"),
    ("GAJAC", "ostalo", ["Gajac", "Stani Sajužnji"], "Gajac i Stani Sajužnji"),
]
PROVIDER = {
    "davatelj": "ARBUROŽA d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Ličko-senjska",
    "jls": ["Novalja"],
}
NAPOMENE = [
    "Raspored se objavljuje po razdobljima (od nekoliko tjedana do tri mjeseca); ljeti je odvoz znatno češći.",
    "Glomazni otpad odvozi se uz predani zahtjev (dan prema području, vidi raspored); nije uključen u kalendar.",
    "Raspored za gospodarstva (Novalja gospodarstva) nije uključen.",
    "Reciklažno dvorište: ponedjeljak, srijeda i petak 08 – 15, utorak i četvrtak 10 – 17, subota 08 – 13 sati.",
]


def centre(w):
    return (w["x0"] + w["x1"]) / 2


def period_of(text):
    m = re.search(r"primjenjuje se od (\d{2})\.(\d{2})\.(\d{4})\. do (\d{2})\.(\d{2})\.(\d{4})", text)
    return m and (date(int(m.group(3)), int(m.group(2)), int(m.group(1))),
                  date(int(m.group(6)), int(m.group(5)), int(m.group(4))))


def dated(a, b, month, day):
    """The date with that day and month nearest to the period a..b."""
    for y in sorted({a.year, b.year}):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if a - timedelta(days=7) <= d <= b + timedelta(days=7):
            return d
    return None


def explicit(text, a, b):
    """'(06. i 20.) 10.', '01., 15. i 29.08.', '14.01.' -> dates (None if a date does not fit the period)."""
    out = []
    for days, month in re.findall(r"\(([^)]*)\)\s*(\d{1,2})\.", text):
        out += [dated(a, b, int(month), int(d)) for d in re.findall(r"\d{1,2}", days)]
    text = re.sub(r"\(([^)]*\d[^)]*)\)\s*\d{1,2}\.", " ", text)
    for days, d, month in re.findall(r"((?:\d{1,2}\.,?\s+(?:i\s+)?)*)(\d{1,2})\.(\d{1,2})\b\.?", text):
        out += [dated(a, b, int(month), int(x)) for x in re.findall(r"\d{1,2}", days) + [d]]
    return out


def parse_cell(text, a, b):
    """{part: [dates]} for a cell; part 'centar' for days marked (Centar), None for the whole row."""
    text = " ".join(text.replace(ONLY_SK, " ").replace("UZ PREDANI ZAHTJEV", " ").split())
    if not text or text in ("NE ODVOZI SE", "PREMA POTREBI", "-"):
        return {}
    dates = explicit(text, a, b)
    if dates:
        if None in dates:
            raise ValueError(f"datum izvan razdoblja: {text!r}")
        words = {DAYS[w] for w in re.findall(r"[A-ZČĆŽŠĐ]+", text) if w in DAYS}
        off = [d for d in dates if words and d.weekday() not in words]
        if off:
            raise ValueError(f"datumi {off} nisu {sorted(DAN[w] for w in words)}: {text!r}")
        return {None: dates}
    days = defaultdict(set)
    if re.fullmatch(r"SVAKI DAN", text):
        days[None] = set(range(7))
    else:
        for m in re.finditer(r"([A-ZČĆŽŠĐ]+)(\s*\(Centar\))?", text):
            w = m.group(1)
            if w in ("SVAKI", "SVAKU"):
                continue
            if w not in DAYS:
                raise ValueError(f"nepoznata riječ {w!r} u {text!r}")
            days["centar" if m.group(2) else None].add(DAYS[w])
    out = {}
    for part, wds in days.items():
        d, out[part] = a, []
        while d <= b:
            if d.weekday() in wds:
                out[part].append(d)
            d += timedelta(days=1)
    return out


def read_period(page, problems):
    """(a, b, {row: {code: (cell text, only Škuncini)}}, notes text)."""
    words = page.extract_words()
    text = " ".join(w["text"] for w in words)
    period = period_of(text)
    if not period:
        problems.append("nema razdoblja u naslovu")
        return None
    a, b = period
    seps = sorted(r["top"] for r in page.rects if r["width"] > 300 and r["height"] < 4)
    bands = []
    for t in seps:
        if not bands or t - bands[-1] > 4:
            bands.append(t)
    heads = {}
    for w in words:
        if w["text"] in HEADS and w["top"] < bands[1] and HEADS[w["text"]] not in heads:
            heads[HEADS[w["text"]]] = (w["x0"] + w["x1"]) / 2
    if set(heads) != set(HEADS.values()):
        problems.append(f"{a}: stupci {sorted(heads)}")
        return None
    xs = sorted(heads.items(), key=lambda kv: kv[1])
    right = next((w["x0"] for w in words if w["text"] == "Radno"), page.width) - 5

    def column(w):
        c = (w["x0"] + w["x1"]) / 2
        return min(xs, key=lambda kv: abs(kv[1] - c))[0]

    rows, last = {}, bands[1]
    for top, bottom in zip(bands[1:], bands[2:]):
        inside = [w for w in words if top < w["top"] < bottom - 2]
        label = " ".join(w["text"] for w in sorted(inside, key=lambda w: (w["top"], w["x0"])) if centre(w) < LABEL_X)
        first = label.split()[0] if label.split() else ""
        if first not in ROWS or "GOSPODARSTVA" in label:
            continue
        near = [w["text"] for w in inside if abs(centre(w) - LABEL_X) < 12]
        if near:
            problems.append(f"{a}: riječi između stupca područja i miješanog otpada: {near}")
        cells = defaultdict(list)
        for w in sorted(inside, key=lambda w: (round(w["top"]), w["x0"])):
            if centre(w) >= LABEL_X:
                cells[column(w)].append(w["text"])
        rows[ROWS[first]] = {c: " ".join(t) for c, t in cells.items()}
        last = bottom
    notes = " ".join(w["text"] for w in sorted((w for w in words if w["top"] > last and w["x0"] < right),
                                               key=lambda w: (round(w["top"]), w["x0"])))
    return a, b, rows, notes


def read_notes(notes, a, b, year, problems):
    """(dates without collection {date: codes or None (all)}, extra [(date, code, rows)], places note)."""
    rest = re.sub(r"\s*-\s+", " ", notes.replace("NAPOMENA:", " "))
    drop, extra, places = {}, [], None
    # business notes (not included)
    rest = re.sub(r"\*\*\* *Gospodarstva Novalja:.*?(?=\*\*\*|IZVA|Od \d|\d{2}\.\d{2}\. NEMA|$)", " ", rest, flags=re.I)
    m = re.search(r"\*\*\* *((?:[A-ZČĆŽŠĐ][\w-]*, *)+[A-ZČĆŽŠĐ][\w-]*): *((?:[A-ZČĆŽ]+,? *)+)", rest)
    if m:
        places = (m.group(1).replace(",", ", ").replace("  ", " "), m.group(2).strip(" ,"))
        rest = rest.replace(m.group(0), " ")
    for m in re.finditer(r"IZVA\w* ODVOZ (\d{2})\.(\d{2})\.(\d{4})\. i (\d{2})\.(\d{2})\.(\d{4})\.", rest):
        for d in (date(int(m.group(3)), int(m.group(2)), int(m.group(1))),
                  date(int(m.group(6)), int(m.group(5)), int(m.group(4)))):
            if d.year == year:
                problems.append(f"izvanredni odvoz {d} bez vrste otpada")
        rest = rest.replace(m.group(0), " ")
    for m in re.finditer(r"Od (\d{2})\.(\d{2})\. ?(\d{4})\. do (\d{2})\.(\d{2})\.(\d{4})\. NEMA ODVOZA SELEKTIVNOG "
                         r"OTPADA I GLOMAZNOG OTPADA", rest):
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        end = dated(a, b, int(m.group(5)), int(m.group(4)))  # the year is misprinted in one PDF
        while end and d <= end:
            drop[d] = "KPSLG"
            d += timedelta(days=1)
        rest = rest.replace(m.group(0), " ")
    for m in re.finditer(r"(\d{2})\.(\d{2})\. NEMA ODVOZA! */ *IZVANREDNI ODVOZ (\d{2})\.(\d{2})\. MKO -? ?"
                         r"Barbati i Lun i PET/MET -? ?Novalja i Stara Novalja", rest):
        drop[dated(a, b, int(m.group(2)), int(m.group(1)))] = None
        new = dated(a, b, int(m.group(4)), int(m.group(3)))
        extra += [(new, "M", ("BARBATI", "LUN")), (new, "PL", ("NOVALJA", "STARA"))]
        rest = rest.replace(m.group(0), " ")
    rest = re.sub(r"\*\*\* *GOSPODARSTVA NOVALJA \(Centar\) MKO dodatno [\d.]+", " ", rest)
    rest = re.sub(r"\(\d{2}\.\d{2}\.\d{4}\. i \d{2}\.\d{2}\.\d{4}\.\) nema odvoza -->", " ", rest)
    if rest.strip(" *-"):
        problems.append(f"{a}: nepoznata napomena: {rest.strip()!r}")
    return drop, extra, places


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    html = fetch(PAGE).decode("utf-8", "replace")
    urls = sorted(set(re.findall(r'href="(https?://[^"]+/Raspored-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{4})\.g[^"]*\.pdf)"',
                                 html)))
    wanted = []
    for url, d1, m1, d2, m2, y in urls:  # the file name has the period; the end year is in it
        end = date(int(y), int(m2), int(d2))
        start = date(int(y) - (int(m1) > int(m2)), int(m1), int(d1))
        if start.year <= year <= end.year:
            wanted.append((start, end, url))
    wanted.sort()
    if not wanted:
        sys.exit(f"Na {PAGE} nema rasporeda za {year}.")
    dates = defaultdict(dict)  # zone index -> {date: codes}
    places_notes, covered = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (start, end, url) in enumerate(wanted):
            path = Path(tmp) / f"{i}.pdf"
            fetch(url, path)
            got = read_period(pdfplumber.open(path).pages[0], problems)
            if not got:
                continue
            a, b, rows, notes = got
            if (a, b) != (start, end):
                problems.append(f"{url}: razdoblje u PDF-u {a} – {b}")
            covered.append((a, b))
            if set(rows) != set(ROWS.values()):
                problems.append(f"{a}: retci {sorted(rows)}")
                continue
            drop, extra, places = read_notes(notes, a, b, year, problems)
            if places:
                places_notes.append((a, b, places))
            print(f"Razdoblje {a:%d.%m.%Y.} – {b:%d.%m.%Y.}: {url.rsplit('/', 1)[1]}")
            for z, (row, part, _, _) in enumerate(ZONES):
                for code, text in rows[row].items():
                    if code == "G":
                        continue
                    if row == "GAJAC" and part == "ostalo" and ONLY_SK in text:
                        continue
                    try:
                        parsed = parse_cell(text, a, b)
                    except ValueError as e:
                        problems.append(f"{a} {row} {code}: {e}")
                        continue
                    if "centar" in parsed and row != "NOVALJA":
                        problems.append(f"{a} {row} {code}: (Centar) izvan Novalje")
                    for p, ds in parsed.items():
                        if p == "centar" and part != "centar":
                            continue
                        for d in ds:
                            if d.year == year and not (d in drop and (drop[d] is None or code in drop[d])):
                                dates[z][d] = dates[z].get(d, "") + code
                for d, codes, rws in extra:
                    if row in rws and d.year == year:
                        dates[z][d] = dates[z].get(d, "") + codes + "!"
    covered.sort()
    for (a1, b1), (a2, b2) in zip(covered, covered[1:]):
        if a2 != b1 + timedelta(days=1):
            problems.append(f"razdoblja nisu uzastopna: {b1} – {a2}")
    first, last = max(covered[0][0], date(year, 1, 1)), min(covered[-1][1], date(year, 12, 31))

    zones = {}
    for z, (row, part, ulice, desc) in enumerate(ZONES):
        rows = []
        for d, codes in sorted(dates[z].items()):
            clean = "".join(dict.fromkeys(c for c in codes if c != "!"))
            if len(clean) != len(codes.replace("!", "")):
                problems.append(f"{desc}: {d} vrsta dvaput ({codes})")
            rows.append((d, clean, "!" in codes))
        for mo in range(first.month, last.month + 1):
            full = date(year, mo, 1) >= first and (mo < last.month or last.day >= 28)
            n = sum(1 for d, c, _ in rows if d.month == mo and "M" in c)
            if full and not 4 <= n <= 31:
                problems.append(f"{desc}: {n} odvoza miješanog otpada u {mo}. mjesecu")
        zone = {"jls": "Novalja", "podrucje": desc, "ulice": ulice}
        if row == "GAJAC" and part == "ostalo":
            zone["napomena"] = "Papir, plastika, metal i biootpad (a u nekim razdobljima i staklo) odvoze se samo u Škuncinima."
        if row in ("NOVALJA", "STARA") and places_notes:
            zone["napomena"] = "Za naselja s posebnim danima miješanog otpada vidi napomene."
        zone["raw"] = {str(year): podaci.month_lines(rows)}
        zones[str(z + 1)] = zone
        counts = defaultdict(int)
        for _, c, _ in rows:
            for x in c:
                counts[x] += 1
        print(f"Zona {z + 1} {desc}: " + ", ".join(f"{c} {n}" for c, n in sorted(counts.items())))

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    extra_notes = [f"{p[0]}: miješani otpad {p[1].lower()} (razdoblje {a:%d.%m.} – {b:%d.%m.%Y.})"
                   for a, b, p in places_notes]
    data = {**PROVIDER, "napomene": NAPOMENE + [
        f"Objavljeni rasporedi pokrivaju {first:%d.%m.} – {last:%d.%m.%Y.}; kasniji mjeseci dodaju se kad se objave.",
        "Pomaci zbog blagdana: prema napomenama i datumima u rasporedima (npr. 6.1. bez odvoza, izvanredni odvoz 7.1.); "
        "inače se odvozi prema rasporedu.",
    ] + extra_notes, "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years (and months no longer linked) of zones that did not change
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("ulice") == zone["ulice"]:
                kept = [r for r in podaci.iter_dates(prev, year) if r[0] < first]
                zone["raw"] = {**prev["raw"], str(year): podaci.month_lines(kept + list(podaci.iter_dates(zone, year)))}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
