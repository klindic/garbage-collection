"""Daruvar, Dežanovac, Đulovac, Končanica, Sirač: Darkom d.o.o. (darkom-daruvar.hr), colour calendars per zone.

    python3 -m izvori.darkom [--year 2026]

The page "Raspored odvoza komunalnog otpada" links one Excel PDF per zone (Grad Daruvar 1-4, the
municipalities, the settlements Kip and Šibovac); the file names keep an old upload folder, so the
year in the title is checked. Each PDF is a 12-month grid read by row and column (komunalac_jurdani.mreza;
a misprinted number on an empty day is only reported). Green cells mark the mixed waste days: in Daruvar
every working day of every other week, and the weekday of a street comes from the HTML street list
"Raspored odvoza otpada na području grada Daruvara" (streets matched by name; a street the list gives
under several days goes into each of those zones, a street it does not list gets a zone without mixed
waste); in the municipalities only the days of their rounds are green, one zone per weekday. A cell
split yellow/blue is the monthly plastic and paper day. Biowaste ("Biootpad - SVAKI PONEDJELJAK") is
printed for the Daruvar zones only. Holidays: the notes under the months ("1. siječnja - Nova godina
(odrada 2. siječnja)") give the substitute days; other holidays follow the printed rule "U slučaju
neradnog dana, odvoz otpada vršit će se prvi idući radni dan" (Monday to Friday); such days are marked
as moved, and a plastic/paper day moved before a holiday is recognised by the holiday in its week.
"""
import argparse
import html as htmlmod
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.komunalac_jurdani import mreza, short
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import colour

SLUG = "darkom"
SITE = "https://www.darkom-daruvar.hr"
PAGE = SITE + "/raspored-odvoza-komunalnog-otpada/"
STREETS = SITE + "/raspored-odvoza-otpada-na-podrucju-grada-daruvara/"
GORNJI = SITE + "/2025/04/02/obavijest-promjena-dana-odvoza-mko-gornji-daruvar/"
FILES = {  # PDF name: (JLS, title in the PDF, zone label)
    "Daruvar1": ("Daruvar", "GRAD DARUVAR 1", "Daruvar 1"), "Daruvar2": ("Daruvar", "GRAD DARUVAR 2", "Daruvar 2"),
    "Daruvar3": ("Daruvar", "GRAD DARUVAR 3", "Daruvar 3"), "Daruvar4": ("Daruvar", "GRAD DARUVAR 4", "Daruvar 4"),
    "Dezanovac": ("Dežanovac", "OPĆINA DEŽANOVAC", "Dežanovac"), "Dulovac": ("Đulovac", "OPĆINA ĐULOVAC", "Đulovac"),
    "Koncanica": ("Končanica", "OPĆINA KONČANICA", "Končanica"), "Sirac": ("Sirač", "OPĆINA SIRAČ", "Sirač"),
    "Kip-i-Sibovac": ("Sirač", "NASELJA KIP I ŠIBOVAC", "Kip i Šibovac"),
}
GREEN, YELLOW, BLUE = (0.439, 0.678, 0.278), (1.0, 1.0, 0.0), (0.0, 0.69, 0.941)
EMPTY = {None, (1.0, 1.0, 1.0), (0.859, 0.859, 1.0)}  # white, weekend lavender
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DAN_I = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom"]
HEAD = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "ČETVRTAK": 3, "PETAK": 4}
BIO = {"SVAKI PONEDJELJAK": 0, "SVAKI UTORAK": 1, "SVAKA SRIJEDA": 2, "SVAKI ČETVRTAK": 3, "SVAKI PETAK": 4}
GEN = ["siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja", "kolovoza", "rujna", "listopada",
       "studenoga", "prosinca"]
GENERIC = {"ulica", "ul", "trg", "šet", "dr"}
# spellings that differ between the zone PDFs and the street list (zone PDF -> street list)
ALIAS = {"drage biželja": "d. bizelja", "t. g. massaryka": "t. g. masaryka", "d. cesrića": "d. cesarića",
         "f. burjana": "frante buriana", "trg b. nelipića": "trg b. nelepića", "cvjetna": "cvijetna",
         "prvomjska": "prvomajska", "p. zrinskog": "n. š. zrinskog", "p. zrinskog (zgrade)": "n. š. zrinskog",
         "52. samostalnog bataljuna": "samostalni bataljun", "j. račića": "j. rašića"}
EXTRA = {"gornji daruvar": (1, GORNJI)}  # streets missing from the list: weekday from a notice
PROVIDER = {
    "davatelj": "Darkom d.o.o.",
    "web": SITE,
    "zupanija": "Bjelovarsko-bilogorska",
    "jls": ["Daruvar", "Dežanovac", "Đulovac", "Končanica", "Sirač"],
    "nazivi": {"P": "Plastika", "K": "Papir"},
    "bioNapomena": "Biootpad (smeđi spremnik) odvozi se samo u Gradu Daruvaru, jednom tjedno prema zoni.",
}


def words(name):
    """(significant words, initials) of a street name, without generic words and qualifiers ('do ...', '(...)')."""
    s = re.split(r"\(| do | od |\. do ", name.lower() + " ")[0]
    toks = re.findall(r"[a-zčćšžđ0-9]+\.?", s)
    short = [w for w in toks if w.endswith(".") and len(w) <= 4 and w[:-1] not in ("dar", "bran", "šet", "ul")]
    return [w for w in toks if w not in short and w.strip(".") not in GENERIC], "".join(w[:-1] for w in short)


def same(a, b):
    """'Trg Bran. Daruvara' ~ 'Trg branitelja Daruvara', 'J. Runjanina' ~ 'Josipa Runjanina'; initials must agree."""
    (wa, ia), (wb, ib) = words(a), words(b)
    if ia and ib and ia != ib or not wa or not wb:
        return False
    if len(wa) != len(wb):  # a first name written out on one side only: 'Marina Držića' / 'M. Držića'
        (wa, ia), (wb, ib) = sorted([(wa, ia), (wb, ib)], key=lambda x: len(x[0]))
        extra = wb[:len(wb) - len(wa)]
        if ia and "".join(w[0] for w in extra) != ia[:len(extra)] or not ia and len(wa) > 1:
            return False  # without initials only a bare surname form may match ('Jelačićeva')
        wb = wb[len(extra):]
    return all(x.strip(".")[:5] == y.strip(".")[:5] or (x.endswith(".") and y.startswith(x[:-1]))
               or (y.endswith(".") and x.startswith(y[:-1])) for x, y in zip(wa, wb))


def street_list(page):
    """[(weekday, name, display)] from the HTML street list."""
    out = []
    for m in re.finditer(r"<h5[^>]*>\s*(PONEDJELJAK|UTORAK|SRIJEDA|ČETVRTAK|PETAK)\s*</h5>(.*?)</ul>", page, re.S):
        for li in re.findall(r"<li>(.*?)</li>", m.group(2), re.S):
            parts = [" ".join(htmlmod.unescape(re.sub(r"<[^>]+>", "", p)).split()).strip(" –-")
                     for p in re.split(r"<br\s*/?>", li)]
            head, subs = parts[0], [p for p in parts[1:] if p]
            if subs:  # "Donji Daruvar" with its streets
                out += [(HEAD[m.group(1)], s, f"{s} ({head})") for s in subs]
            elif " – " in head:  # "Dabrovac – Doljani"
                a, b = head.split(" – ", 1)
                out.append((HEAD[m.group(1)], a, f"{a} ({b})"))
            elif " i dio niza " in head:  # "Zagrebačka i dio niza Jelačićeva od Cerika do SAN-MET-a"
                a, b = head.split(" i dio niza ", 1)
                out += [(HEAD[m.group(1)], a, a), (HEAD[m.group(1)], b, f"{b} (dio niza)")]
            else:
                out.append((HEAD[m.group(1)], head, head))
    return out


def zone_streets(text):
    """'A, B (x, y), C' after the title -> [('A', None), ('B', None), ('x', 'B'), ('y', 'B'), ('C', None)]."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    out = []
    for p in (" ".join(p.split()).strip() for p in parts + [cur]):
        m = re.fullmatch(r"(Vrbovac|Doljani) \((.+)\)", p)  # a settlement with its streets
        if m:
            out.append((m.group(1), None))
            out += [(s.strip(), m.group(1)) for s in m.group(2).split(",")]
        elif p:
            out.append((p, None))
    return out


def weekdays(name, parent, lst):
    """Weekdays of a zone street in the street list ({} if not listed) and the names it matched."""
    key = ALIAS.get(name.lower(), name)
    hits = [(w, disp) for w, n, disp in lst if same(key, n)]
    if not hits and name.lower() in EXTRA:
        return {EXTRA[name.lower()][0]}, [EXTRA[name.lower()][1]]
    if not hits and parent:
        return weekdays(parent, None, lst)
    return {w for w, _ in hits}, [d for _, d in hits]


def notes(text, year, problems):
    """{holiday: substitute day} from '1. siječnja - Nova godina (odrada 2. siječnja)'."""
    out = {}
    hol = set(pravila.blagdani(year))
    for d1, m1, d2, m2 in re.findall(r"(\d{1,2})\. ([a-zčćšž]+) - [^()]*?\(odrada (\d{1,2})\. ([a-zčćšž]+)\)", text):
        try:
            a, b = date(year, GEN.index(m1) + 1, int(d1)), date(year, GEN.index(m2) + 1, int(d2))
        except ValueError:
            problems.append(f"nejasna napomena o blagdanu: {d1}. {m1} / {d2}. {m2}")
            continue
        if a not in hol or not 0 < (b - a).days < 5 or b.weekday() > 4:
            problems.append(f"napomena: {a} -> {b} nije vjerojatna")
        out[a] = b
    return out


def next_workday(d, hol):
    while d.weekday() > 4 or d in hol:
        d += timedelta(days=1)
    return d


def read(page, year, name, problems):
    """Calendar data of one PDF."""
    text = " ".join((page.extract_text() or "").split())
    cells, probs = mreza(page, year, strict=False)
    problems += [f"{name}: {p}" for p in probs]
    wrong = [d for d, c in sorted(cells.items()) if c["printed"] != d.day]
    for d in wrong:  # a misprint in the provider's grid; the date comes from the position
        print(f"{name}: u kalendaru na mjestu {d:%d.%m.} piše {cells[d]['printed']} (datum prema položaju)")
    if len(wrong) > 2:
        problems.append(f"{name}: {len(wrong)} krivo otisnutih brojeva, raspored stupaca možda nije dobar")
    hol = set(pravila.blagdani(year))
    moves = notes(text, year, problems)
    green = {d for d, c in cells.items() if c["fill"] == GREEN}
    pk = []
    rects = [r for r in page.rects if r.get("fill") and r["width"] > 3 and r["height"] > 3]
    for d, c in sorted(cells.items()):
        cols = {colour(r) for r in rects if r["x0"] < c["x"] + 5 and r["x1"] > c["x"] - 5 and r["top"] <= c["y"] <= r["bottom"]}
        if YELLOW in cols or BLUE in cols:
            if not {YELLOW, BLUE} <= cols:
                problems.append(f"{name}: {d:%d.%m.} samo {'plastika' if YELLOW in cols else 'papir'}")
            pk.append(d)
        elif c["fill"] not in EMPTY | {GREEN}:
            problems.append(f"{name}: nepoznata boja {c['fill']} na {d:%d.%m.}")
    bio = [w for k, w in BIO.items() if f"Biootpad - {k}" in text]
    return {"text": text, "green": green, "pk": pk, "moves": moves, "hol": hol, "bio": bio[0] if bio else None}


def mixed(cal, wd, year, problems, name):
    """[(date, moved)] of the mixed waste round on weekday wd: every other week as coloured; a holiday in a
    week of the round goes to the noted substitute or the next working day, which must be coloured."""
    regular = sorted(d for d in cal["green"] if d.weekday() == wd)
    parity = Counter(d.isocalendar()[1] % 2 for d in regular).most_common(1)[0][0]
    for d in cal["pk"]:  # a plastic/paper cell can only show one colour
        if d.weekday() == wd and d.isocalendar()[1] % 2 == parity and d not in cal["hol"]:
            print(f"{name}: {d:%d.%m.} je dan plastike i papira u tjednu odvoza miješanog otpada ({DAN[wd]}); "
                  "pretpostavka: tog dana odvozi se i miješani otpad")
            regular.append(d)
    regular.sort()
    out = [(d, False) for d in regular if d.isocalendar()[1] % 2 == parity]
    for d in regular:
        if d.isocalendar()[1] % 2 != parity:
            problems.append(f"{name}: {d:%d.%m.} u tjednu bez odvoza ({DAN[wd]})")
    for h in sorted(cal["hol"]):
        if h.weekday() == wd and h.isocalendar()[1] % 2 == parity:
            new = cal["moves"].get(h) or next_workday(h, cal["hol"])
            if new in cal["green"] or new in cal["pk"]:
                out.append((new, True))
            else:
                problems.append(f"{name}: blagdan {h:%d.%m.}, a zamjenski dan {new:%d.%m.} nije označen")
    weeks = {(d - timedelta(days=d.weekday())) for d, moved in out if not moved} | \
            {h - timedelta(days=h.weekday()) for h in cal["hol"] if h.weekday() == wd}
    monday = date(year, 1, 1) - timedelta(days=date(year, 1, 1).weekday())
    while monday.year <= year:
        d = monday + timedelta(days=wd)
        if d.year == year and d.isocalendar()[1] % 2 == parity and monday not in weeks:
            problems.append(f"{name}: nema odvoza miješanog otpada u tjednu {d:%d.%m.} ({DAN[wd]})")
        monday += timedelta(days=7)
    return sorted(out)


def weekly(year, wd, cal):
    """Every weekday wd, a holiday moved to the noted substitute or the next working day."""
    out = []
    for d in pravila.tjedno(year, ["pon", "uto", "sri", "čet", "pet"][wd]):
        if d in cal["hol"]:
            out.append((cal["moves"].get(d) or next_workday(d, cal["hol"]), True))
        else:
            out.append((d, False))
    return out


def plastic_paper(cal, problems, name):
    """[(date, moved)] of the split cells: one a month on one weekday, earlier only before a holiday that week."""
    wd = Counter(d.weekday() for d in cal["pk"]).most_common(1)[0][0]
    out = []
    for d in cal["pk"]:
        regular = d + timedelta(days=wd - d.weekday())
        if d.weekday() != wd and regular not in cal["hol"]:
            problems.append(f"{name}: plastika i papir {d:%d.%m.} nije {DAN[wd]}, a tog tjedna nema blagdana")
        out.append((d, d.weekday() != wd))
    regular = [d + timedelta(days=wd - d.weekday()) for d in cal["pk"]]  # every 4 weeks
    if any((b - a).days != 28 for a, b in zip(regular, regular[1:])) or not 12 <= len(regular) <= 14:
        problems.append(f"{name}: plastika i papir nisu svaka 4 tjedna: {', '.join(f'{d:%d.%m.}' for d in cal['pk'])}")
    return wd, out


def build(rows_by_code):
    merged = defaultdict(lambda: ["", False])
    for code, rows in rows_by_code.items():
        for d, moved in rows:
            if code in merged[d][0]:
                continue
            merged[d][0] += code
            merged[d][1] |= moved
    return [(d, c, m) for d, (c, m) in sorted(merged.items())]


def check(name, rows, problems):
    per = defaultdict(Counter)
    for d, codes, _ in rows:
        per[d.month].update(codes)
    for m in range(1, 13):
        n = per[m]
        if n["M"] and not 1 <= n["M"] <= 3 or n["B"] and not 4 <= n["B"] <= 5 or not 1 <= n["P"] == n["K"] <= 2:
            problems.append(f"{name}: {m}. mjesec {dict(n)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = fetch(PAGE).decode("utf-8", "replace")
    urls = {}
    for url in re.findall(r'href="([^"]+\.pdf)"', page):
        stem = url.rsplit("/", 1)[1][:-4]
        if stem in FILES:
            urls[stem] = url
    missing = [f for f in FILES if f not in urls]
    if missing:
        sys.exit(f"Nema PDF-a za {', '.join(missing)} na {PAGE}")
    lst = street_list(fetch(STREETS).decode("utf-8", "replace"))
    if len({w for w, _, _ in lst}) != 5 or len(lst) < 80:
        sys.exit(f"Popis ulica na {STREETS} se promijenio ({len(lst)} ulica)")
    problems, zones = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for stem, (jls, title, label) in FILES.items():
            path = Path(tmp) / f"{stem}.pdf"
            fetch(urls[stem], path)
            with pdfplumber.open(path) as pdf:
                cal = read(pdf.pages[0], year, label, problems)
            text = cal["text"]
            if f"U {year}. GODINI" not in text or title not in text:
                problems.append(f"{label}: naslov nije '{title}' za {year}. ({urls[stem]})")
                continue
            pk_wd, pk = plastic_paper(cal, problems, label)
            green_wd = sorted(w for w, n in Counter(d.weekday() for d in cal["green"]).items() if n >= 10)
            print(f"{label}: miješani {'/'.join(DAN[w] for w in green_wd)}, plastika i papir {DAN[pk_wd]}"
                  + (f", biootpad {DAN[cal['bio']]}" if cal["bio"] is not None else "")
                  + f"; blagdani: {', '.join(f'{a:%d.%m.}->{b:%d.%m.}' for a, b in cal['moves'].items())}")
            base = {"P": pk, "K": pk}
            if cal["bio"] is not None:
                base["B"] = weekly(year, cal["bio"], cal)
            pk_note = f"Plastika i papir svaka četiri tjedna ({DAN_I[pk_wd]})."
            bio_note = f" Biootpad {DAN_I[cal['bio']]}." if cal["bio"] is not None else ""
            if jls == "Daruvar":
                body = text.split(title, 1)[1].split("LEGENDA", 1)[0]
                groups = defaultdict(list)
                for name, parent in zone_streets(body):
                    wds, hit = weekdays(name, parent, lst)
                    shown = f"{name} ({parent})" if parent else name
                    for w in wds or {None}:
                        groups[w].append((shown, len(wds) > 1))
                    print(f"   {shown:45} -> {', '.join(DAN[w] for w in sorted(wds)) or 'nije na popisu'}  {hit}")
                for w in sorted(groups, key=lambda w: (w is None, w)):
                    ulice = [s for s, _ in groups[w]]
                    double = [s for s, amb in groups[w] if amb]
                    if w is None:
                        rows = build(base)
                        desc = f"{label} – dan odvoza miješanog otpada nije objavljen: {short(ulice)}"
                        note = ("Ove ulice nisu na Darkomovom popisu ulica po danima, pa dan odvoza miješanog otpada "
                                "(svaki drugi tjedan) nije poznat. " + pk_note + bio_note)
                    else:
                        rows = build({**base, "M": mixed(cal, w, year, problems, label)})
                        desc = f"{label} – {DAN[w]}: {short(ulice)}"
                        note = f"Miješani otpad svaki drugi tjedan {DAN_I[w]}. " + pk_note + bio_note
                        if double:
                            note += (" Ulice " + ", ".join(double) + " su na popisu ulica za više dana (vjerojatno dio "
                                     "ulice), pa su i u zonama tih dana.")
                    zones.append(({"jls": jls, "podrucje": desc, "opis": f"{label}: " + " ".join(body.split()),
                                   "ulice": ulice, "napomena": note}, rows))
            else:
                only_fri = re.search(r"Petkom se miješani komunalni otpad kupi samo u naseljima: ([^!]+)!", text)
                for w in green_wd:
                    rows = build({**base, "M": mixed(cal, w, year, problems, label)})
                    if only_fri and w == 4:
                        ulice = [s.strip() for s in only_fri.group(1).split(",")]
                        desc, note = f"{label} – petak: {', '.join(ulice)}", ""
                    elif only_fri:
                        ulice, desc, note = [jls], f"{label} – {DAN[w]}: ostala naselja", ""
                    elif label == "Kip i Šibovac":
                        ulice, desc, note = ["Kip", "Šibovac"], f"Kip i Šibovac – {DAN[w]}", ""
                    elif len(green_wd) > 1:
                        ulice = [jls]
                        desc = f"{label} – naselja s odvozom miješanog otpada {DAN_I[w]}"
                        note = (f"U kalendaru općine miješani otpad je označen {' i '.join(DAN_I[x] for x in green_wd)}; "
                                "koja naselja imaju koji dan nije objavljeno. ")
                    else:
                        ulice, desc, note = [jls], f"{label} – {DAN[w]}", ""
                    zones.append(({"jls": jls, "podrucje": desc, "opis": f"{label}: {title.title()}", "ulice": ulice,
                                   "napomena": note + f"Miješani otpad svaki drugi tjedan {DAN_I[w]}. " + pk_note}, rows))
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": PAGE, "napomene": [
        "Miješani komunalni otpad (zeleni spremnik) odvozi se svaki drugi tjedan; u Daruvaru dan ovisi o ulici "
        f"(popis ulica po danima: {STREETS}).",
        "Plastika (žuti) i papir (plavi spremnik) odvoze se isti dan, svaka četiri tjedna prema kalendaru zone.",
        "Blagdani: zamjenski dani prema napomenama u kalendarima („odrada”); inače se, prema kalendaru, odvoz u slučaju "
        "neradnog dana obavlja prvi idući radni dan (ponedjeljak–petak). Takvi dani označeni su kao pomaknuti.",
        "Ljeti (od 29.6. tijekom srpnja i kolovoza) otpad se sakuplja od 6:00 sati; spremnike iznijeti večer prije.",
        "Glomazni otpad jednom godišnje bez naknade (do 5 m³) uz zahtjev; kontakt: otpad@darkom-daruvar.hr, 043/440-750.",
    ], "zone": {}}
    for i, (zone, rows) in enumerate(zones, 1):
        check(f"zona {i} ({zone['podrucje'][:30]})", rows, problems)
        if not zone["ulice"]:
            problems.append(f"zona {i}: nema ulica")
        prev = old.get(str(i), {})
        prev = prev.get("raw", {}) if prev.get("podrucje") == zone["podrucje"] else {}
        data["zone"][str(i)] = {**zone, "raw": {**prev, str(year): podaci.month_lines(rows)}}
        n = Counter(c for _, codes, _ in rows for c in codes)
        print(f"zona {i:2} {zone['podrucje'][:70]:70} " + " ".join(f"{c}{n[c]}" for c in "MBPK" if n[c]))
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
