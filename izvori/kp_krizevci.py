"""Križevci, Gornja Rijeka, Kalnik, Sveti Ivan Žabno, Sveti Petar Orehovec: Komunalno poduzeće Križevci d.o.o.

    python3 -m izvori.kp_krizevci [--year 2026]

The home page of komunalno.hr links the year booklet "Kalendar-<year>.pdf" (InDesign, pages are 2-up
spreads, so every page is cut into a left and a right half). After the instructions, each half page with
a table is one area: a banner with the city or municipality ("Grad Križevci - okolna naselja"), the list
of streets or settlements, then a table with one row per month and one column per waste type (Miješani,
Reciklabilni, Papir, Staklo, Biootpad, Glomazni; the set varies per area, each with a time such as
"od 15 sati"). Cells hold D.M. dates stacked over several lines; words are put into month rows by the
filled cell of the month label and into columns by the x position of the column headings (pdfplumber).
"Reciklabilni" is plastic, metal and tetrapak (code P). Bulky waste dates are the days on which bulky
waste is taken away if ordered at least 3 days before.
Holidays: the booklet prints changed dates in red ("promjene označeno crveno"); red dates are marked as
moved and must lie within a week of a public holiday.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import blagdani

SLUG = "kp-krizevci"
SITE = "https://komunalno.hr"
PAGES = [SITE + "/", SITE + "/cistoca/"]
MONTHS = ["Siječanj", "Veljača", "Ožujak", "Travanj", "Svibanj", "Lipanj", "Srpanj", "Kolovoz", "Rujan",
          "Listopad", "Studeni", "Prosinac"]
COLS = {"Miješani": "M", "Reciklabilni": "P", "Papir": "K", "Staklo": "S", "Biootpad": "B", "Glomazni": "G"}
COL_NAME = {"M": "miješani", "P": "reciklabilni", "K": "papir", "S": "staklo", "B": "biootpad", "G": "glomazni"}
JLS = {"Grad Križevci": "Križevci", "Gornja Rijeka": "Gornja Rijeka", "Kalnik": "Kalnik",
       "Sveti Ivan Žabno": "Sveti Ivan Žabno", "Sveti Petar Orehovec": "Sveti Petar Orehovec"}
RED = (0.93, 0.112, 0.142)
DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.")
# collections per area and year (min, max)
COUNTS = {"M": (24, 54), "P": (12, 27), "K": (12, 27), "S": (3, 13), "B": (20, 54), "G": (1, 6)}
PROVIDER = {
    "davatelj": "Komunalno poduzeće Križevci d.o.o.",
    "web": SITE,
    "zupanija": "Koprivničko-križevačka",
    "jls": ["Križevci", "Gornja Rijeka", "Kalnik", "Sveti Ivan Žabno", "Sveti Petar Orehovec"],
    "nazivi": {"P": "Reciklabilni otpad (plastika, metal, tetrapak)", "G": "Glomazni otpad (samo uz narudžbu)"},
    "bioNapomena": "Biootpad se odvozi samo u dijelovima Grada Križevaca navedenima u kalendaru.",
    "napomene": [
        "Datumi otisnuti crveno u kalendaru (promjene zbog blagdana i praznika) označeni su kao pomaknuti.",
        "Kante postavite na javnu površinu prije vremena odvoza navedenog za vrstu otpada (npr. miješani od 15 sati, "
        "ostalo od 7 sati); vrijeme je u napomeni zone.",
        "Glomazni otpad odvozi se na predviđene datume samo uz narudžbu najkasnije 3 dana prije (048 720 918, "
        "www.komunalno.hr ili kupon iz kalendara); jedan odvoz godišnje je besplatan.",
        "Višak otpada: miješani u vrećama Komunalnog poduzeća, reciklabilni u narančastim, papir u plavim i "
        "biootpad u smeđim vrećama (besplatne na blagajni i u reciklažnom dvorištu).",
        "Reciklažno dvorište Donji Cubinec 30: ponedjeljak 8–18, utorak–petak 8–16, subota 8–14 sati "
        "(048 693 561). Informacije: 048 720 918, info@komunalno.hr.",
    ],
}


def calendar_url(year):
    for page in PAGES:
        html = fetch(page).decode("utf-8", "replace")
        m = re.search(rf'href="([^"]*/Kalendar-{year}[^"/]*\.pdf)"', html)
        if m:
            return m.group(1) if m.group(1).startswith("http") else SITE + m.group(1)
    return None


def lines_of(words, tol=2.5):
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0] - w["top"]) < tol:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    return [" ".join(x["text"] for x in sorted(ws, key=lambda w: w["x0"])) for _, ws in lines]


def streets(text):
    """'A, B, C i D.' -> [A, B, C, D]; ' i ' inside an item ('Križevčine Donje i Gornje') stays."""
    items = [" ".join(s.split()).strip(" .") for s in text.split(",")]
    items = [s for s in items if s]
    if items and " i " in items[-1]:
        items[-1:] = items[-1].split(" i ")
    return items


def read_half(half, year, problems, where):
    """One area: {title, streets, times, rows: [(date, code, red)]} or None when there is no table."""
    words = half.extract_words(extra_attrs=["non_stroking_color", "size"])
    heads = [w for w in words if w["text"] in COLS]
    if len(heads) < 2:
        return None
    head_top = min(w["top"] for w in heads)
    title = " ".join(w["text"] for w in sorted([w for w in words if w["size"] > 15 and w["top"] < head_top],
                                               key=lambda w: (round(w["top"]), w["x0"])))
    title_bottom = max((w["bottom"] for w in words if w["size"] > 15 and w["top"] < head_top), default=0)
    text = " ".join(lines_of([w for w in words if title_bottom < w["top"] < head_top - 2 and w["size"] < 15]))
    cols = {COLS[w["text"]]: (w["x0"] + w["x1"]) / 2 for w in heads}
    times = {}
    for w in words:
        if head_top < w["top"] < head_top + 20 and w["text"] not in COLS:
            code = min(cols, key=lambda c: abs(cols[c] - (w["x0"] + w["x1"]) / 2))
            times[code] = (times.get(code, "") + " " + w["text"]).strip()
    # month rows: the filled cell around each month label
    bands = {}
    for w in words:
        if w["text"] in MONTHS:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            cells = [r for r in half.rects if r["fill"] and r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"]
                     and r["width"] < 100]
            if not cells:
                problems.append(f"{where}: nema ćelije za {w['text']}")
                continue
            r = min(cells, key=lambda r: r["width"] * r["height"])
            bands[MONTHS.index(w["text"]) + 1] = (r["top"], r["bottom"], r["x1"])
    if len(bands) != 12:
        if bands:
            problems.append(f"{where}: mjeseci u tablici: {sorted(bands)}")
        return None
    table_top, label_right = min(b[0] for b in bands.values()), min(b[2] for b in bands.values())
    rows = []
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        if cy < table_top or w["x0"] < label_right - 1 or w["text"] in MONTHS:
            continue
        month = next((m for m, (a, b, _) in bands.items() if a <= cy <= b), None)
        if month is None:
            continue  # page number under the table
        m = DATE.fullmatch(w["text"])
        if not m:
            problems.append(f"{where}: nepoznata riječ {w['text']!r} u retku {MONTHS[month - 1]}")
            continue
        code = min(cols, key=lambda c: abs(cols[c] - cx))
        if abs(cols[code] - cx) > 25:
            problems.append(f"{where}: {w['text']} nije ni pod jednim stupcem")
            continue
        day, mon = int(m.group(1)), int(m.group(2))
        if mon != month:
            problems.append(f"{where}: {w['text']} u retku {MONTHS[month - 1]}")
            continue
        try:
            d = date(year, mon, day)
        except ValueError:
            problems.append(f"{where}: nemoguć datum {w['text']}")
            continue
        colour = tuple(round(float(v), 3) for v in (w["non_stroking_color"] or (0,)))
        if colour not in (RED, (0.0,), (0.0, 0.0, 0.0)):
            problems.append(f"{where}: {w['text']} boje {colour}")
        rows.append((d, code, colour == RED))
    return {"title": title, "text": text, "streets": streets(text), "times": times, "rows": rows,
            "cols": list(cols)}


def unmarked_moves(area, hol, where):
    """Black dates off the type's weekday that replace a holiday on that weekday (within 6 days) are moved
    too: the booklet sometimes leaves them black (biootpad 3.4. for Easter Monday 6.4.)."""
    main = {}
    for code in {c for _, c, _ in area["rows"]} - {"G"}:
        days = Counter(d.weekday() for d, c, red in area["rows"] if c == code and not red)
        main[code] = days.most_common(1)[0][0]
    rows = []
    for d, code, red in area["rows"]:
        if not red and code in main and d.weekday() != main[code] and any(
                h.weekday() == main[code] and abs((d - h).days) <= 6 for h in hol):
            print(f"   UPOZORENJE {where} {COL_NAME[code]} {d:%d.%m.}: nije crveno, ali zamjenjuje blagdan "
                  f"({podaci.DAYS[d.weekday()]} umjesto {podaci.DAYS[main[code]]}); označeno kao pomaknuto")
            red = True
        rows.append((d, code, red))
    area["rows"] = rows


def check(where, area, hol, problems):
    """Weekday per type (red dates excepted), red dates near a holiday, counts, no double dates."""
    by = {}
    for d, code, red in area["rows"]:
        by.setdefault(code, []).append((d, red))
    for code in area["cols"]:
        if code not in by:
            problems.append(f"{where}: stupac {COL_NAME[code]} bez datuma")
    for code, ds in by.items():
        dup = [d for d, n in Counter(d for d, _ in ds).items() if n > 1]
        if dup:
            problems.append(f"{where} {COL_NAME[code]}: dvaput {dup}")
        lo, hi = COUNTS[code]
        if not lo <= len(ds) <= hi:
            problems.append(f"{where} {COL_NAME[code]}: {len(ds)} odvoza, očekivano {lo}-{hi}")
        for d, red in ds:
            if red and not any(abs((d - h).days) <= 7 for h in hol):
                problems.append(f"{where} {COL_NAME[code]} {d:%d.%m.}: crveno, a nema blagdana blizu")
            if d.weekday() == 6:
                problems.append(f"{where} {COL_NAME[code]} {d:%d.%m.}: nedjelja")
        if code == "G":
            continue
        days = Counter(d.weekday() for d, red in ds if not red)
        main = days.most_common(1)[0][0]
        for d, red in ds:
            if not red and d.weekday() != main:
                problems.append(f"{where} {COL_NAME[code]} {d:%d.%m.}: {podaci.DAYS[d.weekday()]}, a ostali "
                                f"{podaci.DAYS[main]} (nije crveno)")
        if code == "M":
            per_month = Counter(d.month for d, _ in ds)
            if any(not 1 <= per_month[m] <= 5 for m in range(1, 13)):
                problems.append(f"{where} miješani po mjesecima: {dict(sorted(per_month.items()))}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    url = calendar_url(year)
    if not url:
        sys.exit(f"Nema Kalendar-{year}.pdf na {', '.join(PAGES)}. Ništa nije upisano.")
    hol = blagdani(year)
    problems, areas = [], []
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "kalendar.pdf"
        fetch(url, pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            first = " ".join((pdf.pages[0].extract_text() or "").split())
            if f"otpada za {year}" not in re.sub(r"(.)\1", r"\1", first):  # title chars are doubled
                problems.append(f"naslovnica nije kalendar za {year}")
            for pno, page in enumerate(pdf.pages, 1):
                w = page.width
                halves = [page.crop((0, 0, w / 2, page.height)), page.crop((w / 2, 0, w, page.height))] \
                    if w > page.height else [page]
                for side, half in zip("LD", halves):
                    where = f"str. {pno}{side if len(halves) > 1 else ''}"
                    area = read_half(half, year, problems, where)
                    if area:
                        area["where"] = where
                        areas.append(area)
    if not 15 <= len(areas) <= 20:
        problems.append(f"{len(areas)} tablica, očekivano ~17")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    zones = {}
    for area in areas:
        where = f"{area['where']} {area['title']}"
        base = area["title"].split(" - ")[0]
        jls = JLS.get(base)
        if not jls:
            problems.append(f"{where}: nepoznata općina {area['title']!r}")
            continue
        unmarked_moves(area, hol, where)
        check(where, area, hol, problems)
        if not area["streets"]:
            problems.append(f"{where}: nema popisa ulica")
        mixed = Counter(d.weekday() for d, c, red in area["rows"] if c == "M" and not red).most_common(1)
        day = podaci.DAYS[mixed[0][0]] if mixed else "?"
        ulice = area["streets"]
        title = area["title"].replace(" - ", " – ")
        zone = {"jls": jls,
                "podrucje": f"{title} ({day}) – " + ", ".join(ulice[:3]) + (" …" if len(ulice) > 3 else ""),
                "opis": area["text"], "ulice": ulice}
        times = "; ".join(f"{COL_NAME[c]} {t}" for c, t in area["times"].items())
        if times:
            zone["napomena"] = f"Vrijeme odvoza: {times}."
        merged = {}
        for d, code, red in area["rows"]:
            c, mv = merged.get(d, ("", False))
            merged[d] = (c + code, mv or red)
        key = str(len(zones) + 1)
        prev = old.get(key, {})
        zone["raw"] = {**(prev.get("raw", {}) if prev.get("podrucje") == zone["podrucje"] else {}),
                       str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in merged.items()])}
        zones[key] = zone
        cnt = Counter(code for _, code, _ in area["rows"])
        print(f"Zona {key} ({area['where']}): {zone['podrucje']}: " + ", ".join(f"{c} {cnt[c]}" for c in "MPKSBG"
                                                                               if cnt[c])
              + f", crveno {sum(1 for *_, red in area['rows'] if red)}, ulica {len(ulice)}")
    names = Counter((z["jls"], u) for z in zones.values() for u in z["ulice"])
    for (jls, n), c in names.items():
        if c > 1:
            print(f"   UPOZORENJE: {jls}, {n} je u {c} zone: "
                  + ", ".join(k for k, z in zones.items() if z["jls"] == jls and n in z["ulice"]))
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "izvor": url, "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona iz {url})")


if __name__ == "__main__":
    main()
