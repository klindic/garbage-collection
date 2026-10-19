"""Ilok: Kom-Ilok d.o.o., the yearly notice with standing rules (mixed waste weekday per street, n-th Tuesday recyclables).

    python3 -m izvori.kom_ilok [--year 2026]

Every January the company posted "Obavijest o sakupljanju komunalnog otpada u <year>. godini" with a PDF
(found through the WordPress REST API). Section 1 lists the streets of Ilok per mixed waste weekday
(Monday, Wednesday with Šarengrad, Bapska and Mohovo, Thursday, Friday; weekly) and the Tuesday rules:
paper on the 1st Tuesday in Ilok and the 2nd Tuesday in the villages and the Friday area of Ilok,
plastic on the 3rd and 4th Tuesday likewise. Section 9 lists the year's holidays without collection and
their replacement days, always the next working day (Monday to Friday); that rule is applied to the
computed dates. If no notice exists for the requested year, the newest older one is used and the
napomene say so ("pravila iz 2025.; provjerite kod davatelja"); its holiday table is then only used as
the rule. Biowaste and bulky waste (on request) have no dates.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import blagdani, mjesecno, tjedno

SLUG = "kom-ilok"
SITE = "https://kom-ilok.hr"
POSTS = SITE + "/?rest_route=/wp/v2/posts&search=sakupljanju%20komunalnog%20otpada&per_page=20&_fields=date,link,title,content"
DAYS = {"PONEDJELJAK": "pon", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet"}
DAY_NAME = {"pon": "ponedjeljak", "sri": "srijeda", "čet": "četvrtak", "pet": "petak"}
VILLAGES = ["Šarengrad", "Bapska", "Mohovo"]
TUESDAY = [  # the rule lines of the notice, in order: (n-th Tuesday, code, area)
    (1, "K", r"Svaki 1\. utorak u mjesecu ?– ?otpadni papir i karton ?– ?u Iloku"),
    (2, "K", r"Svaki 2\. utorak u mjesecu ?- ?otpadni papir i karton ?– ?Šarengrad, Bapska i Mohovo i područje grada Iloka koje se prema rasporedu odvoza MKO odvozi petkom"),
    (3, "P", r"Svaki 3\. utorak u mjesecu ?– ?plastika ?– ?u Iloku"),
    (4, "P", r"Svaki 4\. utorak u mjesecu ?- ?plastika ?– ?Šarengrad, Bapska, Mohovo i područje grada Iloka koje se prema rasporedu odvoza MKO odvozi petkom"),
]
PROVIDER = {
    "davatelj": "Kom-Ilok d.o.o.",
    "web": SITE,
    "zupanija": "Vukovarsko-srijemska",
    "jls": ["Ilok"],
    "nazivi": {"K": "Papir i karton", "P": "Plastika"},
}
NAPOMENE = [
    "Odvoz svakog radnog dana od ponedjeljka do petka; posude iznijeti do 7:00 sati.",
    "Odvoz koji padne na blagdan prebacuje se na sljedeći radni dan (prema tablici zamjenskih odvoza iz "
    "obavijesti davatelja).",
    "Glomazni otpad: jednom godišnje do 3 m³ po prijavi na 032/827-362 ili 032/827-350.",
    "Reciklažno dvorište: Ivana Gorana Kovačića 158, Ilok (pon, sri, pet 7–15; uto, čet 14–20; sub 8–12).",
]


def notice(year):
    """(year of the notice, PDF url) of the newest 'Obavijest o sakupljanju komunalnog otpada u Y. godini' ≤ year."""
    found = []
    for p in json.loads(fetch(POSTS)):
        m = re.search(r"U (\d{4})\. GODINI", p["title"]["rendered"].upper())
        pdf = re.search(r'href="([^"]+\.pdf)"', p["content"]["rendered"])
        if m and pdf and int(m.group(1)) <= year:
            found.append((int(m.group(1)), pdf.group(1) if pdf.group(1).startswith("http") else SITE + pdf.group(1)))
    if not found:
        sys.exit(f"Nema obavijesti o sakupljanju otpada do {year}. ({POSTS}). Ništa nije upisano.")
    return max(found)


def parse(text):
    """({weekday: ([streets], [villages])}, [tuesday rule lines found])."""
    part = text.split("RECIKLABILNOG OTPADA")[1].split("2. LOKACIJA")[0]
    part = re.sub(r"\(\s*PAPIR I PLASTIKA\s*\)", "", part)
    blocks = re.split(r"^\s*(PONEDJELJAK|UTORAK|SRIJEDA|ČETVRTAK|PETAK)\b.*$", part, flags=re.M)
    days, tuesday = {}, []
    for name, body in zip(blocks[1::2], blocks[2::2]):
        body = " ".join(body.split())
        if name == "UTORAK":
            tuesday = [bool(re.search(rx, body)) for _, _, rx in TUESDAY]
            continue
        villages = [v for v in VILLAGES if v.upper() in body]
        for v in VILLAGES:
            body = body.replace(v.upper(), "")
        streets = [s.strip(" .") for s in body.split(",") if s.strip(" .")]
        days[DAYS[name]] = (streets, villages)
    return days, tuesday


def holiday_rule(text):
    """The notice's holiday table must be 'next working day' throughout; returns the pairs found."""
    pairs = re.findall(r"(\d{2})\.\s*(\d{2})\.\s*[–-]\s*\w+\s+(\d{2})\.\s*(\d{2})\.\s*[–-]", text.split("TERMINI KADA")[-1])
    return [((int(a), int(b)), (int(c), int(d))) for a, b, c, d in pairs]


def next_workday(d, hol):
    d += timedelta(days=1)
    while d.weekday() > 4 or d in hol:
        d += timedelta(days=1)
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    src_year, url = notice(year)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "obavijest.pdf"
        fetch(url, path)
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True,
                              check=True).stdout
    days, tuesday = parse(text)
    problems = []
    if sorted(days) != sorted(DAYS.values()) or not all(tuesday) or len(tuesday) != 4:
        problems.append(f"obavijest se promijenila: dani {sorted(days)}, pravila utorka {tuesday}")
    # the holiday table of the notice's year must follow 'next working day'
    pairs = holiday_rule(text)
    src_hol = set(blagdani(src_year))
    bad = [p for p in pairs if next_workday(date(src_year, p[0][1], p[0][0]), src_hol) != date(src_year, p[1][1], p[1][0])]
    if len(pairs) < 5 or bad:
        problems.append(f"tablica blagdana nije 'sljedeći radni dan': {bad or pairs}")
    hol = set(blagdani(year))
    parts = []  # (weekday, places, village/Friday group for recyclables)
    for day in DAYS.values():
        streets, villages = days.get(day, ([], []))
        if streets:
            parts.append((day, streets, day == "pet"))
        if villages:
            parts.append((day, villages, True))
    zones = {}
    for n, (day, places, outer) in enumerate(parts, 1):
        rows = {}
        for d in tjedno(year, day):
            rows.setdefault(*((next_workday(d, hol), ["", True]) if d in hol else (d, ["", False])))[0] += "M"
        for k, code, _ in TUESDAY:
            if (k in (2, 4)) != outer:
                continue
            for d in mjesecno(year, "uto", k):
                key, moved = (next_workday(d, hol), True) if d in hol else (d, False)
                r = rows.setdefault(key, ["", False])
                r[0] += code
                r[1] |= moved
        out = sorted((d, c, mv) for d, (c, mv) in rows.items())
        cnt = Counter(c for _, codes, _ in out for c in codes)
        if not (52 <= cnt["M"] <= 53 and cnt["K"] == 12 and cnt["P"] == 12):
            problems.append(f"zona {n}: broj odvoza {dict(cnt)}")
        where = ", ".join(places[:4]) + (", …" if len(places) > 4 else "")
        rec = "papir 2. i plastika 4. utorak" if outer else "papir 1. i plastika 3. utorak"
        zones[str(n)] = {"jls": "Ilok", "podrucje": f"{DAY_NAME[day].capitalize()} ({rec}): {where}",
                         "ulice": places, "raw": {str(year): podaci.month_lines(out)}}
        print(f"Zona {n} ({DAY_NAME[day]}, {rec}): " + ", ".join(f"{c} {cnt[c]}" for c in "MKP")
              + f", mjesta {len(places)}")
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    napomene = list(NAPOMENE)
    if src_year < year:
        napomene.insert(0, f"Za {year}. nije objavljena nova obavijest: raspored je izračunat iz pravila iz "
                           f"{src_year}.; provjerite kod davatelja (032/827-362).")
        print(f"Pravila iz {src_year}. primijenjena na {year}.")
    data = {**PROVIDER, "izvor": url, "napomene": napomene, "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(zones)} zona)")


if __name__ == "__main__":
    main()
