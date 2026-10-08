"""Kolan: Čistoća i održavanje Kolan d.o.o. (CIOK, ciok.hr), month calendars for Kolan, Mandre and Kolanjski Gajac.

    python3 -m izvori.ciok [--year 2026]

The page "Raspored odvoza otpada" links one PDF per month or two months (Raspored_odvoza_10-2026.pdf,
..._12-2025-01-2026.pdf, ..._07--2026_Kolan.pdf; the names are irregular, so every link with the year
in its name is read). Each page is a month calendar titled "KOLAN - SRPANJ 2026" (one settlement) or
"KOLAN-MANDRE-K. GAJAC – SIJEČANJ 2026" (the same dates for all three); the day number sits in the
top right corner of its cell and the waste types are written below it. Words are read with pdfplumber;
text that Word clipped away (an extra "3", a second "BIOOTPAD - KANTA") is dropped by checking the
rendered page for ink under each word. The weekday comes from the x position under the PON..NED heading
and must match the date; every day of the month must be on the page once. Holidays are built into the
calendars (holiday names in red, no collection that day). "Po potrebi – EKO otok" (eco island emptied as
needed) is not a collection date. Months no longer linked are kept from podaci/ciok.json.
"""
import argparse
import calendar
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import pdfplumber
from PIL import Image

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "ciok"
SITE = "https://www.ciok.hr"
PAGE = SITE + "/index.php/cistoca/raspored-odvoza-otpada"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
HEAD = ["PON", "UTO", "SRI", "ČET", "PET", "SUB", "NED"]
ZONES = {"1": "Kolan", "2": "Mandre", "3": "Kolanjski Gajac"}
TITLES = {"KOLAN": ["1"], "MANDRE": ["2"], "KOLANJSKI GAJAC": ["3"], "KOLAN-MANDRE-K. GAJAC": ["1", "2", "3"]}
TYPES = {"MIJEŠANI": "M", "BIOOTPAD": "B", "PLASTIKA": "P", "PAPIR": "K", "METAL": "L", "STAKLO": "S",
         "GLOMAZNI": "G"}
FILLER = {"KOMUNALNI", "KOMUN", "ALNI", "OTPAD", "OTPAD+", "+", "-", "–", "/", "I", "KARTON", "KANTA", "EE",
          "(ZAHTJEV)", "PO", "POTREBI", "EKO", "OTOK"}
HOLIDAY = {"NOVA", "GODINA", "SVETA", "TRI", "KRALJA", "USKRS", "USKRSNI", "PONEDJELJAK", "PRAZNIK", "RADA",
           "DAN", "DRŽAVNOSTI", "TIJELOVO", "A.", "B.", "P.", "DOM.", "ZAHVALNOSTI", "ZAVALNOST", "V.", "GOSPA",
           "SVI", "SVETI", "SJEĆANJA", "BOŽIĆ", "SV.", "STJEPAN"}
PROVIDER = {
    "davatelj": "Čistoća i održavanje Kolan d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zadarska",
    "jls": ["Kolan"],
    "nazivi": {"L": "Metal (zajedno sa staklom)", "S": "Staklo", "G": "Glomazni i EE otpad (na zahtjev)"},
}
NAPOMENE = [
    "Raspored se objavljuje mjesečno ili za dva mjeseca; ljeti (lipanj-kolovoz) zaseban raspored za Kolan, "
    "Mandre i Kolanjski Gajac, inače zajednički.",
    "Metal i staklo odvoze se zajedno (\"METAL / STAKLO\").",
    "Glomazni otpad i EE otpad odvoze se na označene dane samo uz zahtjev (obrazac na ciok.hr).",
    "Na blagdane označene u rasporedu nema odvoza; pomaci su već upisani u datume.",
    "Kontakt: 023/698-017, info@ciok.hr (radnim danom 07:00-15:00).",
]
NOTE_KG = "Staklo, plastika, papir i metal: EKO otok, pražnjenje po potrebi (kad je tako navedeno u rasporedu); nisu upisani kao odvoz."


def visible(words, png, scale):
    """Words with ink under them on the rendered page (Word clips overflowing cell text away)."""
    img = np.asarray(Image.open(png).convert("RGB")).astype(int)
    ink = (765 - img.sum(axis=2)) > 150
    out = []
    for w in words:
        y0, y1 = int(w["top"] * scale) + 1, int(w["bottom"] * scale) - 1
        x0, x1 = int(w["x0"] * scale) + 1, int(w["x1"] * scale) - 1
        box = ink[y0:y1, x0:x1]
        if box.size and box.mean() > 0.03:
            out.append(w)
    return out


def read_page(page, png, scale, year):
    """(zones, month, {date: codes}, eco island seen, problems) for one calendar page."""
    words = visible(page.extract_words(extra_attrs=["size"]), png, scale)
    heads = [w for w in words if w["text"] in HEAD]
    row = sorted((w for w in heads if abs(w["top"] - heads[0]["top"]) < 3), key=lambda w: w["x0"]) if heads else []
    if [w["text"] for w in row] != HEAD:
        return None, None, {}, False, [f"zaglavlje dana {[w['text'] for w in row]}"]
    title = " ".join(w["text"] for w in sorted((w for w in words if w["bottom"] < row[0]["top"]), key=lambda w: w["x0"]))
    m = re.fullmatch(r"(.+?) [–-] (\w+) (\d{4})", title)
    if not m or m.group(1) not in TITLES or m.group(2) not in MONTHS:
        return None, None, {}, False, [f"naslov {title!r}"]
    zones, month, y = TITLES[m.group(1)], MONTHS.index(m.group(2)) + 1, int(m.group(3))
    if y != year:
        return zones, (y, month), {}, False, []
    cx = [(w["x0"] + w["x1"]) / 2 for w in row]
    cw = (cx[-1] - cx[0]) / 6
    nums = [w for w in words if w["text"].isdigit() and w["size"] >= 12 and w["top"] > row[0]["bottom"]]
    tops = sorted({round(w["top"]) for w in nums})
    rows = [t for i, t in enumerate(tops) if i == 0 or t - tops[i - 1] > 6]
    pitch = float(np.median(np.diff(rows))) if len(rows) > 1 else page.height
    found, seen, eco, problems = {}, Counter(), False, []
    for n in nums:
        col = min(range(7), key=lambda i: abs(cx[i] - (n["x1"] - cw / 2 + 4)))
        day = int(n["text"])
        if not 1 <= day <= calendar.monthrange(y, month)[1]:
            problems.append(f"{m.group(2)}: nemoguć dan {day}")
            continue
        d = date(y, month, day)
        seen[d] += 1
        if col != d.weekday():
            problems.append(f"{d}: u stupcu {HEAD[col]}, a to je {HEAD[d.weekday()]}")
        r = max(i for i, t in enumerate(rows) if t <= round(n["top"]) + 6)
        bottom = rows[r + 1] - 2 if r + 1 < len(rows) else rows[r] + pitch - 2
        cell = [w for w in words if n["bottom"] - 1 <= w["top"] < bottom and w["size"] < 12
                and abs((w["x0"] + w["x1"]) / 2 - cx[col]) < cw / 2]
        texts = [w["text"].upper().strip(",") for w in cell]
        unknown = [t for t in texts if t not in TYPES and t not in FILLER and t not in HOLIDAY]
        if unknown:
            problems.append(f"{d}: nepoznate riječi {unknown}")
        codes = {TYPES[t] for t in texts if t in TYPES}
        if "POTREBI" in texts:  # "Po potrebi – EKO otok": eco island, emptied as needed
            eco = True
            codes -= set("PKLS")
        if codes:
            found[d] = "".join(sorted(codes))
    for day in range(1, calendar.monthrange(y, month)[1] + 1):
        if seen[date(y, month, day)] != 1:
            problems.append(f"{date(y, month, day)} je na stranici {seen[date(y, month, day)]} puta")
    return zones, (y, month), found, eco, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    links = sorted(set(re.findall(rf'href="([^"]*/Raspored_odvoza_[^"]*{year}[^"]*\.pdf)"', html)))
    if not links:
        sys.exit(f"Na {PAGE} nema rasporeda za {year}")
    problems, months, eco_zones = [], {z: {} for z in ZONES}, set()
    with tempfile.TemporaryDirectory() as tmp:
        for link in links:
            path = Path(tmp) / link.rsplit("/", 1)[1]
            fetch(link if link.startswith("http") else SITE + "/" + link.lstrip("/"), path)
            pdf = pdfplumber.open(path)
            for i, page in enumerate(pdf.pages, 1):
                png = Path(tmp) / f"{path.stem}-{i}"
                subprocess.run(["pdftoppm", "-f", str(i), "-l", str(i), "-r", "144", "-png", "-singlefile",
                                str(path), str(png)], check=True)
                zones, ym, found, eco, probs = read_page(page, png.with_suffix(".png"), 2, year)
                problems += [f"{path.name} str. {i}: {p}" for p in probs]
                if not ym or ym[0] != year:
                    continue
                for z in zones:
                    if ym[1] in months[z]:
                        problems.append(f"{path.name}: {ZONES[z]} {ym[1]:02d}/{year} je u više datoteka")
                    months[z][ym[1]] = found
                    if eco:
                        eco_zones.add(z)
                print(f"{path.name} str. {i}: {', '.join(ZONES[z] for z in zones)} {ym[1]:02d}/{year}, "
                      f"{len(found)} dana odvoza")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": list(NAPOMENE), "zone": {}}
    for z, name in ZONES.items():
        rows = [(d, c, False) for found in months[z].values() for d, c in found.items()]
        kept = [x for x in podaci.iter_dates(old["zone"].get(z, {"raw": {}}), year) if x[0].month not in months[z]]
        rows += kept
        for m in sorted(months[z]):
            n = Counter(c for d, codes in months[z][m].items() for c in codes)
            if not 4 <= n["M"] <= 31:
                problems.append(f"{name} {year}-{m:02d}: {n['M']} odvoza miješanog otpada")
        if not rows:
            problems.append(f"{name}: nema datuma za {year}")
            continue
        covered = sorted(set(months[z]) | {d.month for d, _, _ in kept})
        print(f"zona {z} {name}: mjeseci {covered}" + (f" (iz ranijih podataka: {sorted({d.month for d, _, _ in kept})})"
                                                       if kept else ""))
        data["zone"][z] = {"jls": "Kolan", "podrucje": name, "ulice": [name],
                           **({"napomena": NOTE_KG} if z in eco_zones else {}),
                           "raw": {**old["zone"].get(z, {}).get("raw", {}), str(year): podaci.month_lines(rows)}}
    allm = sorted({m for z in data["zone"].values() for m in range(1, 13)
                   if any(l.startswith(f"{m:02d}-") for l in z["raw"].get(str(year), []))})
    if allm and allm != list(range(1, 13)):
        data["napomene"].insert(0, f"Za {year}. objavljeni su mjeseci: {', '.join(f'{m}.' for m in allm)} "
                                   "(raspored izlazi mjesec po mjesec).")
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
