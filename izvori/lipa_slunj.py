"""Slunj: Komunalno društvo Lipa d.o.o. (komunalnodrustvolipa.hr), one zone per weekday route (Mon-Thu).

    python3 -m izvori.lipa_slunj [--year 2026]

Mixed waste: the post "Gospodarenje otpadom" (WordPress REST) has the permanent plan "UTVRĐENI PLAN ODVOZA
OTPADA", a table of weekday -> streets and settlements; every route is collected each week on its weekday.
Paper and plastic/metal (same day, for the whole town): the image "Raspored_primopredaje_<year>..." from the
media library lists the dates for July-December only; they are transcribed below, checked (Fridays, 1-2 a
month) and the image's sha256 is pinned, so a new image stops the script until it is read again. Months
outside the image are kept from the previous run of podaci/lipa-slunj.json.
Holidays: the company posts a PDF "Promjena rasporeda odvoza ..." for a holiday that moves collection (e.g.
5.8.2026: Wednesday -> Thursday, Thursday -> Friday); the script reads every such PDF of the year and
applies the moves (marked as moved). Other holidays have no notice and the dates stay as they are.
"""
import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "lipa-slunj"
SITE = "https://komunalnodrustvolipa.hr"
POST = SITE + "/wp-json/wp/v2/posts?slug=gospodarenje-otpadom&_fields=id,link,content"
MEDIA = SITE + "/wp-json/wp/v2/media?search={q}&per_page=50&_fields=id,date,source_url"
# paper and plastic/metal image, transcribed (both on the same days)
IMAGE = {"year": 2026, "file": "Raspored_primopredaje_2026_edited.jpg",
         "sha256": "1a65f5f7f1344686e76f65a99504b34e80d53e7565dc2d389cab3698b75c1881", "months": range(7, 13),
         "dates": "10.07. 31.07. 14.08. 28.08. 11.09. 25.09. 30.10. 27.11. 18.12."}
DAYS = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "ČETVRTAK": 3}
DAN = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak"]
KEY = ["pon", "uto", "sri", "čet"]
MONTHS = ["siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja", "kolovoza", "rujna",
          "listopada", "studenoga", "prosinca"]
NOT_PLACES = {"kontejneri zgrade i gospodarski subjekti"}
PROVIDER = {
    "davatelj": "Komunalno društvo Lipa d.o.o.",
    "web": SITE,
    "zupanija": "Karlovačka",
    "jls": ["Slunj"],
    "nazivi": {"P": "Plastika i metal (žuta)", "K": "Papir (plava)"},
}
NAPOMENE = [
    "Miješani komunalni otpad odvozi se jednom tjedno prema utvrđenom planu odvoza (dan u tjednu po ulicama i "
    "naseljima); posude treba iznijeti ispred ulaza objekta na dan odvoza.",
    "Papir i plastika/metal odvoze se isti dan za cijeli grad. Objavljen je raspored samo za srpanj–prosinac "
    "2026.; za siječanj–lipanj 2026. raspored nije pronađen.",
    "Blagdani: kad se odvoz pomiče, Lipa objavljuje obavijest (npr. 5.8.2026.: srijeda → četvrtak, četvrtak → "
    "petak); takvi pomaci su upisani i označeni. Za ostale blagdane obavijest nije objavljena i datumi nisu "
    "pomaknuti.",
    "Glomazni otpad: kontejner (5 m³) na zahtjev, jednom godišnje besplatno na adresi korisnika, tel. 047 801 815; "
    "glomazni otpad može se odložiti i u reciklažnom dvorištu Grada Slunja (pon–sub).",
    "Komunalno društvo Lipa d.o.o., Petra Svačića 5, Slunj, tel. 047 777 790.",
]


def plain(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def routes(content, problems):
    """[(weekday, area text)] from the table of the plan."""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.S):
        cells = [plain(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) == 2 and cells[0].upper() in DAYS:
            out.append((DAYS[cells[0].upper()], cells[1]))
    if [d for d, _ in out] != [0, 1, 2, 3]:
        problems.append(f"tablica plana odvoza: našao dane {[d for d, _ in out]}, očekivano pon-čet")
    return out


def moves_from_notices(year, tmp, problems):
    """{old date: new date} from the "Promjena rasporeda" PDFs of the year in the media library."""
    moves = {}
    for item in json.loads(fetch(MEDIA.format(q="promjena"))):
        url = item["source_url"]
        if not url.lower().endswith(".pdf") or not item["date"].startswith(str(year)):
            continue
        pdf = Path(tmp) / url.rsplit("/", 1)[1]
        fetch(url, pdf)
        text = " ".join(subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout.split())
        body = text.split("Obavijest", 1)[-1].split("Hvala")[0]  # without the letter date above the title
        found = []
        for m in re.finditer(r"(\d{1,2})\.\s?(\d{2})\.\s?(\d{4})|(\d{1,2})\.\s(" + "|".join(MONTHS) + r"|studenog)",
                             body):
            if m.group(1):
                found.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
            else:
                month = MONTHS.index(m.group(5)) + 1 if m.group(5) in MONTHS else 11
                found.append(date(year, month, int(m.group(4))))
        found = [d for d in found if d.year == year]
        pairs = list(zip(found[0::2], found[1::2]))
        if len(found) % 2 or not pairs:
            problems.append(f"obavijest {url}: ne mogu pročitati parove datuma {found}")
            continue
        for old, new in pairs:
            if new - old != timedelta(days=1) or old.weekday() > 3:
                problems.append(f"obavijest {url}: neočekivan pomak {old} -> {new}")
            moves[old] = new
        print(f"Obavijest {url.rsplit('/', 1)[1]}: " + ", ".join(f"{o:%d.%m.} → {n:%d.%m.}" for o, n in pairs))
    return moves


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    post = json.loads(fetch(POST))
    if not post:
        sys.exit("Objava 'Gospodarenje otpadom' nije pronađena. Ništa nije upisano.")
    link, plan = post[0]["link"], routes(post[0]["content"]["rendered"], problems)

    recyc = []
    media = json.loads(fetch(MEDIA.format(q="primopredaje")))
    current = [m for m in media if f"_{year}" in m["source_url"]]
    if year == IMAGE["year"]:
        pinned = [m for m in current if m["source_url"].endswith("/" + IMAGE["file"])]
        newer = [m["source_url"] for m in media if pinned and m["date"] > pinned[0]["date"]]
        if not pinned or newer:
            problems.append(f"slika se promijenila, prepisati ponovno: nova slika {newer or 'nije pronađena'}")
        else:
            body = fetch(pinned[0]["source_url"])
            sha = hashlib.sha256(body).hexdigest()
            if sha != IMAGE["sha256"]:
                problems.append(f"slika se promijenila, prepisati ponovno: {pinned[0]['source_url']} (sha256 {sha})")
        for d, m in re.findall(r"(\d\d)\.(\d\d)\.", IMAGE["dates"]):
            x = date(year, int(m), int(d))
            if x.weekday() != 4 or x.month not in IMAGE["months"] or x in pravila.blagdani(year):
                problems.append(f"papir/plastika {x:%d.%m.}: nije radni petak u razdoblju slike")
            recyc.append(x)
        per_month = Counter(x.month for x in recyc)
        if any(not 1 <= per_month[m] <= 2 for m in IMAGE["months"]):
            problems.append(f"papir/plastika: neobičan broj odvoza po mjesecu {dict(per_month)}")
    elif current:
        problems.append(f"raspored papira i plastike za {year} nije prepisan: {[m['source_url'] for m in current]}")

    with tempfile.TemporaryDirectory() as tmp:
        moves = moves_from_notices(year, tmp, problems)
    hol = set(pravila.blagdani(year))
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": link, "napomene": NAPOMENE, "zone": {}}
    for z, (wd, text) in enumerate(plan, 1):
        rows = {}
        for d in pravila.tjedno(year, KEY[wd]):
            new = moves.get(d, d)
            if new in rows:
                problems.append(f"zona {z}: {new} dvaput")
            rows[new] = ["M", new != d]
            if d in hol and d not in moves:
                print(f"PRETPOSTAVKA: {d:%d.%m.} ({DAN[wd].lower()}) je blagdan bez obavijesti; odvoz ostaje.")
        for d in recyc:
            rows.setdefault(d, ["", False])[0] += "PK"
        months = set(IMAGE["months"]) if year == IMAGE["year"] else set()
        for d, codes, mv in podaci.iter_dates(old.get(str(z), {"raw": {}}), year):  # keep earlier P/K months
            kp = "".join(c for c in codes if c in "PK")
            if kp and d.month not in months and d.weekday() == 4:
                rows.setdefault(d, ["", False])[0] += kp
        for d, (codes, mv) in rows.items():
            if "M" in codes and not mv and d.weekday() != wd:
                problems.append(f"zona {z}: {d} nije {DAN[wd].lower()}")
        cnt = Counter(d.month for d, (codes, _) in rows.items() if "M" in codes)
        if any(not 4 <= cnt[m] <= 5 for m in range(1, 13)):
            problems.append(f"zona {z}: broj odvoza miješanog po mjesecu {dict(cnt)}")
        names = [n.strip() for n in text.split(",") if n.strip() and n.strip() not in NOT_PLACES]
        data["zone"][str(z)] = {
            "jls": "Slunj",
            "podrucje": f"{DAN[wd]} – " + ", ".join(names[:5]) + (", …" if len(names) > 5 else ""),
            "opis": text, "ulice": names,
            "raw": {**old.get(str(z), {}).get("raw", {}),
                    str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])},
        }
        total = Counter(c for codes, _ in rows.values() for c in codes)
        print(f"Zona {z} ({DAN[wd]}): M {total['M']}, P {total['P']}, K {total['K']}, "
              f"pomaknuto {sum(1 for _, mv in rows.values() if mv)}, ulica/naselja {len(names)}")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(data['zone'])} zone)")


if __name__ == "__main__":
    main()
