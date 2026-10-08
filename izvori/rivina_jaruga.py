"""Skradin: Rivina Jaruga d.o.o. (rivinajaruga.com), mixed waste by settlement (older undated tables) and recyclables.

    python3 -m izvori.rivina_jaruga [--year 2026]

Mixed waste: the page "Komunalni otpad – Raspored odvoza" shows two screenshots of tables from November 2019,
without a year: "LJETNI PERIOD (travanj ... listopad)" and "ZIMSKI PERIOD (studeni ... ožujak)", each with
the settlements collected on every weekday. They were transcribed into SUMMER and WINTER below together with
each image's sha256 (a changed image stops the script). Settlements split into parts in one season
(Bratiškovci, Sonković) are followed part by part; settlements with the same days in both seasons and the
same recyclables day form one zone. The tables are an older undated publication, which every zone says.
Recyclables: the PDF "Raspored odvoza reciklabilnog otpada" (linked from the home page, 07/2026) gives one
weekday per settlement in the first week of the month. "First week" is read as the first such weekday of
the month (days 1-7), as in the company's per-settlement schedules of 2024, and the rule is applied from
the month after the PDF was uploaded. Single collection days announced in notices ("Obavijest za odvoz
korisnog otpada ...", date read from the PDF) apply to every zone. Holidays: for recyclables the PDF says
"the next working day" (applied, marked as moved); for mixed waste no rule is published (dates as computed).
"""
import argparse
import hashlib
import html
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "rivina-jaruga"
SITE = "https://rivinajaruga.com"
MIXED_PAGE = SITE + "/cistoca/odvoz-komunalnog-otpada/raspored-odvoza/"
DAYS = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK", "SUBOTA", "NEDJELJA"]
INSTR = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
# Transcribed by hand from the two screenshots (sha256 of the full-size PNG files).
IMAGES = {
    "Screenshot-2019-11-13-at-13.42.35.png": ("18bd107f24d07803f1fb9e0d6eb85c4991c7417e90defc615557571e58a02f07", "ljeto"),
    "Screenshot-2019-11-13-at-13.43.58.png": ("990392b55a9b8dc751fe8f38012620ad20bedfe1736a39795266dab5cbc4360f", "zima"),
}
SUMMER_MONTHS = [4, 5, 6, 7, 8, 9, 10]   # "LJETNI PERIOD (travanj, svibanj, lipanj, srpanj, kolovoz, rujan, listopad)"
WINTER_MONTHS = [11, 12, 1, 2, 3]        # "ZIMSKI PERIOD (studeni, prosinac, siječanj, veljača, ožujak)"
SUMMER = {
    0: "Skradin, Dubravice, Plastovo - dio",
    1: "Bribir, Piramatovci, Krković, Lađevci, Žažvić, Cicvare, Međare, Bilostanovi",
    2: "Vaćani, Bratiškovci - gornji dio, Rupe, Laškovica, Ićevo, Sladići, Roški slap",
    3: "Bićine, Skorići, Bratiškovci - donji dio, Gorice, Manojlovići, Ždrapanj",
    4: "Skradin, Gračac, Prokljan, Čulišić, Laće, Velika Glava, Pamučari, Sonković - donji dio",
    5: "Sonković - gornji dio, Skradinsko polje",
}
WINTER = {
    0: "Skradin, Dubravice, Plastovo - dio",
    1: "Vaćani, Bribir, Piramatovci, Krković, Lađevci, Žažvić, Cicvare, Međare, Bilostanovi",
    2: "Gorice, Rupe, Laškovica, Ićevo, Sladići, Roški slap, Manojlovići",
    3: "Bićine, Skorići, Bratiškovci, Ždrapanj, Sonković - donji dio, Prokljan",
    4: "Skradin, Gračac, Čulišić, Laće, Velika Glava, Pamučari, Sonković - gornji dio, Skradinsko polje",
}
PROVIDER = {
    "davatelj": "Rivina Jaruga d.o.o.",
    "web": SITE,
    "izvor": MIXED_PAGE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Skradin"],
    "nazivi": {"P": "Reciklabilni (korisni) otpad – zelene i plave vrećice"},
}
NAPOMENE = [
    "Miješani otpad: prema tablicama ljetnog (travanj–listopad) i zimskog (studeni–ožujak) rasporeda objavljenima "
    "u studenom 2019. bez godine; novijeg rasporeda nema – provjerite kod davatelja (022/771-633).",
    "Pravne osobe: miješani otpad svakim danom (nije uključeno).",
    "Za miješani otpad pravilo za blagdane nije objavljeno (tablica navodi samo da se neprikupljeni otpad prikuplja "
    "sljedeći radni dan ili u najkraćem roku); datumi su izračunati iz pravila.",
]


def units(table):
    """{weekday: 'Skradin, Plastovo - dio'} -> {(name, part): {weekdays}} with part None for the whole settlement."""
    out = defaultdict(set)
    for wd, text in table.items():
        for item in text.split(","):
            m = re.fullmatch(r"\s*(.+?)(?:\s*-\s*(gornji dio|donji dio|dio))?\s*", item)
            out[m.group(1), m.group(2)].add(wd)
    return out


def first_week(year, month, wd):
    return next(date(year, month, d) for d in range(1, 8) if date(year, month, d).weekday() == wd)


def next_working(d, hol):
    n = d
    while n in hol or n.weekday() == 6:
        n += timedelta(days=1)
    return n


def pdf_text(url, tmp, name):
    path = Path(tmp) / name
    fetch(url, path)
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))

    # 1. mixed waste images: must be the transcribed ones
    page = fetch(MIXED_PAGE).decode("utf-8", "replace")
    urls = sorted(set(re.sub(r"-\d+x\d+(?=\.png)", "", u) for u in
                      re.findall(r"https://rivinajaruga\.com/wp-content/uploads/\d{4}/\d\d/[^\"'\s,]+?\.png", page)
                      if "Screenshot" in u))
    if sorted(u.rsplit("/", 1)[1] for u in urls) != sorted(IMAGES):
        problems.append(f"slike rasporeda na stranici su se promijenile: {urls}")
    for u in urls:
        name = u.rsplit("/", 1)[1]
        sha = hashlib.sha256(fetch(u)).hexdigest()
        if name in IMAGES and IMAGES[name][0] != sha:
            problems.append(f"slika se promijenila: {name} (sha256 {sha}); prepišite SUMMER/WINTER ponovno")

    # 2. recyclables PDF and notices, linked from the home page
    home = fetch(SITE + "/").decode("utf-8", "replace")
    links = [(" ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split()), u) for u, t in
             re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', home, re.S)]
    rule_links = [u for t, u in links if "Raspored odvoza reciklabilnog otpada" in t]
    rec_rule, start_month, singles = {}, None, []
    with tempfile.TemporaryDirectory() as tmp:
        if len(set(rule_links)) != 1:
            problems.append(f"na naslovnici nije nađen (jedan) raspored reciklabilnog otpada: {rule_links}")
        else:
            url = rule_links[0]
            text = pdf_text(url, tmp, "rec.pdf")
            if "prvi tjedan u mjesecu" not in text or "slijedeći radni dan" not in text:
                problems.append("tekst rasporeda reciklabilnog otpada se promijenio (prvi tjedan / slijedeći radni dan)")
            for day, names in re.findall(r"^\s*(%s):\s*(.+)$" % "|".join(DAYS), text, re.M):
                for n in names.split(","):
                    n = " ".join(n.split())
                    if n in rec_rule:
                        problems.append(f"{n} dvaput u rasporedu reciklabilnog otpada")
                    rec_rule[n] = DAYS.index(day)
            up = re.search(r"/uploads/(\d{4})/(\d\d)/", url)
            uy, um = int(up.group(1)), int(up.group(2))
            start_month = 1 if uy < year else um + 1 if uy == year else 13
            print(f"Reciklabilni: {url} (objavljeno {um:02d}/{uy}), pravilo od mjeseca {start_month}: {rec_rule}")
        for t, u in links:
            m = re.search(r"Obavijest za odvoz korisnog otpada za (\d{2})\.(\d{2})\.(\d{4})", t)
            if not m or int(m.group(3)) != year:
                continue
            text = " ".join(pdf_text(u, tmp, "obavijest.pdf").split())
            n = re.search(r"SAKUPLJAT ĆEMO (\d{2})\.(\d{2})\.(\d{4})\.? \( ?(%s) ?\)" % "|".join(DAYS), text)
            if not n:
                problems.append(f"obavijest {u}: ne nalazim datum")
                continue
            d = date(int(n.group(3)), int(n.group(2)), int(n.group(1)))
            if DAYS[d.weekday()] != n.group(4):
                problems.append(f"obavijest {u}: {d} nije {n.group(4)}")
            if d != date(int(m.group(3)), int(m.group(2)), int(m.group(1))):
                print(f"Napomena: poveznica kaže {m.group(1)}.{m.group(2)}., obavijest (PDF) {d:%d.%m.%Y.}; uzet je datum iz PDF-a")
            singles.append(d)
    print(f"Pojedinačni odvozi reciklabilnog otpada iz obavijesti: {[f'{d:%d.%m.}' for d in singles]}")

    # 3. units and zones
    su, wi = units(SUMMER), units(WINTER)
    bases = defaultdict(set)
    for name, part in list(su) + list(wi):
        bases[name].add(part)
    rows_by_unit = {}
    for name, parts in bases.items():
        for part in (sorted(p for p in parts if p) or [None]):
            s = su.get((name, part)) or su.get((name, None))
            w = wi.get((name, part)) or wi.get((name, None))
            if not s or not w:
                problems.append(f"{name} {part or ''}: nema dana za ljeto ili zimu")
                continue
            r = rec_rule.get(name.upper())
            rows_by_unit[name, part] = (tuple(sorted(s)), tuple(sorted(w)), r)
    missing = sorted(set(rec_rule) - {n.upper() for n in bases})
    if missing:
        print(f"Samo u rasporedu reciklabilnog otpada (nema miješanog, izostavljeno): {missing}")
    groups = defaultdict(list)
    order = [k for wd in sorted(SUMMER) for k in rows_by_unit if (k in su and wd in su[k]) or
             (k[1] and (k[0], None) in su and wd in su[k[0], None])]
    for k in dict.fromkeys(order):
        groups[rows_by_unit[k]].append(k)

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": NAPOMENE + [
        "Reciklabilni otpad: prvi tjedan u mjesecu po naseljima (raspored iz srpnja 2026.; 'prvi tjedan' je shvaćen "
        "kao prvi taj dan u tjednu u mjesecu, od 1. do 7., kao u rasporedima tvrtke iz 2024.), primijenjeno od "
        "sljedećeg mjeseca nakon objave; na blagdan sljedeći radni dan (pravilo iz PDF-a). Ranije u godini: "
        "pojedinačni odvozi iz obavijesti (" + ", ".join(f"{d:%d.%m.%Y.}" for d in singles) + ")."
        + (" Navedeno samo za reciklabilni otpad (nema u tablicama miješanog otpada), nije uključeno: "
           + ", ".join(m.title() for m in missing) + "." if missing else "")], "zone": {}}
    for (s_days, w_days, rday), members in groups.items():
        out = {}
        d = date(year, 1, 1)
        while d.year == year:
            if d.weekday() in (s_days if d.month in SUMMER_MONTHS else w_days):
                out[d] = ["M", False]
            d += timedelta(days=1)
        rec_dates = [(x, False) for x in singles]
        if rday is not None and start_month:
            for m in range(start_month, 13):
                base = first_week(year, m, rday)
                new = next_working(base, hol)
                rec_dates.append((new, new != base))
        for x, moved in rec_dates:
            cur = out.setdefault(x, ["", False])
            if "P" in cur[0]:
                problems.append(f"{members[0]}: P dvaput {x}")
            cur[0] += "P"
            cur[1] = cur[1] or moved
        per = Counter((c, x.month) for x, (codes, _) in out.items() for c in codes)
        for (c, m), n in per.items():
            lim = (1, 1) if c == "P" else (4, 10)
            if not lim[0] <= n <= lim[1]:
                problems.append(f"{members[0]}: {c} {n} puta u mjesecu {m}")
        names = [f"{n} ({p})" if p else n for n, p in members]
        k = str(len(data["zone"]) + 1)
        sd = " i ".join(INSTR[i] for i in s_days)
        wd = " i ".join(INSTR[i] for i in w_days)
        zone = {
            "jls": "Skradin",
            "podrucje": ", ".join(names) + f" – ljeti {sd}, zimi {wd}",
            "ulice": names,
            "napomena": f"Miješani otpad ljeti (travanj–listopad) {sd}, zimi (studeni–ožujak) {wd} – prema starijoj "
                        "tablici bez godine (2019.). "
                        + (f"Reciklabilni otpad {'prva' if rday in (2, 5, 6) else 'prvi'} {DAYS[rday].lower()} u mjesecu "
                           f"(od {start_month}. mjeseca)."
                           if rday is not None else "Naselje nije navedeno u rasporedu reciklabilnog otpada (2026.); "
                           "uključeni su samo odvozi iz općih obavijesti."),
        }
        prev = old["zone"].get(k, {})
        keep = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("ulice") == names else {}
        zone["raw"] = {**keep, str(year): podaci.month_lines([(x, c, mv) for x, (c, mv) in out.items()])}
        data["zone"][k] = zone
        print(f"Zona {k}: {zone['podrucje']} – {dict(Counter(c for codes, _ in out.values() for c in codes))}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
