"""Drenovci: Drenovci d.o.o. za komunalne djelatnosti, the year's leaflet (recyclable dates, mixed waste days).

    python3 -m izvori.drenovci [--year 2026]

The leaflet "LETAK-<year>.-DRENOVCI.pdf" is linked from the home page of drenovci-komunalno.hr. Its table
has the paper and plastic dates (monthly, Mondays and Tuesdays) and one date each for glass, metal and
textile, in two settlement groups (Drenovci, Đurići, Račinovci | Posavski Podgajci, Rajevo Selo), each
as two half-year columns; pdfplumber word positions give the columns, and in a column the paper block
and the plastic block are told apart where the months start again (glass, metal and textile sit on
their label's line). The dates are compared with the same table on the page "Raspored odvoza
komunalnog otpada". Mixed waste is a rule: one weekday per settlement ("SRIJEDA - POSAVSKI PODGAJCI /
RAJEVO SELO", …); the leaflet does not say how often, so it is written as every week, and no holiday
shifts are published (the dates are kept, with a note). Bulky waste is collected on request.
"""
import argparse
import html
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import DANI, tjedno

SLUG = "drenovci"
SITE = "https://drenovci-komunalno.hr"
PAGE = SITE + "/raspored-odvoza-komunalnog-otpada/"
DAYS = {"PONEDJELJAK": "pon", "UTORAK": "uto", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet"}
SINGLE = {"STAKLO": "S", "METAL": "L", "TEKSTIL": "T"}
PLACES = ["Drenovci", "Đurići", "Račinovci", "Posavski Podgajci", "Rajevo Selo"]
DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\.")
PROVIDER = {
    "davatelj": "Drenovci d.o.o. za komunalne djelatnosti",
    "web": SITE,
    "zupanija": "Vukovarsko-srijemska",
    "jls": ["Drenovci"],
    "nazivi": {"P": "Plastika (žuti/smeđi spremnik)", "K": "Papir i karton (plavi spremnik)",
               "L": "Metal", "T": "Tekstil"},
}
NAPOMENE = [
    "Miješani komunalni otpad (zeleni čipirani spremnik): letak navodi dan u tjednu po naselju, ali ne i "
    "učestalost; upisan je odvoz svaki tjedan na taj dan. Pomaci zbog blagdana nisu objavljeni.",
    "Od 1. srpnja 2026. odvoz počinje u 06:00; spremnike iznesite večer prije ili do 06:00 na dan odvoza.",
    "Glomazni otpad odvozi se jednom godišnje po pozivu (do 3 m³) ili se predaje u reciklažnom dvorištu "
    "(Toljani 132, Drenovci, pon–pet 7–15).",
    "Mobilno reciklažno dvorište obilazi Posavske Podgajce, Rajevo Selo, Đuriće i Račinovce: {mrd}",
    "Informacije: Drenovci d.o.o., Franje Hanamana 1, tel. 032/861-644.",
]


def leaflet_url(year):
    page = fetch(SITE + "/").decode("utf-8", "replace")
    urls = sorted(set(re.findall(rf'href="([^"]*LETAK-{year}[^"]*\.pdf)"', page, re.I)))
    if len(urls) != 1:
        sys.exit(f"Letak za {year} na {SITE}: {urls}. Ništa nije upisano.")
    return urls[0]


def read_table(path, year, problems):
    """{group: {code: [dates]}} (group 0 = Drenovci, Đurići, Račinovci; 1 = Posavski Podgajci, Rajevo Selo)."""
    words = pdfplumber.open(path).pages[0].extract_words()
    labels = {w["text"]: w for w in words if w["text"] in ("PAPIR", "PLASTIKA", *SINGLE)}
    last = labels["TEKSTIL"]["bottom"] + 5
    heads = [w for w in words if w["text"] in ("DRENOVCI", "POSAVSKI") and w["top"] < labels["PAPIR"]["top"]]
    split = sum(w["x0"] for w in heads) / len(heads) + 40
    dates = [w for w in words if DATE.fullmatch(w["text"]) and w["top"] < last]
    out = {0: {}, 1: {}}
    for col in sorted({round(w["x0"] / 10) for w in dates}):
        cells = sorted((w for w in dates if round(w["x0"] / 10) == col), key=lambda w: w["top"])
        group = 0 if cells[0]["x0"] < split else 1
        block, prev = 0, 0
        for w in cells:
            d = date(*map(int, DATE.fullmatch(w["text"]).groups()[::-1]))
            single = next((c for t, c in SINGLE.items() if abs(labels[t]["top"] - w["top"]) < 5), None)
            if single:
                code = single
            else:
                if d.month < prev:
                    block += 1
                prev = d.month
                code = ["K", "P"][block] if block < 2 else None
            if code is None or d.year != year:
                problems.append(f"datum {w['text']} izvan očekivanog bloka")
                continue
            out[group].setdefault(code, []).append(d)
    return {g: {c: sorted(v) for c, v in t.items()} for g, t in out.items()}


def mixed_rules(path):
    """[(weekday, [(settlement, [streets] or None)])] from 'SRIJEDA - POSAVSKI PODGAJCI / RAJEVO SELO' …"""
    text = " ".join(subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True,
                                   check=True).stdout.split())
    part = re.search(r"((?:%s) - .*?)(?:KRUPNI|GLOMAZNI)" % "|".join(DAYS), text).group(1)
    out = []
    for day, body in re.findall(r"(%s) - (.*?)(?=(?:%s) - |$)" % ("|".join(DAYS), "|".join(DAYS)), part):
        items = []
        for item in [x.strip() for x in body.split("/") if x.strip()]:
            place = next((p for p in PLACES if item.upper().endswith(p.upper())), None)
            if place and item.upper() != place.upper():  # "Ulica Padež, Gunjanska i Nova ulica Rajevo Selo"
                streets = item[: -len(place)].strip()
                streets = re.sub(r"^Ulica\s+", "", streets)
                items.append((place, [s.strip() for s in re.split(r",|\si\s", streets) if s.strip()]))
            elif place:
                items.append((place, None))
            else:
                raise ValueError(f"nepoznato naselje u pravilu: {item!r}")
        out.append((DAYS[day], items))
    return out


def page_dates(year):
    """All dd.mm.yyyy. dates of the year on the HTML schedule page (for the cross-check)."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", fetch(PAGE).decode("utf-8", "replace")))
    return Counter(date(int(y), int(m), int(d)) for d, m, y in DATE.findall(text) if int(y) == year)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    url = leaflet_url(year)
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "letak.pdf"
        fetch(url, path)
        table = read_table(path, year, problems)
        rules = mixed_rules(path)
        plain = " ".join(subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True).stdout.split())
    mrd = re.findall(r"\d{2}\.\d{2}\.\d{4}\.", plain.split("MOBILNO RECIKLAŽNO DVORIŠTE")[-1])
    groups = {p: (0 if i < 3 else 1) for i, p in enumerate(PLACES)}
    for g, t in table.items():
        for c, ds in t.items():
            want = 12 if c in "KP" else 1
            if len(ds) != want or len({d.month for d in ds}) != want:
                problems.append(f"skupina {g + 1} {c}: {len(ds)} datuma, očekivano {want} (po mjesec)")
            if len({d.weekday() for d in ds}) != 1:
                problems.append(f"skupina {g + 1} {c}: različiti dani u tjednu")
    leaflet = Counter(d for t in table.values() for ds in t.values() for d in ds)
    web = page_dates(year)
    if leaflet != web:
        problems.append(f"letak i stranica se razlikuju: {sorted((leaflet - web).elements())} / {sorted((web - leaflet).elements())}")
    zones, n = {}, 0
    for day, items in rules:
        # one zone per recyclables group within the weekday; streets of a settlement make their own zone
        parts = {}
        for place, streets in items:
            key = (groups[place], bool(streets))
            parts.setdefault(key, []).append((place, streets))
        for (g, has_streets), members in parts.items():
            rows = {d: "M" for d in tjedno(year, day)}
            for c, ds in table[g].items():
                for d in ds:
                    rows[d] = rows.get(d, "") + c
            n += 1
            if has_streets:
                ulice = [f"{s} ({p})" for p, ss in members for s in ss]
                where = "; ".join(f"{p}: " + ", ".join(ss) for p, ss in members)
            else:
                ulice = [p for p, _ in members]
                where = ", ".join(ulice)
            others = [(p, ss) for _, its in rules for p, ss in its if ss and not has_streets]
            zone = {"jls": "Drenovci",
                    "podrucje": f"{podaci.DAYS[list(DANI).index(day)].capitalize()}: {where}",
                    "ulice": ulice}
            exc = [f"{p} – {', '.join(ss)}" for p, ss in others if p in ulice]
            if exc:
                zone["napomena"] = "Osim ulica s drugim danom odvoza miješanog otpada: " + "; ".join(exc) + "."
            zone["raw"] = {str(year): podaci.month_lines([(d, c, False) for d, c in rows.items()])}
            zones[str(n)] = zone
            cnt = Counter(c for c in "".join(rows.values()))
            print(f"Zona {n} ({where}): " + ", ".join(f"{c} {cnt[c]}" for c in "MKPSLT"))
    if sorted({p for _, its in rules for p, _ in its}) != sorted(PLACES):
        problems.append(f"pravilo miješanog otpada ne pokriva sva naselja: {rules}")
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    napomene = [t.format(mrd=", ".join(mrd) or "datumi na letku") for t in NAPOMENE]
    data = {**PROVIDER, "izvor": url, "napomene": napomene, "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(zones)} zona)")


if __name__ == "__main__":
    main()
