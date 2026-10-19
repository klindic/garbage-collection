"""Otok Krk (Krk, Baška, Dobrinj, Malinska-Dubašnica, Omišalj, Punat, Vrbnik): Ponikve eko otok Krk d.o.o.

    python3 -m izvori.ponikve [--year 2026]

One island-wide colour calendar on eko.ponikve.hr/kalendarodvoza: an A4 PDF for the whole year (vector,
the text layer has only the day numbers) and a PNG "2. polugodište" published on 1.7. for July to
December. Every day has at most one waste type, given by the cell colour (brown biowaste, blue paper,
yellow plastic and metal, green mixed waste and nappies, grey glass). January to June are read from the
PDF with kalendar_boje.read_page (same-colour cells are merged into wider rects, so the fill under each
day number is tested). July to December come from the PNG: its colours were sampled at the corners of
every day cell of the grid (the circle around a holiday covers the cell centre) and are kept in this
script; the run checks the image's sha256, samples it again and compares, and fails if the image
changed. The PDF's July-December is compared with the PNG and differences are printed. Holidays are
circled in the calendar and the collection days are drawn as they really are (no extra shifts), so
the dates are used as published; holidays without any collection are listed in the notes.
"""
import argparse
import calendar
import hashlib
import io
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import read_page

SLUG = "ponikve"
SITE = "https://eko.ponikve.hr"
PAGE = SITE + "/kalendarodvoza"
JLS = ["Krk", "Baška", "Dobrinj", "Malinska-Dubašnica", "Omišalj", "Punat", "Vrbnik"]
PALETTE = {(0.66, 0.42, 0.10): "B", (0.0, 0.629, 0.893): "K", (1.0, 0.932, 0.0): "P",
           (0.0, 0.596, 0.279): "M", (0.54, 0.539, 0.537): "S"}
# PNG "2. polugodište" (1500x999): 3 x 2 month blocks, 7 columns of 49 px, week rows of 44 px
PNG = {
    "name": "ponikve_kalendar_odvoza_2026_2dio.png",
    "sha256": "11d186744aaef7107dfd67d5f43adb1ebce3b70727248a9e4dabb0fa77c7a1e7",
    "months": range(7, 13),
    "x0": [145, 582, 1011], "y0": [114, 489.5], "dx": 49, "dy": 44,
}
RGB = {"B": (168, 107, 26), "K": (0, 160, 228), "P": (255, 238, 0), "M": (0, 152, 71), "S": (138, 137, 137),
       "-": (255, 255, 255)}
# July-December as sampled from the PNG (and checked by eye): one character per day, "-" = no collection
DRUGI_DIO = {
    7: "SBPB-BMKBPB-BM-BPB-BMKBPB-BMSBP",
    8: "B-BMKBPB-BMSBPB-BMKBPB-BMSBPB-B",
    9: "MKBPB-BMSBPB-BMKBP--BM-BP--BMK",
    10: "B---BMSBP--B-KB---BM-BP--B-KB--",
    11: "-BMSBP--B-KB---BM-BP--B-KB---B",
    12: "MSBP--B-KB---BM-BP--B-KB---BM-B",
}
NOTE_6AM = "OD 1.6. DO 30.9. ODVOZ OTPADA ZAPOČINJE OD 6:00 SATI"
PROVIDER = {
    "davatelj": "Ponikve eko otok Krk d.o.o.",
    "web": SITE,
    "zupanija": "Primorsko-goranska",
    "jls": JLS,
    "nazivi": {"B": "Biorazgradivi otpad", "K": "Papir, karton i višeslojna kartonska ambalaža",
               "P": "Plastika i metal", "M": "Miješani komunalni otpad i pelene"},
}


def sample_png(data, year):
    """{date: code} from the PNG grid; problems if a cell is not clearly one colour or the numbers are off."""
    from PIL import Image
    import numpy as np
    im = np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(int)
    found, problems = {}, []
    for i, m in enumerate(PNG["months"]):
        bx, by = PNG["x0"][i % 3], PNG["y0"][i // 3]
        first, n = date(year, m, 1).weekday(), calendar.monthrange(year, m)[1]
        for cell in range(42):
            x, y = int(bx + cell % 7 * PNG["dx"]), int(by + cell // 7 * PNG["dy"])
            bg = np.median([im[y + dy, x + dx] for dx in (2, 3, 45, 46) for dy in (2, 3, 40, 41)], axis=0)
            code = min(RGB, key=lambda k: max(abs(bg - RGB[k])))
            ink = int((abs(im[y + 10:y + 34, x + 12:x + 37].reshape(-1, 3) - bg).max(axis=1) > 80).sum())
            day = cell - first + 1
            if (ink > 15) != (1 <= day <= n):  # a day number must be printed exactly in the cells of the month
                problems.append(f"slika: {m}. mjesec, ćelija {cell}: {'nema broja' if ink <= 15 else 'broj izvan mjeseca'}")
            if max(abs(bg - RGB[code])) > 40:
                problems.append(f"slika: {day}.{m}. nejasna boja {bg.tolist()}")
            if 1 <= day <= n and code != "-":
                found[date(year, m, day)] = code
    return found, problems


def check(rows, year, problems):
    """Each type on its usual weekdays (those it has at least 4 times a year), plausible month counts."""
    days = defaultdict(Counter)
    for d, code in rows.items():
        days[code][d.weekday()] += 1
    usual = {c: {w for w, n in cnt.items() if n >= 4} for c, cnt in days.items()}
    for d, code in sorted(rows.items()):
        if d.weekday() not in usual[code]:
            problems.append(f"{d:%d.%m.}: {code} nije na uobičajen dan u tjednu")
        if d.year != year:
            problems.append(f"{d}: izvan godine")
    per = defaultdict(Counter)
    for d, code in rows.items():
        per[d.month][code] += 1
    limits = {"B": (7, 14), "M": (2, 5), "K": (1, 5), "P": (1, 5), "S": (1, 3)}
    for m in range(1, 13):
        for code, (lo, hi) in limits.items():
            if not lo <= per[m][code] <= hi:
                problems.append(f"{m}. mjesec: {code} {per[m][code]} puta")
    return usual


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    pdf_url = next(iter(re.findall(rf'href="([^"]*kalendar_odvoza_{year}[^"]*\.pdf)"', html)), None)
    png_url = next(iter(re.findall(rf'src="([^"]*kalendar_odvoza_{year}_2dio\.png)"', html)), None)
    if not pdf_url:
        sys.exit(f"Nema PDF kalendara za {year} na {PAGE}")
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "kalendar.pdf"
        fetch(pdf_url, path)
        with pdfplumber.open(path) as pdf:
            page = pdf.pages[0]
            text = " ".join((page.extract_text() or "").split())
            pdf_rows, probs = read_page(page, year, PALETTE)
    problems += [f"PDF: {p}" for p in probs]
    if NOTE_6AM.replace(" ", "") not in text.replace(" ", ""):
        print(f"Napomena: u PDF-u nema rečenice '{NOTE_6AM}'")
    print(f"PDF: {pdf_url}: {len(pdf_rows)} dana s odvozom")
    rows = {d: c for d, c in pdf_rows.items() if d.month < 7}
    source = f"PDF ({pdf_url})"
    if png_url and year == 2026:
        meta = {}
        data = fetch(png_url, meta=meta)
        digest = hashlib.sha256(data).hexdigest()
        if not png_url.endswith(PNG["name"]) or digest != PNG["sha256"]:
            sys.exit(f"Slika se promijenila ({png_url}, sha256 {digest}, Last-Modified {meta.get('Last-Modified')}): "
                     "ponovno očitati boje (sample_png), provjeriti ih okom i ažurirati DRUGI_DIO i sha256. Ništa nije upisano.")
        sampled, probs = sample_png(data, year)
        problems += probs
        stored = {date(year, m, i + 1): c for m, s in DRUGI_DIO.items() for i, c in enumerate(s) if c != "-"}
        if any(len(s) != calendar.monthrange(year, m)[1] for m, s in DRUGI_DIO.items()):
            problems.append("DRUGI_DIO: krivi broj dana u mjesecu")
        if sampled != stored:
            problems.append(f"slika se ne slaže s prepisanim podacima: {sorted(set(sampled.items()) ^ set(stored.items()))[:5]}")
        diff = [f"{d:%d.%m.} PDF {pdf_rows.get(d, '-')} / slika {stored.get(d, '-')}"
                for d in sorted(set(stored) | {d for d in pdf_rows if d.month >= 7}) if pdf_rows.get(d) != stored.get(d)]
        print(f"Slika (srpanj–prosinac): {png_url}, Last-Modified {meta.get('Last-Modified')}; razlika prema PDF-u: "
              + (", ".join(diff) if diff else "nema"))
        rows.update(stored)
        source = f"PDF za siječanj–lipanj ({pdf_url}) i slika 2. polugodišta za srpanj–prosinac ({png_url})"
    else:
        print("Nema slike drugog polugodišta: cijela godina iz PDF-a")
        rows.update({d: c for d, c in pdf_rows.items() if d.month >= 7})
    usual = check(rows, year, problems)
    hol = [h for h in pravila.blagdani(year) if h.weekday() < 6]
    without = [h for h in hol if h not in rows]
    names = {"B": "biootpad", "K": "papir", "P": "plastika i metal", "M": "miješani", "S": "staklo"}
    dani = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
    print("Uobičajeni dani: " + ", ".join(f"{names[c]} {'/'.join(dani[w] for w in sorted(ws))}" for c, ws in usual.items()))
    print("Blagdani bez odvoza: " + ", ".join(f"{h:%d.%m.}" for h in without))
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    napomene = [
        "Isti raspored vrijedi za cijeli otok Krk (sve općine i Grad Krk).",
        "Od 1.6. do 30.9. odvoz otpada započinje od 6:00 sati.",
        "Raspored odvoza otpada odnosi se samo na kante preuzete od Ponikava.",
        "Blagdani su zaokruženi u kalendaru, a odvozi su ucrtani na stvarne dane; prema kalendaru nema odvoza na "
        + ", ".join(f"{h:%d.%m.}" for h in without) + ".",
        f"Izvor: {source}.",
        "Informacije: 051/654-666.",
    ]
    data = {**PROVIDER, "izvor": PAGE, "napomene": napomene, "zone": {}}
    lines = podaci.month_lines([(d, c, False) for d, c in rows.items()])
    for i, jls in enumerate(JLS, 1):
        prev = old.get(str(i), {}).get("raw", {}) if old.get(str(i), {}).get("jls") == jls else {}
        data["zone"][str(i)] = {"jls": jls, "podrucje": f"{jls} – cijelo područje (jedinstveni raspored za otok Krk)",
                                "ulice": [jls], "raw": {**prev, str(year): lines}}
    n = Counter(rows.values())
    print(f"{len(rows)} dana: " + " ".join(f"{c}{n[c]}" for c in "MBPKS"))
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
