"""Našice and Donja Motičina: Našički park d.o.o. (nasicki-park.hr), one zone per weekday.

    python3 -m izvori.nasicki_park [--year 2026]

Mixed waste and biowaste (brown bin) are collected every week on the same weekday ("tjedni odvoz
biootpada ... jednak rasporedu odvoza miješanog komunalnog otpada", notice of 24.6.2022). The weekday of
every street and settlement is in the PDF table "Raspored odvoza miješanog komunalnog otpada, biootpada"
(Word table, truck 1 = the town, truck 2 = suburbs, Podgorač and Donja Motičina; dated January 2024 and
still linked as current), read with pdfplumber. Plastic (yellow) and paper (blue) are collected once a
month on the same weekday, in the week coloured in the year calendar PNG on the page "Sakupljanje i
odvoz papira i plastike"; those weeks are kept in this script (PLASTIC, PAPER) with the image's sha256
and every day cell of the image is checked against them by pixel colour on its fixed grid. Holiday
shifts come from the company's notices (WordPress posts, a few only as a PDF): a moved collection is
marked as moved, "prema redovitom rasporedu" keeps the date; holidays without a notice keep the date
and are listed in a note. Općina Podgorač has had another provider since 2023, so its settlements are
left out.
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

import numpy as np
import pdfplumber
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "nasicki-park"
SITE = "https://nasicki-park.hr"
REST = SITE + "/wp-json/wp/v2/pages?slug={slug}&_fields=id,link,modified,content"
SCHEDULE_PAGE = "cjenik-i-raspored-odvoza-komunalnog-otpada"
CALENDAR_PAGE = "sakupljanje-i-odvoz-papira-i-plastike-u-2023-godini"
POSTS = SITE + "/wp-json/wp/v2/posts?search=odvoz&after={a}T00:00:00&before={b}T00:00:00&per_page=100" \
               "&_fields=date,link,content"
# year calendar: file name -> (sha256, Mondays of the plastic weeks, Mondays of the paper weeks), read by hand
CALENDARS = {
    "kalendar2026_Veliko2.png": (
        "dcdbdffbd12405368cc378b8d30ed1a8368fe2d437b20a4903ea59b5dca3f8d9",
        "01-19 02-16 03-16 04-13 05-18 06-15 07-20 08-17 09-14 10-19 11-16 12-14",
        "01-26 02-23 03-23 04-20 05-25 06-22 07-27 08-24 09-21 10-26 11-23 12-21"),
}
# fixed grid of the 1000x1238 calendar: centre of the Monday cell in the first week row of each month column
# and row, column and row pitch (px)
GRID_X, GRID_Y, PX, PY = (29, 363, 699), (83.8, 403.8, 682.8, 1002.8), 45.25, 41.4
FILLS = {(255, 255, 0): "P", (42, 96, 153): "K", (255, 0, 0): "blagdan", (255, 255, 255): None,
         (242, 242, 242): None}
DAYS = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "ČETVRTAK": 3, "PETAK": 4}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
# settlements of the suburban truck by JLS (Državni zavod za statistiku, popis 2011.)
PODGORAC = {"Bijela Loza", "Budimci", "Kelešinka", "Kršinci", "Ostrošinci", "Podgorač", "Poganovci",
            "Razbojište", "Stipanovci"}
MOTICINA = {"Donja Motičina", "Gornja Motičina", "Seona"}
NASICE = {"Mala Londžica", "Velika Londžica", "Vukojevci", "Velimirovac", "Markovac Našički", "Lila", "Ribnjak",
          "Lađanska", "Jelisavac", "Ceremošnjak", "Makloševac", "Martin", "Brezik",
          "Makloševac - Dom za starije i nemoćne", "V. Lisinskog od zaobilaznice"}
UNKNOWN = {"Marinović Brdo"}  # hamlet whose JLS could not be confirmed: left out
ACRONYMS = {"OŠ", "SŠ", "OBŽ", "HEP", "FINA", "LIDL", "JYSK", "NAC", "HV", "SPAR", "BOSO", "II", "III"}
COMMON = {"ULICA", "CESTA", "RUJNA", "BRIGADE", "ZGRADE", "PRODUŽETAK", "SKLADIŠTE", "KOLODVOR", "ŠUMA",
          "BRANITELJA", "PIVOVARA"}  # lower case unless first: 'Sokolska ulica', '21. rujna'

PROVIDER = {
    "davatelj": "Našički park d.o.o.",
    "web": SITE,
    "izvor": SITE + "/djelatnosti/gospodarenje-otpadom-i-cistoca/" + SCHEDULE_PAGE + "/",
    "zupanija": "Osječko-baranjska",
    "jls": ["Našice", "Donja Motičina"],
    "nazivi": {"P": "Plastična ambalaža"},
    "bioNapomena": "Biootpad (smeđa posuda ili vrećica) odvozi se svaki tjedan na isti dan kad i miješani otpad.",
}
NAPOMENE = [
    "Miješani komunalni otpad i biootpad odvoze se svaki tjedan na dan iz rasporeda; plastika (žuti tjedan) i "
    "papir (plavi tjedan) jednom mjesečno, na isti dan u tjednu označenom na kalendaru.",
    "Raspored po ulicama i naseljima je iz siječnja 2024. (i dalje objavljen kao važeći).",
    "Glomazni otpad: jedan odvoz godišnje (do 3 m³) na prijavu, ili bez naknade u reciklažno dvorište.",
    "Reciklažno dvorište Našice, Krndijska ulica bb: ponedjeljak–petak 7–19, subota 8–13 sati; "
    "tel. 091 261 3291.",
    "Općina Podgorač od 2023. ima drugog davatelja usluge, pa njezina naselja nisu uključena.",
]


def nice(text):
    """'MAKLOŠEVAC - DOM ZA STARIJE I NEMOĆNE' -> 'Makloševac - Dom za starije i nemoćne'."""
    text = " ".join(text.split())
    text = re.sub(r"\bI(?= [^\W\d_]{2})(?! II)", "i", text)
    lower = [False]  # after 'za', 'do', 'od' the rest is ordinary words: 'Dom za starije i nemoćne'

    def word(m):
        w = m.group(0)
        if w in ACRONYMS or len(w) == 1:
            return w
        if w in ("ZA", "DO", "OD"):
            lower[0] = True
        return w.lower() if lower[0] or (w in COMMON and m.start() > 0) else w.capitalize()
    return re.sub(r"[^\W\d_]+", word, text)


def schedule(pdf_path, problems):
    """[(truck, weekday, [names])] from the two tables of the PDF."""
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        tables = pdf.pages[0].extract_tables()
    for table in tables:
        i = next((i for i, r in enumerate(table[:3]) if re.search(r"KAMION SMEĆAR \d", r[0] or "")), None)
        if i is None:
            continue
        truck = int(re.search(r"KAMION SMEĆAR (\d)", table[i][0]).group(1))
        if table[i + 1] != list(DAYS):
            problems.append(f"kamion {truck}: neočekivano zaglavlje {table[i + 1]}")
            continue
        for col, day in enumerate(DAYS.values()):
            names = [nice(r[col]) for r in table[i + 2:] if r[col] and r[col].strip()]
            out.append((truck, day, names))
    if sorted({t for t, _, _ in out}) != [1, 2]:
        problems.append(f"u PDF-u nisu nađena oba kamiona: {sorted({t for t, _, _ in out})}")
    return out


def check_calendar(path, year, plastic, paper):
    """Problems where a day cell's colour differs from the plastic/paper weeks; red (holiday) cells."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    problems, red = [], set()
    for m in range(1, 13):
        monday = date(year, m, 1) - timedelta(days=date(year, m, 1).weekday())
        d = date(year, m, 1)
        while d.month == m:
            k, col = (d - monday).days // 7, d.weekday()
            cx, cy = GRID_X[(m - 1) % 3] + col * PX, GRID_Y[(m - 1) // 3] + k * PY
            votes = Counter(FILLS.get(tuple(int(v) for v in np.median(
                a[int(cy + dy) - 2:int(cy + dy) + 3, int(cx + dx) - 2:int(cx + dx) + 3].reshape(-1, 3), axis=0)), "?")
                for dx in (-15, 15) for dy in (-12, 12))
            got = votes.most_common(1)[0][0]
            week = d - timedelta(days=col)
            want = "P" if week in plastic and col < 5 else "K" if week in paper and col < 5 else None
            if got == "blagdan" and want is None:
                red.add(d)
            elif got != want or len(votes) > 1:
                problems.append(f"slika: {d:%d.%m.} je {got}, očekivano {want or 'bez boje'}")
            d += timedelta(days=1)
    return problems, red


def notices(year, problems):
    """{holiday: new date, or None for 'prema redovitom rasporedu'} from the company's posts."""
    posts = json.loads(fetch(POSTS.format(a=f"{year - 1}-11-01", b=f"{year + 1}-01-31")))
    hol = set(pravila.blagdani(year))
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for post in posts:
            raw = post["content"]["rendered"]
            text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
            pdfs = re.findall(r'data-downloadurl="([^"]+?wpdmdl=\d+)', raw)
            if "otpad" in text and len(text) < 200 and pdfs:  # notice only as a PDF
                pdf = Path(tmp) / "obavijest.pdf"
                fetch(html.unescape(pdfs[0]), pdf)
                text = " ".join(subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True,
                                               text=True).stdout.split())
            pat = r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\.?([^.]{0,160}?)(prema redovitom rasporedu|" \
                  r"odvoziti u \w+ (\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}))"
            for m in re.finditer(pat, text):
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                if d not in hol:
                    continue
                new = None if m.group(5).startswith("prema") else \
                    date(int(m.group(8)), int(m.group(7)), int(m.group(6)))
                if new and not 0 < abs((new - d).days) <= 6:
                    problems.append(f"obavijest {post['link']}: pomak {d} -> {new} nije vjerojatan")
                out[d] = new
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    sched_html = json.loads(fetch(REST.format(slug=SCHEDULE_PAGE)))[0]["content"]["rendered"]
    cal_html = json.loads(fetch(REST.format(slug=CALENDAR_PAGE)))[0]["content"]["rendered"]
    pdf_url = re.search(r'data-downloadurl="([^"]+?wpdmdl=\d+)', sched_html)
    png_url = re.search(rf'src="([^"]+/kalendar{year}[^"/]*\.png)"', cal_html)
    if not pdf_url or not png_url:
        sys.exit(f"Nema rasporeda (PDF) ili kalendara za {year} (PNG) na stranicama Našičkog parka.")
    name = png_url.group(1).rsplit("/", 1)[1]
    with tempfile.TemporaryDirectory() as tmp:
        pdf, png = Path(tmp) / "raspored.pdf", Path(tmp) / name
        fetch(html.unescape(pdf_url.group(1)), pdf)
        fetch(png_url.group(1), png)
        sha = hashlib.sha256(png.read_bytes()).hexdigest()
        if name not in CALENDARS or CALENDARS[name][0] != sha:
            sys.exit(f"slika se promijenila ili nije provjerena: {name} (sha256 {sha}); "
                     "prepišite žute i plave tjedne u CALENDARS.")
        plastic, paper = ({date(year, *map(int, x.split("-"))) for x in weeks.split()}
                          for weeks in CALENDARS[name][1:])
        img_problems, red = check_calendar(png, year, plastic, paper)
        problems += img_problems
        routes = schedule(pdf, problems)
    hol = set(pravila.blagdani(year))
    if red - hol:
        problems.append(f"crveno na kalendaru, a nije blagdan: {sorted(red - hol)}")
    for weeks, code in ((plastic, "P"), (paper, "K")):
        if sorted(d.month for d in weeks) != list(range(1, 13)) or any(d.weekday() for d in weeks):
            problems.append(f"{code}: tjedni nisu ponedjeljci, jedan u svakom mjesecu")
    moves = notices(year, problems)
    # one zone per JLS and weekday: the town truck and the suburban truck on the same day share the dates
    zones, left_out = {}, []
    for truck, wd, names in routes:
        for n in names:
            if truck == 1 or n in NASICE:
                jls = "Našice"
            elif n in MOTICINA:
                jls = "Donja Motičina"
            elif n in PODGORAC or n in UNKNOWN:
                left_out.append(n)
                continue
            else:
                problems.append(f"kamion {truck}, {DAN[wd]}: naselje {n!r} nije svrstano ni u jednu JLS")
                continue
            z = zones.setdefault((jls, wd), {1: [], 2: []})
            z[truck].append(n)
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": list(NAPOMENE), "zone": {}}
    unannounced = set()
    for (jls, wd), trucks in sorted(zones.items(), key=lambda kv: (kv[0][0] != "Našice", kv[0][1])):
        rows = {}
        for d in pravila.tjedno(year, list(pravila.DANI)[wd]):
            week = d - timedelta(days=wd)
            rows[d] = "MB" + ("P" if week in plastic else "") + ("K" if week in paper else "")
        out = []
        for d, codes in rows.items():
            if d in moves and moves[d]:
                out.append((moves[d], codes, True))
            else:
                out.append((d, codes, False))
                if d in hol and d not in moves:
                    unannounced.add(d)
        n = Counter(c for _, codes, _ in out for c in codes)
        if not (52 <= n["M"] == n["B"] <= 53 and n["P"] == n["K"] == 12):
            problems.append(f"{jls} {DAN[wd]}: {dict(n)}")
        if len({d for d, _, _ in out}) != len(out):
            problems.append(f"{jls} {DAN[wd]}: isti datum dvaput")
        for d, codes, moved in out:
            if not moved and d.weekday() != wd:
                problems.append(f"{jls} {DAN[wd]}: {d} nije {DAN[wd]}")
        names = trucks[1] + trucks[2]
        label = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
        parts = [f"kamion {t} ({'grad' if t == 1 else 'prigradska naselja'}): {', '.join(v)}"
                 for t, v in trucks.items() if v]
        key = str(len(data["zone"]) + 1)
        prev = old["zone"].get(key, {})
        data["zone"][key] = {
            "jls": jls,
            "podrucje": f"{DAN[wd].capitalize()} – {label}",
            "opis": "; ".join(parts),
            "ulice": list(dict.fromkeys(names)),
            "raw": {**({k: v for k, v in prev.get("raw", {}).items() if k != str(year)}
                       if prev.get("jls") == jls else {}),
                    str(year): podaci.month_lines(out)},
        }
        print(f"{jls}, {DAN[wd]}: {len(names)} ulica/naselja, {dict(n)}")
    shown = [f"{d:%d.%m.} → {new:%d.%m.}" if new else f"{d:%d.%m.} redovito" for d, new in sorted(moves.items())]
    print("Obavijesti za blagdane:", ", ".join(shown) or "-")
    if moves:
        data["napomene"].append("Odvoz na blagdane prema obavijestima Našičkog parka: " + ", ".join(
            f"{d.day}.{d.month}. premješten na {new.day}.{new.month}." if new else f"{d.day}.{d.month}. redovito"
            for d, new in sorted(moves.items())) + ".")
    if unannounced:
        data["napomene"].append("Za blagdane " + ", ".join(f"{d.day}.{d.month}." for d in sorted(unannounced)) +
                                " obavijest o odvozu nije objavljena; datumi su ostavljeni prema rasporedu "
                                "(obavijesti se objavljuju na nasicki-park.hr).")
    if left_out:
        print("Izostavljeno (Općina Podgorač ili nepoznata JLS):", ", ".join(sorted(set(left_out))))
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
