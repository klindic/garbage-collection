"""Bebrina, Brodski Stupnik, Gornja Vrba, Klakar, Oriovac, Podcrkavlje, Sibinj, Sikirevci, Slavonski Šamac: Jakob Becker d.o.o.

    python3 -m izvori.jakob_becker [--year 2026]

Every municipality has a page on new.jakob-becker.hr with JPG screenshots of Excel tables; the file names mix
tariffs and schedules ("Cjenik_raspored_*"), so the schedule image of each page is chosen by its printed title
("KALENDAR ..." / "RASPORED ...", not "CJENIK ...") and named below. The image list of every page comes from the
WordPress REST API; an image that is neither the schedule nor a known tariff stops the script, and so does a
schedule image whose sha256 changed ("slika se promijenila"), because the content is transcribed here:
the 2026 date tables of Bebrina, Brodski Stupnik, Podcrkavlje and Sibinj, and the standing rules ("svaki petak",
"drugi petak u mjesecu") of Gornja Vrba, Klakar, Oriovac, Sikirevci and Slavonski Šamac, published in January
2025 and still on the pages; their 2026 dates are computed. Bukovlje only has a 2025 calendar and is left out.
Holiday shifts are not published: published dates are kept as printed and computed dates are not moved.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "jakob-becker"
SITE = "https://new.jakob-becker.hr"
API = SITE + "/wp-json/wp/v2/pages?slug={slugs}&_fields=slug,link,modified,content&per_page=20"
UPLOADS = SITE + "/wp-content/uploads/"
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
# images on the pages that are not schedules (tariffs, an old recycling-yard plan, a broken link)
IGNORE = {
    "bebrina": {"Cjenik_Bebrina_2.jpg", "Cjenik_Bebrina24.jpg",
                "KALENDAR-RECIKLAZNOG-DVORISTA-U-2023.-GOD-OPCINA-BEBRINA01.jpg"},
    "brodskistupnik": {"Cjenik_raspored_Brodski_stupnik_23.jpg"},
    "bukovlje-2": {"Cjenik_raspored_Bukovlje21.jpg", "Cjenik_raspored_Bukovlje24.jpg"},  # 2025 calendar, tariff
    "gornja-vrba": {"Cjenik_raspored_GornjaVrba26.jpg"},
    "klakar": {"Cjenik_Klakar21.jpg"},
    "duplicated-brodski-stupnik-936": {"Cjenik_raspored_Oriovac29.jpg"},
    "podcrkavlje": {"Cjenik_raspored_podcrkavlje30.jpg"},
    "sibinj": {"Cjenik_raspored_Sibinj30.jpg"},
    "sikirevci": {"Cjenik_raspored_SIKIREVCI20.jpg"},
    "samac": {"Cjenik_Slavonski_Samac_19.jpg"},
}
LEFT_OUT = {"bukovlje-2": "Bukovlje"}
# schedule image per page: (upload path, sha256 of the transcribed file, printed title)
IMAGES = {
    "bebrina": ("2026/01/Cjenik_raspored_Bebrina24.jpg",
                "2e449fcb949aaf4e2f7b1d015a6f5899ca76aa76c6ba56c99c588baddc2b15dc",
                "KALENDAR ODVOZA KOMUNALNOG I KORISNOG OTPADA U 2026. GOD. OPĆINA BEBRINA"),
    "brodskistupnik": ("2025/12/Cjenik_raspored_Brodski_stupnik_18.jpg",
                       "8bde462a0a8eec64a8382bc411f956a7857a790cfb30045842f746149c5e1124",
                       "KALENDAR ODVOZA KOMUNALNOG I KORISNOG OTPADA U 2026. GOD. OPĆINA BRODSKI STUPNIK"),
    "gornja-vrba": ("2025/01/Cjenik_raspored_GornjaVrba20.jpg",
                    "f4deae6d1a9eaa2dcb732ebcb7401f29766fc54442bac624172680202a9a958b",
                    "RASPORED USLUGA ODVOZA OTPADA-OPĆINA GORNJA VRBA"),
    "klakar": ("2025/01/Cjenik_raspored_Klakar19.jpg",
               "97779d7872f5a703f76ee5fe3e15f813069a5a394d05fb839d67f59227538952",
               "RASPORED USLUGA ODVOZA OTPADA-OPĆINA KLAKAR"),
    "duplicated-brodski-stupnik-936": ("2025/01/Cjenik_raspored_Oriovac27.jpg",
                                       "6c74c3b61a683102402af8ccc74b8f5b70fba23a791236f9f8e412d182407b86",
                                       "RASPORED USLUGA ODVOZA OTPADA-OPĆINA ORIOVAC"),
    "podcrkavlje": ("2026/01/Cjenik_raspored_podcrkavlje29.jpg",
                    "0dfaed6f78937c543aee4a75a24cbaa4374bb96b4975c93241c9e5a7eef33dde",
                    "KALENDAR ODVOZA KOMUNALNOG I KORISNOG OTPADA U 2026. GOD. OPĆINA PODCRKAVLJE"),
    "sibinj": ("2026/01/Cjenik_raspored_Sibinj27.jpg",
               "e6f014071abe312b0a3cdea85d8ea2d848b185785138a185f93227834f705ab2",
               "RASPORED USLUGA ODVOZA OTPADA u 2026 OPĆINA SIBINJ"),
    "sikirevci": ("2025/01/Cjenik_raspored_SIKIREVCI19.jpg",
                  "e51442d79fc247f70762855cc1c7e0f091811f72a8515e14ce3773d16b413b9d",
                  "RASPORED USLUGA ODVOZA OTPADA-OPĆINA SIKIREVCI"),
    "samac": ("2025/01/Cjenik_raspored_Slavonski_Samac_11.jpg",
              "bbcb66a962ef6d4551211ab8b7a9f177d008f9b73563b24a5c1c7374d99d7cba",
              "RASPORED USLUGA ODVOZA OTPADA-OPĆINA SLAVONSKI ŠAMAC"),
}
GLASS_MONTHS = (4, 8, 12)
# zones in the order of the municipalities. "dates": transcribed {codes: "DD.MM. ..."} for the year of the table;
# "rules": [(codes, weekday, n-th weekday of the month or 0 = every week, months or None = all)];
# "every": {code: (weekday, days between dates)} checks for transcribed dates; "odd": dates printed off the pattern
ZONES = [
    dict(page="bebrina", jls="Bebrina", year=2026, podrucje="Općina Bebrina – miješani svaki drugi petak, "
         "plastika i papir jednom mjesečno, staklo travanj, kolovoz, prosinac", ulice=["Bebrina (cijela općina)"],
         dates={"M": "02.01. 16.01. 30.01. 13.02. 27.02. 13.03. 27.03. 10.04. 24.04. 08.05. 22.05. 05.06. 19.06. "
                     "03.07. 17.07. 31.07. 14.08. 28.08. 11.09. 25.09. 09.10. 23.10. 06.11. 20.11. 04.12. 18.12.",
                "PK": "16.01. 27.02. 27.03. 22.05. 19.06. 17.07. 25.09. 23.10. 20.11.",
                "PKS": "24.04. 28.08. 18.12."},
         every={"M": ("pet", 14)}),
    dict(page="brodskistupnik", jls="Brodski Stupnik", year=2026, podrucje="Općina Brodski Stupnik – miješani "
         "svaki drugi četvrtak, plastika i papir jednom mjesečno, staklo travanj, kolovoz, prosinac",
         ulice=["Brodski Stupnik (cijela općina)"],
         dates={"M": "08.01. 22.01. 05.02. 19.02. 05.03. 19.03. 02.04. 16.04. 30.04. 14.05. 28.05. 11.06. 25.06. "
                     "09.07. 23.07. 06.08. 20.08. 03.09. 17.09. 01.10. 15.10. 29.10. 12.11. 26.11. 10.12. 24.12.",
                "PK": "22.01. 19.02. 19.03. 28.05. 25.06. 23.07. 17.09. 15.10. 26.11.",
                "PKS": "16.04. 20.08. 24.12."},
         every={"M": ("čet", 14)}),
    dict(page="gornja-vrba", jls="Gornja Vrba", podrucje="Općina Gornja Vrba – miješani svaki utorak, plastika i "
         "papir 1. utorak u mjesecu, staklo 1. utorak u travnju, kolovozu i prosincu",
         ulice=["Gornja Vrba (cijela općina)"],
         rules=[("M", "uto", 0, None), ("PK", "uto", 1, None), ("S", "uto", 1, GLASS_MONTHS)]),
    dict(page="klakar", jls="Klakar", podrucje="Općina Klakar – miješani svaki utorak, plastika i papir 1. srijeda "
         "u mjesecu, staklo 1. srijeda u travnju, kolovozu i prosincu", ulice=["Klakar (cijela općina)"],
         rules=[("M", "uto", 0, None), ("PK", "sri", 1, None), ("S", "sri", 1, GLASS_MONTHS)]),
    dict(page="duplicated-brodski-stupnik-936", jls="Oriovac", podrucje="Oriovac – Slavonski Kobaš, Lužani, Kujnik, "
         "Malino, Ciglenik, Bečic, Živike i Pričac: miješani svaki petak, plastika i papir 2. petak",
         ulice=["Slavonski Kobaš", "Lužani", "Kujnik", "Malino", "Ciglenik", "Bečic", "Živike", "Pričac"],
         rules=[("M", "pet", 0, None), ("PK", "pet", 2, None), ("S", "pet", 2, GLASS_MONTHS)]),
    dict(page="duplicated-brodski-stupnik-936", jls="Oriovac", podrucje="Oriovac – Oriovac i Radovanje: miješani "
         "svaki ponedjeljak, plastika i papir 2. ponedjeljak", ulice=["Oriovac", "Radovanje"],
         rules=[("M", "pon", 0, None), ("PK", "pon", 2, None), ("S", "pon", 2, GLASS_MONTHS)]),
    dict(page="podcrkavlje", jls="Podcrkavlje", year=2026, podrucje="Podcrkavlje – Tomica, Rastušje, Podcrkavlje "
         "i Grabarje: miješani svaki drugi četvrtak, plastika i papir 2. srijeda u mjesecu",
         ulice=["Tomica", "Rastušje", "Podcrkavlje", "Grabarje"],
         dates={"M": "01.01. 15.01. 29.01. 12.02. 26.02. 12.03. 26.03. 09.04. 23.04. 07.05. 21.05. 04.06. 18.06. "
                     "02.07. 16.07. 30.07. 13.08. 27.08. 10.09. 24.09. 08.10. 22.10. 05.11. 19.11. 03.12. 17.12. "
                     "31.12."},
         every={"M": ("čet", 14)}, rules=[("PK", "sri", 2, None), ("S", "sri", 2, GLASS_MONTHS)]),
    dict(page="podcrkavlje", jls="Podcrkavlje", year=2026, podrucje="Podcrkavlje – Brodski Zdenci, Donji Slatinik, "
         "Dubovik, Glogovica, Gornji Slatinik, Kindrovo, Matković Mala i Oriovčić: miješani svaki drugi četvrtak, "
         "plastika i papir 3. srijeda u mjesecu",
         ulice=["Brodski Zdenci", "Donji Slatinik", "Dubovik", "Glogovica", "Gornji Slatinik", "Kindrovo",
                "Matković Mala", "Oriovčić"],
         dates={"M": "01.01. 15.01. 29.01. 12.02. 26.02. 12.03. 26.03. 09.04. 23.04. 07.05. 21.05. 04.06. 18.06. "
                     "02.07. 16.07. 30.07. 13.08. 27.08. 10.09. 24.09. 08.10. 22.10. 05.11. 19.11. 03.12. 17.12. "
                     "31.12."},
         every={"M": ("čet", 14)}, rules=[("PK", "sri", 3, None), ("S", "sri", 3, GLASS_MONTHS)]),
    dict(page="sibinj", jls="Sibinj", year=2026, podrucje="Sibinj – Sibinj, Jakačina Mala, Grgurevići, Grižići, "
         "Ravan, Brčino, Čelikovići i Gornji Andrijevci: miješani svaka srijeda, papir 1. srijeda, plastika "
         "jednom mjesečno petkom",
         ulice=["Sibinj", "Jakačina Mala", "Grgurevići", "Grižići", "Ravan", "Brčino", "Čelikovići",
                "Gornji Andrijevci"],
         dates={"P": "09.01. 06.02. 06.03. 03.04. 15.05. 12.06. 10.07. 07.08. 04.09. 02.10. 13.11. 10.12.",
                "S": "28.04. 17.08. 21.12."},
         every={"P": ("pet", 0)}, odd={"10.12.": "P četvrtkom, kako je tiskano"},
         rules=[("M", "sri", 0, None), ("K", "sri", 1, None)]),
    dict(page="sibinj", jls="Sibinj", year=2026, podrucje="Sibinj – Slobodnica, Bartolovci, Gromačnik i Završje: "
         "miješani svaka srijeda, papir 1. četvrtak, plastika jednom mjesečno petkom",
         ulice=["Slobodnica", "Bartolovci", "Gromačnik", "Završje"],
         dates={"P": "23.01. 20.02. 20.03. 17.04. 29.05. 26.06. 24.07. 21.08. 18.09. 16.10. 27.11. 11.12.",
                "S": "29.04. 18.08. 22.12."},
         every={"P": ("pet", 0)}, rules=[("M", "sri", 0, None), ("K", "čet", 1, None)]),
    dict(page="sikirevci", jls="Sikirevci", podrucje="Općina Sikirevci – miješani svaki ponedjeljak, plastika i "
         "papir 1. ponedjeljak u mjesecu, staklo 1. ponedjeljak u travnju, kolovozu i prosincu",
         ulice=["Sikirevci (cijela općina)"],
         rules=[("M", "pon", 0, None), ("PK", "pon", 1, None), ("S", "pon", 1, GLASS_MONTHS)]),
    dict(page="samac", jls="Slavonski Šamac", podrucje="Općina Slavonski Šamac – miješani svaki ponedjeljak, "
         "plastika i papir 4. ponedjeljak u mjesecu, staklo 4. ponedjeljak u travnju, kolovozu i prosincu",
         ulice=["Slavonski Šamac (cijela općina)"],
         rules=[("M", "pon", 0, None), ("PK", "pon", 4, None), ("S", "pon", 4, GLASS_MONTHS)]),
]
PROVIDER = {
    "davatelj": "Jakob Becker d.o.o.",
    "web": SITE,
    "izvor": SITE + "/",
    "zupanija": "Brodsko-posavska",
    "jls": ["Bebrina", "Brodski Stupnik", "Gornja Vrba", "Klakar", "Oriovac", "Podcrkavlje", "Sibinj", "Sikirevci",
            "Slavonski Šamac"],
    "nazivi": {"P": "Plastika", "S": "Staklo (boce i teglice)"},
    "napomene": [
        "Plastika i papir odvoze se zajedno, istog dana (osim u Sibinju, gdje papir i plastika imaju svoje dane).",
        "Pomaci zbog blagdana nisu objavljeni: datumi su upisani kako su tiskani ili izračunati iz pravila; "
        "odvoz na blagdan provjeriti kod Jakob Becker d.o.o.",
        "Gornja Vrba, Klakar, Oriovac, Sikirevci i Slavonski Šamac: pravila odvoza objavljena su u siječnju 2025. "
        "i još su na stranicama općina; datumi za tekuću godinu izračunati su iz njih.",
        "Otpadno staklo (boce i teglice) svi korisnici mogu dovesti u reciklažno dvorište Jakob Becker d.o.o., "
        "Vrbska ulica 16, Gornja Vrba, od ponedjeljka do petka od 07:00 do 14:00 sati.",
        "Općina Bukovlje (također Jakob Becker) ima objavljen samo kalendar za 2025. i nije uključena.",
    ],
}


def original(url):
    """Upload file name without the WordPress size suffix ('x-661x1024.jpg' -> 'x.jpg')."""
    return re.sub(r"-\d+x\d+(?=\.\w+$)", "", url.rsplit("/", 1)[1])


def page_images(year):
    """{page slug: (page link, {image file name: url})} from the WordPress REST API."""
    pages = set(IMAGES) | set(LEFT_OUT)
    data = json.loads(fetch(API.format(slugs=",".join(sorted(pages)))))
    out = {}
    for p in data:
        srcs = re.findall(r'<img[^>]+?src="([^"]+)"', p["content"]["rendered"])
        out[p["slug"]] = (p["link"], {original(s): s for s in srcs})
    return out


def computed(year, rules):
    """{date: codes} from [(codes, weekday, n, months)] rules."""
    out = {}
    for codes, dan, n, months in rules:
        dates = pravila.tjedno(year, dan) if n == 0 else pravila.mjesecno(year, dan, n)
        for d in dates:
            if months is None or d.month in months:
                out[d] = out.get(d, "") + codes
    return out


def transcribed(year, dates):
    out = {}
    for codes, text in dates.items():
        for t in text.split():
            dd, mm = map(int, t.rstrip(".").split("."))
            d = date(year, mm, dd)
            out[d] = out.get(d, "") + codes
    return out


def check(zone, rows, year):
    """Problems: weekdays and spacing of transcribed dates, duplicates, counts per month."""
    problems = []
    odd = zone.get("odd", {})
    for code, (dan, step) in zone.get("every", {}).items():
        days = sorted(d for d, c in rows.items() if code in c)
        for d in days:
            if d.weekday() != pravila.DANI[dan] and f"{d:%d.%m.}" not in odd:
                problems.append(f"{d:%d.%m.} {code}: {DAN[d.weekday()]}, očekivano {DAN[pravila.DANI[dan]]}")
        for a, b in zip(days, days[1:]):
            if step and (b - a).days != step:
                problems.append(f"{code}: {a:%d.%m.} -> {b:%d.%m.} nije razmak od {step} dana")
    count = Counter((d.month, c) for d, codes in rows.items() for c in codes)
    weekly = any(r[2] == 0 and "M" in r[0] for r in zone.get("rules", []))
    for m in range(1, 13):
        lo, hi = (4, 5) if weekly else (2, 3)
        if not lo <= count[m, "M"] <= hi:
            problems.append(f"mjesec {m}: {count[m, 'M']} odvoza miješanog, očekivano {lo}-{hi}")
        for c in "PK":
            if count[m, c] != 1:
                problems.append(f"mjesec {m}: {count[m, c]} odvoza {c}, očekivano 1")
        if count[m, "S"] != (m in GLASS_MONTHS):
            problems.append(f"mjesec {m}: {count[m, 'S']} odvoza stakla")
    if "dates" in zone and zone["year"] != year:
        problems.append(f"prepisana tablica je za {zone['year']}., traženo {year}.")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    pages = page_images(year)
    ok, checked = True, {}
    for slug in list(IMAGES) + list(LEFT_OUT):
        if slug not in pages:
            print(f"{slug}: stranica nije pronađena preko WordPress API-ja")
            ok = False
            continue
        link, imgs = pages[slug]
        known = IGNORE.get(slug, set()) | ({IMAGES[slug][0].rsplit("/", 1)[1]} if slug in IMAGES else set())
        new = sorted(set(imgs) - known)
        if slug in LEFT_OUT:
            if new:
                print(f"{LEFT_OUT[slug]}: nove slike na {link}: {', '.join(new)} – provjeriti ima li rasporeda za {year}.")
            continue
        if new:
            print(f"{link}: nove slike {', '.join(new)} – provjeriti je li to novi raspored, pa dopuniti IMAGES/IGNORE")
            ok = False
        path, sha, title = IMAGES[slug]
        name = path.rsplit("/", 1)[1]
        if name not in imgs:
            print(f"{link}: nema više slike rasporeda {name}")
            ok = False
            continue
        got = hashlib.sha256(fetch(UPLOADS + path)).hexdigest()
        if got != sha:
            print(f"{link}: slika se promijenila, prepisati ponovno ({name}, sha256 {got})")
            ok = False
            continue
        checked[slug] = link
        print(f"{title}: slika provjerena ({name})")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    hol = set(pravila.blagdani(year))
    for i, zone in enumerate(ZONES, start=1):
        if zone["page"] not in checked:
            ok = False
            continue
        rows = computed(year, zone.get("rules", []))
        if "dates" in zone:
            for d, codes in transcribed(zone["year"], zone["dates"]).items():
                rows[d] = rows.get(d, "") + codes
        problems = check(zone, rows, year)
        on_holiday = [f"{d:%d.%m.} {c}" for d, c in sorted(rows.items()) if d in hol]
        print(f"Zona {i} ({zone['podrucje'].split(':')[0].split(' – ')[0]}): {len(rows)} dana odvoza; "
              f"na blagdan (bez pomaka, nije objavljeno): {', '.join(on_holiday) or '-'}")
        for d in zone.get("odd", {}):
            print(f"   iznimka: {d} {zone['odd'][d]}")
        for p in problems:
            print(f"   PROBLEM {p}")
        if problems:
            ok = False
            continue
        napomena = f"Izvor: {checked[zone['page']]}"
        if "dates" not in zone:
            napomena += " (pravila objavljena u siječnju 2025., datumi izračunati)"
        old = data["zone"].get(str(i), {})
        data["zone"][str(i)] = {
            "jls": zone["jls"], "podrucje": zone["podrucje"], "ulice": zone["ulice"], "napomena": napomena,
            "raw": {**old.get("raw", {}), str(year): podaci.month_lines([(d, c, False) for d, c in rows.items()])},
        }
    if not ok:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
