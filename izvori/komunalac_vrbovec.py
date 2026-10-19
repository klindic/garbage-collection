"""Vrbovec, Brckovljani, Dubrava, Farkaševac, Gradec, Preseka, Rakovec: Komunalac Vrbovec d.o.o., 15 routes.

    python3 -m izvori.komunalac_vrbovec [--year 2026]

The page "Raspored odvoza komunalnog otpada kroz tjedan" links one PDF per route ("Relacija-N-<Dan>-<JLS>.pdf",
Word, page 1). The heading names the municipality and lists the settlements or streets; below are twelve
month blocks with the route's weekday dates and, in the "Vrsta otpada" column, a bin picture or "/" (no
collection). A date word is paired with the picture on the same row (pdfplumber page.images); the picture
is identified by its pixel colours (page rendered at 72 dpi): green bin = mixed waste, blue + orange bins =
paper and plastic (PK), all three = mixed, paper and plastic on the same day.
Holidays: holiday dates are printed in red. The rule in the PDF is "odvoz sljedeći radni dan ili prema
obavijesti": a red date with a bin is moved to the date in the company's notice for that route (WordPress
posts "Obavijest o odvozu otpada", which sometimes move it a day earlier), otherwise to the next working
day; a red date with "/" has no collection.
"""
import argparse
import html
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalac-vrbovec"
SITE = "https://www.komunalac-vrbovec.hr"
PAGE = SITE + "/komunalni-otpad/raspored-odvoza-komunalnog-otpada-kroz-tjedan/"
POSTS = SITE + "/wp-json/wp/v2/posts?search=odvozu&per_page=100&after={after}&_fields=date,link,title,content"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
DAYS = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak", "Subota"]
DAY_NOTICE = {"PONEDJELJKOM": 0, "UTORKOM": 1, "SRIJEDOM": 2, "ČETVRTKOM": 3, "PETKOM": 4, "SUBOTOM": 5}
JLS = {"VRBOVEC": "Vrbovec", "VRBOVCA": "Vrbovec", "BRCKOVLJANI": "Brckovljani", "DUBRAVA": "Dubrava",
       "FARKAŠEVAC": "Farkaševac", "GRADEC": "Gradec", "PRESEKA": "Preseka", "RAKOVEC": "Rakovec"}
DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.")
PROVIDER = {
    "davatelj": "Komunalac Vrbovec d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zagrebačka",
    "jls": ["Vrbovec", "Brckovljani", "Dubrava", "Farkaševac", "Gradec", "Preseka", "Rakovec"],
}
NAPOMENE = [
    "Kante i/ili vreće s logotipom Komunalca Vrbovec iznijeti najkasnije do 7 sati ujutro na dan odvoza.",
    "Papir (plava kanta) i plastika (narančasta kanta) odvoze se zajedno jednom mjesečno; izvan Grada "
    "Vrbovca toga dana nema odvoza miješanog otpada.",
    "Blagdani i praznici: odvoz sljedeći radni dan ili prema obavijesti na komunalac-vrbovec.hr i Radiju "
    "Vrbovec. Upisani su datumi iz obavijesti, a gdje obavijest još nije objavljena sljedeći radni dan "
    "(vidi napomenu zone).",
    "Višak miješanog otpada u zelenim vrećama s logotipom (120 l), Komunalac Vrbovec, Kolodvorska 29.",
    "Reciklažno dvorište na odlagalištu Beljavina (za građane Grada Vrbovca): zimi pon–sub 7–18 h, ljeti "
    "pon–pet 7–21 h i sub 8–18 h; nedjeljom i praznikom zatvoreno. Telefon 01 2790 380.",
]


def naslov(name):
    """'ZAGREBAČKA ULICA' -> 'Zagrebačka ulica', 'P.BRANITELJA' -> 'P.Branitelja'."""
    out = re.sub(r"(?<![^\s.(+])(\w)(\w*)", lambda m: m.group(1).upper() + m.group(2).lower(), name.strip())
    return re.sub(r"(?<=\w) (Ulica|Cesta)\b", lambda m: " " + m.group(1).lower(), out)


def route_pdfs(page_html):
    """[(number, weekday name, url)] in route order."""
    out = {}
    for url in re.findall(r'href="([^"]+/Relacija-(\d+)-([A-Za-zČčĆćŠšŽž]+)-[^"/]+\.pdf)"', page_html):
        out.setdefault(int(url[1]), (int(url[1]), url[2], url[0]))
    return [out[k] for k in sorted(out)]


def bin_codes(im, box):
    """Codes of the bins in a picture from its pixels: green M, blue K, orange P."""
    n = Counter()
    for x in range(int(box[0]) + 1, int(box[2])):
        for y in range(int(box[1]) + 1, int(box[3])):
            r, g, b = im.getpixel((x, y))[:3]
            if g > r + 40 and g > b + 20:
                n["M"] += 1
            elif b > r + 60 and b > g + 40:
                n["K"] += 1
            elif r > 180 and 60 < g < 210 and b < 90:
                n["P"] += 1
    total = sum(n.values())
    return "".join(c for c in "MPK" if total and n[c] > 0.12 * total)


def read_route(path, year, problems, where):
    """{jls, places, day, rows: [(date, codes or '' for '/', red)]}."""
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(extra_attrs=["non_stroking_color"])
        text = page.extract_text() or ""
        im = page.to_image(resolution=72).original.convert("RGB")
        lines = text.splitlines()
        m = next((re.search(r"U (\d{4})\. GODINI\s*[-–]\s*(?:OPĆINA\s+)?(.+)$", l) for l in lines
                  if "GODINI" in l), None)
        if not m or int(m.group(1)) != year or m.group(2).strip() not in JLS:
            problems.append(f"{where}: naslov nije raspored za {year}: {lines[:2]}")
            return None
        start = next(i for i, l in enumerate(lines) if "GODINI" in l) + 1
        end = next((i for i, l in enumerate(lines) if l.split() and l.split()[0] == "SIJEČANJ"), None)
        places = [naslov(p) for p in ", ".join(lines[start:end]).split(",") if p.strip()]
        heads = {MONTHS.index(w["text"]) + 1: w for w in words if w["text"] in MONTHS}
        days = Counter(w["text"] for w in words if w["text"] in DAYS)
        if len(heads) != 12 or len(days) != 1:
            problems.append(f"{where}: mjeseci {sorted(heads)}, dani {dict(days)}")
            return None
        day = DAYS.index(next(iter(days)))
        bins = [i for i in page.images if i["bottom"] - i["top"] < 30 and i["top"] > heads[1]["top"]]
        kinds, used, rows = {}, set(), []
        for w in words:
            dm = DATE.fullmatch(w["text"])
            if not dm or w["top"] < heads[1]["top"]:
                continue
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            above = [k for k, h in heads.items() if h["top"] < w["top"]]
            month = min(above, key=lambda k: abs((heads[k]["x0"] + heads[k]["x1"]) / 2 + 30 - cx)
                        + (w["top"] - heads[k]["top"]) / 10)
            d = date(year, int(dm.group(2)), int(dm.group(1)))
            if d.month != month:
                problems.append(f"{where}: {w['text']} u bloku {MONTHS[month - 1]}")
            red = tuple(w["non_stroking_color"] or (0,)) not in ((0,), (0.0, 0.0, 0.0))
            row = [i for i in bins if abs((i["top"] + i["bottom"]) / 2 - cy) < 10 and w["x1"] < i["x0"] < w["x1"] + 120]
            slash = [x for x in words if x["text"] == "/" and abs((x["top"] + x["bottom"]) / 2 - cy) < 8
                     and w["x1"] < x["x0"] < w["x1"] + 120]
            if len(row) + len(slash) > 1:
                problems.append(f"{where} {w['text']}: {len(row)} slika i {len(slash)} '/' u retku")
                continue
            if not row and not slash:
                print(f"   UPOZORENJE {where} {w['text']}: prazna ćelija (ni kanta ni '/'), nema odvoza")
                continue
            codes = ""
            if row:
                img = row[0]
                key = (img.get("name"), tuple(img["srcsize"]))
                if key not in kinds:
                    kinds[key] = bin_codes(im, (img["x0"], img["top"], img["x1"], img["bottom"]))
                codes = kinds[key]
                used.add(id(img))
                if codes not in ("M", "PK", "MPK"):
                    problems.append(f"{where} {w['text']}: nepoznata slika {key} ({codes!r})")
                    continue
            rows.append((d, codes, red))
        if len(used) != len(bins):
            problems.append(f"{where}: {len(bins) - len(used)} slika kanti bez datuma")
    print(f"   {where}: slike " + ", ".join(f"{k[0]} {k[1][0]}x{k[1][1]} = {v}" for k, v in sorted(kinds.items())))
    return {"jls": JLS[m.group(2).strip()], "places": places, "day": day, "rows": rows,
            "opis": ", ".join(" ".join(l.split()).strip(" ,") for l in lines[start:end])}


def notices(year, problems):
    """Holiday notices: [{jls, day, places, new, link}] from the WordPress posts."""
    body = fetch(POSTS.format(after=f"{year - 1}-11-01T00:00:00"))
    out = []
    for post in json.loads(body):
        t = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", post["content"]["rendered"])).split())
        if "zbog" not in t or "odnosno" not in t:
            continue
        m_day = re.search(r"vrši\s+(\w+)", t)
        m_new = re.search(r"odnosno\s+u\s+\w+[\s,\-–]*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", t)
        m_area = re.search(r"područj\w*\s+(?:grada|općine|općina)\s+([A-Za-zČĆŽŠĐčćžšđ]+)", t, re.I)
        if not (m_day and m_new and m_area) or m_day.group(1).upper() not in DAY_NOTICE \
                or m_area.group(1).upper() not in JLS:
            problems.append(f"obavijest nije razumljiva: {post['link']}")
            continue
        new = date(int(m_new.group(3)), int(m_new.group(2)), int(m_new.group(1)))
        if new.year != year:
            continue
        m_places = re.search(rf"{m_area.group(1)}\s*[-–]\s*(.+?)\s+da ćemo", t)
        places = {naslov(p) for p in re.split(r",\s*|\s+[iI]\s+", m_places.group(1))} if m_places else set()
        out.append({"jls": JLS[m_area.group(1).upper()], "day": DAY_NOTICE[m_day.group(1).upper()],
                    "places": places, "new": new, "link": post["link"]})
    return out


def holiday_move(d, route, found, problems, where):
    """(new date, note) for a red date with a collection."""
    match = {n["new"] for n in found if n["jls"] == route["jls"] and n["day"] == route["day"]
             and abs((n["new"] - d).days) <= 6 and (not n["places"] or n["places"] & set(route["places"]))}
    if len(match) > 1:
        problems.append(f"{where} {d}: obavijesti se ne slažu: {sorted(match)}")
    if match:
        return match.pop(), "obavijest"
    new = pravila.primijeni_blagdane([d], "sljedeci")[0][0]
    why = "obavijest još nije objavljena" if d >= date.today() else "obavijest nije pronađena"
    print(f"   PRETPOSTAVKA {where}: {d:%d.%m.} → {new:%d.%m.} (sljedeći radni dan, {why})")
    return new, f"sljedeći radni dan, {why}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    routes = route_pdfs(fetch(PAGE).decode("utf-8", "replace"))
    if len(routes) != 15 or [r[0] for r in routes] != list(range(1, 16)):
        sys.exit(f"Na {PAGE} {len(routes)} relacija umjesto 15: {[r[0] for r in routes]}. Ništa nije upisano.")
    problems = []
    found = notices(year, problems)
    print(f"Obavijesti o pomaku odvoza za {year}: {len(found)}")
    hol = set(pravila.blagdani(year))
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    zones = {}
    with tempfile.TemporaryDirectory() as tmp:
        for num, dayname, url in routes:
            where = f"Relacija {num}"
            pdf = Path(tmp) / url.rsplit("/", 1)[1]
            fetch(url, pdf)
            route = read_route(pdf, year, problems, where)
            if not route:
                continue
            if podaci.DAYS[route["day"]] != dayname.lower().replace("cetvrtak", "četvrtak"):
                problems.append(f"{where}: dan u PDF-u {DAYS[route['day']]}, u nazivu datoteke {dayname}")
            merged, moves = {}, []
            for d, codes, red in route["rows"]:
                if d.weekday() != route["day"]:
                    problems.append(f"{where} {d:%d.%m.}: {podaci.DAYS[d.weekday()]}")
                if red != (d in hol):
                    why = "crveno, a nije blagdan" if red else "blagdan, a nije crveno"
                    problems.append(f"{where} {d:%d.%m.}: {why}")
                if not codes:
                    continue
                new, how = (d, None)
                if red:
                    new, how = holiday_move(d, route, found, problems, where)
                    moves.append(f"{d:%d.%m.} → {new:%d.%m.} ({how})")
                if new in merged:
                    problems.append(f"{where}: {new} dvaput")
                merged[new] = (codes, bool(how))
            months = Counter(d.month for d in merged)
            pk = Counter(d.month for d, (c, _) in merged.items() if "K" in c)
            if any(not 1 <= months[m] <= 5 or pk[m] != 1 for m in range(1, 13)):
                problems.append(f"{where}: odvoza po mjesecima {dict(sorted(months.items()))}, papira i plastike "
                                f"{dict(sorted(pk.items()))}")
            n_m = sum(1 for c, _ in merged.values() if "M" in c)
            if not 20 <= n_m <= 53:
                problems.append(f"{where}: {n_m}x miješani")
            if not route["places"]:
                problems.append(f"{where}: nema naselja ni ulica")
            ulice = route["places"]
            zone = {"jls": route["jls"],
                    "podrucje": f"Relacija {num}, {podaci.DAYS[route['day']]} – " + ", ".join(ulice[:3])
                                + (" …" if len(ulice) > 3 else ""),
                    "opis": route["opis"], "ulice": ulice}
            if moves:
                zone["napomena"] = "Pomaci zbog blagdana: " + "; ".join(moves) + "."
            key = str(num)
            prev = old.get(key, {})
            zone["raw"] = {**(prev.get("raw", {}) if prev.get("podrucje") == zone["podrucje"] else {}),
                           str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in merged.items()])}
            zones[key] = zone
            cnt = Counter(ch for c, _ in merged.values() for ch in c)
            print(f"Zona {key}: {zone['jls']}: {zone['podrucje']}: M {cnt['M']}, PK {cnt['K']}, "
                  f"bez odvoza {sum(1 for _, c, _ in route['rows'] if not c)}, pomaknuto {len(moves)}, "
                  f"naselja/ulica {len(ulice)}")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "napomene": NAPOMENE, "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
