"""Okučani: Sloboština d.o.o. (slobostina.com), paper and plastic calendar for the whole municipality.

    python3 -m izvori.slobostina [--year 2026]

The article "Raspored odvoza otpada <year>. godine" on slobostina.com shows one scanned leaflet
(/images/Raspored_odvoza_otpada.jpg, the same file name every year) with six paper dates and six plastic dates,
all Mondays, alternating every other month. The dates were transcribed by hand into DATES and are kept with the
image's sha256 (a changed image stops the script). Mixed waste is not published. The dates are explicit, so no
holiday rule is applied; the script checks that none of them falls on a public holiday.
"""
import argparse
import hashlib
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "slobostina"
SITE = "http://www.slobostina.com"
# year -> (image sha256, paper dates, plastic dates), read from the leaflet
DATES = {2026: ("a4a51589e32a2d79492523aec60ce656d41bc88637f4f9d066fa434ddfcc82d0",
                "12.01. 02.03. 04.05. 06.07. 07.09. 02.11.",
                "02.02. 13.04. 01.06. 03.08. 05.10. 07.12.")}
PROVIDER = {
    "davatelj": "Sloboština d.o.o.",
    "web": SITE,
    "zupanija": "Brodsko-posavska",
    "jls": ["Okučani"],
    "nazivi": {"P": "Plastika i plastična ambalaža (žuta kanta)", "K": "Papir i papirna ambalaža (plava kanta)"},
    "napomene": [
        "Objavljen je samo kalendar odvoza papira i plastike (svaki drugi mjesec, ponedjeljkom); raspored odvoza "
        "miješanog komunalnog otpada nije objavljen – informacije: Sloboština d.o.o., 035/371-144, "
        "slobostina@sb.t-com.hr.",
        "Papir, plastiku i druge korisne sirovine moguće je besplatno predati u reciklažnom dvorištu Općine Okučani "
        "(Industrijska zona).",
    ],
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    if year not in DATES:
        sys.exit(f"Za {year}. nema prepisanog kalendara u skripti (DATES).")
    home = fetch(SITE + "/").decode("utf-8", "replace")
    article = re.search(rf'href="([^"]*raspored-odvoza-otpada-{year}[^"]*)"', home)
    if not article:
        sys.exit(f"Na {SITE} nema članka „Raspored odvoza otpada {year}. godine”.")
    page_url = article.group(1) if article.group(1).startswith("http") else SITE + article.group(1)
    page = fetch(page_url).decode("utf-8", "replace")
    img = re.search(r'src="([^"]*/images/[^"]*[Rr]aspored[^"]*\.jpe?g)"', page)
    if not img:
        sys.exit(f"Na {page_url} nema slike rasporeda.")
    img_url = img.group(1) if img.group(1).startswith("http") else SITE + "/" + img.group(1).lstrip("/")
    sha_want, paper, plastic = DATES[year]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raspored.jpg"
        meta = {}
        fetch(img_url, path, meta=meta)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if sha != sha_want:
        sys.exit(f"slika se promijenila, prepisati ponovno: {img_url} (sha256 {sha}, "
                 f"Last-Modified {meta.get('Last-Modified', '?')})")
    rows = [(date(year, int(t[3:5]), int(t[:2])), code, False)
            for text, code in ((paper, "K"), (plastic, "P")) for t in text.split()]
    problems = []
    hol = set(pravila.blagdani(year))
    for d, code, _ in rows:
        if d.weekday() != 0:
            problems.append(f"{code} {d} nije ponedjeljak")
        if d in hol:
            problems.append(f"{code} {d} je blagdan")
    months = sorted(d.month for d, _, _ in rows)
    if months != list(range(1, 13)) or len({d for d, _, _ in rows}) != 12:
        problems.append(f"očekivan jedan odvoz mjesečno (papir i plastika naizmjence): {months}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    out = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(out)["zone"].get("1", {}).get("raw", {}) if out.exists() else {}
    data = {**PROVIDER, "izvor": page_url, "zone": {"1": {
        "jls": "Okučani",
        "podrucje": "Cijela općina Okučani – papir i plastika ponedjeljkom (miješani otpad nije objavljen)",
        "ulice": ["Okučani"],
        "napomena": "Kalendar vrijedi za cijelo područje Općine Okučani; odvoz miješanog otpada nije u kalendaru.",
        "raw": {**{y: v for y, v in old.items() if y != str(year)}, str(year): podaci.month_lines(rows)},
    }}}
    podaci.save(SLUG, data)
    print(f"Papir: {paper}; plastika: {plastic}")
    print(f"Upisano: {out.relative_to(podaci.ROOT)} (1 zona)")


if __name__ == "__main__":
    main()
