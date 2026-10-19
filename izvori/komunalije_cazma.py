"""Čazma: Komunalije d.o.o. Čazma, standing rules for 4 zones (transcribed from two JPG images).

    python3 -m izvori.komunalije_cazma [--year 2026]

The page "Raspored odvoza otpada" on komunalije.hr shows two images: raspored_odvoza_otpada.jpg (four
zones with their settlements; mixed waste every week on the zone's day, paper on the 1st and 3rd and
plastic on the 2nd and 4th such weekday of the month, biowaste) and raspored_odvoza_biootpada.jpg
(zone 1's biowaste: the Monday streets with Dereza and the Tuesday streets with Grabovnica). The rules
below are transcribed by hand; the script downloads both images and stops if a file's sha256 differs
from the transcribed one, so a new image is never read with stale rules. Zone 1 is split by its
biowaste day; Čazma streets on neither biowaste list get no biowaste. No holiday shifts are published:
the computed dates are kept (napomena says so). A 5th weekday of a month has no paper or plastic.
"""
import argparse
import hashlib
import re
import sys
from collections import Counter

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import mjesecno, tjedno

SLUG = "komunalije-cazma"
SITE = "https://www.komunalije.hr"
PAGE = SITE + "/index.php/komunalni-otpad/raspored-odvoza-otpada"
IMAGES = {  # file: sha256 of the transcribed version (Last-Modified Mon, 03 Aug 2026 10:14:56 GMT)
    "raspored_odvoza_otpada.jpg": "55d1d1ed46a6f7685955c33bc743c706194f788d964a5d883797912a6f0aba48",
    "raspored_odvoza_biootpada.jpg": "b275bb060b43c15c7be5045f5aac61e768050996684050a0678690f01806e4a2",
}
# zone: (mixed waste day, paper/plastic day, settlements, biowaste day or None) as in the images
ZONES = [
    ("Zona 1", "pon", "uto", ["Čazma", "Dereza", "Grabovnica"], None),
    ("Zona 2", "sri", "sri", ["Suhaja", "Pobjenik", "Pobrđani", "Pavličani", "Vrtlinska", "Andigola", "Vučani",
                       "Gornji Miklouš", "Donji Miklouš", "Novo selo", "Martinac", "Bojana"], "sri"),
    ("Zona 3", "čet", "čet", ["Bosiljevo", "Opčevac", "Palančani", "Dapci", "Sovari", "Marčani", "Prokljuvani",
                       "Gornji Lipovčani", "Donji Lipovčani", "Prnjarovac", "Grabik", "Dragičevac", "Cerina"], "čet"),
    ("Zona 4", "pet", "pet", ["Gornji Draganec", "Milaševac", "Vagovina", "Donji Draganec", "Komuševac", "Siščani",
                       "Zdenčec"], "pet"),
]
BIO_ZONE1 = {  # zone 1 biowaste: "strana Dereza ponedjeljkom, strana Grabovnica utorkom"
    "pon": ["Ulica hrvatskih branitelja", "Česmanska ulica", "Omladinska ulica", "Ulica Franje Vidovića",
            "Ulica 26. lipnja", "Livadarska ulica", "Ulica Antuna Gustava Matoša", "Ulica Alojzija Vulinca",
            "Trg Čazmanskog kaptola", "Ulica braće Radić", "Ulica kralja Tomislava", "Dereza"],
    "uto": ["Ulica Milana Novačića", "Ribarska ulica", "Ulica bana Josipa Jelačića", "Ulica kralja Zvonimira",
            "Ulica Gjure Jankesa", "Voćarska ulica", "Ulica svetog Andrije", "Moslavačka ulica", "Grabovnica"],
}
DAY_NAME = {"pon": "ponedjeljak", "uto": "utorak", "sri": "srijeda", "čet": "četvrtak", "pet": "petak"}
PROVIDER = {
    "davatelj": "Komunalije d.o.o. Čazma",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Bjelovarsko-bilogorska",
    "jls": ["Čazma"],
    "bioNapomena": "Biootpad: zona 1 strana Dereza ponedjeljkom, strana Grabovnica utorkom (ulice na popisu), "
                   "zone 2–4 svaki tjedan na dan miješanog otpada.",
}
NAPOMENE = [
    "Raspored je trajan (slike na komunalije.hr, ažurirane 3. 8. 2026.); datumi su izračunati iz pravila.",
    "Papir se odvozi 1. i 3., plastika 2. i 4. dan odvoza u mjesecu; peti tjedan u mjesecu nema odvoza papira "
    "i plastike.",
    "Pomaci zbog blagdana nisu objavljeni; datumi koji padaju na blagdan ostavljeni su kako jesu.",
    "Smeđe spremnike za biootpad iznesite najkasnije do 7:00 sati na dan odvoza.",
]


def check_images():
    """sha256 of each image on the page against the transcribed version."""
    page = fetch(PAGE).decode("utf-8", "replace")
    problems = []
    for name, digest in IMAGES.items():
        m = re.search(rf'src="([^"]*{re.escape(name)})"', page)
        if not m:
            problems.append(f"slika {name} više nije na stranici {PAGE}")
            continue
        url = m.group(1) if m.group(1).startswith("http") else SITE + "/" + m.group(1).lstrip("/")
        got = hashlib.sha256(fetch(url)).hexdigest()
        if got != digest:
            problems.append(f"slika se promijenila: {name} (sha256 {got}); prepišite pravila ponovo")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = check_images()
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    parts = []
    for name, day, pday, places, bio in ZONES:
        if bio is None:  # zone 1: split by biowaste day, the rest of Čazma without biowaste
            for bday, streets in BIO_ZONE1.items():
                parts.append((name, day, pday, streets, bday, f"biootpad {DAY_NAME[bday]}"))
            parts.append((name, day, pday, ["Čazma"], None, "ostale ulice Čazme"))
        else:
            parts.append((name, day, pday, places, bio, None))
    zones = {}
    for n, (name, day, pday, places, bio, extra) in enumerate(parts, 1):
        rows = {d: "M" for d in tjedno(year, day)}
        for k in (1, 2, 3, 4):
            for d in mjesecno(year, pday, k):
                rows[d] = rows.get(d, "") + ("K" if k in (1, 3) else "P")
        for d in tjedno(year, bio) if bio else []:
            rows[d] = rows.get(d, "") + "B"
        out = sorted((d, c, False) for d, c in rows.items())
        cnt = Counter(c for _, codes, _ in out for c in codes)
        if not (52 <= cnt["M"] <= 53 and cnt["K"] == 24 and cnt["P"] == 24 and (not bio or 52 <= cnt["B"] <= 53)):
            problems.append(f"{name}: broj odvoza {dict(cnt)}")
        pp = "" if pday == day else f", papir i plastika {DAY_NAME[pday]}"
        label = f"{name} ({DAY_NAME[day]}{pp}{', ' + extra if extra else ''})"
        zone = {"jls": "Čazma", "podrucje": f"{label}: " + ", ".join(places[:4]) + (", …" if len(places) > 4 else ""),
                "ulice": places}
        if name == "Zona 1" and bio is None:
            zone["napomena"] = ("Ulice Čazme koje nisu na popisu odvoza biootpada (slika „Raspored odvoza "
                                "biootpada”); za njih biootpad nije upisan.")
        zone["raw"] = {str(year): podaci.month_lines(out)}
        zones[str(n)] = zone
        print(f"Zona {n} ({label}): " + ", ".join(f"{c} {cnt[c]}" for c in "MBKP") + f", mjesta {len(places)}")
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "napomene": NAPOMENE, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json ({len(zones)} zona)")


if __name__ == "__main__":
    main()
