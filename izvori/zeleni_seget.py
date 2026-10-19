"""Zeleni Seget d.o.o.: Općina Seget (Seget Vranjica, Seget Donji, Zagora), year calendar 2026.

    python3 -m izvori.zeleni_seget [--year 2026]

The page "Raspored odvoza otpada" links the year calendar (ZS_odvoz_kalendar_YYYY.pdf, one picture: 12 month
columns x 31 day rows) and lists the current weekly rules in HTML ("Aktualni raspored odvoza": Vranjica mixed
waste Monday and Thursday, paper Tuesday, plastic Friday; Donji mixed Tuesday and Friday, paper Monday,
plastic Thursday; Zagora mixed Wednesday). The HTML rules give only weekdays, while the calendar shows that
paper and plastic go weekly from May to October and every other week otherwise (alternating with Zagora,
whose paper/plastic is not on the page), so the dates come from the calendar. It is read by pixel sampling:
grid lines give the cells, the cell colour must match the weekday (Saturday yellow, Sunday orange, missing
days grey), and in each cell every line of coloured text is one area name, its colour the waste type (green
mixed, blue paper, yellow plastic, purple seasonal mixed waste for hotels and restaurants, left out) and its
width the name (DONJI < ZAGORA < VRANJICA). Every cell is checked against the HTML weekday rules. The PDF's
sha256 is stored and the script stops ("slika se promijenila") when the file changes, so the grid reading is
re-checked by hand. Holidays: the calendar has collections on public holidays (e.g. 1.1., 6.1., 6.4.) and no
moved dates; the dates are used as they are.
"""
import argparse
import colorsys
import hashlib
import html
import re
import subprocess
import sys
import tempfile
from calendar import monthrange
from collections import Counter
from datetime import date
from pathlib import Path

from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "zeleni-seget"
SITE = "https://zeleniseget.hr"
PAGE = SITE + "/wp/?page_id=8952"
PDF_SHA256 = {2026: "c6bdaa5417815be75a976477562a4cff28b9fca4a6413d3c3c55120c9edbcce5"}  # ZS_odvoz_kalendar_2026.pdf
AREAS = {"VRANJICA": (31, 37), "ZAGORA": (26, 30), "DONJI": (15, 25)}  # label width in pixels
COLOURS = {"G": "M", "B": "K", "Y": "P", "V": None}  # V = seasonal mixed waste for hotels and restaurants
BG = {"week": (255, 255, 255), "sat": (255, 255, 204), "sun": (255, 204, 153), "none": (230, 230, 230)}
ZAGORA_RECYCLING = {"K": "pon", "P": "čet"}  # not on the page; as in the calendar (e.g. 9.2., 12.2.)
DAYS = {"ponedjeljak": "pon", "utorak": "uto", "srijeda": "sri", "četvrtak": "čet", "petak": "pet", "subota": "sub",
        "ponedjeljkom": "pon", "utorkom": "uto", "srijedom": "sri", "četvrtkom": "čet", "petkom": "pet"}
HTML_AREAS = {"Seget Vranjica": "VRANJICA", "Seget Donji": "DONJI", "Seget Zagora – sva naselja": "ZAGORA"}
ZONES = [("VRANJICA", "Seget Vranjica", ["Seget Vranjica"]),
         ("DONJI", "Seget Donji", ["Seget Donji"]),
         ("ZAGORA", "Zagora (Bristivica, Prapatnica, Ljubitovica, Seget Gornji)",
          ["Bristivica", "Prapatnica", "Ljubitovica", "Seget Gornji"])]
DANI = list(pravila.DANI)
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Zeleni Seget d.o.o.",
    "web": SITE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Seget"],
    "nazivi": {"K": "Papir (plavi spremnik)", "P": "Plastika (žuti spremnik)"},
}
NAPOMENE = [
    "Datumi su iz godišnjeg kalendara odvoza (PDF); papir i plastika od svibnja do listopada odvoze se svaki "
    "tjedan, a ostatak godine svaki drugi tjedan (naizmjence Donji/Vranjica i Zagora).",
    "Na stranici piše da je aktualni (ljetni) raspored privremeno produžen; ako vrijedi i nakon listopada, papir i "
    "plastika mogli bi se i dalje odvoziti tjedno – provjerite obavijesti na zeleniseget.hr.",
    "Sezonski subotnji odvoz miješanog otpada (ljubičasto u kalendaru) obavlja se po dogovoru za hotele i "
    "restorane, ne za kućanstva i iznajmljivače, pa nije upisan.",
    "Prema kalendaru otpad se odvozi i blagdanima; pomaknutih datuma nema.",
    "Mobilno reciklažno dvorište za Zagoru srijedom 9–13 sati: Seget Gornji (kod Doma) 1., Bristivica (kod groblja) "
    "2., Prapatnica (kod groblja) 3., Ljubitovica (kod škole) 4. srijeda u mjesecu.",
    "Zeleni otok, Ulica kralja Zvonimira 60: utorak 8–13, četvrtak 11–17 sati.",
    "Zeleni Seget d.o.o., Trg hrvatskog viteza Špire Ševe Frzelina 1, Seget Donji; 021/880-037, info@zeleniseget.hr.",
]


def html_rules(page, problems):
    """{area: {"M": 'pon čet', "K": 'uto', "P": 'pet'}} from the 'Aktualni raspored odvoza' tabs."""
    i = page.find("Aktualni raspored odvoza")
    j = page.find("Radno vrijeme Zelenog Otoka", i)
    if i < 0 or j < 0:
        problems.append("na stranici nema odlomka 'Aktualni raspored odvoza'")
        return {}
    out = {}
    for title, body in re.findall(r'w-tabs-section-title">(.*?)</div>.*?<ul>(.*?)</ul>', page[i:j], re.S):
        area = HTML_AREAS.get(html.unescape(title).strip())
        if not area:
            problems.append(f"nepoznato područje na stranici: {html.unescape(title)!r}")
            continue
        for kind, days in re.findall(r"<strong>(\w+)</strong>\s*&#8211;\s*([^<]+)</li>", body):
            words = [w for w in re.split(r"\s+i\s+|,\s*", days.lower().replace("svaka ", "").strip()) if w]
            if not words or any(w not in DAYS for w in words):
                problems.append(f"{area}: nepoznati dani {days!r}")
                continue
            out.setdefault(area, {})[{"MKO": "M", "Plastika": "P", "Papir": "K"}[kind]] = " ".join(DAYS[w] for w in words)
    if set(out) != set(AREAS) or any("M" not in r for r in out.values()):
        problems.append(f"pravila sa stranice: {out}")
    return out


def say(rule):
    """'pon čet' -> 'ponedjeljak, četvrtak'."""
    return ", ".join(DAN[DANI.index(k)] for k in rule.split())


def classify(p):
    r, g, b = p
    if max(p) - min(p) < 80 or p in BG.values():
        return None
    h = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] * 360
    return "G" if 70 <= h <= 180 else "B" if 190 <= h <= 270 else "V" if 280 <= h <= 345 else "Y" if 35 <= h <= 65 else None


def runs(values):
    out = []
    for v in values:
        if out and v == out[-1][1] + 1:
            out[-1][1] = v
        else:
            out.append([v, v])
    return [a for a, _ in out]


def read_calendar(png, year, problems):
    """{date: [(colour, area)]} from the calendar picture."""
    im = Image.open(png).convert("RGB")
    px, grey, (w, h) = im.load(), im.convert("L").load(), im.size
    cols = [0] + runs([x for x in range(w) if sum(grey[x, y] < 200 for y in range(h)) > h * 0.3])
    rows = runs([y for y in range(h) if sum(grey[x, y] < 200 for x in range(w)) > w * 0.3])
    if len(cols) != 13 or len(rows) != 33:
        problems.append(f"mreža kalendara: {len(cols)} stupaca, {len(rows)} redaka (očekivano 13 i 33)")
        return {}
    found = {}
    for m in range(1, 13):
        x0, x1 = cols[m - 1], cols[m]
        for d in range(1, 32):
            y0, y1 = rows[d], rows[d + 1]
            real = d <= monthrange(year, m)[1]
            kind = "none" if not real else {5: "sat", 6: "sun"}.get(date(year, m, d).weekday(), "week")
            if px[x0 + 4, y0 + 4] != BG[kind]:
                problems.append(f"{d}.{m}.: boja ćelije {px[x0 + 4, y0 + 4]} ne odgovara danu ({kind})")
            pts = {}
            for y in range(y0 + 2, y1 - 1):
                for x in range(x0 + (x1 - x0) // 2, x1 - 2):
                    c = classify(px[x, y])
                    if c:
                        pts.setdefault(y, []).append((x, c))
            lines = []
            for y in sorted(pts):
                if lines and y - lines[-1][-1] <= 2:
                    lines[-1].append(y)
                else:
                    lines.append([y])
            labels = []
            for ys in lines:
                ps = [p for y in ys for p in pts[y]]
                if len(ps) < 15:
                    continue
                colour = Counter(c for _, c in ps).most_common(1)[0][0]
                xs = [x for x, c in ps if c == colour]
                width = max(xs) - min(xs) + 1
                area = next((a for a, (lo, hi) in AREAS.items() if lo <= width <= hi), None)
                if colour == "V" and width == 24:
                    area = "DONJI"  # "DONJI*" (seasonal, marked with an asterisk)
                if not area:
                    problems.append(f"{d}.{m}.: natpis širine {width} px ({colour}) nije prepoznat")
                    continue
                labels.append((colour, area))
            if not real:
                if labels:
                    problems.append(f"{d}.{m}.: natpis u ćeliji dana koji ne postoji")
                continue
            found[date(year, m, d)] = labels
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    page = fetch(PAGE).decode("utf-8", "replace")
    rules = html_rules(page, problems)
    link = re.search(rf'href="([^"]*ZS_odvoz_kalendar_{year}\.pdf)"', page)
    if not link:
        sys.exit(f"Na {PAGE} nema kalendara za {year}.")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "kalendar.pdf"
        digest = hashlib.sha256(fetch(link.group(1), pdf)).hexdigest()
        if digest != PDF_SHA256.get(year):
            sys.exit(f"Slika se promijenila (ili je nova), čitanje mreže treba ponovno provjeriti: {link.group(1)} "
                     f"(sha256 {digest})")
        subprocess.run(["pdfimages", "-png", "-f", "1", "-l", "1", str(pdf), str(Path(tmp) / "img")], check=True)
        png = max(Path(tmp).glob("img-*.png"), key=lambda p: p.stat().st_size)
        found = read_calendar(png, year, problems)
    print(f"Kalendar {link.group(1)}: {len(found)} dana; pravila sa stranice: {rules}")
    mko_by_day = {k: {a for a, r in rules.items() if k in r.get("M", "").split()} for k in DANI}
    recycling = {a: {c: r.get(c, ZAGORA_RECYCLING[c] if a == "ZAGORA" else None) for c in "KP"} for a, r in rules.items()}
    rows = {a: [] for a in AREAS}
    seasonal = 0
    for d, labels in sorted(found.items()):
        day = DANI[d.weekday()]
        green = {a for c, a in labels if c == "G"}
        if green != mko_by_day[day]:
            problems.append(f"{d}: miješani otpad za {sorted(green)}, a po pravilima {sorted(mko_by_day[day])}")
        codes = {}
        for colour, area in labels:
            code = COLOURS[colour]
            if code is None:
                seasonal += 1
                if day != "sub":
                    problems.append(f"{d}: sezonski odvoz (ljubičasto) nije u subotu")
                continue
            if code in "KP" and recycling.get(area, {}).get(code) != day:
                problems.append(f"{d}: {code} za {area} u {day}, a po pravilima {recycling.get(area, {}).get(code)}")
            if code in codes.get(area, ""):
                problems.append(f"{d}: {code} dvaput za {area}")
            codes[area] = codes.get(area, "") + code
        for area, c in codes.items():
            rows[area].append((d, c, False))
    zones = {}
    for z, (area, podrucje, places) in enumerate(ZONES, 1):
        got = Counter(c for _, cs, _ in rows[area] for c in cs)
        for m in range(1, 13):
            per = Counter(c for d, cs, _ in rows[area] if d.month == m for c in cs)
            k = len(rules[area]["M"].split())
            if not 4 * k <= per["M"] <= 5 * k:
                problems.append(f"{area} {m}. mjesec: miješani otpad {per['M']} puta")
            if area != "ZAGORA" and not all(1 <= per[c] <= 5 for c in "KP"):
                problems.append(f"{area} {m}. mjesec: papir {per['K']}, plastika {per['P']}")
            if per["K"] != per["P"] and abs(per["K"] - per["P"]) > 1:
                problems.append(f"{area} {m}. mjesec: papir {per['K']}, plastika {per['P']}")
        zones[str(z)] = {"jls": "Seget", "podrucje": podrucje, "ulice": places,
                         "napomena": f"Miješani otpad: {say(rules[area]['M'])}; papir: {say(recycling[area]['K'])}, "
                                     f"plastika: {say(recycling[area]['P'])} (prema kalendaru tjedno ili svaki drugi tjedan).",
                         "raw": {str(year): podaci.month_lines(rows[area])}}
        print(f"Zona {z} ({podrucje}): " + ", ".join(f"{c} {n}" for c, n in sorted(got.items())))
    print(f"Sezonskih (ljubičastih) natpisa izostavljeno: {seasonal}")
    for p in problems[:40]:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "izvor": PAGE, "napomene": NAPOMENE + [f"Kalendar: {link.group(1)}"], "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
