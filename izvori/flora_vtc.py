"""Virovitica, Suhopolje, Lukač, Gradina, Špišić Bukovica: Flora VTC d.o.o. (flora-vtc.hr), one zone per weekday and
city/municipality.

    python3 -m izvori.flora_vtc [--year 2026]

The page "Zbrinjavanje komunalnog otpada" links the leaflet PDF "Obavijest o prikupljanju ... otpada" whose first
page (text layer, two columns, read with pdfplumber) lists the streets and settlements of every weekday by
city/municipality; mixed waste is collected once a week on that day ("odvozi 1x tjedno prema rasporedu"). The
user page "Info za korisnike" links the calendar images kalendar/virovitica.jpg (blue = paper weeks, yellow =
plastic weeks) and kalendar/biootpad.jpg (brown = biowaste weeks); every street is collected on its weekday in
a coloured week. The calendars only cover August 2026 – February 2027, so only that window is written (earlier
months already in podaci/flora-vtc.json are kept). The coloured weeks and the holiday legend of the calendars
are kept in CALENDAR with the images' sha256; every run samples every day cell of both images on their fixed
grids and stops if a cell disagrees. Holidays follow the legend: a "neradni dan" moves that day's collection to
the Saturday (marked as moved), the other holidays are normal collection days.
"""
import argparse
import calendar
import hashlib
import html
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pdfplumber
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "flora-vtc"
SITE = "https://flora-vtc.hr"
PAGE = SITE + "/djelatnost/zbrinjavanje-komunalnog-otpada/"
INFO = SITE + "/2011/05/kutak-za-korisnike/"
# calendar images (read by hand and by pixel colour): file -> sha256; months shown; Mondays of the coloured weeks;
# holiday legend: holiday -> replacement day (None = "redovan odvoz otpada")
CALENDAR = {
    "images": {"virovitica.jpg": "fdbded408a866393a94daf22fbcce7204a7f81f880f21ca4c5a6c70c7fd664bf",
               "biootpad.jpg": "7c5ad518853fdc8fc0d9f2167d9dcf37490eb30d6dfb9b89217f756cc3300eb2"},
    "months": [(2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12), (2027, 1), (2027, 2)],
    "K": "2026-08-03 2026-08-17 2026-08-31 2026-09-14 2026-10-05 2026-10-19 2026-11-02 2026-11-16 2026-11-30 "
         "2026-12-14 2027-01-04 2027-01-18 2027-02-01 2027-02-15",
    "P": "2026-08-10 2026-08-24 2026-09-07 2026-09-21 2026-10-12 2026-10-26 2026-11-09 2026-11-23 2026-12-07 "
         "2026-12-21 2027-01-11 2027-01-25 2027-02-08 2027-02-22",
    "B": "2026-08-03 2026-08-17 2026-08-31 2026-09-14 2026-10-05 2026-10-19 2026-11-02 2026-11-16 2026-11-30 "
         "2026-12-14 2027-01-04 2027-01-18 2027-02-01 2027-02-15",
    "holidays": {"2026-08-05": None, "2026-11-18": None, "2026-12-25": "2026-12-26", "2027-01-01": "2027-01-02",
                 "2027-01-06": None},
}
# fixed grids (px): centre x of the Monday column of the left and right month column, column pitch, centre y of
# the first week row of each pair of months, row pitch; colour classes looked for in each image
GRIDS = {"virovitica.jpg": ((29.4, 265.4), 30.8, (193, 344, 495, 646), 19.1, "KP"),
         "biootpad.jpg": ((45, 275), 30.0, (195, 342.5, 490.5, 637.5), 18.5, "B")}
DAYS = {"Ponedjeljak": 0, "Utorak": 1, "Srijeda": 2, "Četvrtak": 3, "Petak": 4}
JLS = {"GRAD VIROVITICA": "Virovitica", "OPĆINA LUKAČ": "Lukač", "OPĆINA SUHOPOLJE": "Suhopolje",
       "OPĆINA ŠPIŠIĆ BUKOVICA": "Špišić Bukovica", "OPĆINA GRADINA": "Gradina"}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Flora VTC d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Virovitičko-podravska",
    "jls": ["Virovitica", "Lukač", "Suhopolje", "Špišić Bukovica", "Gradina"],
    "nazivi": {"M": "Miješani komunalni otpad (zelena posuda)", "P": "Plastika i plastična ambalaža (žuta posuda)",
               "K": "Papir i papirna ambalaža (plava posuda)"},
    "bioNapomena": "Biootpad (smeđa posuda) odvozi se u tjednima označenim na kalendaru biootpada, na dan iz "
                   "rasporeda ulica.",
}
NAPOMENE = [
    "Miješani komunalni otpad odvozi se jednom tjedno na dan iz rasporeda ulica; papir i biootpad u tjednima "
    "označenim plavo odnosno smeđe, plastika u tjednima označenim žuto na kalendaru, na isti dan u tjednu.",
    "Ljeti (od početka lipnja do kraja rujna) otpad se odvozi od 5 do 13 sati; posude treba iznijeti večer prije, "
    "najkasnije do 5 sati.",
    "Glomazni otpad: jednom godišnje besplatno do 5 m³ uz dogovor s Florom VTC. Reciklažna dvorišta: Virovitica "
    "(Florin put 14, ponedjeljak–petak 8–18), Suhopolje (Pčelić 194, srijeda 9–17), Špišić Bukovica (Bukovački "
    "vinogradi 10, utorak 8–16, četvrtak 10–18 sati).",
    "Oznake „Papir/Plastika (EKO-otoci i pravne osobe)” u rasporedu odnose se na eko-otoke i pravne osobe.",
    "Info centar Flora VTC: 0800 444 110.",
]


def iso(text):
    return date.fromisoformat(text)


def colour_classes(a):
    """Per pixel: P yellow/orange, K blue, B brown, '.' anything else."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    out = np.full(r.shape, ".", dtype="<U1")
    out[(r > 220) & (g > 160) & (g < 225) & (b < 80)] = "P"
    out[(r < 60) & (g > 140) & (g < 210) & (b > 200)] = "K"
    out[(r > 170) & (r < 225) & (g > 95) & (g < 145) & (b < 90)] = "B"
    return out


def read_cells(path, name, months):
    """{date: code or ""} for every day cell; a sixth week row shares the Monday cell of the fifth (split
    diagonally: upper left = 5th row, lower right = 6th row)."""
    xs, px, ys, py, wanted = GRIDS[name]
    cls = colour_classes(np.asarray(Image.open(path).convert("RGB")).astype(int))
    out = {}
    for i, (y, m) in enumerate(months):
        first, ndays = date(y, m, 1).weekday(), calendar.monthrange(y, m)[1]
        six = (first + ndays - 1) // 7 == 5
        for day in range(1, ndays + 1):
            d = date(y, m, day)
            row = (first + day - 1) // 7
            cx = round(xs[i % 2] + d.weekday() * px)
            cy = round(ys[i // 2] + min(row, 4) * py)
            if row == 5:
                cell = cls[cy + 2:cy + 8, cx + 4:cx + 13]
            elif row == 4 and six and d.weekday() == 0:
                cell = cls[cy - 7:cy - 1, cx - 13:cx - 4]
            else:
                cell = cls[cy - 5:cy + 6, cx - 9:cx + 10]
            n = Counter(cell.ravel().tolist())
            code = max(wanted, key=lambda k: n[k])
            out[d] = code if n[code] >= 0.1 * cell.size else ""
    return out


def expected(cal):
    """{date: codes} of the coloured cells the calendars should show, with the holiday moves applied."""
    moves = {iso(h): iso(n) for h, n in cal["holidays"].items() if n}
    out = {}
    for code in "KPB":
        for w in cal[code].split():
            for k in range(5):
                d = iso(w) + timedelta(days=k)
                out.setdefault(moves.get(d, d), set()).add(code)
    return out


def sections(pdf_path, problems):
    """[(weekday, jls, text)] from the first page of the leaflet (two columns)."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        lines = (page.crop((0, 50, 287, page.height)).extract_text() + "\n" +
                 page.crop((287, 50, 590, page.height)).extract_text()).splitlines()
    out, day = [], None
    for line in lines:
        line = " ".join(line.split())
        if line.startswith("Molimo"):
            break
        if line.split(" ")[0] in DAYS:
            day = DAYS[line.split(" ")[0]]
        elif line in JLS:
            out.append([day, JLS[line], []])
        elif out:
            out[-1][2].append(line)
        elif line and not line.startswith(("Papir", "Plastika")):
            problems.append(f"redak prije prvog naslova: {line!r}")
    return [(d, j, " ".join(t)) for d, j, t in out]


def split_names(text):
    """Comma-separated names, keeping commas inside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return [n for n in out if n]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    cal = CALENDAR
    months = cal["months"]
    if year not in {y for y, _ in months}:
        sys.exit(f"Objavljeni kalendari ({months[0][1]}/{months[0][0]}–{months[-1][1]}/{months[-1][0]}) ne "
                 f"obuhvaćaju {year}. godinu.")
    page = html.unescape(fetch(PAGE).decode("utf-8", "replace"))
    info = html.unescape(fetch(INFO).decode("utf-8", "replace"))
    pdfs = sorted(set(re.findall(r'href="([^"]+Obavijest-o-prikupljanju-otpada[^"]*\.pdf)"', page)))
    if len(pdfs) != 1:
        sys.exit(f"Na {PAGE} nije pronađen jedan letak „Obavijest o prikupljanju otpada”: {pdfs}")
    images = {}
    for name in cal["images"]:
        found = re.findall(rf'href="(https?://[^"]+/kalendar/{re.escape(name)})"', info)
        images[name] = found[0] if found else f"{SITE}/wp-content/uploads/kalendar/{name}"
    problems = []
    cells = {}
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "obavijest.pdf"
        fetch(pdfs[0], pdf)
        parts = sections(pdf, problems)
        for name, url in images.items():
            path = Path(tmp) / name
            fetch(url, path)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if sha != cal["images"][name]:
                sys.exit(f"slika se promijenila, prepisati ponovno: {url} (sha256 {sha})")
            for d, code in read_cells(path, name, months).items():
                if code:
                    cells.setdefault(d, set()).add(code)
    want = expected(cal)
    window = (date(*months[0], 1), date(months[-1][0], months[-1][1], calendar.monthrange(*months[-1])[1]))
    for d in sorted(set(want) | set(cells)):
        if want.get(d, set()) != cells.get(d, set()):
            problems.append(f"{d}: u skripti {''.join(sorted(want.get(d, ''))) or '-'}, na kalendaru "
                            f"{''.join(sorted(cells.get(d, ''))) or '-'}")
    holidays = {iso(h): (iso(n) if n else None) for h, n in cal["holidays"].items()}
    weekday_hol = {h for y in {window[0].year, window[1].year} for h in pravila.blagdani(y)
                   if window[0] <= h <= window[1] and h.weekday() < 5}
    if weekday_hol != set(holidays):
        problems.append(f"legenda blagdana ne odgovara blagdanima u razdoblju: {sorted(weekday_hol ^ set(holidays))}")
    for code in "KPB":
        weeks = [iso(w) for w in cal[code].split()]
        if any(w.weekday() for w in weeks) or len(set(weeks)) != len(weeks):
            problems.append(f"{code}: tjedni nisu različiti ponedjeljci")
    # zones: one per (jls, weekday), in the leaflet's order of municipalities
    order = list(dict.fromkeys(j for _, j, _ in parts))
    if set(order) != set(PROVIDER["jls"]):
        problems.append(f"u letku su JLS {order}, očekivano {PROVIDER['jls']}")
    zones = {}
    for wd, jls, text in parts:
        zones.setdefault((jls, wd), []).append(text)
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "napomene": list(NAPOMENE), "zone": {}}
    regular = ", ".join(f"{h.day}.{h.month}." for h, n in sorted(holidays.items()) if not n)
    moved = ", ".join(f"{h.day}.{h.month}. → {n.day}.{n.month}." for h, n in sorted(holidays.items()) if n)
    data["napomene"].insert(1, f"Kalendari papira, plastike i biootpada objavljeni su za razdoblje od "
                               f"{window[0]:%d.%m.%Y.} do {window[1]:%d.%m.%Y.}; podaci obuhvaćaju samo to razdoblje "
                               "(raniji mjeseci nisu objavljeni).")
    data["napomene"].insert(2, f"Blagdani prema kalendaru: {regular} redovan odvoz; neradni dani {moved} (odvoz u "
                               "subotu, označeno kao premješteno).")
    for (jls, wd) in sorted(zones, key=lambda k: (order.index(k[0]), k[1])):
        text = " ".join(zones[(jls, wd)])
        names = split_names(text)
        ulice = [re.sub(r"^naselja s klancima:\s*", "", n) for n in names if "bez odv." not in n]
        rows = {}
        d = window[0] + timedelta(days=(wd - window[0].weekday()) % 7)
        while d <= window[1]:
            codes = "M" + "".join(c for c in "KPB" if any(iso(w) <= d < iso(w) + timedelta(days=7)
                                                          for w in cal[c].split()))
            new = holidays.get(d) or d
            rows[new] = (codes, new != d)
            d += timedelta(days=7)
        cnt = Counter((day.year, day.month) for day in rows)
        problems += [f"{jls} {DAN[wd]}: {k} odvoza u {m}/{y}" for (y, m), k in cnt.items() if not 4 <= k <= 5]
        for day, (codes, mv) in rows.items():
            if not mv and day.weekday() != wd:
                problems.append(f"{jls} {DAN[wd]}: {day} nije {DAN[wd]}")
        key = str(len(data["zone"]) + 1)
        prev = old.get(key, {})
        keep = []
        if prev.get("jls") == jls and prev.get("podrucje", "").startswith(DAN[wd].capitalize()):
            keep = [r for r in podaci.iter_dates(prev) if not window[0] <= r[0] <= window[1]]
        allrows = keep + [(day, codes, mv) for day, (codes, mv) in rows.items()]
        raw = {}
        for y in sorted({r[0].year for r in allrows}):
            raw[str(y)] = podaci.month_lines([r for r in allrows if r[0].year == y])
        data["zone"][key] = {
            "jls": jls,
            "podrucje": f"{DAN[wd].capitalize()} – " + ", ".join(ulice[:4]) + (" …" if len(ulice) > 4 else ""),
            "opis": text,
            "ulice": ulice,
            "raw": raw,
        }
        n = Counter(c for codes, _ in rows.values() for c in codes)
        print(f"{jls}, {DAN[wd]}: {len(ulice)} ulica/naselja, {dict(n)}, premješteno: "
              f"{', '.join(f'{x:%d.%m.%Y.}' for x, (_, mv) in sorted(rows.items()) if mv) or '-'}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
