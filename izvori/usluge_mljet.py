"""Usluge Mljet d.o.o. (in the register: Komunalno Mljet d.o.o.): Općina Mljet, east and west part of the island.

    python3 -m izvori.usluge_mljet [--year 2026]

Mixed waste: the page "Raspored odvoza komunalnog otpada" has a small HTML table weekdays -> settlements
(Monday and Thursday in the east, Tuesday and Friday in the west, all year). The year PDF linked from the
page "Raspored odvoza glomaznog otpada" lists, per group of settlements, one or two dates a month for the
separately collected waste in bags (EE waste, paper, plastic, metal, glass and textile together) and the
two-day windows for free bulky waste. The PDF's settlement lists are a little longer than the HTML ones;
the groups are matched by their shared settlements. No holiday rule is published for mixed waste, so the
weekly dates are kept as they are; the PDF dates are used as listed.
"""
import argparse
import html as htmllib
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "usluge-mljet"
SITE = "https://usluge-mljet.hr"
PAGE = SITE + "/raspored-odvoza-komunalnog-otpada/"
PDF_PAGE = SITE + "/raspored-odvoza-glomaznog-otpada/"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
DAN = {"pon": "ponedjeljak", "uto": "utorak", "sri": "srijeda", "čet": "četvrtak", "pet": "petak"}
DAYS = {"PONEDJELJAK": "pon", "UTORAK": "uto", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet"}
PROVIDER = {
    "davatelj": "Usluge Mljet d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Mljet"],
    "nazivi": {"P": "Odvojeni otpad u vrećama (EE otpad, papir, plastika, metal, staklo, tekstil)"},
    "napomene": [
        "Raspored objavljuje Usluge Mljet d.o.o. (OIB 06551246049, Zabrežje 2, Babino Polje); u registru "
        "davatelja za Općinu Mljet naveden je Komunalno Mljet d.o.o. (OIB 78985387533) na istoj adresi.",
        "Odvojeni otpad (EE otpad, papir, plastika, metal, staklo i tekstil) u vrećama iznosi se dan prije "
        "odvoza kraj kanti; poslije navedenog datuma odvojeni otpad se ne prikuplja. Besplatne vreće: radnim "
        "danom 8 – 15 sati.",
        "Glomazni otpad odvozi se besplatno u navedena dva dana (iznijeti dan prije na vidljivo mjesto).",
        "Pomaci odvoza miješanog otpada zbog blagdana nisu objavljeni.",
        "Reciklažno dvorište Žukovac: radnim danom 8 – 16 sati.",
        "Kontakt: 020/745-187, info@usluge-mljet.hr.",
    ],
}


def naslov(name):
    return " ".join(w.capitalize() for w in name.split())


def places(text):
    return [naslov(p) for p in re.split(r",\s*", text.strip(" ,")) if p.strip()]


def mixed_groups(page):
    """[(weekday keys, settlements)] from the HTML table (th: weekdays, td: settlements)."""
    out = []
    for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", page, re.S):
        days = re.sub(r"<[^>]+>|&nbsp;", " ", th).split()
        if not days or any(d not in DAYS for d in days):
            continue
        out.append(([DAYS[d] for d in days], [p.strip() for p in htmllib.unescape(re.sub(r"<[^>]+>", "", td)).split(",")]))
    return out


def pdf_blocks(lines, year, problems):
    """[(settlements, [dates])] of the bagged waste: a heading of names, then 'MONTH DD.MM.YYYY. …' lines."""
    blocks, heading = [], []
    for line in lines:
        m = re.match(r"([A-ZČĆŽŠĐ]+) ((?:\d\d\.\d\d\.\d{4}\.? ?)+)$", line.strip())
        if m and m.group(1) in MONTHS:
            if heading:
                blocks.append((places(" ".join(heading)), []))
                heading = []
            if not blocks:
                problems.append(f"datumi bez naselja: {line}")
                continue
            month = MONTHS.index(m.group(1)) + 1
            for d, mm, y in re.findall(r"(\d\d)\.(\d\d)\.(\d{4})", m.group(2)):
                if (int(mm), int(y)) != (month, year):
                    problems.append(f"{d}.{mm}.{y} u retku {m.group(1)}")
                else:
                    blocks[-1][1].append(date(year, month, int(d)))
        elif re.fullmatch(r"[A-ZČĆŽŠĐ ,]+", line.strip()) and "," in line and "OTPAD" not in line:
            heading.append(line.strip())
        else:
            heading = []
    return blocks


def bulky(text, year):
    """[(settlements, [date, date])] from 'DD.MM.YYYY. i NAMES DD.MM.YYYY. NAMES'."""
    out = []
    for d1, m1, y1, a, d2, m2, y2, b in re.findall(
            r"(\d\d)\.(\d\d)\.(\d{4})\.? i ([A-ZČĆŽŠĐ ,]+?) (\d\d)\.(\d\d)\.(\d{4})\. ([A-ZČĆŽŠĐ ,]+)", text):
        if int(y1) == int(y2) == year:
            out.append((places(a + " " + b), [date(year, int(m1), int(d1)), date(year, int(m2), int(d2))]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    groups = mixed_groups(fetch(PAGE).decode("utf-8", "replace"))
    if len(groups) != 2:
        sys.exit(f"Na {PAGE} nije nađena tablica dana odvoza ({groups})")
    links = re.findall(rf'href="(https?://[^"]+/raspored-{year}[^"]*\.pdf)"', fetch(PDF_PAGE).decode("utf-8", "replace"))
    if not links:
        sys.exit(f"Na {PDF_PAGE} nema PDF-a rasporeda za {year}.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raspored.pdf"
        fetch(links[0], path)
        with pdfplumber.open(path) as pdf:
            lines = [l for p in pdf.pages for l in (p.extract_text() or "").splitlines()]
    blocks = pdf_blocks(lines, year, problems)
    big = bulky(" ".join(lines), year)
    if len(blocks) != 2 or len(big) < 2:
        problems.append(f"PDF: {len(blocks)} skupina odvojenog otpada, {len(big)} termina glomaznog")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, (days, html_places) in enumerate(groups, 1):
        block = [b for b in blocks if set(html_places) & set(b[0])]
        if len(block) != 1:
            problems.append(f"{', '.join(html_places)}: {len(block)} skupina u PDF-u")
            continue
        names, bags = block[0]
        if set(html_places) - set(names):
            problems.append(f"naselja s weba nisu u PDF-u: {sorted(set(html_places) - set(names))}")
        dates = {d: "M" for day in days for d in pravila.tjedno(year, day)}
        per_month = Counter(d.month for d in bags)
        if len(bags) != len(set(bags)) or len(per_month) != 12 or not set(per_month.values()) <= {1, 2}:
            problems.append(f"{names[0]}: odvojeni otpad po mjesecima {dict(per_month)}")
        usual = Counter(d.weekday() for d in bags).most_common(1)[0][0]
        for d in bags:
            if d.weekday() != usual:
                problems.append(f"{names[0]}: odvojeni otpad {d:%d.%m.} nije u isti dan u tjednu kao ostali")
            dates[d] = dates.get(d, "") + "P"
        windows = [w for p, w in big if set(p) & set(names)]
        for p, w in big:
            if set(p) & set(names) and not set(p) <= set(names):
                problems.append(f"{names[0]}: glomazni {w[0]:%d.%m.} i za druga naselja: {sorted(set(p) - set(names))}")
        for w in windows:
            for d in w:
                dates[d] = dates.get(d, "") + "G"
        if len(windows) < 1:
            problems.append(f"{names[0]}: nema termina glomaznog otpada")
        rows = [(d, c, False) for d, c in dates.items()]
        data["zone"][str(z)] = {
            "jls": "Mljet",
            "podrucje": f"{' i '.join(DAN[d] for d in days).capitalize()} – {', '.join(html_places)}",
            "ulice": names,
            "raw": {**old.get(str(z), {}).get("raw", {}), str(year): podaci.month_lines(rows)},
        }
        print(f"Zona {z} ({', '.join(days)}: {', '.join(names)}): {len(rows)} odvoza, odvojeni {len(bags)}, "
              f"glomazni {' '.join(f'{d:%d.%m.}' for w in windows for d in w)}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
