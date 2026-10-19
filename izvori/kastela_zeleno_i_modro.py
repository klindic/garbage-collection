"""Kaštela: Zeleno i modro d.o.o. (zelenoimodro.hr), one colour year calendar per area (4 areas).

    python3 -m izvori.kastela_zeleno_i_modro [--year 2026]

The "Plan primopredaje otpada" PDFs are linked on the notice page ("Obavijest o načinu korištenja
javne usluge"). Each is one page with twelve month grids (PON UTO SRI ČET PET SUB NED, two months per
row) and a coloured cell behind every collection day: green mixed waste, blue paper, yellow plastic,
brown biowaste (Radun i Rudine only), red a non-working day without collection. kalendar_boje reads
the grids (every day in its weekday column, every day of the year exactly once, unknown colours are
errors); this script also checks the legend colours against their labels, that every coloured cell
holds a day number, and that the counts per type are plausible. Otherwise nothing is written.
"""
import argparse
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import pdfplumber

import podaci
from izvori.sisak_gos import UA
from kalendar_boje import colour, lookup, read_page

SLUG = "kastela-zeleno-i-modro"
SITE = "https://zelenoimodro.hr"
PAGE = SITE + "/gospodarenje-otpadom/obavijest-o-nacinu-koristenja-javne-usluge/"
PALETTE = {(0.0, 0.69, 0.314): "M", (0.0, 0.69, 0.941): "K", (1.0, 1.0, 0.0): "P", (0.659, 0.424, 0.169): "B",
           (1.0, 0.0, 0.0): None}  # red: non-working day, no collection
IGNORE = ((1.0, 1.0, 1.0), (0.875, 0.882, 0.91))  # white, grey weekday header row
LEGEND = {"Miješani otpad": "M", "Papir i karton": "K", "Plastika": "P", "Biootpad": "B"}
PER_YEAR = {"M": (45, 110), "K": (20, 30), "P": (20, 30), "B": (30, 60)}
# file name part -> zone; the title of the PDF must name the area (the word in "title")
ZONES = {
    "kastel-sucurac": dict(zona="1", title="Sućurac", podrucje="Kaštel Sućurac", ulice=["Kaštel Sućurac"]),
    "kastel-gomilica-kambelovac-luksic": dict(
        zona="2", title="Kambelovac", podrucje="Kaštel Gomilica, Kaštel Kambelovac i Kaštel Lukšić",
        ulice=["Kaštel Gomilica", "Kaštel Kambelovac", "Kaštel Lukšić"]),
    "kastel-stari-novi-stafilic": dict(
        zona="3", title="Štafilić", podrucje="Kaštel Stari, Kaštel Novi i Kaštel Štafilić",
        ulice=["Kaštel Stari", "Kaštel Novi", "Kaštel Štafilić"]),
    "radun-i-rudine": dict(zona="4", title="RUDINE", podrucje="Radun i Rudine", ulice=["Radun", "Rudine"],
                           napomena="Biootpad se odvozi samo na području Raduna i Rudina (od lipnja 2025.)."),
}
PROVIDER = {
    "davatelj": "Zeleno i modro d.o.o.",
    "web": SITE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Kaštela"],
    "nazivi": {"P": "Plastika"},
    "bioNapomena": "Biootpad se odvozi samo u Radunu i Rudinama.",
    "napomene": [
        "Crveno označeni neradni dani u kalendaru (npr. 1.1. i 25.12.) su bez odvoza.",
        "Otpadni tekstil odlaže se u narančaste spremnike na javnim površinama, problematični otpad u reciklažno "
        "dvorište (Kaštel Sućurac, Rudine - Put Žabic 16).",
        "Glomazni otpad iz kućanstava odvozi se na zahtjev (obrazac na zelenoimodro.hr).",
        "Rad sa strankama: ponedjeljak - petak 08:00 - 14:00.",
    ],
}

_last = [0.0]


def fetch(url, dest=None, tries=4):
    """GET with the project User-Agent, at most 2 requests a second, retries with backoff."""
    for attempt in range(tries):
        wait = 0.5 - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                body = r.read()
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == tries - 1 or getattr(e, "code", 500) in (400, 403, 404, 410):
                raise
            time.sleep(2 ** (attempt + 1))
    if dest:
        Path(dest).write_bytes(body)
    return body


def area_pdfs(year):
    """{file name part: url} of the year's calendars linked on the notice page (and the home page)."""
    found = {}
    for url in (PAGE, SITE + "/"):
        html = fetch(url).decode("utf-8", "replace")
        for link in sorted(set(re.findall(r'href="(https?://[^"]+\.pdf)"', html))):
            m = re.search(rf"/(?:obavijest-o-nacinu-prikupljanja|plan-primopredaje[a-z-]*?)-(.+)-{year}(?:-\d+)?\.pdf$",
                          link)
            if m:
                found[m.group(1)] = link  # sorted: later uploads (YYYY/MM, -1 ...) come last
    return found


def check_page(page, year, found, zone):
    """Extra checks: title, legend colours, coloured cells without a day number, counts per type."""
    problems = []
    words = page.extract_words()
    title = " ".join(w["text"] for w in words if w["top"] < 50)
    if f"{year}." not in title or zone["title"] not in title:
        problems.append(f"title {title!r} does not name {zone['title']} and {year}.")
    shapes = [s for s in page.rects + page.curves if s.get("fill") and colour(s)]
    for label, code in LEGEND.items():
        first = label.split()[0]
        for w in (w for w in words if w["text"] == first and w["top"] < 60):
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            under = [s for s in shapes if s["x0"] <= cx <= s["x1"] and s["top"] <= cy <= s["bottom"]
                     and colour(s) not in IGNORE]
            got = {lookup(colour(s), PALETTE) for s in under}
            if got != {code}:
                problems.append(f"legend '{label}' has colour {got}, expected {code}")
    days = [w for w in words if w["text"].isdigit()]
    for s in shapes:
        if not (25 <= s["x1"] - s["x0"] <= 45 and 10 <= s["bottom"] - s["top"] <= 20):
            continue  # day cells are about 35 x 15 pt
        if colour(s) in IGNORE or colour(s) == (0.0, 0.0, 0.0):
            continue
        if not any(s["x0"] <= (w["x0"] + w["x1"]) / 2 <= s["x1"]
                   and s["top"] <= (w["top"] + w["bottom"]) / 2 <= s["bottom"] for w in days):
            problems.append(f"coloured cell {colour(s)} at {s['x0']:.0f},{s['top']:.0f} without a day number")
    counts = Counter(t for c in found.values() for t in c)
    for t, n in counts.items():
        lo, hi = PER_YEAR[t]
        if not lo <= n <= hi:
            problems.append(f"{n}x {t} in {year} is implausible")
    for t in "MKP":
        if not counts.get(t):
            problems.append(f"no {t} collections")
    return problems, counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    pdfs = area_pdfs(year)
    if not pdfs:
        sys.exit(f"Nema kalendara za {year} na {PAGE}")
    ok = True
    for name in ZONES:
        if name not in pdfs:
            print(f"{ZONES[name]['podrucje']}: nema PDF-a za {year}")
            ok = False
    with tempfile.TemporaryDirectory() as tmp:
        for name, url in sorted(pdfs.items(), key=lambda kv: ZONES.get(kv[0], {}).get("zona", kv[0])):
            if name not in ZONES:
                print(f"{name}: NOVO područje, dodaj ga u ZONES u {__file__}")
                ok = False
                continue
            zone = ZONES[name]
            pdf_path = Path(tmp) / f"{name}.pdf"
            fetch(url, pdf_path)
            pages = pdfplumber.open(pdf_path).pages
            if len(pages) != 1:
                print(f"Zona {zone['zona']}: {len(pages)} stranica, očekivana 1")
                ok = False
                continue
            found, problems = read_page(pages[0], year, PALETTE, ignore=IGNORE)
            if not problems:
                more, counts = check_page(pages[0], year, found, zone)
                problems += more
                holidays = [d for d in red_days(pages[0], year) if d not in found]
                print(f"Zona {zone['zona']} ({zone['podrucje']}): {len(found)} odvoza {dict(sorted(counts.items()))}, "
                      f"crveno (bez odvoza): {', '.join(f'{d:%d.%m.}' for d in holidays) or '-'}; "
                      f"{url.rsplit('/', 1)[1]}")
            for p in problems[:15]:
                print(f"   PROBLEM {p}")
            if problems:
                print(f"Zona {zone['zona']} ({zone['podrucje']}): PROBLEM")
                ok = False
                continue
            old = data["zone"].get(zone["zona"], {})
            entry = {"jls": "Kaštela", "podrucje": zone["podrucje"], "ulice": zone["ulice"]}
            if zone.get("napomena"):
                entry["napomena"] = zone["napomena"]
            entry["raw"] = {**old.get("raw", {}),
                            str(year): podaci.month_lines([(d, c, False) for d, c in found.items()])}
            data["zone"][zone["zona"]] = entry
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"] = dict(sorted(data["zone"].items()))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


def red_days(page, year):
    """Days whose cell is red (non-working day without collection)."""
    found, _ = read_page(page, year, {**{k: "X" for k in PALETTE if PALETTE[k]}, (1.0, 0.0, 0.0): "R"}, ignore=IGNORE)
    return sorted(d for d, c in found.items() if c == "R")


if __name__ == "__main__":
    main()
