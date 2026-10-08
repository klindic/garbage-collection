"""Đurđenovac and Feričanci: RAD d.o.o. Đurđenovac, standing rules (no dated schedule is published).

    python3 -m izvori.rad_djurdjenovac [--year 2026]

RAD has no website of its own; its documents are on djurdjenovac.hr ("Objave društva RAD d.o.o. Đurđenovac").
Općina Đurđenovac: the leaflet "Raspored odvoza otpada za Općinu Đurđenovac" (a 2020 scan, kept in RULES with
its sha256) gives the weekday of every settlement: mixed waste (green bin) in the 1st and 3rd full week of the
month, plastic and metal bags and paper bags in the 2nd full week ("puni tjedan": a Monday–Friday week inside the
month, i.e. the week of the n-th Monday). It says that everything is collected as scheduled on holidays too.
Općina Feričanci: the text document "... u primjeni od 01.08.2022." (read and checked on every run): mixed waste
on the 1st, 3rd and 4th Friday, paper on the 2nd Friday, plastic and metal on the 4th Thursday; no holiday rule
is published, so the computed dates are kept and a note says so. Both sets of rules are old, which the notes say.
"""
import argparse
import hashlib
import html
import re
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "rad-djurdjenovac"
PAGE = "https://www.djurdjenovac.hr/objave-drustva-rad-d-o-o-durdenovac"
LEAFLET = "Raspored odvoza otpada za Općinu Đurđenovac.pdf"
LEAFLET_SHA = "d69d8942737411710ff46b3ea5fda4f49f4ec6657f7b9916288e5e1a3fca511f"
# Đurđenovac leaflet (2020), read by hand: zone settlements as printed, weekday of mixed waste, plastic+metal, paper
RULES = [
    (["Klokočevci", "Lipine", "Pribiševci", "Šaptinovci"], 0, 0, 2),
    (["Đurđenovac 1", "Sušine"], 1, 1, 3),
    (["Đurđenovac 2", "Gabrilovac", "Ličko Novo Selo", "N.N. Selo"], 2, 1, 3),
    (["Beljevina", "Bokšić", "Bokšić Lug", "Krčevina", "Teodorovac"], 3, 0, 2),
]
FULL_NAMES = {"N.N. Selo": "Našičko Novo Selo"}
# Feričanci text, rule sentence -> (code, weekday, n-th weekdays of the month)
FERICANCI = {
    r"Miješani komunalni otpad \(zelena kanta\) – prvi, treći i četvrti petak u mjesecu": ("M", "pet", (1, 3, 4)),
    r"Papir\s*-\s*drugi petak u mjesecu": ("K", "pet", (2,)),
    r"Plastika i metal\s*-\s*četvrti četvrtak u mjesecu": ("P", "čet", (4,)),
}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
INS = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
PROVIDER = {
    "davatelj": "RAD d.o.o. Đurđenovac",
    "web": PAGE,
    "izvor": PAGE,
    "zupanija": "Osječko-baranjska",
    "jls": ["Đurđenovac", "Feričanci"],
    "nazivi": {"M": "Miješani komunalni otpad (zelena kanta)", "P": "Plastika i metalna ambalaža (vreće)",
               "K": "Papir (plava vreća)"},
    "napomene": [
        "RAD nema raspored s datumima: datumi su izračunati iz trajnih pravila (Đurđenovac: letak iz 2020.; "
        "Feričanci: raspored u primjeni od 1.8.2022.). Pravila su stara; provjerite kod davatelja.",
        "Đurđenovac: miješani otpad u prvom i trećem punom tjednu u mjesecu, plastika i metal te papir u drugom "
        "punom tjednu (puni tjedan = ponedjeljak–petak unutar mjeseca); prema letku sav se otpad odvozi prema "
        "rasporedu i u slučaju praznika.",
        "Feričanci: miješani otpad prvi, treći i četvrti petak u mjesecu, papir drugi petak, plastika i metal četvrti "
        "četvrtak; pomaci zbog blagdana nisu objavljeni, pa su datumi ostavljeni prema pravilu.",
        "RAD d.o.o., Trg dr. Franje Tuđmana 6, Đurđenovac: 031/601-516, rad.d.o.o@os.t-com.hr.",
    ],
}


def nth_monday_week(year, n):
    """Mondays of the n-th full week of every month (the week of the n-th Monday)."""
    return pravila.mjesecno(year, "pon", n)


def odt_text(path):
    xml = zipfile.ZipFile(path).read("content.xml").decode("utf-8")
    xml = re.sub(r"</text:p>|</text:h>", "\n", xml)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", xml)).split())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = html.unescape(fetch(PAGE).decode("utf-8", "replace"))
    links = re.findall(r'href="([^"]+)"', page)
    leaflet = [u for u in links if u.endswith(LEAFLET)]
    fer = sorted(((date(*map(int, reversed(m.group(1).split(".")))), u) for u in links
                  for m in [re.search(r"Feričanci u primjeni od (\d{2}\.\d{2}\.\d{4})", u)] if m), reverse=True)
    if not leaflet or not fer:
        sys.exit(f"Na {PAGE} nisu pronađeni letak za Đurđenovac i raspored za Feričanci.")
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        pdf, odt = Path(tmp) / "letak.pdf", Path(tmp) / "fericanci.odt"
        fetch(leaflet[0], pdf)
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if sha != LEAFLET_SHA:
            sys.exit(f"slika se promijenila, prepisati ponovno: {leaflet[0]} (sha256 {sha})")
        fetch(fer[0][1], odt)
        text = odt_text(odt)
    since = fer[0][0]
    fer_rules = []
    for pattern, rule in FERICANCI.items():
        if not re.search(pattern, text):
            problems.append(f"Feričanci: u dokumentu nema pravila {pattern!r}: {text[:300]}")
        fer_rules.append(rule)
    print(f"Feričanci: pravila u primjeni od {since:%d.%m.%Y.}")
    hol = set(pravila.blagdani(year))
    data = {**PROVIDER, "zone": {}}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    zones = []
    for names, m_wd, p_wd, k_wd in RULES:
        rows = [(mon + timedelta(days=m_wd), "M", False) for n in (1, 3) for mon in nth_monday_week(year, n)]
        rows += [(mon + timedelta(days=p_wd), "P", False) for mon in nth_monday_week(year, 2)]
        rows += [(mon + timedelta(days=k_wd), "K", False) for mon in nth_monday_week(year, 2)]
        full = [FULL_NAMES.get(n, n) for n in names]
        desc = f"miješani {INS[m_wd]}, plastika i metal {INS[p_wd]}, papir {INS[k_wd]}"
        zones.append(("Đurđenovac", f"{', '.join(full)} – {desc}", ", ".join(names), full, rows,
                      "Podjela naselja Đurđenovac na Đurđenovac 1 i 2 nije objavljena; provjerite kod RAD-a."
                      if any(n.startswith("Đurđenovac ") for n in names) else None))
    rows = [(d, code, False) for code, dan, ns in fer_rules for n in ns for d in pravila.mjesecno(year, dan, n)]
    zones.append(("Feričanci", "Cijela općina Feričanci – miješani i papir petkom, plastika i metal četvrtkom",
                  None, ["Feričanci"], rows, None))
    for z, (jls, desc, opis, ulice, rows, note) in enumerate(zones, 1):
        n = Counter((d.month, c) for d, c, _ in rows)
        per = {"M": 3, "K": 1, "P": 1} if jls == "Feričanci" else {"M": 2, "K": 1, "P": 1}
        problems += [f"zona {z}: {k}× {c} u {m}. mjesecu" for (m, c), k in n.items() if k != per[c]]
        if len({(d, c) for d, c, _ in rows}) != len(rows):
            problems.append(f"zona {z}: isti datum dvaput")
        merged = {}
        for d, c, _ in rows:
            merged[d] = merged.get(d, "") + c
        on_hol = sorted(d for d in merged if d in hol)
        if on_hol:
            print(f"zona {z} ({jls}): odvoz na blagdan {', '.join(f'{d:%d.%m.}' for d in on_hol)} "
                  f"({'prema letku odvoz i na blagdan' if jls == 'Đurđenovac' else 'pomak nije objavljen'})")
        zone = {"jls": jls, "podrucje": desc}
        if opis:
            zone["opis"] = opis
        zone["ulice"] = ulice
        zone["napomena"] = ("pravila iz 2020.; provjerite kod davatelja" if jls == "Đurđenovac" else
                            f"pravila iz {since.year}. (u primjeni od {since:%d.%m.%Y.}); provjerite kod davatelja")
        if note:
            zone["napomena"] += ". " + note
        prev = old.get(str(z), {}).get("raw", {})
        zone["raw"] = {**{y: v for y, v in prev.items() if y != str(year)},
                       str(year): podaci.month_lines([(d, c, False) for d, c in merged.items()])}
        data["zone"][str(z)] = zone
        print(f"zona {z}: {jls} – {', '.join(ulice)}: {dict(Counter(c for _, c, _ in rows))}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
