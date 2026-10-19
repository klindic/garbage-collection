"""Plaški: Plaški KOM d.o.o. (plaski-kom.hr), one zone for the whole municipality.

    python3 -m izvori.plaski_kom [--year 2026]

The post "Raspored odvoza otpada za <year> godinu ..." (WordPress REST) states the rules (paper the last
Monday, plastic the last Tuesday of the month) and its featured image (a 1024x1024 PNG table) lists the 24
dates; they are transcribed below and checked against the rules. Mixed waste was collected once a week on
Wednesday or Thursday depending on the street (the split was never published), so it is left out until
30.6.; the notice "Obavijest o novom rasporedu odvoza miješanog komunalnog otpada" (an image) says that from
1.7.2026 the whole municipality is collected every Wednesday. Both images' sha256 are pinned, so a new image
stops the script until it is read again. No holiday rule is published: the dates stay as computed (the
script prints the Wednesdays that fall on a public holiday) and a note says so.
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

SLUG = "plaski-kom"
SITE = "https://www.plaski-kom.hr"
POSTS = SITE + "/wp-json/wp/v2/posts?search=raspored%20odvoza&per_page=20&_fields=id,date,link,title,featured_media,content"
MEDIA = SITE + "/wp-json/wp/v2/media/{id}?_fields=id,source_url"
# year table (featured image of the year post), transcribed: month -> (paper, plastic) day of month
TABLE = {"year": 2026, "sha256": "1820a17d2e4a4234898d944df193261675fbe07520d8b6257b6270c98b48ad62",
         "dates": {1: (26, 27), 2: (23, 24), 3: (30, 31), 4: (27, 28), 5: (25, 26), 6: (29, 30), 7: (27, 28),
                   8: (31, 25), 9: (28, 29), 10: (26, 27), 11: (30, 24), 12: (28, 29)}}
# notice image: mixed waste for the whole municipality every Wednesday from this date
NOTICE = {"file": "Komunalno-obavijest-.png", "sha256": "48788be9a6e4d6cb0b7a93162b7e6794ca071c696008bbe82dbe68cb7e99696f",
          "from": date(2026, 7, 1), "day": "sri"}
PROVIDER = {
    "davatelj": "Plaški KOM d.o.o.",
    "web": SITE,
    "zupanija": "Karlovačka",
    "jls": ["Plaški"],
    "napomene": [
        "Papir se odvozi svaki zadnji ponedjeljak u mjesecu, plastika svaki zadnji utorak u mjesecu.",
        "Miješani komunalni otpad: od 1. srpnja 2026. za cijelo područje Plaškog jednom tjedno, srijedom "
        "(spremnike iznijeti do 7:00). Do 30. lipnja odvozilo se srijedom ili četvrtkom ovisno o ulici, a podjela "
        "ulica nije objavljena, pa ti datumi nisu upisani.",
        "Pomaci zbog blagdana nisu objavljeni; datumi su izračunati bez pomaka (npr. srijeda 5.8. i 18.11. su "
        "blagdani). Provjerite kod davatelja: 047 573 074.",
        "Ostale vrste otpada: mobilno reciklažno dvorište uz prethodnu najavu na 047 573 074.",
    ],
}


def image(media_url, sha, problems):
    body = fetch(media_url)
    digest = hashlib.sha256(body).hexdigest()
    if digest != sha:
        problems.append(f"slika se promijenila, prepisati ponovno: {media_url} (sha256 {digest})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    if year != TABLE["year"]:
        sys.exit(f"Raspored za {year} nije prepisan. Ništa nije upisano.")
    problems = []
    posts = json.loads(fetch(POSTS))
    main_post = next((p for p in posts if f"za {year}" in p["title"]["rendered"]), None)
    notice = next((p for p in posts if "novom rasporedu" in p["title"]["rendered"]), None)
    if not main_post or not main_post["featured_media"] or not notice:
        sys.exit("Objava s rasporedom ili obavijest o novom rasporedu nije pronađena. Ništa nije upisano.")
    newer = [p["link"] for p in posts if p["date"] > notice["date"]]
    if newer:
        print(f"UPOZORENJE: novije objave o rasporedu, provjerite ih: {newer}")
    media = json.loads(fetch(MEDIA.format(id=main_post["featured_media"])))
    image(media["source_url"], TABLE["sha256"], problems)
    m = re.search(r'(https?://[^"\s,]+/' + re.escape(NOTICE["file"]) + ")", notice["content"]["rendered"])
    if m:
        image(m.group(1), NOTICE["sha256"], problems)
    else:
        problems.append(f"obavijest više ne sadrži sliku {NOTICE['file']}: {notice['link']}")

    rows = {}
    for month, (k, p) in TABLE["dates"].items():
        for code, day, dan in (("K", k, "pon"), ("P", p, "uto")):
            d = date(year, month, day)
            if d != pravila.mjesecno(year, dan, -1)[month - 1]:
                problems.append(f"{d:%d.%m.} {code}: nije zadnji {dan} u mjesecu")
            rows[d] = rows.get(d, "") + code
    for d in pravila.tjedno(year, NOTICE["day"]):
        if d >= NOTICE["from"]:
            rows[d] = rows.get(d, "") + "M"
    hol = set(pravila.blagdani(year))
    for d in sorted(rows):
        if d in hol:
            print(f"PRETPOSTAVKA: {d:%d.%m.} ({podaci.DAYS[d.weekday()]}) je blagdan; pomak nije objavljen, datum ostaje.")
    cnt = Counter((d.month, c) for d, codes in rows.items() for c in codes)
    for month in range(1, 13):
        if cnt[month, "K"] != 1 or cnt[month, "P"] != 1:
            problems.append(f"mjesec {month}: papir {cnt[month, 'K']}x, plastika {cnt[month, 'P']}x")
        if month >= NOTICE["from"].month and not 4 <= cnt[month, "M"] <= 5:
            problems.append(f"mjesec {month}: {cnt[month, 'M']}x miješani")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": main_post["link"], "zone": {"1": {
        "jls": "Plaški",
        "podrucje": "Cijelo područje Općine Plaški",
        "ulice": ["Plaški"],
        "napomena": f"Miješani otpad srijedom od {NOTICE['from']:%d.%m.%Y.} (obavijest: {notice['link']}).",
        "raw": {**old.get("1", {}).get("raw", {}),
                str(year): podaci.month_lines([(d, c, False) for d, c in rows.items()])},
    }}}
    total = Counter(c for codes in rows.values() for c in codes)
    print("Zona 1 (Plaški): " + ", ".join(f"{c} {total[c]}" for c in "MPK"))
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json (1 zona)")


if __name__ == "__main__":
    main()
