"""Garešnica, Berek, Hercegovac, Velika Trnovitica: Komunalac d.o.o. Garešnica, one zone per weekday route.

    python3 -m izvori.komunalac_garesnica [--year 2026]

The "Dokumenti" page links one PDF per city or municipality ("Raspored-sakupljanja-otpada-Grad-Garesnica.pdf",
...). Each weekday heading (PONEDJELJAK ... PETAK) is a route: the settlements (in Garešnica also the
streets, in brackets), then one block per waste type whose label spans two or three lines (MIJEŠANI
KOMUNALNI OTPAD, OTPADNA PLASTIKA, OTPADNI PAPIR, OTPADNO STAKLO, OTPADNI METAL) with the dates as a list
separated by semicolons that wraps over several lines. Lines are rebuilt from word positions (pdfplumber):
a label word in the left column starts a type block, the dates on the following lines belong to it.
Biowaste is a rule, "Od 1. veljače svaku srijedu, osim blagdana i praznika tada idući radni dan", applied
where the route has it (not in Berek and Velika Trnovitica).
Holidays: the PDF's table "TERMINI ODVOZA OTPADA U VRIJEME BLAGDANA i PRAZNIKA" gives the replacement day
for each holiday; those days are built into the lists (07.01. instead of 06.01.) and are marked as moved,
and the same table moves the biowaste Wednesdays that fall on a holiday.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalac-garesnica"
SITE = "https://komunalac-garesnica.hr"
PAGE = SITE + "/dokumenti/"
FILES = {"Grad-Garesnica": "Garešnica", "Opcina-Berek": "Berek", "Opcina-Hercegovac": "Hercegovac",
         "Opcina-Velika-Trnovitica": "Velika Trnovitica"}
WEEKDAYS = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK", "SUBOTA"]
STARTS = {"MIJEŠANI", "OTPADNA", "OTPADNI", "OTPADNO", "OTPADI", "BIOOTPAD"}
TYPES = [("MIJEŠANI", "M"), ("PLASTIKA", "P"), ("PAPIR", "K"), ("STAKLO", "S"), ("METAL", "L")]
DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.?[;,]?")
DAY_ACC = {"ponedjeljak": 0, "utorak": 1, "srijedu": 2, "četvrtak": 3, "petak": 4, "subotu": 5}
MONTH_GEN = {"siječnja": 1, "veljače": 2, "ožujka": 3, "travnja": 4, "svibnja": 5, "lipnja": 6, "srpnja": 7,
             "kolovoza": 8, "rujna": 9, "listopada": 10, "studenoga": 11, "studenog": 11, "prosinca": 12}
COUNTS = {"M": (24, 28), "P": (12, 14), "K": (12, 14), "S": (12, 14), "L": (12, 14)}
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Garešnica",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Bjelovarsko-bilogorska",
    "jls": ["Garešnica", "Berek", "Hercegovac", "Velika Trnovitica"],
    "nazivi": {"P": "Plastika (vreća)", "K": "Papir (vreća)", "S": "Staklo (vreća)", "L": "Metal (vreća)"},
    "bioNapomena": "Biootpad se od 1. veljače odvozi svake srijede (Grad Garešnica i Općina Hercegovac).",
}
NAPOMENE = [
    "Spremnici i vreće za papir, plastiku, staklo i metal moraju na dan odvoza biti vidljivi na javnoj površini "
    "od 7 do 15 sati, a u lipnju, srpnju i kolovozu od 6 do 14 sati.",
    "Blagdani: odvoz prema tablici „Termini odvoza otpada u vrijeme blagdana i praznika” iz rasporeda; ti su "
    "datumi označeni kao pomaknuti.",
    "Dodatne vreće za miješani i reciklabilni otpad: sjedište tvrtke, ponedjeljak–petak 7–14:30.",
    "Reciklažna dvorišta: Garešnica, Industrijska ulica 17 (pon–pet 7–17, sub 7–12); Velika Mlinska 69A "
    "(pon–pet 7:30–14:30, ljeti 6:30–14).",
]


def naslov(name):
    """'KANIŠKA IVA' -> 'Kaniška Iva'; mixed-case text ('Ul. V. Nazora') stays."""
    name = " ".join(name.split())
    return " ".join(w[:1] + w[1:].lower() for w in name.split()) if name.isupper() else name


def places(text):
    """'GAREŠNICA - (Kolodvorska ul., Ul. V. Nazora), CIGLENICA, M. VUKOVJE,' -> streets and settlements."""
    items, depth, cur = [], 0, ""
    for ch in text + ",":
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out = []
    for it in filter(None, items):
        m = re.fullmatch(r"(.+?)\s*-?\s*\((.*)\)", it)
        if m:  # streets of one settlement
            out += [naslov(s).strip(" .") + ("." if s.strip().endswith(".") else "") for s in m.group(2).split(",")
                    if s.strip()]
        else:
            out.append(naslov(it).strip(" ."))
    return [o for o in out if o]


def lines_of(pdf):
    """All lines of the document in reading order: [[words left to right]]."""
    out = []
    for page in pdf.pages:
        lines = []
        for w in sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"])):
            if lines and abs(lines[-1][0] - w["top"]) < 3:
                lines[-1][1].append(w)
            else:
                lines.append([w["top"], [w]])
        out += [sorted(ws, key=lambda w: w["x0"]) for _, ws in lines]
    return out


def parse(pdf, year, problems, name):
    """Routes [{day, area, blocks: [(label, [date tokens])], bio}] and the holiday table {holiday: new date}."""
    routes, table, state, pending = [], {}, None, None
    lines = lines_of(pdf)
    left = min(w["x0"] for ws in lines for w in ws if DATE.fullmatch(w["text"]) and w["text"].endswith(";"))
    for ws in lines:
        text = " ".join(w["text"] for w in ws)
        first = ws[0]["text"]
        if text.strip() in WEEKDAYS:
            routes.append({"day": WEEKDAYS.index(text.strip()), "area": [], "blocks": [], "bio": None})
            state = "area"
            continue
        if text.startswith("TERMINI ODVOZA OTPADA"):
            state = "table"
            continue
        if text.startswith("PODSJETNIK"):
            state = None
            continue
        if state == "table":
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\.\s*[–-]\s*\w+\s*[–-]", text)
            if m:
                pending = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                continue
            m = re.search(r"Otpad se odvozi u \w+ (\d{2})\.(\d{2})\.(\d{4})", text)
            if m and pending:
                table[pending] = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                pending = None
            elif pending and text.strip() and not text.strip().startswith(("na žrtvu", "Vukovara")):
                if "odvozi" in text:
                    problems.append(f"{name}: ne razumijem pravilo za blagdan {pending}: {text!r}")
            continue
        if state == "area":
            if first == "NASELJE":
                continue
            if first in STARTS:
                state = "types"
            else:
                routes[-1]["area"].append(text)
                continue
        if state == "types":
            label = [w["text"] for w in ws if w["x1"] < left]  # left: where the date lists start
            rest = [w["text"] for w in ws if w["x1"] >= left]
            if label and label[0] == "BIOOTPAD":
                routes[-1]["bio"] = " ".join(rest)
                continue
            if label and label[0] in STARTS:
                routes[-1]["blocks"].append([label, rest])
            elif routes[-1]["blocks"]:
                routes[-1]["blocks"][-1][0] += label
                routes[-1]["blocks"][-1][1] += rest
            elif text.strip():
                problems.append(f"{name}: redak izvan bloka: {text!r}")
    return routes, table


def to_dates(tokens, year, problems, where):
    out = []
    for t in tokens:
        m = DATE.fullmatch(t)
        if not m:
            problems.append(f"{where}: ne razumijem {t!r}")
            continue
        try:
            out.append(date(year, int(m.group(2)), int(m.group(1))))
        except ValueError:
            problems.append(f"{where}: nemoguć datum {t}")
    if out != sorted(out):
        problems.append(f"{where}: datumi nisu poredani: {', '.join(f'{d:%d.%m.}' for d in out)}")
    return out


def bio_dates(rule, year, table, hol, problems, where):
    """'Od 1. veljače svaku srijedu, osim blagdana i praznika tada idući radni dan' -> [(date, moved)]."""
    m = re.fullmatch(r"Od (\d{1,2})\. (\w+) svak[iu] (\w+), osim blagdana i praznika tada idući radni dan", rule)
    if not m or m.group(2) not in MONTH_GEN or m.group(3) not in DAY_ACC:
        problems.append(f"{where}: ne razumijem pravilo za biootpad: {rule!r}")
        return []
    start = date(year, MONTH_GEN[m.group(2)], int(m.group(1)))
    out = []
    d = start + timedelta(days=(DAY_ACC[m.group(3)] - start.weekday()) % 7)
    while d.year == year:
        if d in hol:
            new = table.get(d) or pravila.primijeni_blagdane([d], "sljedeci")[0][0]
            if d not in table:
                print(f"   PRAVILO {where}: biootpad {d:%d.%m.} (blagdan, nema ga u tablici) → idući radni dan "
                      f"{new:%d.%m.}")
            out.append((new, True))
        else:
            out.append((d, False))
        d += timedelta(days=7)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    found = {}
    for url, name in re.findall(r'href="([^"]*/uploads/(?:\d{4}/\d\d/)?Raspored-sakupljanja-otpada-([A-Za-z-]+?)'
                                r'(?:-\d+)?\.pdf)"', html):
        if name in FILES:
            found[FILES[name]] = url  # last link wins (newest upload is listed last)
    missing = [j for j in FILES.values() if j not in found]
    if missing:
        sys.exit(f"Na {PAGE} nema rasporeda za: {', '.join(missing)}. Ništa nije upisano.")
    hol = set(pravila.blagdani(year))
    problems, zones = [], {}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    with tempfile.TemporaryDirectory() as tmp:
        for jls in FILES.values():
            url = found[jls]
            pdf_path = Path(tmp) / url.rsplit("/", 1)[1]
            fetch(url, pdf_path)
            with pdfplumber.open(pdf_path) as pdf:
                head = " ".join((pdf.pages[0].extract_text() or "").split())
                routes, table = parse(pdf, year, problems, jls)
            if f"u {year}. godini" not in head:
                problems.append(f"{jls}: PDF nije raspored za {year}")
            for h, new in table.items():
                if h not in hol or abs((new - h).days) > 6 or new in hol or new.weekday() == 6:
                    problems.append(f"{jls}: pomak blagdana {h} → {new} nije vjerojatan")
            print(f"{jls}: {len(routes)} relacija, blagdani: "
                  + ", ".join(f"{h:%d.%m.}→{n:%d.%m.}" for h, n in sorted(table.items())))
            for route in routes:
                day = podaci.DAYS[route["day"]]
                where = f"{jls} {day}"
                rows = {}
                for label, tokens in route["blocks"]:
                    lab = " ".join(label)
                    code = next((c for k, c in TYPES if k in lab), None)
                    if not code:
                        problems.append(f"{where}: nepoznata vrsta {lab!r}")
                        continue
                    dates = to_dates(tokens, year, problems, f"{where} {lab}")
                    lo, hi = COUNTS[code]
                    if not lo <= len(dates) <= hi or len(set(dates)) != len(dates):
                        problems.append(f"{where} {lab}: {len(dates)} datuma ({len(set(dates))} različitih)")
                    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
                    step = 14 if code == "M" else 28
                    if any(not step - 6 <= g <= step + 6 for g in gaps):
                        problems.append(f"{where} {lab}: razmaci {gaps}")
                    for d in dates:
                        moved = d.weekday() != route["day"]
                        if moved and not any(new == d and h.weekday() == route["day"] for h, new in table.items()):
                            problems.append(f"{where} {lab}: {d:%d.%m.} ({podaci.DAYS[d.weekday()]}) nije dan "
                                            "relacije ni zamjenski dan iz tablice blagdana")
                        if d in hol:
                            problems.append(f"{where} {lab}: {d:%d.%m.} je blagdan")
                        r = rows.setdefault(d, ["", False])
                        if code in r[0]:
                            problems.append(f"{where}: {code} dvaput {d}")
                        r[0] += code
                        r[1] = r[1] or moved
                if route["bio"]:
                    for d, moved in bio_dates(route["bio"], year, table, hol, problems, where):
                        r = rows.setdefault(d, ["", False])
                        r[0] += "B"
                        r[1] = r[1] or moved
                ulice = places(" ".join(route["area"]))
                if not ulice:
                    problems.append(f"{where}: nema naselja")
                key = str(len(zones) + 1)
                short = ", ".join(ulice[:3]) + (" …" if len(ulice) > 3 else "")
                zone = {"jls": jls, "podrucje": f"{jls}, {day} – {short}",
                        "opis": " ".join(" ".join(route["area"]).split()).rstrip(","), "ulice": ulice}
                if not route["bio"]:
                    zone["napomena"] = "Raspored ne navodi odvoz biootpada za ovo područje."
                prev = old.get(key, {})
                zone["raw"] = {**(prev.get("raw", {}) if prev.get("podrucje") == zone["podrucje"] else {}),
                               str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])}
                zones[key] = zone
                cnt = Counter(ch for c, _ in rows.values() for ch in c)
                print(f"   Zona {key}: {zone['podrucje'][:80]}: "
                      + ", ".join(f"{c} {cnt[c]}" for c in "MBPKSL" if cnt[c])
                      + f", pomaknuto {sum(1 for _, mv in rows.values() if mv)}, mjesta {len(ulice)}")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "napomene": NAPOMENE, "zone": zones})
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
