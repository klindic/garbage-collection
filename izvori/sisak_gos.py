"""Sisak and surroundings: Gospodarenje otpadom Sisak d.o.o. (gos.hr), one PDF per zone.

    python3 -m izvori.sisak_gos [--year 2026]

Finds the zone PDFs on the year's schedule page, reads dates and bin types with extract_pdf.py
(page 1) and the streets/settlements of each zone (page 2), and writes podaci/sisak-gos.json.
Other years already in the file are kept.
"""
import argparse
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import podaci
from extract_pdf import extract

SLUG = "sisak-gos"
PAGE = "https://gos.hr/raspored-sakupljanja-otpada-{year}/"
UA = {"User-Agent": "Mozilla/5.0 (odvoz-otpada; +https://klindic.github.io/garbage-collection/)"}

# Written by hand from the PDFs and the schedule page; the dates come from the PDFs.
ZONES = {
    "1": dict(jls="Sisak", podrucje="Zeleni Brijeg, Segestica, Herbos"),
    "2": dict(jls="Martinska Ves", podrucje="Općina Martinska Ves"),
    "3": dict(jls="Sisak", podrucje="Naselja prema Zagrebu (Odra, Stupno, Žabno, Greda…)",
              bezBioU="naseljima Stara Drenčina, Vurot i Jazvenik"),
    "4": dict(jls="Lekenik", podrucje="Općina Lekenik"),
    "5": dict(jls="Sisak", podrucje="Centar, Vrbina, Tomčev put", pilotOd="2026-10-01",
              napomena="Od listopada traje testni projekt: plastika, staklo i metal odvoze se 2x mjesečno."),
    "6": dict(jls="Sisak", podrucje="Prečki Sisak, Viktorovac, Brzaj, Podjarak, Caprag"),
    "7": dict(jls="Sisak", podrucje="Buićevo naselje, Capraške poljane, Crnac, naselja prema Sunji",
              bezBioU="naseljima Klobučak, Madžari, Letovanci i Staro Selo te na Capraškim poljanama"),
    "8": dict(jls="Sunja", podrucje="Općina Sunja (i bivša Zona 11)",
              napomena="Od 1.9.2026. zona uključuje i Pobrđane, Čapljane, Jasenovčane, Papiće, Kostreše Šaške, "
                       "Timarce, Slovince, Šaš, Donju i Gornju Letinu, Radonju Luku, Malu Paukovu, Kladare, "
                       "Sjeverovac, Malu i Veliku Gradusu, Vukoševac i Blinjsku Gredu."),
    "9": dict(jls="Sisak", podrucje="Galdovo, Hrastelnica, Palanjek, Novo Selo Palanječko",
              bezBioU="naselju Palanjek i Ulici Put Palanjek"),
    "10": dict(jls="Sisak", podrucje="Budaševo, Topolovac, naselja Donje Posavine",
               bezBioU="naseljima Donje Posavine"),
}
SKIP = {"11": "od 1. 9. 2026. naselja Zone 11 pripadaju Zoni 8"}
PROVIDER = {
    "davatelj": "Gospodarenje otpadom Sisak d.o.o.",
    "web": "https://gos.hr",
    "zupanija": "Sisačko-moslavačka",
    "jls": ["Sisak", "Martinska Ves", "Lekenik", "Sunja"],
    "nazivi": {"P": "Plastika, staklo i metal"},
    "bioNapomena": "Biootpad samo za korisnike koji su odabrali predaju biootpada u spremnicima.",
    "napomene": [
        "Spremnike iznijeti na javnu površinu najkasnije do 07:00.",
        'Reciklažna dvorišta: "Sisak Stari" (Kralja Zvonimira 7B) i "Novi Sisak" (Capraška 4), '
        "pon-pet 08-20, sub 08-13.",
    ],
}


def fetch(url, dest=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        body = r.read()
    if dest:
        Path(dest).write_bytes(body)
    return body


def zone_pdfs(year):
    """{zone: pdf url} from the schedule page; the newest file wins when a zone has several."""
    html = fetch(PAGE.format(year=year)).decode("utf-8", "replace")
    found = {}
    for url in sorted(set(re.findall(r'href="(https?://[^"]+\.pdf)"', html))):
        name = url.rsplit("/", 1)[1]
        m = re.search(r"Zona[_-]0*(\d+)", name, re.I)
        if m and str(year) in name:
            found[m.group(1)] = url  # sorted URLs: later upload folders (YYYY/MM) come last
    return found


def area_text(pdf, zone):
    """Page 2: the text after the last 'ZONA N' heading up to 'OPĆE INFORMACIJE', on one line."""
    text = subprocess.run(["pdftotext", "-f", "2", "-l", "2", str(pdf), "-"],
                          capture_output=True, text=True, check=True).stdout
    before = text.split("OPĆE INFORMACIJE")[0]
    parts = re.split(rf"^ZONA {zone}[ \t]*$", before, flags=re.M)
    return " ".join(parts[-1].split()) if len(parts) > 1 else ""


def streets(opis):
    """'AREA (street, street (sub, sub)), AREA2 (...)' -> flat list of streets and settlements."""
    items, depth, cur = [], 0, ""
    for ch in opis + ",":
        if ch == "(":
            depth += 1
            if depth == 1:
                cur = ""  # text before the top-level bracket is an area heading
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                items.append(cur)
                cur = ""
                continue
        if ch == "," and depth <= 1:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    out = []
    for it in items:
        it = re.sub(r"\(.*", "", it)  # drop nested lists like "Marijana Celjaka P/N (…"
        it = re.sub(r"^.*?(?:\*+Od [\d.]+\s*-|:)\s*", "", it.strip())  # "***Od 1.9.2026. - X", "GALDOVO: X"
        it = it.strip(" .;*")
        if not it or it.isupper() and len(it.split()) > 3:
            continue
        out.append(it.title() if it.isupper() else it)
    return list(dict.fromkeys(out))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    args = ap.parse_args(argv)
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE.format(year=args.year)
    pdfs = zone_pdfs(args.year)
    if not pdfs:
        sys.exit(f"Nema PDF-ova za {args.year} na {data['izvor']}")
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for zone, url in sorted(pdfs.items(), key=lambda kv: int(kv[0])):
            if zone in SKIP:
                print(f"Zona {zone}: preskočena ({SKIP[zone]})")
                continue
            if zone not in ZONES:
                print(f"Zona {zone}: NOVA, dodaj je u ZONES u {__file__}")
                ok = False
                continue
            pdf = Path(tmp) / f"zona{zone}.pdf"
            fetch(url, pdf)
            rows, problems = extract(pdf, args.year)
            print(f"Zona {zone}: {len(rows)} odvoza, {url.rsplit('/', 1)[1]}")
            for p in problems:
                print(f"   PROBLEM {p}")
            if problems:
                ok = False
                continue
            opis = area_text(pdf, zone)
            old = data["zone"].get(zone, {})
            data["zone"][zone] = {**ZONES[zone], "opis": opis, "ulice": streets(opis),
                                  "raw": {**old.get("raw", {}), str(args.year): podaci.month_lines(rows)}}
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"] = dict(sorted(data["zone"].items(), key=lambda kv: int(kv[0])))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
