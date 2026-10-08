"""Drniš, Ružić: Gradska čistoća Drniš d.o.o. (gradskacistoca-drnis.hr), five weekday zones of settlements.

    python3 -m izvori.gradska_cistoca_drnis [--year 2026]

The notice "Plan odvoza komunalnog otpada, biootpada i reciklabilnog otpada ... od 01.01.<year>. do
31.12.<year>." links a brochure (album/001.jpg ... 012.jpg) and a scanned colour calendar (KALENDAR_<year>
*.pdf), all pictures without text. Page 2 of the brochure lists the five zones (settlements), the weekday of
each zone (I Monday ... V Friday) and the holiday moves of mixed waste per zone ("zbog praznika 06.04. zona se
prikuplja ranije 04.04."); page 3 is a table of plastic and paper weeks ("Sve zone prema svom danu odvoza od
12.01. do 16.01.": every zone on its own weekday in that week) and metal-glass dates per zone. These were
transcribed into ZONES, MOVES, PLASTIC, PAPER and METAL_GLASS below with the sha256 of both brochure pages and
of the calendar PDF (checked against them by eye); a changed file stops the script. Zones III and IV hold
settlements of both towns and are split by JLS. Holidays: the brochure's moves are applied (marked as moved);
every weekday holiday must be covered by one. Later notices about changed collection days must be known
(KNOWN_NOTICES), otherwise the script stops.
"""
import argparse
import hashlib
import re
import sys
from collections import Counter
from datetime import date, timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "gradska-cistoca-drnis"
SITE = "https://www.gradskacistoca-drnis.hr"
NOTICES = SITE + "/aktualno/obavijesti"
YEAR = 2026  # the transcription below is for this year only
FILES = {  # file name -> sha256 of the checked file
    "002.jpg": "c86308857cbaa528ceb6e23ab9de313718cd6226a14266bfb6bdece33b1b6cf9",
    "003.jpg": "b0c737df7cc981dfc4a732f3d149e809cd80952e3a2ef950151d8c5ec42b5968",
    "KALENDAR_2026_v2.pdf": "8804abbc4e7cd0bb952b64fcb910c459979aeaa8dc6219f1c1c695dcaa47329d",
}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
INSTR = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
# brochure page 2: zone -> (weekday, settlements)
ZONES = {
    "I": (0, ["Drniš"]),
    "II": (1, ["Badanj", "Varoš", "Parčić", "Miočić", "Biočić", "Štikovo", "Lišnjak", "Velušić", "Trbounje", "Kričke"]),
    "III": (2, ["Otavice", "Gradac", "Baljci", "Ružić", "Moseć", "Žitnić", "Sedramić", "Pakovo Selo", "Pokrovnik",
                "Radonić"]),
    "IV": (3, ["Umljanovići", "Kljake", "Čavoglave", "Mirlović Polje", "Širitovci", "Karalić", "Bogatić Miljevački",
               "Brištane Gornje"]),
    "V": (4, ["Kaočine", "Ključ", "Drinovci Nos Kalik", "Brištane Donje", "Siverić", "Kadina Glavica", "Tepljuh"]),
}
RUZIC = {"Baljci", "Čavoglave", "Gradac", "Kljake", "Mirlović Polje", "Moseć", "Otavice", "Ružić", "Umljanovići"}
# brochure page 2 "Napomene": holiday -> collection day, per zone
MOVES = {"I": {"04-06": "04-04", "06-22": "06-20"}, "II": {"01-06": "01-05"}, "III": {"08-05": "08-04", "11-18": "11-21"},
         "IV": {"01-01": "01-02", "06-04": "06-05"}, "V": {"05-01": "04-30", "12-25": "12-24"}}
# brochure page 3: weeks (Monday-Friday) of plastic and paper, metal-glass dates per zone
PLASTIC = ["01-12 01-16", "02-09 02-13", "03-16 03-20", "04-13 04-17", "05-11 05-15", "06-15 06-19", "07-13 07-17",
           "08-17 08-21", "09-14 09-18", "10-12 10-16", "11-09 11-13", "12-14 12-18"]
PAPER = ["01-19 01-23", "03-23 03-27", "05-18 05-22", "07-06 07-10", "08-24 08-28", "10-19 10-23", "12-07 12-11"]
METAL_GLASS = {"I": "01-12 06-08 09-28", "II": "01-20 06-16 10-06", "III": "02-18 06-24 10-14", "IV": "02-26 07-02 10-29",
               "V": "03-20 07-17 11-06"}
KNOWN_NOTICES = {315}  # "... zbog državnog praznika Dana antifašističke borbe": 22.06. -> 20.06., as in the brochure
PROVIDER = {
    "davatelj": "Gradska čistoća Drniš d.o.o.",
    "web": SITE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Drniš", "Ružić"],
    "nazivi": {"M": "Miješani komunalni otpad (zeleni spremnik)", "P": "Plastika i tetrapak (žuti spremnik)",
               "K": "Papir i karton (plavi spremnik)", "L": "Metal i staklo (vrećice)"},
    "bioNapomena": "Biootpad se do uvođenja smeđih kanti odlaže u zeleni spremnik zajedno s miješanim otpadom; "
                   "zeleni otpad do 4 m³ godišnje besplatno u reciklažnom dvorištu.",
}
NAPOMENE = [
    "Odvoz ljeti (01.06.–15.09.) od 6 sati, u ostalom razdoblju od 7 sati.",
    "Plastika i papir: sve zone u navedenom tjednu na svoj dan odvoza; metal i staklo u vrećicama tri puta godišnje "
    "po zoni.",
    "Iznajmljivači i sezonski korisnici koji se prijave do 15.05. imaju dodatni odvoz subotom od 01.06. do 30.09. "
    "(nije uključen; u kalendaru tamnozelene subote).",
    "Glomazni otpad: na zahtjev (obrazac na gradskacistoca-drnis.hr), 4 m³ godišnje besplatno; reciklažno dvorište "
    "Prva Obrtnička ulica 1A, pon–pet 7–15 h.",
    "Pomaci odvoza miješanog otpada zbog blagdana preuzeti su iz brošure (označeni kao pomaknuti).",
]


def d(year, mmdd):
    m, dd = map(int, mmdd.split("-"))
    return date(year, m, dd)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Prepisani raspored vrijedi samo za {YEAR}.; za {year} treba prepisati novu brošuru.")
    problems = []
    listing = fetch(NOTICES).decode("utf-8", "replace")
    plan = sorted(set(re.findall(rf'href="(/aktualno/obavijesti/\d+-plan-odvoza[^"]*{year}[^"]*)"', listing)))
    if len(plan) != 1:
        sys.exit(f"Na {NOTICES} nije nađen (jedan) plan odvoza za {year}: {plan}")
    page_url = SITE + plan[0]
    page = fetch(page_url).decode("utf-8", "replace")
    files = sorted(set(re.findall(rf'(/images/[^"\']*{year}[^"\']*(?:album/\d+\.jpg|KALENDAR[^"\']*\.pdf))', page)))
    names = {f.rsplit("/", 1)[1]: f for f in files}
    for name, sha in FILES.items():
        if name not in names:
            problems.append(f"na stranici plana nema datoteke {name} (nađeno: {sorted(names)})")
            continue
        got = hashlib.sha256(fetch(SITE + names[name])).hexdigest()
        if got != sha:
            problems.append(f"slika se promijenila: {name} (sha256 {got}); prepisati raspored ponovno i upisati novi sha256")
    pid = int(plan[0].split("/")[-1].split("-")[0])
    for nid, title in re.findall(r'href="/aktualno/obavijesti/(\d+)-([^"]+)"', listing):
        if int(nid) > pid and re.search(r"promjen\w*-(termina|rasporeda)", title) and int(nid) not in KNOWN_NOTICES:
            problems.append(f"nova obavijest o promjeni termina: /aktualno/obavijesti/{nid}-{title} – pregledati")

    hol = set(pravila.blagdani(year))
    pl = [(d(year, a), d(year, b)) for a, b in (w.split() for w in PLASTIC)]
    pa = [(d(year, a), d(year, b)) for a, b in (w.split() for w in PAPER)]
    for a, b in pl + pa:
        if a.weekday() != 0 or b - a != timedelta(days=4):
            problems.append(f"tjedan {a}–{b} nije ponedjeljak–petak")
        if any(a <= h <= b for h in hol):
            problems.append(f"u tjednu {a}–{b} je blagdan (pravilo za pomak nije objavljeno)")
    if sorted(a.month for a, _ in pl) != list(range(1, 13)):
        problems.append("plastika nije jednom u svakom mjesecu")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "izvor": page_url, "napomene": NAPOMENE, "zone": {}}
    parts = []
    for jls in ("Drniš", "Ružić"):
        for z, (wd, places) in ZONES.items():
            mine = [p for p in places if (p in RUZIC) == (jls == "Ružić")]
            if mine:
                parts.append((jls, z, wd, mine))
    for jls, z, wd, places in parts:
        moves = {d(year, a): d(year, b) for a, b in MOVES[z].items()}
        rows = {}
        for x in pravila.tjedno(year, list(pravila.DANI)[wd]):
            new = moves.get(x, x)
            rows[new] = ["M", new != x]
        for code, weeks in (("P", pl), ("K", pa)):
            for a, b in weeks:
                x = a + timedelta(days=wd)
                rows.setdefault(x, ["", False])[0] += code
        for mmdd in METAL_GLASS[z].split():
            x = d(year, mmdd)
            if x.weekday() != wd:
                problems.append(f"zona {z}: metal-staklo {x} nije {DAN[wd]}")
            rows.setdefault(x, ["", False])[0] += "L"
        # checks
        for old_d, new_d in moves.items():
            if old_d not in hol or old_d.weekday() != wd or abs((new_d - old_d).days) > 3 or new_d.weekday() == 6:
                problems.append(f"zona {z}: pomak {old_d} -> {new_d} nije vjerojatan")
        for h in hol:
            if h.weekday() == wd and h not in moves:
                problems.append(f"zona {z}: blagdan {h} bez pomaka")
        for x, (codes, moved) in rows.items():
            if x in hol:
                problems.append(f"zona {z}: odvoz na blagdan {x}")
            if not moved and x.weekday() != wd:
                problems.append(f"zona {z}: {x} nije {DAN[wd]}")
            if len(set(codes)) != len(codes):
                problems.append(f"zona {z}: {codes} dvaput {x}")
        per = Counter((c, x.month) for x, (codes, _) in rows.items() for c in codes)
        for (c, m), n in per.items():
            if not (4 <= n <= 5 if c == "M" else n == 1):
                problems.append(f"zona {z}: {c} {n} puta u mjesecu {m}")
        k = str(len(data["zone"]) + 1)
        label = "Drniš (cijeli grad)" if places == ["Drniš"] else ", ".join(places)
        zone = {
            "jls": jls,
            "podrucje": f"Zona {z} – {INSTR[wd]}: {label}",
            "ulice": places,
            "napomena": f"Miješani otpad {INSTR[wd]}"
                        + (" (zbog blagdana: " + ", ".join(f"{a:%d.%m.} → {b:%d.%m.}" for a, b in moves.items()) + ")"
                           if moves else "")
                        + f"; plastika i papir {INSTR[wd]} u tjednima iz rasporeda; metal i staklo "
                        + ", ".join(f"{d(year, x):%d.%m.}" for x in METAL_GLASS[z].split()) + ".",
        }
        prev = old["zone"].get(k, {})
        keep = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("ulice") == places else {}
        zone["raw"] = {**keep, str(year): podaci.month_lines([(x, c, m) for x, (c, m) in rows.items()])}
        data["zone"][k] = zone
        print(f"Zona {k} ({jls}, zona {z}): {len(places)} naselja, {dict(Counter(c for c, _ in rows.values() for c in c))}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
