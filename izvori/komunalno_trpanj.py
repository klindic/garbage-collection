"""Komunalno Trpanj d.o.o.: Općina Trpanj (Trpanj, Gornja and Donja Vrućica, Duba Pelješka), per published period.

    python3 -m izvori.komunalno_trpanj [--year 2026]

The company has no web site of its own: Općina Trpanj posts every new schedule ("Raspored prikupljanja
miješanog komunalnog otpada od 15.09.2026. do opoziva", "… za srpanj i kolovoz 2026.") with a text PDF.
The posts are found with the WordPress REST API (search "otpad"); each PDF gives its period (from a date
"do opoziva", or named months) and a table weekday -> area. Summer tables are night rounds ("NED na PON
00:00-06:00" is the Monday collection) and also list dates for paper and plastic. An open period ends the
day before the next one starts. Areas are mapped to three zones: the centre of Trpanj, the rest of the
village ("cijeli Trpanj" / "naselje Trpanj" = both) and the villages Gornja and Donja Vrućica and Duba
Pelješka. No holiday rule is published ("podložan izmjenama u vrijeme blagdana", announced separately), so
the weekday dates are kept. Days of the year no period covers are left empty; dates already in
podaci/<slug>.json outside the periods read now are kept, so re-runs add new periods.
"""
import argparse
import json
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalno-trpanj"
SITE = "https://www.trpanj.hr"
PAGE = SITE + "/category/komunalno-d-o-o-trpanj/"
POSTS = SITE + "/wp-json/wp/v2/posts?search=otpad&per_page=50&after={after}T00:00:00&_fields=id,date,link,title,content"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
LONG = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK", "SUBOTA", "NEDJELJA"]
SHORT = {"PON": 0, "UT": 1, "UTO": 1, "SRI": 2, "ČET": 3, "PET": 4, "SUB": 5, "NED": 6}
ZONES = {
    "1": ("Trpanj – centralni dio", ["Trpanj", "Trpanj – centralni dio"]),
    "2": ("Trpanj – ostali dio naselja", ["Trpanj"]),
    "3": ("Gornja Vrućica, Donja Vrućica, Duba Pelješka", ["Gornja Vrućica", "Donja Vrućica", "Duba Pelješka"]),
}
PROVIDER = {
    "davatelj": "Komunalno Trpanj d.o.o.",
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Trpanj"],
    "napomene": [
        "Raspored objavljuje Općina Trpanj za svako razdoblje posebno (zimski raspored do opoziva, ljetni noćni "
        "odvoz od 0 do 6 sati); upisana su objavljena razdoblja.",
        "\"Cijeli Trpanj\" i \"naselje Trpanj\" iz rasporeda shvaćeni su kao cijelo naselje Trpanj (zone 1 i 2), "
        "bez Vrućice i Dube Pelješke koje se u rasporedu navode posebno.",
        "Raspored je podložan izmjenama u vrijeme blagdana i praznika; izmjene se objavljuju na trpanj.hr i na "
        "oglasnim pločama, a ovdje nisu upisane.",
        "Kontakt: Komunalno Trpanj d.o.o., Put Dubokog doca 3, 020 743 850, komunalno.trpanj@gmail.com.",
    ],
}


def areas(text, problems, where):
    """'TRPANJ- centralni dio, G. Vručica, D. Vručica, Duba Pelješka' -> ({'1', '3'}, business premises flag)."""
    t = text.lower()
    if "ne prikuplja" in t:
        return set(), False
    zones, villages, business = set(), 0, False
    for part in re.split(r",\s*", t):
        if "centralni" in part:
            zones.add("1")
        elif re.search(r"(cijeli|naselje) trpanj", part):
            zones |= {"1", "2"}
        elif "pos. objekti" in part:
            business = True
        elif re.search(r"vru[čć]ica|duba", part):
            villages += 1
        else:
            problems.append(f"{where}: nepoznato područje {part!r}")
    if villages:
        if villages != 3:
            problems.append(f"{where}: samo dio sela ({text})")
        zones.add("3")
    return zones, business


def period(text):
    """(start, end or None) from 'od 15.09.2026.' or 'SRPANJ I KOLOVOZ 2026. GODINE'."""
    m = re.search(r"\bod (\d\d)\.(\d\d)\.(\d{4})\.", text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))), None
    names = "|".join(MONTHS)
    m = re.search(rf"\b({names})(?: I ({names}))? (\d{{4}})\. GODINE", text)
    if m:
        y, first = int(m.group(3)), MONTHS.index(m.group(1)) + 1
        last = MONTHS.index(m.group(2) or m.group(1)) + 1
        end = date(y + (last == 12), last % 12 + 1, 1) - timedelta(days=1)
        return date(y, first, 1), end
    return None, None


def read_pdf(lines, problems, where):
    """{weekday: zones} for mixed waste, [(date, code, zones)] for paper/plastic, business-premises weekdays."""
    weekly, dated, business, section = {}, [], [], "M"
    section_dates, section_zones, section_day = [], None, None

    def close():
        if section != "M":
            if not section_dates or not section_zones:
                problems.append(f"{where}: {section} bez datuma ili područja")
            for d in section_dates:
                if section_day is not None and d.weekday() != section_day:
                    problems.append(f"{where}: {section} {d:%d.%m.} nije {LONG[section_day].lower()}")
                dated.append((d, section, section_zones or set()))

    for line in lines:
        line = line.strip()
        if line in ("PAPIR", "PLASTIKA", "MJEŠANI KOMUNALNI OTPAD"):
            close()
            section, section_dates, section_zones, section_day = \
                {"PAPIR": "K", "PLASTIKA": "P"}.get(line, "M"), [], None, None
            continue
        m = re.fullmatch(r"(\w+) na (\w+)(?: 00:00-06:00)?(?: (.+))?", line)
        if m and m.group(1) in SHORT and m.group(2) in SHORT:
            day = SHORT[m.group(2)]
            if (SHORT[m.group(1)] + 1) % 7 != day:
                problems.append(f"{where}: noć {m.group(1)} na {m.group(2)}")
            if section == "M":
                weekly[day], biz = areas(m.group(3) or "", problems, where)
            else:
                section_day = day
            continue
        m = re.fullmatch(r"(\w+) (.+)", line)
        if m and m.group(1) in LONG and section == "M":
            weekly[LONG.index(m.group(1))], biz = areas(m.group(2), problems, where)
            if biz:
                business.append(LONG.index(m.group(1)))
            continue
        m = re.fullmatch(r"(\d\d)\.(\d\d)\.(\d{4})\.", line)
        if m and section != "M":
            section_dates.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
            continue
        m = re.fullmatch(r"00:00-06:00 (.+)", line)
        if m and section != "M":
            section_zones = areas(m.group(1), problems, where)[0]
    close()
    if sorted(weekly) != list(range(7)):
        problems.append(f"{where}: dani u tablici {sorted(weekly)}")
    return weekly, dated, business


def merge(old_zone, rows, windows):
    """raw by year: the new rows plus old dates outside the windows read now."""
    keep = [r for r in podaci.iter_dates(old_zone) if not any(a <= r[0] <= b for a, b in windows)] if old_zone else []
    years = defaultdict(list)
    for r in keep + rows:
        years[r[0].year].append(r)
    return {str(y): podaci.month_lines(v) for y, v in sorted(years.items())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems, periods = [], []
    posts = json.loads(fetch(POSTS.format(after=f"{year - 1}-01-01")))
    with tempfile.TemporaryDirectory() as tmp:
        for post in posts:
            title = post["title"]["rendered"]
            if not re.match(r"Raspored (prikupljanja|sakupljanja)", title, re.I):
                continue
            pdfs = re.findall(r'href="([^"]+\.pdf)"', post["content"]["rendered"])
            if len(pdfs) != 1:
                problems.append(f"{post['link']}: {len(pdfs)} PDF-ova")
                continue
            path = Path(tmp) / f"{post['id']}.pdf"
            fetch(pdfs[0].replace("http://", "https://"), path)
            with pdfplumber.open(path) as pdf:
                lines = [l for p in pdf.pages for l in (p.extract_text() or "").splitlines()]
            start, end = period(" ".join(lines))
            if start is None:
                problems.append(f"{pdfs[0]}: nije nađeno razdoblje")
                continue
            weekly, dated, business = read_pdf(lines, problems, pdfs[0].rsplit("/", 1)[1])
            periods.append({"start": start, "end": end, "weekly": weekly, "dated": dated, "business": business,
                            "url": pdfs[0]})
    periods.sort(key=lambda p: p["start"])
    for p, nxt in zip(periods, periods[1:] + [None]):
        if p["end"] is None:
            p["end"] = nxt["start"] - timedelta(days=1) if nxt else date(year, 12, 31)
        elif nxt and p["end"] >= nxt["start"]:
            problems.append(f"razdoblja se preklapaju: {p['url']} i {nxt['url']}")
    periods = [p for p in periods if p["start"] <= date(year, 12, 31) and p["end"] >= date(year, 1, 1)]
    if not periods:
        problems.append(f"nema rasporeda za {year}.")

    rows = {z: {} for z in ZONES}
    windows = []
    for p in periods:
        a, b = max(p["start"], date(year, 1, 1)), min(p["end"], date(year, 12, 31))
        windows.append((a, b))
        print(f"{a:%d.%m.} – {b:%d.%m.%Y}: {p['url']}")
        d = a
        while d <= b:
            for z in p["weekly"].get(d.weekday(), ()):
                rows[z][d] = rows[z].get(d, "") + "M"
            d += timedelta(days=1)
        for d, code, zones in p["dated"]:
            if not a <= d <= b:
                problems.append(f"{d:%d.%m.%Y} ({code}) izvan razdoblja {a:%d.%m.} – {b:%d.%m.}")
            for z in zones:
                rows[z][d] = rows[z].get(d, "") + code
        for z in ZONES:  # full months of the period: weekly count x 4..5
            k = sum(z in zs for zs in p["weekly"].values())
            for m in range(a.month, b.month + 1):
                first, last = date(year, m, 1), date(year + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
                n = sum("M" in c for d, c in rows[z].items() if first <= d <= last)
                if a <= first and last <= b and not 4 * k <= n <= 5 * k:
                    problems.append(f"zona {z} {m}/{year}: {n} odvoza miješanog, očekivano {4 * k}–{5 * k}")
    gaps, d = [], date(year, 1, 1)
    for a, b in sorted(windows) + [(date(year + 1, 1, 1), None)]:
        if a > d:
            gaps.append(f"{d:%d.%m.} – {a - timedelta(days=1):%d.%m.%Y.}")
        d = max(d, b + timedelta(days=1)) if b else d
    for g in gaps:
        print(f"Nije objavljen raspored za {g}")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    business = sorted({LONG[w].lower() for p in periods for w in p["business"]})
    for z, (podrucje, ulice) in ZONES.items():
        for d, c in rows[z].items():
            if len(set(c)) != len(c):
                problems.append(f"zona {z} {d}: ista vrsta dvaput ({c})")
        zone = {"jls": "Trpanj", "podrucje": podrucje, "ulice": ulice}
        if z == "3":
            zone["napomena"] = ("Ljetni datumi za papir i plastiku navedeni su za \"cijeli Trpanj\"; nije jasno "
                                "vrijede li i za Vrućicu i Dubu Pelješku pa ovdje nisu upisani.")
        elif business:
            zone["napomena"] = (f"Zimski raspored predviđa i odvoz za \"Trpanj – pos. objekti\" ({', '.join(business)}); "
                                "taj odvoz ovdje nije upisan.")
        zone["raw"] = merge(old.get(z), [(d, c, False) for d, c in rows[z].items()], windows)
        data["zone"][z] = zone
        print(f"Zona {z} ({podrucje}): {len(rows[z])} odvoza u {year}.")
    if gaps:
        data["napomene"] = data["napomene"] + [f"Za razdoblje {', '.join(gaps)} raspored nije objavljen."]
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
