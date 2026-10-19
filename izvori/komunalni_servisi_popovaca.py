"""Popovača: Komunalni servisi Popovača d.o.o. (ksp.hr), one zone per weekday route (Mon-Fri).

    python3 -m izvori.komunalni_servisi_popovaca [--year 2026]

The page "Raspored odvoza komunalnog otpada" shows one image (PLAN_ODVOZA_ZA_<year>._GODINU_page-0001.jpg,
1241x1754): a year calendar where whole weeks are coloured (yellow = plastic, blue = paper and cardboard,
orange = bulky waste in September) and a table of the streets and settlements collected on each weekday.
Every route is collected on its weekday inside the coloured week; mixed waste goes every week on the same
weekday. The coloured weeks are transcribed below and checked against the image pixel by pixel on its
fixed grid; the image's sha256 is pinned, so a new image stops the script until it is read again.
Holidays: the calendar does not shift any date and the company's notices ("Obavijest o odvozu otpada")
for 6.4., 1.5., 4.6. and 22.6.2026 all say collection runs as normal, so dates stay on their weekday.
The script reads the current notices and stops if one announces a change instead.
"""
import argparse
import calendar
import hashlib
import html
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalni-servisi-popovaca"
SITE = "https://www.ksp.hr"
PAGE = SITE + "/informacije/raspored-odvoza-komunalnog-otpada"
NOTICES = SITE + "/obavijesti"
IMAGE = {"year": 2026, "sha256": "e6f8c98538d47510d4d7996861bbb811ca0b878dd98973ba1461414eab06107e"}
# Monday of every coloured week (transcribed from the image, checked by pixel colour below)
WEEKS = {
    "P": ["05.01.", "02.02.", "02.03.", "06.04.", "04.05.", "01.06.", "06.07.", "03.08.", "31.08.", "05.10.",
          "02.11.", "07.12."],
    "K": ["12.01.", "09.02.", "09.03.", "13.04.", "11.05.", "08.06.", "13.07.", "10.08.", "07.09.", "12.10.",
          "09.11.", "14.12."],
    "G": ["14.09.", "21.09."],  # orange: bulky waste (not turned into dates, see napomene)
}
# image grid: x of the Monday column per month (Jan-Jun, Jul-Dec), y of the six week rows, column pitch
GRID_X = ([65, 256, 450, 644, 834, 1024], [65, 256, 450, 644, 837, 1026])
GRID_Y = ([331, 355, 379, 403, 427, 451], [539, 563, 587, 611, 635, 659])
PITCH = 24.7
ROUTES = [  # (weekday, area text as on the image)
    (0, "D. VLAHINIČKA - cijelo područje, POPOVAČA - Zagrebačka, Ul. Hrv. preporoda, Ul. Mije Stuparića, "
        "Ul. Josipa Badalića, Ul. J.Banderiera, Ul. Milke Trnine, Ul. Braće Weiss, Ul. Alojzija Stepinca, "
        "Ul. Nikole Tesle, Ul. Mate Lovraka, Sisačka (iz smjera centra do pruge), Moslavačka, Kolodvorska, "
        "Industrijska, Radnička, Ul. Hrv. branitelja, Ul. bana J. Jelačića, Ravnik, Ul. Stjepana Šajnovića"),
    (1, "M. SLATINA - cijelo područje, POPOVAČA - Ribnjača, Ul. sv.Huberta, Ul. LJ. Vukotinovića, Slatinska, "
        "Ul. Čupora Moslavačkih, Ul. Tome Bakača, Mikulanica, Vinogradska Mikulanica, Ul. Ivana Pergošića, "
        "Ul. dr. Slavka Polaka i odvojci, Vinogradska ulica, Ul. Zorke Sever, Raičevac, Nemčićeva ulica, "
        "Trg g. Erdödyja, Kutinska ulica, Ul. Ruža, Ul. Jorgovana, Ul. Jaglaca, Ul. Garićkih Pavlina, "
        "Ul. Trnovka, Mučnjakova ulica, Gaborčina, Ul. kraljice Vinograda"),
    (2, "G. JELENSKA - cijelo područje, PODBRĐE - cijelo područje, POPOVAČA - Ul. Trnajec, Ul. Krmelovac, "
        "Voćarska ulica, Jelengradska ulica, Veliko brdo, VOLODER - Zagrebačka, Ul. Graševina, Ul. Frankovke, "
        "Ul. Lešćak, Ul. Moslavca, Ul.Gospođica, Ul.Muškata, Ul. Škrleta, Ul. Maksimilijana Juranića, "
        "Kolodvorska, Ul. Donji Krivaj, Ul. Mate Vezmara, Ul. sv. Barbare, Trg sv. Antuna"),
    (3, "VOLODER - Moslavačka ulica, Manceova ulica, Ul. Vjekoslava Kocha, Ul. Veliki Borik, Mali Borik, "
        "Gornji Krivaj, Hrušovljani, Raskrižje, Ul. sv. Josipa, G. GRAČENICA - cijelo područje, "
        "D. GRAČENICA - cijelo područje, CIGLENICA - cijelo područje"),
    (4, "POPOVAČA - Sisačka (od pruge prema Potoku), POTOK - cijelo područje, DONJA JELENSKA - cijelo područje, "
        "STRUŽEC - cijelo područje, OSEKOVO - cijelo područje"),
]
PLACES = {"D. VLAHINIČKA": "Donja Vlahinička", "M. SLATINA": "Moslavačka Slatina", "G. JELENSKA": "Gornja Jelenska",
          "PODBRĐE": "Podbrđe", "POPOVAČA": "Popovača", "VOLODER": "Voloder", "G. GRAČENICA": "Gornja Gračenica",
          "D. GRAČENICA": "Donja Gračenica", "CIGLENICA": "Ciglenica", "POTOK": "Potok",
          "DONJA JELENSKA": "Donja Jelenska", "STRUŽEC": "Stružec", "OSEKOVO": "Osekovo"}
DAN = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak"]
KEY = ["pon", "uto", "sri", "čet", "pet"]
PROVIDER = {
    "davatelj": "Komunalni servisi Popovača d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Sisačko-moslavačka",
    "jls": ["Popovača"],
    "nazivi": {"P": "Plastika (žuta kanta)", "K": "Papir i karton (plava kanta)",
               "M": "Miješani komunalni otpad (zelena kanta)"},
    "napomene": [
        "Miješani komunalni otpad odvozi se jednom tjedno na dan ulice; plastika i papir jednom mjesečno na dan "
        "ulice u tjednu obojenom žuto (plastika) ili plavo (papir) u kalendaru.",
        "Kante treba iznijeti najkasnije do 7:00 sati na dan odvoza.",
        "Blagdani: kalendar ne pomiče odvoze; prema obavijestima tvrtke (6.4., 1.5., 4.6. i 22.6.2026.) odvoz na "
        "blagdan obavlja se po redovnom rasporedu. Za ostale blagdane pratite obavijesti na ksp.hr.",
        "Glomazni otpad: u rujnu, u tjednima 14.–18.9. i 21.–25.9. (narančasto u kalendaru). Kalendar ne navodi "
        "koji od ta dva tjedna vrijedi za koju ulicu, pa ti datumi nisu upisani.",
        "Biootpad se spominje u obavijestima, ali njegov raspored nije objavljen u kalendaru.",
        "Komunalni servisi Popovača, Kutinska ulica 12, tel. 044 353 592, info@ksp.hr.",
    ],
}


def dm(text, year):
    d, m = text.strip(".").split(".")
    return date(year, int(m), int(d))


def parts(text):
    """Area text -> [(settlement, [streets] or [] for the whole settlement)]."""
    out = []
    for part in (p.strip() for p in text.split(",")):
        m = re.match(r"([A-ZČĆŽŠĐ. ]{4,}?)\s+-\s+(.*)", part)
        if m:
            out.append((PLACES[m.group(1).strip()], []))
            part = m.group(2)
        if part and part != "cijelo područje":
            out[-1][1].append(part)
    return out


def podrucje(wd, groups):
    names = [p + (f" ({', '.join(s[:2])}{', …' if len(s) > 2 else ''})" if s else "") for p, s in groups]
    return f"{DAN[wd]} – " + ", ".join(names)


def check_image(path, year, problems):
    """Every day cell of the calendar must be yellow / blue / orange exactly in the weeks of WEEKS, else white."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    cls = {"P": (r > 200) & (g > 200) & (b < 120), "K": (r < 120) & (g > 120) & (g < 215) & (b > 190),
           "G": (r > 200) & (g > 100) & (g < 190) & (b < 90)}
    want = {}
    for code, mondays in WEEKS.items():
        for mon in mondays:
            for i in range(5):
                want[dm(mon, year) + timedelta(days=i)] = code
    for month in range(1, 13):
        half, i = divmod(month - 1, 6)
        first, days = date(year, month, 1).weekday(), calendar.monthrange(year, month)[1]
        for day in range(1, days + 1):
            row, col = divmod(first + day - 1, 7)
            cx, cy = round(GRID_X[half][i] + col * PITCH), GRID_Y[half][row]
            f = {k: v[cy - 9:cy + 10, cx - 11:cx + 12].mean() for k, v in cls.items()}
            got = [k for k, v in f.items() if v > 0.3]
            unclear = [k for k, v in f.items() if 0.05 < v <= 0.3]
            d = date(year, month, day)
            if got != ([want[d]] if d in want else []) or (unclear and not got):  # red digits blur to orange
                problems.append(f"slika {d:%d.%m.}: boja {got or '-'} ({unclear}), očekivano {want.get(d, '-')}")


def holiday_notices(year, problems):
    """Read the notices about collection on holidays; a notice that is not 'po redovnom rasporedu' is a problem."""
    page = fetch(NOTICES).decode("utf-8", "replace")
    links = dict.fromkeys(re.findall(r'href="(/obavijesti/obavijest[^"]*)"', page))
    for link in links:
        text = fetch(SITE + link).decode("utf-8", "replace")
        m = re.search(r'itemprop="articleBody"(.*?)</div>', text, re.S)
        body = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1) if m else "")).split())
        if not re.search(r"odvoz\w*\s+(miješan|otpad|plastik|papir)", body, re.I) or str(year) not in body:
            continue
        if "redovnom rasporedu" in body:
            print(f"Obavijest {link}: {body[:110]}…")
        else:
            problems.append(f"obavijest o promjeni odvoza, provjeriti i ugraditi: {SITE + link}: {body[:200]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    page = fetch(PAGE).decode("utf-8", "replace")
    m = re.search(rf'src="([^"]*PLAN_ODVOZA_ZA_{year}\._GODINU[^"]*\.jpg)"', page)
    if not m or year != IMAGE["year"]:
        sys.exit(f"Nema provjerenog plana za {year} na {PAGE}. Ništa nije upisano.")
    url = html.unescape(m.group(1))
    url = url if url.startswith("http") else SITE + "/" + url.lstrip("/")
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "plan.jpg"
        fetch(url, img)
        sha = hashlib.sha256(img.read_bytes()).hexdigest()
        if sha != IMAGE["sha256"]:
            sys.exit(f"slika se promijenila, prepisati ponovno: {url} (sha256 {sha}). Ništa nije upisano.")
        check_image(img, year, problems)
    holiday_notices(year, problems)

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, (wd, text) in enumerate(ROUTES, 1):
        rows = {d: "M" for d in pravila.tjedno(year, KEY[wd])}
        for code in "PK":
            for mon in WEEKS[code]:
                d = dm(mon, year) + timedelta(days=wd)
                rows[d] = rows.get(d, "") + code
        for d, c in rows.items():
            if d.weekday() != wd or d.year != year:
                problems.append(f"zona {z}: {d} nije {DAN[wd].lower()}")
        cnt = Counter((d.month, c) for d, codes in rows.items() for c in codes)
        for month in range(1, 13):
            if not 4 <= cnt[month, "M"] <= 5:
                problems.append(f"zona {z}: {cnt[month, 'M']}x M u mjesecu {month}")
            for code in "PK":
                if not 0 <= cnt[month, code] <= 2:
                    problems.append(f"zona {z}: {cnt[month, code]}x {code} u mjesecu {month}")
        for code in "PK":
            if sum(n for (m_, c), n in cnt.items() if c == code) != 12:
                problems.append(f"zona {z}: {code} nije 12 puta godišnje")
        groups = parts(text)
        names = list(dict.fromkeys(n for p, streets in groups for n in [p] + streets))
        data["zone"][str(z)] = {
            "jls": "Popovača",
            "podrucje": podrucje(wd, groups),
            "opis": text, "ulice": names,
            "raw": {**old.get(str(z), {}).get("raw", {}),
                    str(year): podaci.month_lines([(d, c, False) for d, c in rows.items()])},
        }
        total = Counter(c for codes in rows.values() for c in codes)
        print(f"Zona {z} ({DAN[wd]}): " + ", ".join(f"{c} {total[c]}" for c in "MPK") + f", ulica/naselja {len(names)}")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
