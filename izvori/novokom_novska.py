"""Novska: Novokom d.o.o. (novokom-novska.hr), five weekday routes of streets and settlements in Grad Novska.

    python3 -m izvori.novokom_novska [--year 2026]

The year post "Raspored odvoza otpada za YYYY. godinu" (WordPress REST) links one A3 PDF: two pages per
route (January-June and July-December), the route's streets and settlements at the top, then one block
per month with a column per waste type (mixed weekly, biowaste every two weeks, paper, glass and plastic
monthly). Every date is written with its weekday ("PON, 05.01.", "SUBOTA, 10.01."), so each one is read
with pdfplumber word positions (column by header, month by the block label) and checked: the date must
fall in its month block and on the weekday written next to it. Tokens that fail (typos such as
"PON, 29.03." in June) are printed and skipped. Holiday shifts are built into the dates ("UTO, 07.04."
for Easter Monday): a date off the column's usual weekday within a week of a holiday is marked as moved.
"""
import argparse
import html
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "novokom-novska"
SITE = "https://novokom-novska.hr"
POSTS = SITE + "/wp-json/wp/v2/posts?search=raspored%20odvoza&per_page=20&_fields=id,date,link,title,content"
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
COLUMNS = {"MIJEŠANI": "M", "BIOOTPAD": "B", "PAPIR": "K", "STAKLO": "S", "PLASTIKA": "P"}
ROUTES = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK"]
DAY = {"PON": 0, "UTO": 1, "SRI": 2, "ČET": 3, "PET": 4, "SUB": 5, "NED": 6}
PER_MONTH = {"M": (4, 5), "B": (1, 3), "K": (1, 1), "S": (1, 1), "P": (1, 1)}  # biowaste about every two weeks
MAX_TYPOS = 6
FULL_NAMES = {"S. Grabovac": "Stari Grabovac", "Sigetac": "Sigetac Novski"}  # official names, added for search
PROVIDER = {
    "davatelj": "Novokom d.o.o. Novska",
    "web": SITE,
    "zupanija": "Sisačko-moslavačka",
    "jls": ["Novska"],
}
NAPOMENE = [
    "Datumi u rasporedu već uključuju pomake zbog blagdana; takvi su datumi označeni kao pomaknuti.",
    "Raspored vrijedi za ulice i naselja navedena na pojedinoj ruti; za naselja koja nisu navedena provjerite "
    "raspored kod Novokoma.",
]


def year_pdf(year):
    """(post link, PDF url) of the 'Raspored odvoza otpada za YYYY. godinu' post."""
    for post in json.loads(fetch(POSTS)):
        title = html.unescape(post["title"]["rendered"])
        pdfs = [html.unescape(u) for u in re.findall(r'href="([^"]+\.pdf)"', post["content"]["rendered"])
                if "aspored" in u]
        if f"{year}" in title and pdfs:
            return post["link"], pdfs[-1]
    return None, None


def names(text):
    """'SIGETAC I PLESMO, TRG L. I. ORIOVČANINA' -> ['Sigetac', 'Plesmo', 'Trg L. I. Oriovčanina']."""
    out = []
    for part in text.split(","):
        for n in re.split(r"\s+I\s+(?!\.)", " ".join(part.split())):
            n = n.strip(" .")
            if n:
                out.append(" ".join(w if re.fullmatch(r"\w\.?", w) and len(w) <= 2 and w.isupper() and "." in w
                                    else w.capitalize() for w in n.split()))
    return out


def read_page(page, year, problems, typos):
    """One page -> (route weekday name, [areas], {code: [(date, written day)]})."""
    words = page.extract_words()
    route = next((w for w in words if w["text"] in ROUTES and w["x0"] < 100), None)
    heads = {COLUMNS[w["text"]]: w["x0"] for w in words if w["text"] in COLUMNS}
    labels = sorted((w["top"], MONTHS.index(w["text"]) + 1) for w in words if w["text"] in MONTHS)
    if not route or len(heads) != 5 or len(labels) != 6:
        problems.append(f"str. {page.page_number}: ruta {route and route['text']}, stupci {sorted(heads)}, "
                        f"mjeseci {[m for _, m in labels]}")
        return None, [], {}
    top_end = min(w["top"] for w in words if w["text"] in ROUTES) - 5
    lines = {}
    for w in sorted((w for w in words if w["top"] < top_end and w["x0"] > 300), key=lambda w: (round(w["top"]), w["x0"])):
        lines.setdefault(round(w["top"]), []).append(w["text"])
    area = ""
    for line in (" ".join(v) for _, v in sorted(lines.items())):  # a line without a trailing comma ends a name too
        area += (line if not area else (" " if area.rstrip().endswith(",") else ", ") + line)
    rows = {}
    for w in words:
        m = re.fullmatch(r"(\d\d)\.(\d\d)\.", w["text"])
        if not m or w["top"] < labels[0][0] - 30:
            continue
        left = [v for v in words if abs(v["top"] - w["top"]) < 3 and v["x1"] <= w["x0"] and w["x0"] - v["x1"] < 15]
        written = left[-1]["text"].rstrip(",").upper()[:3] if left else ""
        code = max(((c, x) for c, x in heads.items() if x <= w["x0"] + 5), key=lambda t: t[1], default=(None,))[0]
        block = [mo for top, mo in labels if top - 30 <= w["top"]]
        month = block[-1] if block else None
        kind = {"M": "miješani", "B": "biootpad", "K": "papir", "S": "staklo", "P": "plastika"}.get(code, "?")
        token = f"str. {page.page_number} {route['text']} {code}: {kind} '{(left[-1]['text'] + ' ') if left else ''}{w['text']}'"
        try:
            d = date(year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            typos.append(f"{token} nije datum")
            continue
        if d.month != month:
            typos.append(f"{token} stoji u bloku {MONTHS[month - 1] if month else '?'}")
            continue
        if DAY.get(written) != d.weekday():
            typos.append(f"{token}: {d:%d.%m.%Y.} je {podaci.DAYS[d.weekday()]}")
            continue
        if code is None:
            problems.append(f"{token}: stupac nije prepoznat")
            continue
        rows.setdefault(code, []).append(d)
    return route["text"], names(area), rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    link, url = year_pdf(year)
    if not url:
        sys.exit(f"Objava s rasporedom za {year}. nije pronađena. Ništa nije upisano.")
    problems, typos = [], []
    routes = {}
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "raspored.pdf"
        fetch(url, pdf)
        doc = pdfplumber.open(pdf)
        if f"31.12.{year}" not in (doc.pages[0].extract_text() or ""):
            problems.append(f"PDF nije raspored za {year}")
        for page in doc.pages:
            name, areas, rows = read_page(page, year, problems, typos)
            if not name:
                continue
            r = routes.setdefault(name, {"areas": [], "rows": {}})
            if r["areas"] and {a.lower() for a in areas} != {a.lower() for a in r["areas"]}:
                print(f"UPOZORENJE {name}: popis ulica razlikuje se po polugodištima: {r['areas']} / {areas}")
            r["areas"] += [a for a in areas if a.lower() not in {x.lower() for x in r["areas"]}]
            for code, ds in rows.items():
                r["rows"].setdefault(code, []).extend(ds)
    for t in typos:
        print(f"PRESKOČENO (tipfeler): {t}")
    if len(typos) > MAX_TYPOS:
        problems.append(f"previše neispravnih datuma ({len(typos)})")
    if [r for r in ROUTES if r not in routes]:
        problems.append(f"rute: {sorted(routes)}")

    hol = pravila.blagdani(year)
    zones = []
    for name in ROUTES:
        r = routes.get(name)
        if not r:
            continue
        merged = {}
        for code, ds in r["rows"].items():
            if len(set(ds)) != len(ds):
                problems.append(f"{name} {code}: datum dvaput {[d for d, k in Counter(ds).items() if k > 1]}")
            usual = Counter(d.weekday() for d in ds).most_common(1)[0][0]
            for d in sorted(set(ds)):
                moved = d.weekday() != usual
                if moved and not any(abs((d - h).days) <= 7 for h in hol):
                    problems.append(f"{name} {code}: {d:%d.%m.} nije {podaci.DAYS[usual]}, a nema blagdana u blizini")
                if d in hol or d.weekday() == 6:
                    problems.append(f"{name} {code}: {d:%d.%m.} je blagdan ili nedjelja")
                c, m = merged.get(d, ("", False))
                merged[d] = (c + code, m or moved)
            per = Counter(d.month for d in set(ds))
            lo, hi = PER_MONTH[code]
            lo -= sum(1 for t in typos if f" {name} {code}:" in t) and lo > 1  # a skipped typo leaves one fewer
            bad = {m: per.get(m, 0) for m in range(1, 13) if not lo <= per.get(m, 0) <= hi}
            if bad:
                problems.append(f"{name} {code}: broj odvoza po mjesecima {bad}")
        rows = sorted((d, c, m) for d, (c, m) in merged.items())
        dan = podaci.DAYS[ROUTES.index(name)]
        zone = {"jls": "Novska", "podrucje": f"{dan.capitalize()} – " + ", ".join(r["areas"][:4])
                + (", …" if len(r["areas"]) > 4 else ""), "ulice": r["areas"] + [FULL_NAMES[a] for a in r["areas"] if a in FULL_NAMES],
                "rows": rows}
        note = ["Na ovoj ruti raspored ne navodi odvoz biootpada."] if "B" not in r["rows"] else []
        skipped = [t.split(": ", 1)[1] for t in typos if f" {name} " in t]
        if skipped:
            note.append("Izostavljeni neispravni datumi iz rasporeda: " + "; ".join(skipped) + ".")
        if note:
            zone["napomena"] = " ".join(note)
        if not r["areas"]:
            problems.append(f"{name}: nema ulica ni naselja")
        zones.append(zone)
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": link, "napomene": NAPOMENE, "zone": {}}
    for i, z in enumerate(zones, 1):
        rows = z.pop("rows")
        prev = old.get(str(i), {})
        raw = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("podrucje") == z["podrucje"] else {}
        z["raw"] = {**raw, str(year): podaci.month_lines(rows)}
        data["zone"][str(i)] = z
        cnt = Counter(c for _, codes, _ in rows for c in codes)
        print(f"Zona {i}: {z['podrucje'][:70]}: " + ", ".join(f"{c} {cnt[c]}" for c in "MBPKS" if cnt[c])
              + f", pomaknuto {sum(1 for *_, m in rows if m)}, ulica/naselja {len(z['ulice'])}")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona, preskočeno {len(typos)} neispravnih datuma)")


if __name__ == "__main__":
    main()
