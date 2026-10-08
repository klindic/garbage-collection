"""Čistoća Opuzen d.o.o.: Grad Opuzen, streets grouped by their mixed, recyclable and bio waste days, 1.6.2026.-28.2.2027.

    python3 -m izvori.cistoca_opuzen [--year 2026]

The page "Skupljanje i odvoz otpada" shows two photographed leaflets ("Plan primopredaje otpada od 1.6.2026.
do 28.02.2027."). Image 1: the green bin (mixed waste) as weekday -> street lists; image 2: month
calendars June 2026 - February 2027 whose coloured boxes give the dates (yellow plastic and blue paper on
Mondays and Tuesdays, brown biowaste on Thursdays and Fridays), with a street list for each of these
weekdays. The street lists are transcribed below (names cleaned of obvious abbreviations); the calendar
colours are sampled with Pillow/numpy at the measured box positions of each month (the leaflet's grid is
not perfectly regular), and the Wednesday/weekend cells are checked to be empty. Both images are pinned by
sha256, so a new leaflet stops the script ("slika se promijenila"). Streets with the same days form a zone.
No holiday rule is published for mixed waste (shifts are announced as news), so its weekday dates are kept;
the calendar dates are used as drawn. Zažablje is not part of this schedule.
"""
import argparse
import calendar
import hashlib
import io
import re
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
from PIL import Image

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cistoca-opuzen"
SITE = "https://cistoca-opuzen.hr"
PAGE = SITE + "/skupljanje-odvoz-otpada/"
YEAR = 2026  # the plan runs from 1.6.YEAR to the end of February YEAR+1
VALID = ((6, 1), (2, 28))
IMAGES = {
    "WhatsApp-Image-2026-09-11-at-13.51.05.jpeg": "a68f6298e5e1a9be34ac306bdd99edb8b0ae7434560a4d9d6209ff1d2c9c2581",
    "WhatsApp-Image-2026-09-11-at-13.51.051.jpeg": "d72a5c14199cbd2d742073385de936a4fac015fa8eca4024edb9f69d8430ba69",
}
CALENDAR_IMAGE = "WhatsApp-Image-2026-09-11-at-13.51.051.jpeg"
ZD, ZO = "Zagrebačka (do vrtića)", "Zagrebačka (od vrtića)"
CENTAR = ["Stanke Parmača", ZD, "Stjepana Radića", "Pivčeva kala", "Trg kralja Tomislava", "Trg Opuzenske bojne",
          "Nikole Nonkovića", "Fort Opus", "Kraj križa"]
# image 1, "ZELENI SPREMNIK (miješani komunalni otpad)": weekday -> streets
MIXED = {
    0: CENTAR + ["Prašnica", "Jasenska", "Jadranska", "Pinovac", "Strimen", "Crepina 1", "Crepina 2",
                 "Žrtava Domovinskog rata", "Silvija S. Kranjčevića", "Ušće", "Trn", "Josipa bana Jelačića",
                 "Poslovna zona", "Pržinovac"],
    1: ["Trnovska", "Zrinsko-Frankopanska", "Mandarinska", ZO, "Zadarska", "Ivana Meštrovića", "Vukovarska",
        "Ante Starčevića", "Smokovo", "Foša", "Barake", "Matice hrvatske", "Tisno", "Celestina Medovića",
        "Poljanica Mate Pečića", "Poljanica Ner. Gusara", "Ivana Gundulića", "Tina Ujevića", "Kneza Domagoja",
        "Naronska"],
    2: CENTAR + ["Žrtava Domovinskog rata", "Silvija S. Kranjčevića", "Jakova Gotovca"],
    3: ["Prašnica", "Jasenska", "Jadranska", "Pinovac", "Strimen", "Crepina 1", "Crepina 2", "Josipa bana Jelačića",
        "Poslovna zona", "Ušće", "Pržinovac", "Trn"],
    4: CENTAR + [ZO, "Žrtava Domovinskog rata", "Prantrnovo", "Silvija S. Kranjčevića", "Trnovska",
                 "Zrinsko-Frankopanska", "Mandarinska", "Zadarska", "Ivana Meštrovića", "Vukovarska", "Ante Starčevića",
                 "Smokovo", "Foša", "Barake", "Matice hrvatske", "Tisno", "Celestina Medovića", "Poljanica Mate Pečića",
                 "Poljanica Ner. Gusara", "Ivana Gundulića", "Tina Ujevića", "Kneza Domagoja", "Naronska",
                 "Jakova Gotovca"],  # "ZAGREBAČKA CIJELA"
}
# image 2: weekday -> streets (Monday/Tuesday: plastic and paper as coloured; Thursday/Friday: biowaste)
SORTED = {
    0: CENTAR + ["Žrtava Domovinskog rata", "Silvija S. Kranjčevića", "Trnovska", "Zrinsko-Frankopanska",
                 "Poljanica Mate Pečića", "Poljanica Ner. Gusara", "Kneza Domagoja", "Naronska", "Prašnica", "Jasenska",
                 "Pinovac", "Strimen", "Jadranska", "Ivana Meštrovića", "Josipa bana Jelačića", "Ušće", "Pržinovac",
                 "Trn", "Celestina Medovića", "Ivana Gundulića"],
    1: ["Tisno", "Barake", "Crepina 1", "Crepina 2", "Poslovna zona", "Jakova Gotovca", "Trnovska", "Vukovarska",
        "Zadarska", "Ante Starčevića", "Smokovo", "Foša", "Matice hrvatske", "Mandarinska", "Tina Ujevića",
        "Prantrnovo", ZO],  # "PRATRNOVO" on the leaflet
    3: ["Prašnica", "Jasenska", "Jadranska", "Pinovac", "Strimen", "Prantrnovo", "Barake", "Crepina 1", "Crepina 2",
        "Josipa bana Jelačića", "Poslovna zona", "Ušće", "Pržinovac", "Trn"],
    4: CENTAR + [ZO, "Žrtava Domovinskog rata", "Silvija S. Kranjčevića", "Trnovska", "Zrinsko-Frankopanska",
                 "Mandarinska", "Zadarska", "Ivana Meštrovića", "Vukovarska", "Ante Starčevića", "Smokovo", "Foša",
                 "Matice hrvatske", "Tisno", "Celestina Medovića", "Poljanica Mate Pečića", "Poljanica Ner. Gusara",
                 "Ivana Gundulića", "Tina Ujevića", "Kneza Domagoja", "Naronska", "Jakova Gotovca"],  # + "CIJELA"
}
# image 2: box centres per month: x of the Mon, Tue, Thu, Fri columns; y of the week rows from `first_row`
GRID = {
    (2026, 6): ((84.3, 135.9, 237.6, 286.1), 0, (271.6, 310.0, 348.1, 384.8, 422.0)),
    (2026, 7): ((480.8, 532.8, 634.1, 682.5), 0, (262.5, 298.5, 335.6, 373.0, 408.9)),
    (2026, 8): ((86.5, 138.5, 240.0, 289.9), 1, (592.9, 630.8, 668.1, 705.0, 739.5)),
    (2026, 9): ((485.5, 535.7, 641.8, 683.6), 0, (551.0, 587.5, 625.6, 664.5, 706.5)),
    (2026, 10): ((93.5, 145.2, 245.9, 294.4), 0, (856.0, 888.8, 925.2, 961.9, 999.0)),
    (2026, 11): ((488.1, 540.0, 639.9, 694.8), 1, (877.6, 913.5, 951.0, 985.1, 1021.0)),
    (2026, 12): ((965.4, 1017.3, 1115.1, 1169.2), 0, (141.7, 177.2, 214.8, 251.1, 290.2)),
    (2027, 1): ((1353.8, 1405.5, 1506.2, 1554.8), 0, (136.5, 173.9, 212.0, 247.5, 284.2)),
    (2027, 2): ((969.2, 1021.1, 1121.8, 1172.0), 0, (451.1, 490.9, 527.0, 563.0)),
}
PALETTE = {(250, 222, 26): "P", (22, 164, 214): "K", (161, 92, 73): "B", (250, 250, 250): "",
           (234, 204, 204): ""}  # white cell with a red (Sunday) number
TOLERANCE = 45
DAN = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
PROVIDER = {
    "davatelj": "Čistoća Opuzen d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Opuzen"],
    "nazivi": {"M": "Miješani komunalni otpad (zeleni spremnik)", "P": "Plastična ambalaža (žuti spremnik)",
               "K": "Papir i karton (plavi spremnik)", "B": "Biootpad (smeđi spremnik)"},
    "bioNapomena": "Biootpad (smeđi spremnik) odvozi se četvrtkom ili petkom prema popisu ulica.",
    "napomene": [
        "Plan primopredaje otpada vrijedi od 1.6.2026. do 28.2.2027.; raniji plan (do 1.6.2026.) nije upisan.",
        "Spremnici se iznose na javnu površinu do prolaska vozila.",
        "Pomaci odvoza miješanog otpada zbog blagdana nisu objavljeni u planu (objavljuju se kao obavijesti).",
        "Glomazni otpad kupi se petkom, uz najavu na 097 747 75 05.",
        "Općina Zažablje nije obuhvaćena ovim rasporedom (zajednički kontejneri po naseljima).",
    ],
}


def sample(px, cx, cy):
    return np.median(px[int(cy - 9):int(cy + 10), int(cx - 10):int(cx + 11)].reshape(-1, 3), axis=0)


def colour(col, where, problems):
    dist = {k: np.abs(col - k).max() for k in PALETTE}
    key = min(dist, key=dist.get)
    if dist[key] > TOLERANCE:
        problems.append(f"{where}: nepoznata boja {tuple(int(v) for v in col)}")
        return ""
    return PALETTE[key]


def read_calendar(img, problems):
    """{date: code} of the coloured boxes; every Mon/Tue box must be P or K, every Thu/Fri box B, others empty."""
    px = np.asarray(img.convert("RGB")).astype(int)
    out = {}
    for (y, m), (cols, first_row, rows) in GRID.items():
        mon, tue, thu, fri = cols
        xs = [mon, tue, (tue + thu) / 2, thu, fri, 2 * fri - thu, 3 * fri - 2 * thu]
        for day in range(1, calendar.monthrange(y, m)[1] + 1):
            d = date(y, m, day)
            r = (day + calendar.monthrange(y, m)[0] - 1) // 7 - first_row
            if not 0 <= r < len(rows):
                if d.weekday() in (0, 1, 3, 4):
                    problems.append(f"{d}: tjedan izvan izmjerene mreže")
                continue
            code = colour(sample(px, xs[d.weekday()], rows[r]), f"{d}", problems)
            if code and code not in ({0: "PK", 1: "PK", 3: "B", 4: "B"}.get(d.weekday(), "")):
                problems.append(f"{d} ({DAN[d.weekday()]}): neočekivana boja {code}")
            elif code:
                out[d] = code
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Prepisan je samo plan od 1.6.{YEAR}. do 28.2.{YEAR + 1}.")
    page = fetch(PAGE).decode("utf-8", "replace")
    found = {re.sub(r"-\d+x\d+(?=\.jpe?g$)", "", u.rsplit("/", 1)[1]): re.sub(r"-\d+x\d+(?=\.jpe?g$)", "", u)
             for u in re.findall(r'src="(https://cistoca-opuzen\.hr/wp-content/uploads/20\d\d/\d\d/[^"]+\.jpe?g)"', page)}
    found = {n: u for n, u in found.items() if "WhatsApp" in n or n in IMAGES}
    if set(found) != set(IMAGES):
        sys.exit(f"slika se promijenila: na {PAGE} su slike {sorted(found)}, prepisane su {sorted(IMAGES)}")
    bodies = {}
    for name, url in found.items():
        bodies[name] = fetch(url)
        digest = hashlib.sha256(bodies[name]).hexdigest()
        if digest != IMAGES[name]:
            sys.exit(f"slika se promijenila, plan treba ponovno prepisati: {url} (sha256 {digest})")
    problems = []
    boxes = read_calendar(Image.open(io.BytesIO(bodies[CALENDAR_IMAGE])), problems)
    start, end = date(year, *VALID[0]), date(year + 1, *VALID[1])

    # every street: (mixed weekdays, plastic/paper weekdays, bio weekdays)
    streets = sorted({s for lists in (MIXED, SORTED) for v in lists.values() for s in v})
    rules = {}
    for s in streets:
        mixed = tuple(d for d, v in MIXED.items() if s in v)
        rec = tuple(d for d in (0, 1) if s in SORTED[d])
        bio = tuple(d for d in (3, 4) if s in SORTED[d])
        if not mixed or not rec or not bio:
            problems.append(f"{s}: miješani {mixed}, papir/plastika {rec}, bio {bio}")
        rules[s] = (mixed, rec, bio)
    for d, v in list(MIXED.items()) + list(SORTED.items()):
        if len(v) != len(set(v)):
            problems.append(f"popis za {DAN[d]} ima ulicu dvaput")
    order = [s for d in sorted(MIXED) for s in MIXED[d]]
    groups = {}
    for s in sorted(streets, key=order.index):
        groups.setdefault(rules[s], []).append(s)

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, ((mixed, rec, bio), names) in enumerate(groups.items(), 1):
        dates = defaultdict(str)
        d = start
        while d <= end:
            if d.weekday() in mixed:
                dates[d] += "M"
            d += timedelta(days=1)
        for d, code in boxes.items():
            if start <= d <= end and (d.weekday() in rec and code in "PK" or d.weekday() in bio and code == "B"):
                dates[d] += code
        months = defaultdict(lambda: [0, 0])
        for d, c in dates.items():
            months[(d.year, d.month)][0] += "M" in c
            months[(d.year, d.month)][1] += len(c.replace("M", ""))
        for (y, m), (nm, nr) in months.items():
            if not (4 * len(mixed) <= nm <= 5 * len(mixed) and 4 * len(rec + bio) <= nr <= 5 * len(rec + bio)):
                problems.append(f"zona {z} {m}/{y}: miješani {nm}, ostalo {nr}")
        rows = [(d, c, False) for d, c in dates.items()]
        keep = [r for r in podaci.iter_dates(old[str(z)]) if not start <= r[0] <= end] if str(z) in old else []
        years = defaultdict(list)
        for r in keep + rows:
            years[r[0].year].append(r)
        desc = (f"miješani {', '.join(DAN[x] for x in mixed)}; plastika i papir {', '.join(DAN[x] for x in rec)}; "
                f"bio {', '.join(DAN[x] for x in bio)}")
        zone = {"jls": "Opuzen", "podrucje": f"{', '.join(names[:3])}{' …' if len(names) > 3 else ''} ({desc})",
                "ulice": [n.replace(" (do vrtića)", " do vrtića").replace(" (od vrtića)", " od vrtića") for n in names]}
        if len(rec) > 1:
            zone["napomena"] = "Ulica je na planu navedena i za ponedjeljak i za utorak (plastika i papir)."
        zone["raw"] = {str(y): podaci.month_lines(v) for y, v in sorted(years.items())}
        data["zone"][str(z)] = zone
        print(f"Zona {z}: {desc}: {', '.join(names)} – {len(rows)} odvoza")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
