"""Gradska čistoća i usluge d.o.o. (Vrgorac): Grad Vrgorac and Općina Pojezerje, mixed waste, rules from 2022.

    python3 -m izvori.gradska_cistoca_vrgorac [--year 2026]

The post "Raspored odvoza miješanog komunalnog otpada (MKO)" on cistoca-vrgorac.hr links the only published
schedule, a scanned PDF "Raspored odvoza miješanog komunalnog otpada (MKO) (u primjeni od 17. listopada 2022.
godine)": a weekly table (weekday x two shifts) of settlements, plus the streets of the town zones Vrgorac 1
and Vrgorac 2. The scan is transcribed by hand below; the script finds the PDF in the post, checks its sha256
and stops ("slika se promijenila") when it changes. The standing weekly rules are applied to the requested
year, with a note that they date from 2022. No recyclables schedule and no holiday rule are published: the
regular weekdays are kept and a note says so.
"""
import argparse
import hashlib
import re
import sys
from collections import Counter

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "gradska-cistoca-vrgorac"
SITE = "https://cistoca-vrgorac.hr"
PAGE = SITE + "/2021/09/17/raspored-odvoza-mijesanog-komunalnog-otpada-mko/"
PDF_SHA256 = "730e8ac3286dec08c2f9ac4fd27ae711b6fdd860fe1a2e4b3af340ab27ed89a7"  # Raspored-prikupljanja.pdf
RULES_YEAR = 2022
VRGORAC_1 = ["Pčelinjak I, II, III", "Hercegovačka", "Zagrebačka", "Vukovarska", "Hrvatskih iseljenika",
             "Hrvatskih velikana", "Rade Miletića", "Trg Stjepana Radića", "Pod Matokitom", "Put Gradine",
             "Andrije Kačića Miošića", "Matice hrvatske", "Splitska", "Tina Ujevića", "A. G. Matoša"]
VRGORAC_2 = ["Pod Glavicom", "Fra Ivana Rožića", "Težačka", "Škulja", "Šetalište Mate Raosa", "Dr. Dušana Franića",
             "Ul. kralja Tomislava", "Marka Marulića", "Lipanjskih žrtava", "Generala Janka Bobetka",
             "Kardinala Alojza Stepinca", "Domobranska", "Put Plane"]
ZONES = [  # (jls, podrucje, weekdays, places): rows of the scan by JLS; places served on several days apart
    ("Vrgorac", "Vrgorac 1 (gradska zona)", "pon", ["Vrgorac 1"] + VRGORAC_1),
    ("Vrgorac", "M. I. Pivci", "pon sri pet", ["M. I. Pivci"]),
    ("Vrgorac", "Vrgorac 2 (gradska zona), Kotezi, Kutac", "uto", ["Vrgorac 2"] + VRGORAC_2 + ["Kotezi", "Kutac"]),
    ("Vrgorac", "Stilja, Prapatnice, Zavojane, Majići", "uto", ["Stilja", "Prapatnice", "Zavojane", "Majići"]),
    ("Vrgorac", "Gospodarska zona Ravča", "uto čet", ["G. Z. Ravča (gospodarska zona)"]),
    ("Vrgorac", "Banja, Orah, Podprolog", "sri", ["Banja", "Orah", "Podprolog"]),
    ("Vrgorac", "Veliki Prolog, Milošići, Dusina", "čet", ["Veliki Prolog", "Milošići", "Dusina"]),
    ("Vrgorac", "Župa, Rašćane, Kozica, Vlaka, Dragljane, Dubrava", "čet",
     ["Župa", "Rašćane", "Kozica", "Vlaka", "Dragljane", "Dubrava"]),
    ("Vrgorac", "Draževitići, Umčani, Vina", "pet", ["Draževitići", "Umčani", "Vina"]),
    ("Vrgorac", "Ravča, Duge Njive, Kljenak, Višnjica, Kokorići", "sub",
     ["Ravča", "D. Njive (Duge Njive)", "Kljenak", "Višnjica", "Kokorići"]),
    ("Pojezerje", "Pozla Gora, Mali Prolog, Kobiljača, Otrići", "sri", ["Pozla Gora", "Mali Prolog", "Kobiljača", "Otrići"]),
]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DANI = list(pravila.DANI)
OLD = (f"Raspored prema pravilima iz {RULES_YEAR}.; davatelj nije objavio novi raspored – provjerite prije "
       "odlaganja.")
PROVIDER = {
    "davatelj": "Gradska čistoća i usluge d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Vrgorac", "Pojezerje"],
    "napomene": [
        OLD,
        "Jedini objavljeni raspored je tjedni raspored miješanog komunalnog otpada u primjeni od 17.10.2022. "
        "(dvije smjene); raspored za reciklabilni otpad nije objavljen.",
        "Pomaci zbog blagdana nisu objavljeni; upisani su redovni dani odvoza.",
        "Gradska čistoća i usluge d.o.o., Težačka 8, Vrgorac; 021/674-635.",
    ],
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    post = fetch(PAGE).decode("utf-8", "replace")
    links = [u for u, t in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', post, re.S)
             if re.sub(r"<[^>]+>", "", t).strip() == "raspored"]
    if not links:
        sys.exit(f"Na {PAGE} nema poveznice 'raspored'.")
    digest = hashlib.sha256(fetch(links[0])).hexdigest()
    if digest != PDF_SHA256:
        sys.exit(f"Slika se promijenila, raspored treba ponovno prepisati: {links[0]} (sha256 {digest})")
    print(f"Sken {links[0]} (sha256 isti kao u prijepisu); pravila iz {RULES_YEAR}. primijenjena na {year}.")
    print("Pretpostavka: blagdani bez pomaka (pravilo nije objavljeno).")
    problems, zones = [], {}
    seen = Counter(p for _, _, _, places in ZONES for p in places)
    problems += [f"{p} je u više zona" for p, n in seen.items() if n > 1]
    for z, (jls, podrucje, rule, places) in enumerate(ZONES, 1):
        rows = sorted((d, "M", False) for k in rule.split() for d in pravila.tjedno(year, k))
        for d, n in Counter(d for d, _, _ in rows).items():
            if n > 1:
                problems.append(f"zona {z}: {d} dvaput")
        for m in range(1, 13):
            n = sum(1 for d, _, _ in rows if d.month == m)
            if not 4 * len(rule.split()) <= n <= 5 * len(rule.split()):
                problems.append(f"zona {z}: {m}. mjesec {n} odvoza")
        days = ", ".join(DAN[DANI.index(k)] for k in rule.split())
        zones[str(z)] = {"jls": jls, "podrucje": f"{days.capitalize()} – {podrucje}", "ulice": places,
                         "napomena": f"Pravila iz {RULES_YEAR}. (miješani otpad: {days}).",
                         "raw": {str(year): podaci.month_lines(rows)}}
        print(f"Zona {z} ({jls}: {podrucje}): {days}, {len(rows)} odvoza")
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
