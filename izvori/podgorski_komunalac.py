"""Podgorski komunalac d.o.o.: Općina Podgora (Podgora, Drašnice, Igrane, Živogošće), mixed and recyclable waste.

    python3 -m izvori.podgorski_komunalac [--year 2026]

Mixed waste: the page "Kalendar odvoza otpada" draws a month calendar from today onwards; each day cell
has a class: "datumtamna" = the big truck (households in Podgora; Igrane, Drašnice and Živogošće are
collected the day after), "datumsvijetla" = the small truck (narrow streets in all settlements),
"datumbijela" = no collection. Only filled months are used (a month with no truck day is not published
yet); a big-truck day whose next day is a day without collection is not moved anywhere and is printed.
Days that are no longer shown (before today) are kept from podaci/<slug>.json, so running this regularly
builds up the year. Recyclables: the year image "Kalendar odvoza reciklabilnog otpada" (two columns,
Podgora+Drašnice and Igrane+Živogošće, "SVA NASELJA" in winter) is transcribed below and guarded by its
sha256. Holiday shifts are part of both calendars (recyclable dates off the usual weekday are marked as moved).
"""
import argparse
import hashlib
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "podgorski-komunalac"
SITE = "https://podgorski-komunalac.hr"
PAGE = SITE + "/kalendar-odvoza-otpada"
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
YEAR = 2026
REC_SHA256 = "d5804656ac5de9f8671e9b71276efb3acd1af373cb1a99467fad8a2186405543"
# kalendar-odvoza-reciklabilnog-otpada-za-2026-godinu-902.jpg: month -> (Podgora+Drašnice, Igrane+Živogošće)
RECIKLABILNI = {
    1: ("07.01. 21.01.", "07.01. 21.01."),  # SVA NASELJA
    2: ("04.02. 18.02.", "04.02. 18.02."),  # SVA NASELJA
    3: ("04.03. 18.03.", "05.03. 19.03."),
    4: ("01.04. 15.04. 29.04", "02.04. 16.04. 30.04."),
    5: ("13.05. 27.05.", "14.05. 28.05."),
    6: ("03.06. 10.06. 17.06. 24.06.", "05.06. 11.06. 18.06. 25.06."),
    7: ("01.07. 08.07. 15.07. 22.07. 29.07.", "02.07. 09.07. 16.07. 23.07. 30.07."),
    8: ("05.08. 12.08. 19.08. 26.08.", "06.08. 13.08. 20.08. 27.08."),
    9: ("02.09. 09.09. 16.09. 23.09. 30.09.", "03.09. 10.09. 17.09. 24.09."),
    10: ("07.10. 14.10. 21.10. 28.10.", "01.10. 08.10. 15.10. 22.10. 29.10."),
    11: ("04.11. 19.11.", "04.11. 19.11."),  # SVA NASELJA
    12: ("02.12. 16.12.", "02.12. 16.12."),  # SVA NASELJA
}
ZONES = {  # zone: (podrucje, settlements, truck, recyclables column)
    "1": ("Podgora – veliki kamion", ["Podgora"], "tamna", 0),
    "2": ("Drašnice – veliki kamion (dan poslije Podgore)", ["Drašnice"], "tamna+1", 0),
    "3": ("Igrane, Živogošće – veliki kamion (dan poslije Podgore)", ["Igrane", "Živogošće"], "tamna+1", 1),
    "4": ("Podgora, Drašnice – uže ulice (mali kamion)", ["Podgora", "Drašnice"], "svijetla", 0),
    "5": ("Igrane, Živogošće – uže ulice (mali kamion)", ["Igrane", "Živogošće"], "svijetla", 1),
}
PROVIDER = {
    "davatelj": "Podgorski komunalac d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Podgora"],
    "nazivi": {"P": "Reciklabilni otpad"},
    "napomene": [
        "Kalendar miješanog otpada objavljuje se na webu od tekućeg dana unaprijed (popunjen obično do kraja "
        "mjeseca); upisani su dani koji su bili objavljeni kad je skripta pokrenuta.",
        "Veliki kamion: raspored vrijedi za Podgoru, a u Igranima, Drašnicama i Živogošću miješani otpad "
        "preuzima se dan poslije. Mali kamion skuplja u užim ulicama svih naselja.",
        "Od 15.06. kućanstva se prazne svaki drugi dan, gospodarstva svaki dan.",
        "Reciklabilni otpad: dva puta mjesečno zimi (sva naselja zajedno), ljeti svaki tjedan.",
        "Kontakt: 099 6047771, podgorski.komunalac@podgora.hr.",
    ],
}


def html_months(page, year, problems):
    """{date: 'tamna'/'svijetla'/'bijela'} for the days shown, from the swiper slides (only filled months)."""
    out = {}
    for slide in re.split(r'<div class="swiper-slide">', page)[1:]:
        title = re.search(r">\s*(\w+) (\d{4})\s*</div>", slide)
        if not title or title.group(1).lower() not in MONTHS:
            continue
        month, y = MONTHS.index(title.group(1).lower()) + 1, int(title.group(2))
        cells = re.findall(r'<div class="datum1( datum\w+)?">(?:<div class="datum2[^"]*">(\d+)</div>)?</div>', slide)
        days = {}
        for i, (cls, day) in enumerate(cells):
            if not day:
                continue
            d = date(y, month, int(day))
            if i % 7 != d.weekday():
                problems.append(f"{d}: u stupcu {i % 7}, a dan u tjednu je {d.weekday()}")
            kind = cls.strip().replace("datum", "")
            if kind not in ("tamna", "svijetla", "bijela"):
                problems.append(f"{d}: nepoznata klasa {cls!r}")
            days[d] = kind
        if y == year and any(k != "bijela" for k in days.values()):
            out.update(days)
        elif days:
            print(f"{title.group(1)} {y}: još nema odvoza u kalendaru – preskočeno")
    return out


def recycling(year, problems):
    """[(date, column, moved)] from the transcription; Wednesdays (Podgora) / Thursdays (Igrane) except holiday weeks."""
    hol = set(pravila.blagdani(year))
    out = []
    for month, columns in RECIKLABILNI.items():
        for col, text in enumerate(columns):
            for d, m in re.findall(r"(\d\d)\.(\d\d)\.?", text):
                dt = date(year, int(m), int(d))
                if dt.month != month:
                    problems.append(f"reciklabilni {dt} pod mjesecom {month}")
                usual = 2 if col == 0 or month in (1, 2, 11, 12) else 3
                monday = dt - timedelta(days=dt.weekday())
                moved = dt.weekday() != usual
                if moved and not any(monday <= h <= monday + timedelta(days=6) for h in hol):
                    problems.append(f"reciklabilni {dt:%d.%m.} nije {['srijeda', 'četvrtak'][usual - 2]}")
                out.append((dt, col, moved))
    per_month = Counter((m, c) for d, c, _ in out for m in [d.month])
    for (m, c), n in per_month.items():
        if not 2 <= n <= 5:
            problems.append(f"reciklabilni {m}. mjesec, stupac {c}: {n} datuma")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Prepisan je samo kalendar reciklabilnog otpada za {YEAR}.")
    problems = []
    home = fetch(SITE + "/").decode("utf-8", "replace")
    art = re.search(rf'href="(https://podgorski-komunalac\.hr/kalendar-odvoza-reciklabilnog-otpada-za-{year}-godinu-\d+)"', home)
    if not art:
        sys.exit(f"Na naslovnici nema kalendara reciklabilnog otpada za {year}.")
    img = re.search(r'(https://podgorski-komunalac\.hr/dokumenti/kalendar-odvoza-reciklabilnog[^"]+\.jpg)',
                    fetch(art.group(1)).decode("utf-8", "replace"))
    if not img:
        sys.exit(f"{art.group(1)}: nema slike kalendara.")
    digest = hashlib.sha256(fetch(img.group(1))).hexdigest()
    if digest != REC_SHA256:
        sys.exit(f"slika se promijenila, kalendar reciklabilnog otpada treba ponovno prepisati: {img.group(1)} "
                 f"(sha256 {digest})")
    rec = recycling(year, problems)
    shown = html_months(fetch(PAGE).decode("utf-8", "replace"), year, problems)
    if not shown:
        problems.append("kalendar miješanog otpada nema nijedan popunjen dan")
    big = sorted(d for d, k in shown.items() if k == "tamna")
    small = sorted(d for d, k in shown.items() if k == "svijetla")
    if set(big) & {d + timedelta(days=1) for d in big}:
        print("PAŽNJA: veliki kamion dva dana zaredom")
    next_day = []
    for d in big:
        n = d + timedelta(days=1)
        if shown.get(n) == "bijela":
            print(f"Veliki kamion {d:%d.%m.}: dan poslije ({n:%d.%m.}) je dan bez odvoza – za Igrane, Drašnice i "
                  "Živogošće nije upisan")
        elif n in shown or n.month != d.month:
            next_day.append(n)
    window = (min(shown), max(shown)) if shown else None

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, (podrucje, places, truck, col) in ZONES.items():
        mixed = {"tamna": big, "tamna+1": next_day, "svijetla": small}[truck]
        rows = defaultdict(lambda: ["", False])
        for d in mixed:
            rows[d][0] += "M"
        for d, c, moved in rec:
            if c == col:
                rows[d][0] += "P"
                rows[d][1] |= moved
        # keep old mixed waste dates outside the shown days; the year's recyclables come from the image
        keep = [] if z not in old else [(d, c.replace("P", ""), False) for d, c, _ in podaci.iter_dates(old[z], year)
                                        if "M" in c and not (window and window[0] <= d <= window[1])]
        for d, c, _ in keep:
            rows[d][0] = "M" + rows[d][0].replace("M", "")
        n_month = Counter(d.month for d, (c, _) in rows.items() if "M" in c)
        for m, n in n_month.items():
            if n > 31:
                problems.append(f"zona {z}: {n} odvoza u {m}. mjesecu")
        lines = [(d, c, mv) for d, (c, mv) in rows.items()]
        data["zone"][z] = {
            "jls": "Podgora", "podrucje": podrucje, "ulice": places,
            "raw": {**(old.get(z, {}).get("raw", {})), str(year): podaci.month_lines(lines)},
        }
        print(f"Zona {z} ({podrucje}): miješani {len(mixed)} (+{len(keep)} ranije upisanih), reciklabilni "
              f"{sum(1 for _, c, _ in rec if c == col)}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} (miješani od {window[0]:%d.%m.} do {window[1]:%d.%m.%Y})")


if __name__ == "__main__":
    main()
