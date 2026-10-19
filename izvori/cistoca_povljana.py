"""Povljana: Čistoća Povljana d.o.o. (cistoca-povljana.hr), whole municipality, mixed waste by month and recyclables.

    python3 -m izvori.cistoca_povljana [--year 2026]

The page "Raspored odvoza" links the schedules of the year ("RASPORED ODVOZA MJEŠANOG KOMUNALNOG OTPADA
SIJEČNJA DO SRPNJA 2026.", "... RUJAN - LISTOPAD 2026."). The main PDF has a month table of mixed waste
(month -> weekdays, and an IZNIMKE column such as "NEĆE SE VOZITI 1.5.", those dates are dropped) and a
table of recyclables with explicit dates per month for paper, plastic, glass and metal and "SVAKI
PONEDJELJAK" for biowaste; the columns are kept apart by the x position of the words (pdfplumber).
Later notices change the mixed-waste days from a date ("od 7. 9. 2026. godine vršiti PONEDJELJKOM I
ČETVRTKOM"); such a notice is applied up to the end of the last month in its link text. Days not covered by
any schedule get no mixed waste, and mixed-waste dates of earlier runs are kept for those days.
"""
import argparse
import html
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cistoca-povljana"
SITE = "https://www.cistoca-povljana.hr"
PAGE = SITE + "/index.php/cistoca/raspored-odvoza"
MONTHS = ["Siječanj", "Veljača", "Ožujak", "Travanj", "Svibanj", "Lipanj", "Srpanj", "Kolovoz", "Rujan",
          "Listopad", "Studeni", "Prosinac"]
GEN = ["SIJEČNJA", "VELJAČE", "OŽUJKA", "TRAVNJA", "SVIBNJA", "LIPNJA", "SRPNJA", "KOLOVOZA", "RUJNA",
       "LISTOPADA", "STUDENOGA", "PROSINCA"]
DAYS = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak", "Subota", "Nedjelja"]
INSTR = ["PONEDJELJKOM", "UTORKOM", "SRIJEDOM", "ČETVRTKOM", "PETKOM", "SUBOTOM", "NEDJELJOM"]
COLUMNS = {"PAPIR": "K", "PLASTIKA": "P", "STAKLO": "S", "METAL": "L", "BIO": "B"}
PROVIDER = {
    "davatelj": "Čistoća Povljana d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zadarska",
    "jls": ["Povljana"],
    "nazivi": {"P": "Plastika", "L": "Metal", "B": "Biootpad (vrećice)"},
}
NAPOMENE = [
    "Razvrstani otpad odlaže se u vrećice (preuzimaju se u uredu, Trg bana Josipa Jelačića 13 A) ili se donosi na "
    "mobilno reciklažno dvorište (Ante Starčevića, iza sportske dvorane), prema rasporedu u PDF-u.",
    "Glomazni otpad: na zahtjev korisnika u zadane dane (vidi obavijesti na cistoca-povljana.hr).",
    "Od lipnja do rujna veliki zeleni otpad koji ne stane u vrećice za biootpad ne preuzima se.",
]


def lines_of(page):
    out = []
    for w in sorted(page.extract_words(x_tolerance=2), key=lambda w: (w["top"], w["x0"])):
        if out and abs(out[-1][0] - w["top"]) < 3:
            out[-1][1].append(w)
        else:
            out.append((w["top"], [w]))
    return out


def month_bands(words, top, bottom):
    """[(month, top, bottom)] for month names in the left column between top and bottom."""
    names = sorted((w for w in words if w["text"] in MONTHS and w["x0"] < 130 and top < w["top"] < bottom),
                   key=lambda w: w["top"])
    return [(MONTHS.index(w["text"]) + 1, w["top"] - 3, (names[i + 1]["top"] - 3) if i + 1 < len(names) else bottom)
            for i, w in enumerate(names)]


def month_table(pdf, year, problems):
    """({month: (weekdays, {dropped dates})}, {date: codes}) from the main PDF."""
    words = [w for w in pdf.pages[0].extract_words(x_tolerance=2)]
    head = next((w for w in words if w["text"] == "MIJESEC"), None)
    stop = next((w for w in words if w["text"] == "MOLIMO"), None)
    rec = next((w for w in words if w["text"] == "RECIKLABILNOG"), None)
    if not (head and stop and rec):
        problems.append("ne prepoznajem tablice u PDF-u")
        return {}, {}
    mko = {}
    for m, t0, t1 in month_bands(words, head["bottom"], stop["top"]):
        band = [w for w in words if t0 <= w["top"] < t1 and w["x0"] > 130]
        days = [DAYS.index(w["text"]) for w in band if w["text"] in DAYS]
        rest = " ".join(w["text"] for w in band if w["text"] not in DAYS and w["text"] != "-")
        dropped = set()
        for d, mo in re.findall(r"NEĆE SE VOZITI (\d{1,2})\.(\d{1,2})\.", rest):
            dropped.add(date(year, int(mo), int(d)))
        rest = re.sub(r"NEĆE SE VOZITI \d{1,2}\.\d{1,2}\.", "", rest).strip()
        if rest or not days:
            problems.append(f"MKO {MONTHS[m - 1]}: ne razumijem {rest!r} / {days}")
        mko[m] = (days, dropped)
    # recyclables: one row per month, dates under PAPIR / PLASTIKA / STAKLO / METAL, BIO "SVAKI PONEDJELJAK"
    heads = {w["text"]: (w["x0"] + w["x1"]) / 2 for w in words if w["text"] in COLUMNS and w["top"] > rec["bottom"]
             and w["top"] < rec["bottom"] + 40}
    if set(heads) != set(COLUMNS):
        problems.append(f"zaglavlje tablice reciklabilnog otpada: {sorted(heads)}")
        return mko, {}
    head_bottom = max(w["bottom"] for w in words if w["text"] in COLUMNS and w["top"] > rec["bottom"])
    found = defaultdict(str)
    for m, t0, t1 in month_bands(words, head_bottom, pdf.pages[0].height):
        band = [w for w in words if t0 <= w["top"] < t1 and w["x0"] > 130]
        cols = defaultdict(list)
        for w in band:
            col = min(heads, key=lambda c: abs(heads[c] - (w["x0"] + w["x1"]) / 2))
            cols[col].append(w["text"])
        if " ".join(cols.pop("BIO", [])) != "SVAKI PONEDJELJAK":
            problems.append(f"reciklabilni {MONTHS[m - 1]}: biootpad nije 'SVAKI PONEDJELJAK'")
        d = date(year, m, 1)
        while d.month == m:
            if d.weekday() == 0:
                found[d] += "B"
            d += timedelta(days=1)
        for col, toks in cols.items():
            for t in toks:
                mm = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?", t)
                if not mm or int(mm.group(2)) != m:
                    problems.append(f"reciklabilni {MONTHS[m - 1]} {col}: ne razumijem {t!r}")
                    continue
                dd = date(year, m, int(mm.group(1)))
                if COLUMNS[col] in found[dd]:
                    problems.append(f"{col} dvaput {dd}")
                found[dd] += COLUMNS[col]
    return mko, dict(found)


def notice(pdf, label, year, problems):
    """(start, end, weekdays) from 'od 7. 9. 2026. godine vršiti PONEDJELJKOM I ČETVRTKOM'."""
    t = " ".join(" ".join(p.extract_text() or "" for p in pdf.pages).split())
    m = re.search(r"miješanog komunalnog otpada od (\d{1,2})\. ?(\d{1,2})\. ?(\d{4})\. godine vršiti ((?:%s)(?: I (?:%s))*)"
                  % ("|".join(INSTR), "|".join(INSTR)), t)
    months = [GEN.index(w) + 1 if w in GEN else [x.upper() for x in MONTHS].index(w) + 1
              for w in re.findall(r"[A-ZČĆŠŽ]+", label) if w in GEN or w in [x.upper() for x in MONTHS]]
    if not m or not months:
        problems.append(f"obavijest {label!r}: ne razumijem tekst ili razdoblje")
        return None
    start = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    last = max(months)
    end = date(year, last, 1) + timedelta(days=31)
    end = date(end.year, end.month, 1) - timedelta(days=1)
    days = [INSTR.index(x) for x in m.group(4).split(" I ")]
    return start, end, days


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    page = fetch(PAGE).decode("utf-8", "replace")
    items = []
    for li in re.findall(r"<li>(.*?)</li>|<li>(.*?)<ul>", page, re.S):
        li = li[0] or li[1]
        m = re.search(r'(.*?)\s*-\s*<a href="([^"]+\.pdf)"', li, re.S)
        if not m:
            continue
        label = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split())
        if label.startswith("RASPORED ODVOZA MJEŠANOG") and f"{year}." in label:
            items.append((label, m.group(2) if m.group(2).startswith("http") else SITE + m.group(2)))
    if not items:
        sys.exit(f"Na {PAGE} nema rasporeda za {year}")
    mko_rules, recyc, notices = {}, {}, []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (label, url) in enumerate(items):
            path = Path(tmp) / f"{i}.pdf"
            fetch(url, path)
            with pdfplumber.open(path) as pdf:
                text = pdf.pages[0].extract_text() or ""
                if "MIJESEC" in text and "IZNIMKE" in text:
                    if f"{year}." not in text:
                        problems.append(f"{url}: tablica nije za {year}")
                    mko_rules, recyc = month_table(pdf, year, problems)
                    print(f"{label}: MKO za mjesece {sorted(mko_rules)}, reciklabilni {len(recyc)} dana ({url})")
                elif "OBAVIJEST" in text:
                    n = notice(pdf, label, year, problems)
                    if n:
                        notices.append(n)
                        print(f"{label}: od {n[0]:%d.%m.} do {n[1]:%d.%m.} {[DAYS[d] for d in n[2]]} ({url})")
                else:
                    problems.append(f"{url}: nepoznat dokument")
    if not mko_rules:
        problems.append("nije nađena tablica s mjesecima")

    # mixed waste: month table, then notices from their start date
    rows = defaultdict(str)
    covered = set()
    d = date(year, 1, 1)
    while d.year == year:
        rule = None
        for start, end, days in notices:
            if start <= d <= end:
                rule = (days, set())
        if rule is None and d.month in mko_rules and not any(d >= s for s, _, _ in notices if s.month == d.month):
            rule = mko_rules[d.month]
        if rule:
            covered.add(d)
            if d.weekday() in rule[0] and d not in rule[1]:
                rows[d] += "M"
        d += timedelta(days=1)
    for d, codes in recyc.items():
        rows[d] += codes
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    prev = old["zone"].get("1", {})
    kept = 0
    for d, codes, moved in podaci.iter_dates(prev, year) if prev else []:
        if "M" in codes and d not in covered and "M" not in rows[d]:
            rows[d] += "M"
            kept += 1
    uncovered = [date(year, 1, 1) + timedelta(days=i) for i in range((date(year, 12, 31) - date(year, 1, 1)).days + 1)]
    uncovered = [d for d in uncovered if d not in covered]
    spans, start = [], None
    for a, b in zip(uncovered, uncovered[1:] + [None]):
        start = start or a
        if b is None or (b - a).days > 1:
            spans.append(f"{start:%d.%m.}–{a:%d.%m.}")
            start = None
    if spans:
        print(f"Bez rasporeda miješanog otpada: {', '.join(spans)}" + (f" (zadržano {kept} datuma iz ranijeg pokretanja)"
                                                                         if kept else ""))
    # checks
    per = Counter((c, d.month) for d, codes in rows.items() for c in codes)
    for (c, m), n in per.items():
        limits = {"M": (4, 14), "B": (4, 5)}.get(c, (1, 2))
        if not limits[0] <= n <= limits[1]:
            problems.append(f"{c} {n} puta u mjesecu {m}")
    for d, codes in rows.items():
        if len(set(codes)) != len(codes):
            problems.append(f"{d}: {codes} dvaput")
        if d.weekday() == 6:
            problems.append(f"{d}: nedjelja")
    # paper, glass and metal go out together, plastic the day before (the weekday is not fixed: 29./30.9.)
    paper = sorted(d for d, c in recyc.items() if "K" in c)
    for d in paper:
        if recyc[d].replace("B", "") != "KSL" or "P" not in recyc.get(d - timedelta(days=1), ""):
            problems.append(f"reciklabilni {d}: papir, staklo i metal nisu zajedno ili plastika nije dan ranije")
        elif d.weekday() != 3:
            print(f"Napomena: reciklabilni otpad {d:%d.%m.} ({DAYS[d.weekday()].lower()}) – datum kako je objavljen")
    if len([d for d, c in recyc.items() if "P" in c]) != len(paper) or not 12 <= len(paper) <= 16:
        problems.append(f"reciklabilni: {len(paper)} datuma za papir")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")

    month_names = [m.lower() for m in MONTHS]
    mko_desc = []
    for m in sorted(mko_rules):
        days, dropped = mko_rules[m]
        mko_desc.append(f"{month_names[m - 1]}: " + ", ".join(DAYS[i].lower() for i in days)
                        + (f" (ne vozi se {', '.join(f'{x.day}.{x.month}.' for x in sorted(dropped))})" if dropped else ""))
    for s, e, days in notices:
        mko_desc.append(f"od {s.day}.{s.month}. do {e.day}.{e.month}.: " + " i ".join(INSTR[i].lower() for i in days))
    data = {**PROVIDER, "napomene": NAPOMENE + [
        "Miješani otpad prema mjesečnoj tablici i obavijestima Čistoće Povljana; za razdoblja "
        + ", ".join(spans) + " raspored miješanog otpada nije objavljen." if spans else "",
        "Iznimke zbog blagdana za miješani otpad objavljene su u tablici (stupac IZNIMKE); za reciklabilni otpad "
        "objavljeni su točni datumi, a biootpad se odvozi svakog ponedjeljka (pomaci zbog blagdana nisu objavljeni).",
    ], "zone": {"1": {
        "jls": "Povljana",
        "podrucje": "Općina Povljana (cijela općina)",
        "ulice": ["Povljana"],
        "napomena": "Miješani otpad – " + "; ".join(mko_desc) + ". Papir, plastika, staklo i metal jednom mjesečno "
                    "(u srpnju, kolovozu i rujnu dvaput) prema datumima iz rasporeda; biootpad svakog ponedjeljka.",
        "raw": {**{y: v for y, v in prev.get("raw", {}).items() if y != str(year)},
                str(year): podaci.month_lines([(d, c, False) for d, c in rows.items()])},
    }}}
    data["napomene"] = [n for n in data["napomene"] if n]
    print(f"Odvoza {year}: {dict(Counter(c for codes in rows.values() for c in codes))}")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} (1 zona)")


if __name__ == "__main__":
    main()
