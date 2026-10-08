"""KTD Bilan d.o.o.: Općina Orebić, six groups of settlements, per published period.

    python3 -m izvori.ktd_bilan [--year 2026]

KTD Bilan posts every new schedule as a Joomla article in "Obavijesti potrošačima" ("Raspored odvoza
komunalnog otpada za razdoblje 01.10.2026. – 03.05.2027."), with an HTML table: weekday(s) or "svaki drugi
utorak počevši od DD.MM.YYYY" | waste type | settlements. The list pages (5 articles each) are scanned for
these articles and for notices that extend a period ("produljuje se … do 22.06.2026",
"… prema postojećem rasporedu … do 18. svibnja 2026."). Glass (pink bags) goes on the first Thursday of the
month (footnote). Settlement names are mapped to fixed zones; within a period every settlement of a zone
must get the same rules, and tables with special cases the parser does not understand ("Samo
ponedjeljkom: …", "(samo utorak)") are skipped with a message, so nothing is guessed. Holiday changes are
published as separate notices and are not applied. Dates already in podaci/<slug>.json outside the periods
read now are kept, so re-runs add new periods.
"""
import argparse
import html as htmllib
import re
import sys
from collections import defaultdict
from datetime import date, timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "ktd-bilan"
SITE = "https://ktd-bilan.hr"
PAGE = SITE + "/index.php/odnosi-s-javnoscu/obavijesti-potrosacima"
LIST_PAGES = 3  # list pages of 5 articles: about four months of notices
MONTHS = ["siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja", "kolovoza", "rujna",
          "listopada", "studenoga", "prosinca"]
DAY_WORDS = {"ponedjelj": 0, "utor": 1, "srijed": 2, "četvrt": 3, "petak": 4, "petk": 4, "subot": 5, "nedjelj": 6}
DAN = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
TYPES = {"miješani": "M", "papir": "K", "plastika": "P"}
ZONES = {  # zone: (podrucje, settlements as written in the schedules)
    "1": ("Orebić, Stanković, Podvlaštica, Ruskovići", ["Orebić", "Stanković", "Podvlaštica", "Ruskovići"]),
    "2": ("Perna, Kućište, Viganj, Lovište", ["Perna", "Kućište", "Viganj", "Lovište"]),
    "3": ("Podgorje (Bilopolje, Gurići, Karmen, Lampalovo, Stinice, Kaljanovići)", ["Podgorje"]),
    "4": ("Mokalo", ["Mokalo"]),
    "5": ("Pelješka župa (Potomje, Pijavičino, Kuna, Oskorušno)", ["Pelješka župa"]),
    "6": ("Trstenik, Crkvice, V. Prapratna, Borak, Harlovići, Postup, Borje, Podobuće",
          ["Trstenik", "Crkvice", "V. Prapratna", "Borak", "Harlovići", "Postup", "Borje", "Podobuće"]),
}
STREETS = {"3": ["Podgorje", "Bilopolje", "Gurići", "Karmen", "Lampalovo", "Stinice", "Kaljanovići"],
           "5": ["Pelješka župa", "Potomje", "Pijavičino", "Kuna", "Oskorušno"]}
ALIASES = {"Rusković": "Ruskovići", "Podobuče": "Podobuće", "Bilopolje": "Podgorje", "Gurići": "Podgorje",
           "Karmen": "Podgorje", "Lampalovo": "Podgorje", "Stinice": "Podgorje", "Kaljanovići": "Podgorje",
           "Potomje": "Pelješka župa", "Pijavičino": "Pelješka župa", "Kuna": "Pelješka župa",
           "Oskorušno": "Pelješka župa"}
IGNORE = {"ostala manja naselja", "sve ulice", "sva naselja", "sva mjesta"}
PROVIDER = {
    "davatelj": "KTD Bilan d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Orebić"],
    "nazivi": {"M": "Miješani komunalni otpad (zelena kanta)", "K": "Papir i karton (plava kanta)",
               "P": "Plastika i folija (žuta kanta)", "S": "Staklena ambalaža (roza vreće)"},
    "napomene": [
        "KTD Bilan objavljuje raspored za svako razdoblje posebno (ljetni, prijelazni i zimski); upisana su "
        "razdoblja čije se tablice mogu pouzdano pročitati.",
        "Subotom i nedjeljom nema prikupljanja (osim ljeti prema rasporedu). Staklena ambalaža prikuplja se "
        "prvog četvrtka u mjesecu u roza vrećama koje se preuzimaju u KTD Bilan.",
        "Pravne osobe (trgovine, restorani, kampovi, hoteli): papir i plastika po pozivu.",
        "Izmjene zbog blagdana KTD Bilan objavljuje posebnim obavijestima; ovdje nisu upisane.",
        "Kontakt: Fiskovićeva 2, Orebić, 020 713 073, info@bilan.hr.",
    ],
}


def plain(fragment):
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def articles(pages):
    """[(id, title, url, publication date)] of the articles listed on the list pages, oldest first."""
    out = {}
    for page in pages:
        for body in re.findall(r"<article(.*?)</article>", page, re.S):
            m = re.search(r'<h1[^>]*>\s*<a href="([^"]*/(\d+)-[^"]*)"[^>]*>(.*?)</a>', body, re.S)
            when = re.search(r'<time datetime="(\d{4}-\d\d-\d\d)', body)
            if m:
                out[int(m.group(2))] = (plain(m.group(3)), SITE + m.group(1), when and date.fromisoformat(when.group(1)))
    return [(i, *out[i]) for i in sorted(out)]


def article(url):
    page = fetch(url).decode("utf-8", "replace")
    m = re.search(r"<article.*?</article>", page, re.S)
    return m.group(0) if m else ""


def ddmmyyyy(text):
    return [date(int(y), int(m), int(d)) for d, m, y in re.findall(r"(\d{1,2})\.\s?(\d{1,2})\.?\s?(\d{4})", text)]


def day_numbers(text):
    t = text.lower()
    return sorted({n for w, n in DAY_WORDS.items() if re.search(rf"\b{w}", t)})


def grid(table):
    """HTML table -> rows of cell html, rowspans filled in."""
    rows, carry = [], {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = re.findall(r"<t([dh])([^>]*)>(.*?)</t[dh]>", tr, re.S)
        row, col = [], 0
        for _, attrs, content in cells:
            while col in carry:
                row.append(carry[col][1])
                carry[col][0] -= 1
                if not carry[col][0]:
                    del carry[col]
                col += 1
            span = re.search(r'rowspan="(\d+)"', attrs)
            if span and int(span.group(1)) > 1:
                carry[col] = [int(span.group(1)) - 1, content]
            row.append(content)
            col += 1
        while col in carry:
            row.append(carry[col][1])
            carry[col][0] -= 1
            if not carry[col][0]:
                del carry[col]
            col += 1
        rows.append(row)
    return rows


def settlements(cell, issues):
    """Settlement cell -> canonical names; None when the cell has special cases that are not understood."""
    lines = [plain(l) for l in re.split(r"<br\s*/?>|\n", cell)]
    names = []
    for line in lines:
        if not line or line.lower().startswith(("pravne osobe", "samo pravne osobe")):
            continue
        if ":" in line.split(",")[0] and day_numbers(line.split(":")[0]) or re.search(
                r"\((?:samo )?\w+(?: i \w+)?\)", line) and day_numbers(" ".join(re.findall(r"\(([^)]*)\)", line))):
            return None  # "Samo ponedjeljkom: …", "(samo Utorak)": different days inside one row
        line = re.sub(r"^Pelješka župa\s*[–:-]+\s*", "Pelješka župa, ", line)
        for part in re.split(r",\s*|\s+i\s+(?=ostala)", re.sub(r"[()]", ",", line)):
            part = part.strip(" .–-")
            if not part or part.lower() in IGNORE:
                continue
            name = ALIASES.get(part, part)
            if not any(name in members for _, members in ZONES.values()):
                issues.append(f"nepoznato naselje {part!r}")
                continue
            names.append(name)
    return names


def read_period(title, body):
    """{'start', 'end', 'zones': {zone: {(code, weekday, anchor or None)}}, 'glass', 'issues': [...]} or None.

    'issues' lists why the period cannot be used (special cases, unknown names, a zone whose settlements
    get different rules)."""
    dates = ddmmyyyy(title.replace("–", "-"))
    table = re.search(r"<table.*?</table>", body, re.S)
    if len(dates) != 2:
        return None
    issues, rules = [], defaultdict(set)
    for row in grid(table.group(0))[1:] if table else []:
        if len(row) != 3:
            issues.append(f"redak s {len(row)} stupaca")
            continue
        when, kind, where = plain(row[0]), plain(row[1]).lower(), row[2]
        code = next((c for w, c in TYPES.items() if w in kind), None)
        if code is None:
            issues.append(f"nepoznata vrsta {kind!r}")
            continue
        if plain(where).lower().startswith("samo pravne osobe"):
            continue
        anchor = ddmmyyyy(when)
        days = day_numbers(when)
        if anchor and (len(anchor) != 1 or not re.search(r"svak\w+ drug\w+", when, re.I)
                       or days != [anchor[0].weekday()]):
            issues.append(f"ne razumijem {when!r}")
            continue
        names = settlements(where, issues)
        if names is None:
            issues.append(f"posebna pravila u retku {when!r}: {plain(where)!r}")
            continue
        for n in names:
            rules[n] |= {(code, d, anchor[0] if anchor else None) for d in days}
    if not table:
        issues.append("nema tablice")
    zones = {}
    for z, (_, members) in ZONES.items():
        sets = {frozenset(rules.get(n, ())) for n in members}
        if len(sets) != 1:
            issues.append(f"naselja zone {z} imaju različita pravila")
        elif not any(code == "M" for code, _, _ in next(iter(sets))):
            issues.append(f"zona {z} nema miješanog otpada")
        else:
            zones[z] = next(iter(sets))
    for code, wd, anchor in {r for rs in zones.values() for r in rs}:
        if anchor and not dates[0] <= anchor <= dates[1]:
            issues.append(f"početni datum {anchor:%d.%m.%Y} izvan razdoblja")
    glass = bool(re.search(r"Staklena ambalaža prikuplja se svakog prvog četvrtka u mjesecu", plain(body)))
    return {"start": dates[0], "end": dates[1], "zones": zones, "glass": glass, "issues": issues, "title": title}


def extension(title, body):
    """End date (exclusive) from 'produljuje se … do 22.06. 2026' or '… prema postojećem rasporedu … do 18. svibnja 2026.'."""
    text = plain(body)
    if not re.search(r"produljuje|nastavlja se prema postojećem rasporedu", title, re.I):
        return None
    m = re.search(r"\bdo (\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(rf"\bdo (\d{{1,2}})\. ({'|'.join(MONTHS)}) (\d{{4}})", text)
    return m and date(int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1)))


def merge(old_zone, rows, windows):
    keep = [r for r in podaci.iter_dates(old_zone) if not any(a <= r[0] <= b for a, b in windows)] if old_zone else []
    years = defaultdict(list)
    for r in keep + rows:
        years[r[0].year].append(r)
    return {str(y): podaci.month_lines(v) for y, v in sorted(years.items())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    pages = [fetch(PAGE + (f"?start={5 * i}" if i else "")).decode("utf-8", "replace") for i in range(LIST_PAGES)]
    periods = []
    for _, title, url, published in articles(pages):
        schedule = re.match(r"(ISPRAVLJENI )?RASPORED ODVOZA KOMUNALNOG OTPADA ZA RAZDOBLJE", title, re.I)
        if not schedule and not re.search(r"produljuje|nastavlja se prema postojećem rasporedu", title, re.I):
            continue
        body = article(url)
        if schedule:
            p = read_period(title, body)
            if p is None:
                problems.append(f"{title}: razdoblje nije pročitano")
            else:
                periods = [q for q in periods if q["start"] != p["start"]] + [p]  # a corrected article wins
            continue
        end = extension(title, body)
        valid = [p for p in periods if published and p["start"] <= published <= p["end"] + timedelta(days=1)]
        if end and valid:
            print(f"{title}: raspored {valid[-1]['title']} produljen do {end - timedelta(days=1):%d.%m.%Y}")
            valid[-1]["end"] = end - timedelta(days=1)
        elif end:
            print(f"{title}: nema rasporeda koji vrijedi {published:%d.%m.%Y} – produljenje nije primijenjeno")
    periods.sort(key=lambda p: p["start"])
    for p, nxt in zip(periods, periods[1:]):
        p["end"] = min(p["end"], nxt["start"] - timedelta(days=1))
    if periods and periods[-1]["issues"]:  # the newest schedule must be readable
        problems += [f"{periods[-1]['title']}: {i}" for i in periods[-1]["issues"]]
    for p in periods[:-1]:
        if p["issues"] and p["start"].year <= year <= p["end"].year:
            print(f"Preskočeno {p['start']:%d.%m.} – {p['end']:%d.%m.%Y} ({p['title']}): {'; '.join(p['issues'])}")
    periods = [p for p in periods if not p["issues"] and p["start"].year <= year <= p["end"].year]
    if not periods:
        problems.append(f"nema pročitanog rasporeda za {year}.")

    rows = {z: {} for z in ZONES}
    windows = []
    for p in periods:
        a, b = p["start"], p["end"]
        windows.append((a, b))
        print(f"{a:%d.%m.%Y} – {b:%d.%m.%Y}: {p['title']}")
        for z, rules in p["zones"].items():
            for code, wd, anchor in rules:
                d = anchor or a + timedelta(days=(wd - a.weekday()) % 7)
                while d <= b:
                    rows[z][d] = rows[z].get(d, "") + code
                    d += timedelta(days=14 if anchor else 7)
            if p["glass"]:
                for d in (d for y in range(a.year, b.year + 1) for d in pravila.mjesecno(y, "čet", 1)):
                    if a <= d <= b:
                        rows[z][d] = rows[z].get(d, "") + "S"

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, (podrucje, members) in ZONES.items():
        for d, c in rows[z].items():
            if len(set(c)) != len(c):
                problems.append(f"zona {z} {d}: ista vrsta dvaput ({c})")
        kinds = sorted({c for cs in rows[z].values() for c in cs})
        days = sorted({d.weekday() for d, c in rows[z].items() if "M" in c})
        zone = {"jls": "Orebić", "podrucje": f"{podrucje} – miješani {', '.join(DAN[d] for d in days)}",
                "ulice": STREETS.get(z, members)}
        if z == "6" and "K" not in kinds:
            zone["napomena"] = ("Papir i plastika: raspored navodi samo \"Pelješka župa (sva naselja)\"; nije jasno "
                                "odnosi li se to i na ova naselja pa ovdje nisu upisani.")
        if z == "4" and "P" not in kinds:
            zone["napomena"] = "Plastika: Mokalo nije navedeno ni u jednom retku za plastiku."
        zone["raw"] = merge(old.get(z), [(d, c, False) for d, c in rows[z].items()], windows)
        data["zone"][z] = zone
        print(f"Zona {z} ({zone['podrucje']}): {len(rows[z])} odvoza, vrste {''.join(kinds)}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
