"""Pazin, Cerovlje, Gračišće, Karojba, Lupoglav, Motovun, Sveti Petar u Šumi, Tinjan: Usluga d.o.o. Pazin.

    python3 -m izvori.usluga_pazin [--year 2026]

The page "Raspored odvoza otpada YYYY" on usluga-pazin.hr links one PDF per town or municipality (Excel
export with a text layer). Each page of a PDF is one group of settlements ("Naselja: ...") with a
12-month calendar whose grid also shows the greyed days of the neighbouring months; days are read by
row and column (the printed number must agree, see komunalac_jurdani.mreza). A light green fill is
mixed waste; a frame of thin orange rects is plastic and metal, a blue one paper and cardboard, both
together with mixed waste. Holidays are built into the published dates (e.g. the round of a holiday
Thursday or Friday moves to that Saturday) and no other notice was found, so the dates are taken as
printed; a day off the zone's weekday must replace a holiday of that week and is marked as moved.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.komunalac_jurdani import mreza, short, split_list
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import MONTHS, colour

SLUG = "usluga-pazin"
SITE = "https://www.usluga-pazin.hr"
PAGE = SITE + "/hr/usluge/raspored-odvoza-otpada-{year}/"
JLS = {  # file name suffix: (JLS as in jls_davatelj.csv, name in the PDF title)
    "pazin": ("Pazin", "GRADA PAZINA"), "cerovlje": ("Cerovlje", "OPĆINE CEROVLJE"),
    "gracisce": ("Gračišće", "OPĆINE GRAČIŠĆE"), "karojba": ("Karojba", "OPĆINE KAROJBA"),
    "lupoglav": ("Lupoglav", "OPĆINE LUPOGLAV"), "motovun": ("Motovun – Montona", "OPĆINE MOTOVUN"),
    "svpetar": ("Sveti Petar u Šumi", "OPĆINE SVETI PETAR U ŠUMI"), "tinjan": ("Tinjan", "OPĆINE TINJAN"),
}
GREENS = {(0.847, 0.91, 0.659), (0.773, 0.863, 0.494)}  # mixed waste (one darker cell in Motovun)
FRAMES = {(1.0, 0.753, 0.0): "P", (0.0, 0.69, 0.941): "K"}
LEGEND = ["MIJEŠANI KOMUNALNI OTPAD (MKO)", "PAPIR I KARTON + MKO", "PLASTIKA I METAL + MKO"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DAN_I = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
PROVIDER = {
    "davatelj": "Usluga d.o.o. Pazin",
    "web": SITE,
    "zupanija": "Istarska",
    "jls": [j for j, _ in JLS.values()],
    "nazivi": {"P": "Plastika i metal", "K": "Papir i karton"},
    "napomene": [
        "Miješani komunalni otpad odvozi se jednom tjedno; papir i karton te plastika i metal odvoze se "
        "naizmjence, svaki jednom mjesečno, na dan odvoza miješanog otpada.",
        "Blagdani su uračunati u objavljeni raspored: odvoz s nekih blagdana premješten je na subotu tog tjedna "
        "(označeno kao pomaknuto, vidi napomenu zone), a na ostale se odvozi redovno; posebne obavijesti nisu pronađene.",
        "Objavljeni raspored ne sadrži odvoz biootpada.",
    ],
}


def frame(x, y, strips):
    """Code of the frame of thin filled rects (Excel cell border) around a point, or None."""
    for col, code in FRAMES.items():
        ss = [s for s in strips if colour(s) == col]
        hor = [s["top"] for s in ss if s["height"] < 3 and s["x0"] - 1 <= x <= s["x1"] + 1]
        ver = [s["x0"] for s in ss if s["width"] < 3 and s["top"] - 1 <= y <= s["bottom"] + 1]
        if any(0 < y - t < 12 for t in hor) and any(0 < t - y < 12 for t in hor) and \
                any(0 < x - v < 15 for v in ver) and any(0 < v - x < 15 for v in ver):
            return code
    return None


def read_page(page, year, name, problems):
    """(settlement text, [(date, codes)]) of one page."""
    text = " ".join((page.extract_text() or "").split())
    m = re.search(r"Naselja:\s*(.+?)\s*siječanj", text)
    if not m:
        problems.append(f"{name}: nema popisa naselja")
        return "", []
    if not all(l in text for l in LEGEND) or str(year) not in text.split("Naselja:")[0]:
        problems.append(f"{name}: legenda ili godina se promijenila")
    cells, probs = mreza(page, year)
    problems += [f"{name}: {p}" for p in probs]
    bottom = max((c["y"] for c in cells.values()), default=0) + 10  # the legend swatches are below the grid
    strips = [r for r in page.rects if r.get("fill") and colour(r) in FRAMES and min(r["width"], r["height"]) < 3
              and r["top"] < bottom]
    rows, framed = [], Counter()
    for d, c in sorted(cells.items()):
        code = frame(c["x"], c["y"], strips)
        if c["fill"] not in GREENS | {None, (1.0, 1.0, 1.0)}:
            problems.append(f"{name}: nepoznata boja {c['fill']} na {d:%d.%m.}")
        if code:
            framed[code] += 1
            rows.append((d, "M" + code))
        elif c["fill"] in GREENS:
            rows.append((d, "M"))
    for col, code in FRAMES.items():  # every frame (4 strips) must enclose one day
        tops = [s for s in strips if colour(s) == col and s["height"] < 3]
        if len(tops) != 2 * framed[code]:
            problems.append(f"{name}: {len(tops) // 2} okvira boje {code}, a uokvirenih dana {framed[code]}")
    return m.group(1).rstrip(" ."), rows


def check(name, rows, year, hol, problems):
    """Same weekday all year except a day replacing a holiday that week (marked as moved), plausible counts
    per month; returns (weekday, [(date, codes, moved)])."""
    wd = Counter(d.weekday() for d, _ in rows).most_common(1)[0][0]
    per, out = defaultdict(Counter), []
    for d, codes in rows:
        moved = d.weekday() != wd
        if moved and d + timedelta(days=wd - d.weekday()) not in hol:
            problems.append(f"{name}: {d:%d.%m.} nije {DAN[wd]}, a tog tjedna nema blagdana na taj dan")
        out.append((d, codes, moved))
        per[d.month].update(codes)
    for m in range(1, 13):
        n = per[m]
        if not 3 <= n["M"] <= 5 or not 1 <= n["P"] + n["K"] <= 3:
            problems.append(f"{name}: {MONTHS[m - 1].lower()} {dict(n)}")
    if not 10 <= sum(n["P"] for n in per.values()) <= 14 or not 10 <= sum(n["K"] for n in per.values()) <= 14:
        problems.append(f"{name}: plastika/papir {sum(n['P'] for n in per.values())}/{sum(n['K'] for n in per.values())} puta")
    return wd, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = PAGE.format(year=year)
    html = fetch(page).decode("utf-8", "replace")
    urls = {}
    for url in re.findall(r'href="([^"]+\.pdf)"', html):
        m = re.search(rf"raspored_odvoza_mijesanog_i_reciklabilnog_\w*otpada_za_{year}_godinu_(\w+)\.pdf$", url)
        if m and m.group(1) in JLS:
            urls[m.group(1)] = url if url.startswith("http") else SITE + url
    missing = [k for k in JLS if k not in urls]
    if missing:
        sys.exit(f"Nema rasporeda za {', '.join(missing)} na {page}")
    problems, zones = [], []
    hol = set(pravila.blagdani(year))
    with tempfile.TemporaryDirectory() as tmp:
        for key, (jls, title) in JLS.items():
            path = Path(tmp) / f"{key}.pdf"
            fetch(urls[key], path)
            with pdfplumber.open(path) as pdf:
                for i, p in enumerate(pdf.pages, 1):
                    name = f"{jls} str. {i}"
                    if title not in " ".join((p.extract_text() or "").split()):
                        problems.append(f"{name}: naslov nije '{title}'")
                    text, rows = read_page(p, year, name, problems)
                    if not rows:
                        problems.append(f"{name}: nema datuma")
                        continue
                    wd, rows = check(name, rows, year, hol, problems)
                    dates = {d for d, _, _ in rows}
                    moved = [(h, d) for d, _, m in rows if m for h in hol if h == d + timedelta(days=wd - d.weekday())]
                    skipped = [h for h in sorted(hol) if h.weekday() == wd and h not in dates
                               and not any(h == a for a, _ in moved)]
                    ulice = split_list(text)
                    note = f"Miješani otpad {DAN_I[wd]}, papir i plastika naizmjence jednom mjesečno."
                    if moved:
                        note += " Odvoz s blagdana premješten je: " + ", ".join(f"{a:%d.%m.} na {b:%d.%m.}" for a, b in moved) + "."
                    if skipped:
                        note += " Prema rasporedu nema odvoza na blagdan " + ", ".join(f"{h:%d.%m.}" for h in skipped) + "."
                    zones.append(({"jls": jls, "podrucje": f"{DAN[wd].capitalize()} – {short(ulice)}",
                                   "opis": "Naselja: " + text, "ulice": ulice, "napomena": note}, rows))
                    n = Counter(c for _, codes, _ in rows for c in codes)
                    print(f"{name:24} {DAN[wd]:11} M{n['M']} P{n['P']} K{n['K']}  {short(ulice, 4)[:60]}"
                          + (f"  bez odvoza: {', '.join(f'{h:%d.%m.}' for h in skipped)}" if skipped else "")
                          + (f"  pomaknuto: {', '.join(f'{a:%d.%m.}->{b:%d.%m.}' for a, b in moved)}" if moved else ""))
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": page, "zone": {}}
    for i, (zone, rows) in enumerate(zones, 1):
        if not zone["ulice"]:
            problems.append(f"zona {i}: nema naselja")
        prev = old.get(str(i), {})
        prev = prev.get("raw", {}) if prev.get("opis") == zone["opis"] else {}
        data["zone"][str(i)] = {**zone, "raw": {**prev, str(year): podaci.month_lines(rows)}}
    seen = Counter((z["jls"], u) for z in data["zone"].values() for u in z["ulice"])
    for (jls, u), n in seen.items():
        if n > 1:
            print(f"Napomena: {u} ({jls}) je u {n} zone")
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
