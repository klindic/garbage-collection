"""Ivanec, Lepoglava, Bednja, Donja Voća, Klenovnik, Maruševec: IVKOM d.o.o. (ivkom.hr), PDF date tables.

    python3 -m izvori.ivkom [--year 2026]

The page "Raspored odvoza" links one PDF per city or municipality (…/files/<id>_r<year><name>.pdf, Excel
printed with "Microsoft: Print To PDF"). A page holds one to three tables, each under a heading "Grad X -
Raspored odvoza otpada za <year>. godinu - <area>": rows are waste types (rotated labels), columns are
months and cells hold DD.MM. dates, wrapped over several lines. Words are put into rows by the row lines
of the label column and into months by the x position of the month headings (pdfplumber). In the
glass/metal/textile row the cell names the type above its date ("staklo metal 15.04."); paper and plastic
share one row (PK); biowaste from April to October is "svaki petak u mjesecu" (every Friday); bulky
waste is on call and not in the data. Ivanec and Lepoglava print one biowaste table for the whole town
("biootpad - cijeli Ivanec"), applied to every area of that town. The street list is the "Raspored se
odnosi na ..." line under each table.
Holidays: a date marked "*" is made up on the first working day after the holiday, "**" on the working
day before (PDF footnotes); such dates are moved to that day (Monday-Friday, not a holiday) and marked
as moved. A "svaki petak" biowaste Friday that is a public holiday gets the same "*" rule.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import blagdani

SLUG = "ivkom"
SITE = "https://ivkom.hr"
PAGE = SITE + "/raspored-odvoza-otpada"
FILES = {"ivanec": "Ivanec", "lepoglava": "Lepoglava", "marusevec": "Maruševec", "klenovnik": "Klenovnik",
         "bednja": "Bednja", "voca": "Donja Voća"}
AREAS = {"Ivanec": 8, "Lepoglava": 5, "Maruševec": 3, "Bednja": 4, "Klenovnik": 1, "Donja Voća": 1}
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
CELL_TYPES = {"staklo": "S", "metal": "L", "tekstil": "T"}
ASSUMED = set()  # holiday Fridays in "svaki petak" biowaste months, moved by the "*" rule
DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.?(\**)")
TITLE = re.compile(r"(Grad|Općina)\s+(.+?)\s*-\s*Raspored odvoza otpada za (\d{4})\. godinu\s*(?:-\s*(.*))?$")
# collections per area and year (min, max) and per month
YEAR_COUNTS = {"M": (24, 28), "PK": (12, 27), "B": (36, 44), "S": (1, 4), "L": (1, 4), "T": (1, 4)}
PROVIDER = {
    "davatelj": "IVKOM d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Varaždinska",
    "jls": ["Ivanec", "Lepoglava", "Bednja", "Donja Voća", "Klenovnik", "Maruševec"],
    "nazivi": {"P": "Plastična ambalaža", "K": "Papir", "L": "Metalna ambalaža"},
    "bioNapomena": "Biootpad se od travnja do listopada odvozi svaki petak, ostalim mjesecima dvaput mjesečno.",
}
NAPOMENE = [
    "Na dan odvoza otpad treba staviti uz javno-prometnu površinu od 07:00 sati ujutro.",
    "Papir i plastika odvoze se zajedno, istog dana. Staklo, metal i tekstil odvoze se nekoliko puta "
    "godišnje prema rasporedu.",
    "Blagdani: datum označen zvjezdicom (*) nadoknađuje se prvog radnog dana po prazniku, a s dvije "
    "zvjezdice (**) radnog dana prije praznika; takvi su odvozi ovdje upisani na dan nadoknade (ponedjeljak–"
    "petak) i označeni kao pomaknuti.",
    "Krupni (glomazni) otpad: po pozivu, jednom godišnje do 2 m³ bez naknade; prijava na 042 770 558 ili "
    "042 444 078 radnim danom od 07:00 do 15:00.",
    "Reciklažno dvorište Ivanec: utorak 10–18, srijeda i petak 7–15, zadnja subota u mjesecu 7–13 sati. "
    "Reciklažno dvorište Maruševec: ponedjeljak i četvrtak 10–16, prva subota u mjesecu 8–12 sati. Mobilno "
    "reciklažno dvorište prema rasporedu u PDF-u (Lepoglava, Donja Voća).",
    "Kontakt za odvoz otpada: 042 770 558, 042 444 078, otpad@ivkom.hr.",
]


def year_pdfs(year):
    """{JLS: pdf url} from the schedule page."""
    page = fetch(PAGE).decode("utf-8", "replace")
    found = {}
    for href, name in re.findall(rf'href="([^"]*/files/\d+_r{year}([a-z]+)\.pdf)"', page):
        if name in FILES:
            found[FILES[name]] = href if href.startswith("http") else SITE + href
    return found


def lines_of(words, tol=2.5):
    """Words -> [(top, [words left to right])], top to bottom."""
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0] - w["top"]) < tol:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    return [(top, sorted(ws, key=lambda w: w["x0"])) for top, ws in lines]


def text(ws):
    return " ".join(" ".join(w["text"] for w in ws).split())


def rotated(chars):
    """Text of a label rotated 90° counter-clockwise: columns left to right, each read bottom to top."""
    cols = []
    for c in sorted(chars, key=lambda c: c["x0"]):
        if cols and abs(cols[-1][0] - c["x0"]) < 2:
            cols[-1][1].append(c)
        else:
            cols.append([c["x0"], [c]])
    return " ".join("".join(c["text"] for c in sorted(cs, key=lambda c: -c["bottom"])) for _, cs in cols)


def working_day(d, step, hol):
    d += timedelta(days=step)
    while d.weekday() >= 5 or d in hol:
        d += timedelta(days=step)
    return d


def fridays(year, month):
    d = date(year, month, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    while d.month == month:
        yield d
        d += timedelta(days=7)


def read_cell(words, month, year, row, hol, problems, where):
    """[(date, codes, moved, printed date)] from the words of one cell, read line by line."""
    out, kinds, used, phrase = [], "", False, []
    for _, ws in lines_of(words):
        for w in ws:
            t = w["text"]
            m = DATE.fullmatch(t)
            low = t.lower().strip(",.")
            if m:
                day, mon, stars = int(m.group(1)), int(m.group(2)), m.group(3)
                if mon != month:
                    problems.append(f"{where}: {t} u stupcu {MONTHS[month - 1]}")
                    continue
                try:
                    d = date(year, mon, day)
                except ValueError:
                    problems.append(f"{where}: nemoguć datum {t}")
                    continue
                out.append((d, kinds if row == "SLT" else row, stars))  # "" when no type is named in the cell
                used = True
            elif t in ("*", "**") and out:
                out[-1] = (out[-1][0], out[-1][1], out[-1][2] + t)
            elif row == "SLT" and low in CELL_TYPES:
                if used:
                    kinds, used = "", False
                kinds += CELL_TYPES[low]
            elif row == "B" and low in ("svaki", "petak", "u", "mjesecu"):
                phrase.append(low)
            else:
                problems.append(f"{where}: nepoznata riječ {t!r} u stupcu {MONTHS[month - 1]}")
    if phrase:
        if " ".join(phrase) != "svaki petak u mjesecu" or out:
            problems.append(f"{where}: {' '.join(phrase)!r} u stupcu {MONTHS[month - 1]}")
        else:
            out = [(d, "B", "*" if d in hol else "") for d in fridays(year, month)]
            ASSUMED.update(d for d in fridays(year, month) if d in hol)
    rows = []
    for d, codes, stars in out:
        if stars and d not in hol:
            problems.append(f"{where}: {d:%d.%m.}{stars} nije blagdan")
        new = working_day(d, 1 if stars == "*" else -1, hol) if stars else d
        rows.append((new, codes, bool(stars), d))
    return rows


def read_pdf(path, jls, year, hol, problems):
    """Areas [{name, rows: [(date, codes, moved, printed date)], opis}] and the town-wide biowaste rows."""
    areas, shared = [], []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            words = page.extract_words(extra_attrs=["upright"], use_text_flow=True)
            up = [w for w in words if w["upright"]]
            lines = lines_of(up)
            titles = []
            for top, ws in lines:
                m = TITLE.match(text(ws))
                if m:
                    if m.group(2).strip() != jls or int(m.group(3)) != year:
                        problems.append(f"{jls} str. {pno}: naslov {text(ws)!r}")
                    name = (m.group(4) or "").strip()
                    name = f"{jls} {name}" if name.isdigit() else (name or f"{m.group(1)} {jls}")
                    titles.append((top, {"name": name, "rows": [], "opis": "", "unlabeled": [], "notice": ""}))
            if not titles:
                continue
            areas += [a for _, a in titles]
            heads = []
            for top, ws in lines:
                cols = {MONTHS.index(w["text"].lower()) + 1: (w["x0"] + w["x1"]) / 2
                        for w in ws if w["text"].lower() in MONTHS}
                if len(cols) == 12:
                    heads.append((top, cols))
            if not heads:
                problems.append(f"{jls} str. {pno}: nema zaglavlja s mjesecima")
                continue
            left = min(heads[0][1].values()) - 17  # content starts at the January cell
            labels = [c for c in page.chars if not c["upright"] and c["x1"] < left]
            lx = sorted((c["x0"] + c["x1"]) / 2 for c in labels)[len(labels) // 2]
            tops = sorted(r["top"] for r in page.rects if r["height"] < 2 and r["x0"] < lx < r["x1"])
            bounds = [t for i, t in enumerate(tops) if i == 0 or t - tops[i - 1] > 4]
            for a, b in zip(bounds, bounds[1:]):
                content = [w for w in up if a < (w["top"] + w["bottom"]) / 2 < b and (w["x0"] + w["x1"]) / 2 > left]
                label = rotated([c for c in labels if a < (c["top"] + c["bottom"]) / 2 < b]).lower()
                flat = label.replace(" ", "")
                dates = [w for w in content if DATE.fullmatch(w["text"])]
                if not flat:
                    if dates:
                        problems.append(f"{jls} str. {pno}: datumi bez retka ({text(content)[:60]})")
                    continue
                if "krupni" in flat:
                    if any(w["text"] not in ("po", "pozivu") for w in content):
                        problems.append(f"{jls} str. {pno}: krupni otpad nije samo 'po pozivu'")
                    continue
                row = ("M" if "miješani" in flat else "PK" if "papir" in flat or "plastika" in flat
                       else "B" if "biootpad" in flat else "SLT" if "staklo" in flat or "tekstil" in flat else None)
                if row is None:
                    problems.append(f"{jls} str. {pno}: nepoznat redak {label!r}")
                    continue
                head = [cols for top, cols in heads if top < a]
                above = [ar for top, ar in titles if top < a]
                if not head or not above:
                    problems.append(f"{jls} str. {pno}: redak {label!r} bez zaglavlja ili naslova")
                    continue
                cols = head[-1]
                cells = {}
                for w in content:
                    cx = (w["x0"] + w["x1"]) / 2
                    cells.setdefault(min(cols, key=lambda k: abs(cols[k] - cx)), []).append(w)
                area = above[-1]
                rows = []
                for month, ws in sorted(cells.items()):
                    rows += read_cell(ws, month, year, row, hol, problems, f"{jls} {area['name']} {label}")
                if row == "B" and "cijel" in flat:
                    shared.append(rows)
                    continue
                area["rows"] += [r for r in rows if r[1]]
                area["unlabeled"] += [r[3] for r in rows if not r[1]]
            # "Raspored se odnosi na ..." paragraph (may wrap) under each table; a "Poštovani korisnici"
            # line other than the usual "uz javno-prometnu površinu" becomes a zone note
            for i, (top, ws) in enumerate(lines):
                above = [ar for t2, ar in titles if t2 < top]
                t = text(ws)
                if t.startswith("Poštovani korisnici") and "javno-prometnu" not in t and above:
                    above[-1]["notice"] = re.sub(r"^Poštovani korisnici\s*!\s*", "", t)
                if not t.startswith("Raspored se odnosi"):
                    continue
                para = [text(ws)]
                for _, nxt in lines[i + 1:]:
                    t = text(nxt)
                    if t.startswith(("*", "Poštovani")) or TITLE.match(t) or DATE.search(t) or "svaki" in t:
                        break
                    para.append(t)
                if above:
                    above[-1]["opis"] = " ".join(para)
    return areas, shared


def streets(opis, name, jls):
    """'Raspored se odnosi na ..., naselja i ulice: A, B, C.' -> ([A, B, C], note)."""
    if ":" not in opis:
        if "sva naselja i ulice" in opis:  # "Bedenec, Jerovec": all of these settlements
            return [s.strip() for s in name.split(",")], None
        if not opis and "stambene zgrade" in name:  # Ivanec: no list under the buildings table
            return [f"{jls} (stambene zgrade)"], "Popis stambenih zgrada nije objavljen."
        return [], None
    if opis.split(":", 1)[1].strip(" .") == "malo vozilo":  # Bednja-4
        return [f"{jls} (malo vozilo)"], ("Raspored se odnosi na dijelove općine koje obilazi malo vozilo; "
                                          "popis naselja nije objavljen.")
    out, note = [], None
    for item in opis.split(":", 1)[1].split(","):
        item = " ".join(item.split()).strip(" .")
        if not item:
            continue
        if item.startswith("isključivo"):
            note = "Raspored vrijedi " + item + "."
        elif item[0].islower() and out:
            out[-1] += ", " + item  # "Kovačićeva-drugi dio, od semafora prema Lepoglavi"
        else:
            out.append(item)
    return out, note


def check(name, rows, unlabeled, problems):
    """Weekday of regular dates per type, counts per year and month, one date per type and day."""
    by = {}
    for d, codes, moved, _ in rows:
        by.setdefault(codes, []).append((d, moved))
    for codes, ds in by.items():
        if Counter(d for d, _ in ds).most_common(1)[0][1] > 1:
            problems.append(f"{name} {codes}: isti datum dvaput")
        if codes in ("M", "PK", "B"):
            days = Counter(d.weekday() for d, moved in ds if not moved)
            main = days.most_common(1)[0][0]
            for d, moved in ds:
                if not moved and d.weekday() != main:
                    problems.append(f"{name} {codes} {d:%d.%m.}: {podaci.DAYS[d.weekday()]}, a ostali "
                                    f"{podaci.DAYS[main]}")
            per_month = Counter(d.month for d, _ in ds)
            for month in range(1, 13):
                lo, hi = (4, 5) if codes == "B" and 4 <= month <= 10 else (1, 3)
                if not lo <= per_month[month] <= hi:
                    problems.append(f"{name} {codes}: {per_month[month]}x u {month}. mjesecu")
    n = Counter(c for _, codes, _, _ in rows for c in ([codes] if codes == "PK" else codes))
    for code, (lo, hi) in YEAR_COUNTS.items():
        if not lo <= n[code] <= hi and not (code in "SLT" and unlabeled):
            problems.append(f"{name}: {n[code]}x {code}, očekivano {lo}-{hi}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    hol = set(blagdani(year)) | set(blagdani(year + 1))
    urls = year_pdfs(year)
    missing = [j for j in AREAS if j not in urls]
    if missing:
        sys.exit(f"Na {PAGE} nema PDF-a za {year} za: {', '.join(missing)}. Ništa nije upisano.")
    problems, zones, notes_moved = [], {}, set()
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    with tempfile.TemporaryDirectory() as tmp:
        for jls in AREAS:
            pdf = Path(tmp) / urls[jls].rsplit("/", 1)[1]
            fetch(urls[jls], pdf)
            if not pdf.read_bytes().startswith(b"%PDF"):
                problems.append(f"{jls}: {urls[jls]} nije PDF")
                continue
            areas, shared = read_pdf(pdf, jls, year, hol, problems)
            if len(areas) != AREAS[jls]:
                problems.append(f"{jls}: {len(areas)} područja, očekivano {AREAS[jls]}: {[a['name'] for a in areas]}")
            shared_rows = shared[0] if shared else []
            if any(sorted(s) != sorted(shared_rows) for s in shared):
                problems.append(f"{jls}: tablice 'biootpad - cijeli' se razlikuju")
            for area in areas:
                rows = area["rows"]
                has_bio = any("B" in c for _, c, _, _ in rows)
                if not has_bio:
                    if not shared_rows:
                        problems.append(f"{jls} {area['name']}: nema biootpada")
                    rows = rows + shared_rows
                name = f"{jls} {area['name']}"
                check(name, rows, area["unlabeled"], problems)
                for d, codes, moved, printed in rows:
                    if moved:
                        notes_moved.add((printed, d))
                    elif d in hol:
                        print(f"   UPOZORENJE {name}: {d:%d.%m.} ({codes}) je blagdan bez zvjezdice")
                ulice, note = streets(area["opis"], area["name"], jls)
                znotes = [n for n in (note, area["notice"]) if n]
                if area["unlabeled"]:
                    ds = ", ".join(f"{d:%d.%m.}" for d in sorted(area["unlabeled"]))
                    print(f"   UPOZORENJE {name}: u retku staklo/metal/tekstil datumi bez vrste otpada: {ds} "
                          "(nisu upisani)")
                    znotes.append(f"U rasporedu su u retku tekstil/staklo/metal navedeni datumi {ds} bez oznake "
                                  "vrste otpada; nisu upisani, provjerite kod IVKOM-a.")
                if not ulice or ulice[0].endswith(")"):
                    print(f"   UPOZORENJE {name}: nema popisa ulica ni naselja ({area['opis']!r})")
                if not ulice:
                    problems.append(f"{name}: nema popisa ulica ({area['opis']!r})")
                label = area["name"]
                if not label.startswith((jls, "Grad", "Općina")):  # "stambene zgrade Ivanec", "Bedenec, Jerovec"
                    label = f"{jls} – " + re.sub(rf"\s*{jls}$", "", label)
                if ulice[0].endswith(")"):
                    podrucje = label + (" (malo vozilo)" if "malo vozilo" in ulice[0] else "")
                elif label.endswith(", ".join(ulice)):
                    podrucje = label
                else:
                    podrucje = f"{label} – " + ", ".join(ulice[:3]) + (" …" if len(ulice) > 3 else "")
                zone = {"jls": jls, "podrucje": podrucje, "ulice": ulice}
                if area["opis"]:
                    zone["opis"] = area["opis"]
                if not has_bio:
                    znotes.append(f"Biootpad prema rasporedu 'biootpad – {'cijeli' if jls == 'Ivanec' else 'cijela'} "
                                  f"{jls}'.")
                if znotes:
                    zone["napomena"] = " ".join(znotes)
                merged = {}
                for d, codes, moved, _ in rows:
                    c, mv = merged.get(d, ("", False))
                    if set(c) & set(codes):
                        problems.append(f"{name} {d}: {codes} dvaput")
                    merged[d] = (c + codes, mv or moved)
                key = str(len(zones) + 1)
                prev = old.get(key, {})
                zone["raw"] = {**(prev.get("raw", {}) if prev.get("podrucje") == zone["podrucje"] else {}),
                               str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in merged.items()])}
                zones[key] = zone
                cnt = Counter(ch for c, _ in merged.values() for ch in c)
                print(f"Zona {key}: {zone['podrucje']}: " + ", ".join(f"{c} {cnt[c]}" for c in "MBPKSLT" if cnt[c])
                      + f", pomaknuto {sum(1 for _, mv in merged.values() if mv)}, ulica {len(ulice)}")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    moves = ", ".join(f"{a:%d.%m.} → {b:%d.%m.}" for a, b in sorted(notes_moved))
    print(f"Pomaci zbog blagdana (pretpostavka: radni dan = ponedjeljak–petak): {moves}")
    extra = [f"Nadoknade zbog blagdana u {year}.: {moves}."]
    for d in sorted(ASSUMED):
        new = working_day(d, 1, hol)
        print(f"PRETPOSTAVKA: biootpad 'svaki petak' na blagdan {d:%d.%m.} odvozi se {new:%d.%m.} (pravilo *)")
        extra.append(f"Biootpad: za petak {d:%d.%m.} (blagdan) raspored 'svaki petak u mjesecu' ne navodi nadoknadu; "
                     f"upisan je prvi radni dan po prazniku ({new:%d.%m.}).")
    data = {**PROVIDER, "napomene": NAPOMENE + extra, "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
