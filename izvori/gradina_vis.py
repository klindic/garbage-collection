"""Gradina Vis d.o.o.: Grad Vis, four areas; mixed waste from the 2020/21 winter plan, recyclables from notices.

    python3 -m izvori.gradina_vis [--year 2026]

The page "Gospodarenje otpadom" links four "KALENDAR ODVOZA KOMUNALNOG OTPADA" PDFs, one per area, each a plan
"studeni 2020. – travanj 2021. godine": a weekday table where mixed-waste days are tick marks (a SymbolMT glyph,
read with pdfplumber and matched to the weekday heading above it), plastic, glass and paper "Zadnji četvrtak /
petak / utorak u mjesecu" in bags at the collection points, and the holiday rule "** ... MKO se odgađa za
sljedeći radni dan". These are still the only plans; they are applied to the requested year only for their own
months (November to April) with a note that they date from 2020; May to October is not published. The actual
recyclables dates are announced in monthly posts ("Prikupljanje reciklabilnog otpada", a PDF with PAPIR /
PLASTIKA / STAKLO dates), read through the WordPress REST API; only those published dates are written (a date
off the plan's weekday is marked as moved). Holidays: mixed waste on a public holiday moves to the next working
day (pravila.primijeni_blagdane, marked as moved).
"""
import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "gradina-vis"
SITE = "https://gradinavis.hr"
PAGE = SITE + "/djelatnosti/gospodarenje-otpadom/"
POSTS = SITE + "/wp-json/wp/v2/posts?search=reciklabilnog&per_page=50&after={after}&_fields=id,date,link,content"
LINK_TEXT = "KALENDAR ODVOZA KOMUNALNOG OTPADA"
RULES_YEAR = 2020
MONTHS = {"siječanj": 1, "veljača": 2, "ožujak": 3, "travanj": 4, "svibanj": 5, "lipanj": 6, "srpanj": 7,
          "kolovoz": 8, "rujan": 9, "listopad": 10, "studeni": 11, "prosinac": 12}
HEADS = {"Ponedjeljak": "pon", "Utorak": "uto", "Srijeda": "sri", "Četvrtak": "čet", "Petak": "pet",
         "Subota": "sub", "Nedjelja": "ned"}
PLAN_RECYCLING = {"P": "čet", "S": "pet", "K": "uto"}  # "Zadnji četvrtak / petak / utorak u mjesecu"
NOTICE_TYPES = {"PAPIR": "K", "PLASTIKA": "P", "STAKLO": "S"}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DANI = list(pravila.DANI)
OLD = (f"Raspored prema pravilima iz {RULES_YEAR}. (plan za studeni 2020. – travanj 2021.); davatelj nije objavio "
       "novi raspored – provjerite prije odlaganja.")
PROVIDER = {
    "davatelj": "Gradina Vis d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Vis"],
    "nazivi": {"K": "Papir (vreća)", "P": "Plastika (vreća)", "S": "Staklo (vreća)"},
}


def read_plan(path, problems):
    """(area, (first month, last month), weekdays, holiday note found) from one area PDF."""
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        lines = (page.extract_text() or "").splitlines()
        words = page.extract_words()
        marks = [(c["x0"] + c["x1"]) / 2 for c in page.chars if "Symbol" in c["fontname"]]
    period = next((re.fullmatch(r"(\w+) (\d{4})\. – (\w+) (\d{4})\. godine", l.strip()) for l in lines
                   if re.fullmatch(r"\w+ \d{4}\. – \w+ \d{4}\. godine", l.strip())), None)
    if not period or period.group(1) not in MONTHS or period.group(3) not in MONTHS:
        problems.append(f"{path.name}: nema razdoblja plana")
        return None
    area = lines[lines.index(period.group(0)) + 1].strip()
    heads = {HEADS[w["text"]]: (w["x0"] + w["x1"]) / 2 for w in words if w["text"] in HEADS}
    if len(heads) != 7:
        problems.append(f"{path.name}: zaglavlje dana {heads}")
        return None
    days = []
    for x in marks:
        k, cx = min(heads.items(), key=lambda kv: abs(kv[1] - x))
        if abs(cx - x) > 15:
            problems.append(f"{path.name}: oznaka na x={x:.0f} nije ispod dana")
        days.append(k)
    text = " ".join(" ".join(lines).split())
    for phrase in ("Plastika Zadnji četvrtak u mjesecu", "Staklo Zadnji petak u mjesecu", "Papir Zadnja utorak u mjesecu"):
        if phrase.split(" ", 1)[1] not in text:
            problems.append(f"{path.name}: nema '{phrase}'")
    holiday = "prikupljanje MKO se odgađa za sljedeći radni dan" in text
    if not holiday:
        problems.append(f"{path.name}: nema pravila za blagdane")
    return area, (MONTHS[period.group(1)], MONTHS[period.group(3)]), sorted(days, key=DANI.index)


def notices(year, problems):
    """{date: codes} of recyclables from the monthly posts, and the PDF links used."""
    out, used = {}, []
    with tempfile.TemporaryDirectory() as tmp:
        for post in json.loads(fetch(POSTS.format(after=f"{year - 1}-11-01T00:00:00"))):
            for url in re.findall(r'href="([^"]+\.pdf)"', post["content"]["rendered"]):
                pdf = Path(tmp) / f"n{len(used)}.pdf"
                fetch(url, pdf)
                text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
                hits = re.findall(r"(PAPIR|PLASTIKA|STAKLO)\s+(\d{1,2})\.(\d{1,2})\.(\d{4})\.\s+(\w+)", text)
                if len(hits) != 3:
                    problems.append(f"obavijest nije pročitana: {url}")
                    continue
                for kind, d, m, y, day in hits:
                    dt = date(int(y), int(m), int(d))
                    if DAN[dt.weekday()] != day.lower():
                        problems.append(f"{url}: {dt} nije {day.lower()}")
                    if dt.year == year:
                        if NOTICE_TYPES[kind] in out.get(dt, ""):
                            problems.append(f"{dt}: {kind} dvaput")
                        out[dt] = out.get(dt, "") + NOTICE_TYPES[kind]
                if any(int(y) == year for *_, y, _ in hits):
                    used.append(url)
    return out, used


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = fetch(PAGE).decode("utf-8", "replace")
    links = [u for u, t in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', page, re.S)
             if html.unescape(re.sub(r"<[^>]+>", " ", t)).strip().startswith(LINK_TEXT)]
    problems = []
    if len(links) != 4:
        problems.append(f"na {PAGE} {len(links)} kalendara umjesto 4")
    plans = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, url in enumerate(links):
            pdf = Path(tmp) / f"plan{i}.pdf"
            fetch(url, pdf)
            plan = read_plan(pdf, problems)
            if plan:
                plans.append(plan)
                print(f"{url}: {plan[0]}, mjeseci {plan[1]}, miješani otpad {plan[2]}")
    recycling, used = notices(year, problems)
    print(f"Obavijesti o reciklabilnom otpadu za {year}: " + ", ".join(f"{d:%d.%m.} {c}" for d, c in sorted(recycling.items())))
    for p in problems:
        print("   PROBLEM", p)
    if problems or not plans:
        sys.exit("Ništa nije upisano.")
    zones = {}
    for z, (area, (m1, m2), days) in enumerate(plans, 1):
        in_plan = lambda d: d.month >= m1 or d.month <= m2
        plain = sorted(d for k in days for d in pravila.tjedno(year, k) if in_plan(d))
        dates = {}
        for d, moved in pravila.primijeni_blagdane(plain, "sljedeci", year):
            if d.year == year:
                dates[d] = ("M", dates.get(d, ("", False))[1] or moved)
        for d, codes in recycling.items():
            moved = any(DANI[d.weekday()] != PLAN_RECYCLING[c] for c in codes)
            old = dates.get(d, ("", False))
            dates[d] = (old[0] + codes, old[1] or moved)
        rows = sorted((d, c, mv) for d, (c, mv) in dates.items())
        for d, codes, mv in rows:
            if "M" in codes and DANI[d.weekday()] not in days and not mv:
                problems.append(f"zona {z}: {d} nije dan odvoza")
        for m in list(range(1, m2 + 1)) + list(range(m1, 13)):
            n = sum(1 for d, c, _ in rows if d.month == m and "M" in c)
            if not 4 * len(days) - 1 <= n <= 5 * len(days) + 1:
                problems.append(f"zona {z}: {m}. mjesec {n} odvoza miješanog otpada")
        moved = [f"{d:%d.%m.} {c}" for d, c, mv in rows if mv]
        places = [p.strip() for p in re.split(r"\s+[–-]\s+", area)]
        say = ", ".join(DAN[DANI.index(k)] for k in days)
        zones[str(z)] = {"jls": "Vis", "podrucje": " – ".join(places), "ulice": places,
                         "napomena": f"Miješani otpad prema planu iz {RULES_YEAR}. za studeni – travanj: {say}; "
                                     "za svibanj – listopad raspored nije objavljen.",
                         "raw": {str(year): podaci.month_lines(rows)}}
        print(f"Zona {z} ({area}): {say}; " + ", ".join(f"{c} {n}" for c, n in sorted(
            Counter(c for _, cs, _ in rows for c in cs).items())) + (f"; pomaknuto: {', '.join(moved)}"
                                                                     if moved else ""))
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    months = sorted({d.month for d in recycling})
    napomene = [
        OLD,
        "Miješani otpad upisan je samo za mjesece iz plana (siječanj – travanj i studeni – prosinac); ljetni "
        "raspored nije objavljen.",
        "Miješani otpad: u vrijeme blagdana ili neradnih dana odvoz se odgađa za sljedeći radni dan (upisano kao "
        "pomaknuto).",
        "Plastika, staklo i papir u vrećicama odlažu se na sabirnim mjestima pored kontejnera, jednom mjesečno u "
        "zadnjem tjednu mjeseca; datumi se objavljuju mjesečno – upisani su objavljeni datumi ("
        + ", ".join(f"{m}." for m in months) + f" mjesec {year}.).",
        "Glomazni otpad: u svibnju i listopadu (posebna obavijest) i/ili po pozivu.",
        "Obavijesti: " + ", ".join(used) + ".",
    ]
    podaci.save(SLUG, {**PROVIDER, "napomene": napomene, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
