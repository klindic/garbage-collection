"""Novi Vinodolski: Komunalno trgovačko društvo Ivanj d.o.o. (ivanj.net), weekday rules by street.

    python3 -m izvori.ivanj_novi_vinodolski [--year 2026]

The page "Raspored odvoza za YYYY. godinu" links a two-page PDF (text layer; the "ti" ligature comes out as
(cid:415)). Page 1: mixed waste in two groups of streets, "PONEDJELJKOM I PETKOM (tokom cijele godine),
DODATNO SRIJEDOM OD 15.06. – 13.09.YYYY." and "UTORKOM I SUBOTOM ..., DODATNO ČETVRTKOM ...", and the
streets with underground containers (no schedule, RFID card). Page 2: recyclables and biowaste once a
week, Monday, Wednesday or Thursday, by street. A zone is a pair (mixed-waste group, recyclables day) of
the same street; names are matched without case, dots and brackets ("Krasa (uske ulice)" is Krasa).
Both pages say there is no collection on Easter Monday, Christmas and New Year's Day but on the first
following working day (Monday to Saturday here, since Saturday is a collection day).
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
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "ivanj-novi-vinodolski"
SITE = "https://ivanj.net"
PAGE = SITE + "/hr/cistoca-detalji/raspored-odvoza-za-{year}-godinu/"
DAYS = {"PONEDJELJKOM": 0, "UTORKOM": 1, "SRIJEDOM": 2, "ČETVRTKOM": 3, "PETKOM": 4, "SUBOTOM": 5}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
ALIASES = {"korzohrvbranitelja": "korzohrvatskihbranitelja"}
PROVIDER = {
    "davatelj": "Komunalno trgovačko društvo Ivanj d.o.o.",
    "web": SITE,
    "zupanija": "Primorsko-goranska",
    "jls": ["Novi Vinodolski"],
    "nazivi": {"P": "Reciklabilni otpad"},
}
NAPOMENE = [
    "Miješani komunalni otpad odvozi se dvaput tjedno, a od 15.6. do 13.9. triput tjedno; reciklabilni i "
    "biorazgradivi otpad jednom tjedno (raspored navodi samo dan u tjednu, uzeto je svaki tjedan).",
    "Ulice s polupodzemnim spremnicima (središte Novog Vinodolskog i dio Klenovice) odlažu miješani otpad u bilo koje "
    "doba; RFID kartice za pristup mogu zatražiti svi korisnici.",
    "Ivanj d.o.o.: 051/445-550, ivanj@ivanj.net.",
]


def norm(name):
    key = re.sub(r"\(.*?\)|[\s.]", "", name.lower())
    return ALIASES.get(key, key)


def streets(text):
    return [" ".join(s.split()) for s in re.split(r",\s*", text) if s.strip()]


def blocks(text):
    """[(heading, text)] for the upper-case weekday headings of a page."""
    pat = r"((?:PONEDJELJKOM|UTORKOM|SRIJEDOM|ČETVRTKOM|PETKOM|SUBOTOM)(?: (?:I|[A-ZČĆŽŠĐ]{2,}))*(?: \([^)]*\))?" \
          r"(?:,? DODATNO \w+ OD [\d.]+ – [\d.]+)?)"
    parts = re.split(pat, text)
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def next_working(d, hol):
    while d in hol or d.weekday() == 6:
        d += timedelta(days=1)
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    page = PAGE.format(year=year)
    html = fetch(page).decode("utf-8", "replace")
    links = re.findall(r'href="([^"]+Raspored-odvoza[^"]*\.pdf)"', html, re.I)
    links = [l for l in links if str(year) in l]
    if not links:
        sys.exit(f"Na {page} nema PDF-a za {year}.")
    url = links[-1]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raspored.pdf"
        fetch(url, path)
        pages = [" ".join((p.extract_text() or "").replace("(cid:415)", "ti").split())
                 for p in pdfplumber.open(path).pages]
    p1, p2 = pages[0], pages[1]
    if f"MIJEŠANOG KOMUNALNOG OTPADA U {year}" not in p1 or f"BIORAZGRADIVOG KOMUNALNOG OTPADA U {year}" not in p2:
        problems.append(f"raspored nije za {year}. godinu")
    for p in (p1, p2):
        if not re.search(r"neće se vrši\w* na Uskrsni ponedjeljak, Božić i Novu godinu, već prvi sljedeći radni dan", p):
            problems.append("pravilo za blagdane se promijenilo")
    hol_rule = {pravila.uskrs(year) + timedelta(days=1), date(year, 12, 25), date(year, 1, 1)}
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))

    # page 1: two mixed-waste groups and the underground containers
    mko = {}
    body1 = p1.split("Korisnici javne usluge iz ulica pokrivenih")[0]
    for head, text in blocks(body1):
        m = re.match(r"(\w+) I (\w+) \(tokom cijele godine\),? DODATNO (\w+) OD (\d{2})\.(\d{2})\. – "
                     r"(\d{2})\.(\d{2})\.(\d{4})", head)
        if not m or not all(g in DAYS for g in m.groups()[:3]):
            problems.append(f"str. 1: nepoznat naslov {head!r}")
            continue
        start = date(int(m.group(8)), int(m.group(5)), int(m.group(4)))
        end = date(int(m.group(8)), int(m.group(7)), int(m.group(6)))
        mko[head] = ([DAYS[m.group(1)], DAYS[m.group(2)]], DAYS[m.group(3)], start, end, streets(text))
    m = re.search(r"pokrivenih polupodzemnim spremnicima u Novom Vinodolskom \((.+?)\) i Klenovici \((.+?)\)", p1)
    shared = streets(m.group(1)) if m else []
    shared_klenovica = streets(m.group(2)) if m else []
    if len(mko) != 2 or not shared:
        problems.append(f"str. 1: {len(mko)} skupina miješanog otpada, {len(shared)} ulica s polupodzemnim spremnicima")

    # page 2: recyclables and biowaste, one weekday per street
    rec = {}
    body2 = p2.split("Molimo korisnike")[0]
    for head, text in blocks(body2):
        if head not in DAYS:
            problems.append(f"str. 2: nepoznat naslov {head!r}")
            continue
        for s in streets(text):
            if norm(s) in rec and rec[norm(s)][0] != DAYS[head]:
                problems.append(f"str. 2: {s} ima dva dana")
            rec[norm(s)] = (DAYS[head], s)
    if len(set(d for d, _ in rec.values())) != 3:
        problems.append("str. 2: očekivana tri dana (pon, sri, čet)")

    # zones: (mixed-waste group, recyclables day)
    groups, seen = defaultdict(list), set()
    for head, (_, _, _, _, names) in mko.items():
        for s in names:
            r = rec.get(norm(s))
            groups[(head, r[0] if r else None)].append(s)
            seen.add(norm(s))
    for s in shared:
        r = rec.get(norm(s))
        groups[("shared", r[0] if r else None)].append(s)
        seen.add(norm(s))
    for key, (wd, s) in rec.items():
        if key not in seen:
            groups[(None, wd)].append(s)
            print(f"Ulica samo u rasporedu reciklabilnog otpada: {s}")

    zones, no_dates = {}, []
    order = list(mko) + ["shared", None]
    for i, ((head, wd), names) in enumerate(sorted(groups.items(), key=lambda g: (order.index(g[0][0]),
                                                                                   -1 if g[0][1] is None else g[0][1])), 1):
        if head == "shared" and wd is None:  # underground containers and no recyclables day: nothing to list
            no_dates += names
            continue
        rows = []
        d = date(year, 1, 1)
        while d.year == year:
            codes = ""
            if head in mko:
                days, extra, a, b = mko[head][:4]
                if d.weekday() in days or (d.weekday() == extra and a <= d <= b):
                    codes += "M"
            if wd is not None and d.weekday() == wd:
                codes += "PB"
            if codes:
                rows.append((d, codes))
            d += timedelta(days=1)
        merged = {}
        for d, codes in rows:
            new = next_working(d + timedelta(days=1), hol) if d in hol_rule else d
            if new.year != year:
                continue
            c, m = merged.get(new, ("", False))
            merged[new] = ("".join(dict.fromkeys(c + codes)), m or new != d)
        parts = []
        if head in mko:
            days, extra = mko[head][:2]
            parts.append(f"miješani {DAN[days[0]]} i {DAN[days[1]]} (ljeti i {DAN[extra]})")
        elif head == "shared":
            parts.append("miješani u polupodzemne spremnike")
        if wd is not None:
            parts.append(f"reciklabilni i biootpad {DAN[wd]}")
        zone = {"jls": "Novi Vinodolski", "podrucje": "; ".join(parts).capitalize() + " – " +
                ", ".join(names[:3]) + (" …" if len(names) > 3 else ""), "ulice": names}
        if head is None:
            zone["napomena"] = "Ulica nije u rasporedu miješanog otpada."
        elif head == "shared":
            zone["napomena"] = "Polupodzemni spremnici za miješani otpad (RFID kartica), bez rasporeda odvoza."
        elif wd is None:
            zone["napomena"] = "Ulica nije u rasporedu reciklabilnog i biorazgradivog otpada."
        zone["raw"] = {str(year): podaci.month_lines([(d, c, m) for d, (c, m) in merged.items()])}
        for mo in range(1, 13):
            n = sum(1 for d, (c, _) in merged.items() if d.month == mo and "M" in c)
            if head in mko and not 7 <= n <= 14:
                problems.append(f"zona {i}: {n} odvoza miješanog otpada u {mo}. mjesecu")
        i = len(zones) + 1
        zones[str(i)] = zone
        print(f"Zona {i}: {len(names)} ulica – {zone['podrucje'][:110]}")

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    moved = sorted({(d, next_working(d + timedelta(days=1), hol)) for d in hol_rule})
    data = {**PROVIDER, "izvor": page, "napomene": NAPOMENE + [
        f"Klenovica: polupodzemni spremnici za miješani otpad u ulicama {', '.join(shared_klenovica)}.",
    ] + ([f"Polupodzemni spremnici, a bez rasporeda reciklabilnog otpada: {', '.join(no_dates)}."] if no_dates else []) + [
        "Blagdani: na Uskrsni ponedjeljak, Božić i Novu godinu nema odvoza, već prvi sljedeći radni dan (" +
        ", ".join(f"{a:%d.%m.} → {b:%d.%m.}" for a, b in moved) + "); ostali blagdani su redovni dani odvoza.",
        f"Izvor: {url}",
    ], "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years of zones that did not change
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
