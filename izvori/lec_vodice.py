"""Vodice, Tribunj: Leć d.o.o. (lec.hr), mixed and recyclable waste by street lists, summer and winter rules.

    python3 -m izvori.lec_vodice [--year 2026]

The pages "Raspored odvoza miješanog komunalnog otpada za kućanstva" and "Raspored odvoza reciklabilnog
otpada" hold one table per place (VODICE, SRIMA, TRIBUNJ): a street list with a winter and a summer row,
each with its months ("siječanj - travanj i listopad - prosinac") and days ("PONEDJELJAK i ČETVRTAK",
"PRVI I TREĆI UTORAK U MJESECU"). Both pages list the same street groups in the same order; they are
matched by position and the street lists must agree. The hinterland villages (Gaćelezi, Čista Mala,
Čista Velika, Grabovci) and the island of Prvić are only described in the intro text: the rules in
ZALEDJE and PRVIC below were taken from it, and the sentences they come from must still be on the page
word for word, so a changed text stops the script. Recyclable waste (paper, plastic, metal) is one code, P.
No holiday rule is published: the dates stay as computed, except the moves announced in the company's
notices linked from the page ("... umjesto 1.5.2026.g., petak, izvršiti će se u četvrtak 30.4.2026.g."),
which are marked as moved.
"""
import argparse
import difflib
import html
import re
import sys
from collections import Counter
from datetime import date, timedelta

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "lec-vodice"
SITE = "https://www.lec.hr"
MIXED = SITE + "/cistoca/odvoz-komunalnog-otpada/raspored-odvoza"
RECYC = SITE + "/cistoca/odvoz-reciklabilnog-otpada/raspored-odvoza-korisnog-otpada"
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
DAYS = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK", "SUBOTA", "NEDJELJA"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
INSTR = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
ORD = {"PRVI": 1, "PRVA": 1, "DRUGI": 2, "DRUGA": 2, "TREĆI": 3, "TREĆA": 3, "ČETVRTI": 4, "ČETVRTA": 4}
ORD_TXT = {1: "prvi", 2: "drugi", 3: "treći", 4: "četvrti"}
ORD_FEM = {1: "prva", 2: "druga", 3: "treća", 4: "četvrta"}  # srijeda, subota, nedjelja
PLACES = {"VODICE": "Vodice", "SRIMA": "Vodice", "TRIBUNJ": "Tribunj"}
# The season sentence for the town tables (checked against the months in the tables).
TOWN_TEXT = ("Područje grada Vodica, naselja Srima i Općine Tribunj - jedanput tjedno u periodu od 10. do kraja "
             "04. mjeseca (zimski termin), te dva puta tjedno u periodu od 05. do kraja 09. mjeseca (ljetni termin).")
# Hinterland villages: mixed waste Thursday, in July and August also Sunday; recyclables first Thursday.
ZALEDJE_TEXT = ("Područje naselja Gaćelezi, Čista Mala, Čista Velika i Grabovci - od siječnja do kraja lipnja i od "
                "rujna do kraja prosinca: 1 x tjedno, svaki četvrtak (zimski termin); srpanj i kolovoz: 2 x tjedno, "
                "četvrtak i nedjelja (ljetni termin)")
ZALEDJE_REC_TEXT = ("Za naselja Čista Velika, Čista Mala, Gaćelezi i Grabovci sakupljanje reciklabilnog otpada vrši "
                    "se svakog prvog četvrtka u mjesecu.")
ZALEDJE = {"mixed": [((1, 1), (6, 30), [3]), ((7, 1), (8, 31), [3, 6]), ((9, 1), (12, 31), [3])],
           "recyc": [(1, 3)]}
ZALEDJE_NAMES = ["Gaćelezi", "Čista Mala", "Čista Velika", "Grabovci"]
# Prvić (Prvić Luka, Šepurine): mixed waste only, by date periods.
PRVIC_TEXT = ("jedanput tjedno - petak - u periodu od 01. do 04. mjeseca (01.01.-27.04.) te od 11. do 12. mjeseca "
              "(03.11. -31.12.), dva puta tjedno - ponedjeljak i petak - u periodu od 28.04. do 08.06 . i od 29.09. "
              "do 02.11., tri puta tjedno - ponedjeljak, srijeda i petak - u periodu od 09.06. - 29.06. i od 01.09. "
              "- 28.09. te ponedjeljak, srijeda, petak i subota u periodu od 30.06. do 31.08.")
PRVIC = [((1, 1), (4, 27), [4]), ((4, 28), (6, 8), [0, 4]), ((6, 9), (6, 29), [0, 2, 4]),
         ((6, 30), (8, 31), [0, 2, 4, 5]), ((9, 1), (9, 28), [0, 2, 4]), ((9, 29), (11, 2), [0, 4]),
         ((11, 3), (12, 31), [4])]
PRVIC_RECYC_TEXT = ("Do uspostave uvjeta za sakupljanje reciklabilnog otpada korisnici s otoka Prvića takav otpad "
                    "mogu samostalno dovesti u Vodice, na adresu Put Gaćeleza 3/b.")
NOTICE = re.compile(r"umjesto (\d{1,2})\.(\d{1,2})\.(\d{4})\.\s*g?\.?,\s*(\w+),\s*"
                    r"izvr\w+ će se u (\w+) (\d{1,2})\.(\d{1,2})\.(\d{4})")
PROVIDER = {
    "davatelj": "Leć d.o.o.",
    "web": SITE,
    "izvor": MIXED,
    "zupanija": "Šibensko-kninska",
    "jls": ["Vodice", "Tribunj"],
    "nazivi": {"P": "Reciklabilni otpad (papir, plastika, metal)"},
    "bioNapomena": "Biootpad: kućno kompostiranje (kompostiranje u vlastitom komposteru).",
}
NAPOMENE = [
    "Raspored za kućanstva; za pravne osobe Leć objavljuje poseban raspored (nije uključen).",
    "Staklo i tekstil: odvoz četvrtkom i petkom na poziv korisnika (tipizirane vrećice: Obala Juričev Ive Cota 9, "
    "Vodice).",
    "Glomazni otpad: besplatno jednom godišnje do 4 m³ na zahtjev, tel. 022/443-787 ili info@lec.hr.",
    "Do uspostave reciklažnog dvorišta otpad se može samostalno dovesti na adresu Put Gaćeleza 3/b, Vodice "
    "(pon–pet 7–14 h).",
    "Okvirno vrijeme odvoza: zimi 6–13 h, ljeti 5–12 h.",
]


def text(fragment):
    """HTML fragment -> plain text on one line."""
    t = re.sub(r"<(br|/p|p)\b[^>]*>", " ", fragment)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("\xa0", " ")
    return " ".join(t.split())


def months_of(season):
    """'ZIMSKI TERMIN siječanj - travanj i listopad - prosinac 1 x tjedno' -> ({1, 2, 3, 4, 10, 11, 12}, 1)."""
    low = season.lower()
    out = set()
    for a, b in re.findall(r"([a-zčćšž]+)\s*-\s*([a-zčćšž]+)", low):
        if a not in MONTHS or b not in MONTHS:
            return None, None
        out |= set(range(MONTHS.index(a) + 1, MONTHS.index(b) + 2))
    m = re.search(r"(\d) x (tjedno|mjesečno)", low)
    return out, int(m.group(1)) if m else None


def rule_of(cell):
    """'PONEDJELJAK i ČETVRTAK okvirno ...' -> ('t', [0, 3]); 'PRVI I TREĆI UTORAK U MJESECU' -> ('m', [(1, 1), (3, 1)])."""
    t = cell.split("okvirno")[0].strip().upper()
    m = re.fullmatch(r"((?:\w+)(?: I \w+)*) (\w+) U MJESECU", t)
    if m:
        nums = m.group(1).split(" I ")
        if m.group(2) in DAYS and all(n in ORD for n in nums):
            return "m", [(ORD[n], DAYS.index(m.group(2))) for n in nums]
        return None
    days = t.split(" I ")
    return ("t", [DAYS.index(d) for d in days]) if all(d in DAYS for d in days) else None


def tables(page, problems):
    """{place: [(streets text, [(months, per, rule), ...]), ...]} from the tables under <h5>PLACE</h5>."""
    out = {}
    for place, body in re.findall(r"<h5>\s*(\w+)\s*</h5>\s*<table[^>]*>(.*?)</table>", page, re.S):
        groups = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
            cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
            if cells and "tnormal_head" in cells[0][0]:
                continue
            if cells and "rowspan" in cells[0][0]:
                groups.append([text(cells[0][1]), []])
                cells = cells[1:]
            if len(cells) != 2 or not groups:
                problems.append(f"{place}: neočekivan redak tablice: {text(row)[:80]!r}")
                continue
            season, day = text(cells[0][1]), text(cells[1][1])
            months, per = months_of(season)
            rule = rule_of(day)
            if not months or rule is None or per != len(rule[1]):
                problems.append(f"{place}: ne razumijem {season!r} / {day!r}")
                continue
            groups[-1][1].append((months, rule))
        out[place] = groups
    return out


def streets(cell):
    """'Račice I, II, Stablinac I, II i III, GrgurevTonča' -> ['Račice I', 'Račice II', 'Stablinac I', ...]."""
    out = []
    for part in cell.replace("GrgurevTonča", "Grgurev Tonča").split(","):
        part = " ".join(part.split())
        if not part:
            continue
        romans = re.fullmatch(r"([IVXL]+)(?: i ([IVXL]+))?", part)
        if romans and out:
            base = re.sub(r"\s+[IVXL]+$", "", out[-1])
            out += [f"{base} {r}" for r in romans.groups() if r]
            continue
        m = re.fullmatch(r"(.+?) ([IVXL]+) i ([IVXL]+)", part)
        out += [f"{m.group(1)} {m.group(2)}", f"{m.group(1)} {m.group(3)}"] if m else [part]
    return out


def letters(s):
    return re.sub(r"[^a-zčćšžđ]", "", s.lower())


def dates(year, seasons):
    """[(months, ('t', weekdays) | ('m', [(n, weekday)]))] -> sorted dates."""
    out = set()
    for months, (kind, items) in seasons:
        for m in months:
            first = date(year, m, 1)
            days = [first + timedelta(days=i) for i in range(31) if (first + timedelta(days=i)).month == m]
            if kind == "t":
                out |= {d for d in days if d.weekday() in items}
            else:
                for n, wd in items:
                    out.add([d for d in days if d.weekday() == wd][n - 1])
    return sorted(out)


def periods(year, spec):
    """[((m, d), (m, d), weekdays)] -> dates; the periods must tile the whole year."""
    out, covered = [], Counter()
    for (m0, d0), (m1, d1), wds in spec:
        d = date(year, m0, d0)
        while d <= date(year, m1, d1):
            covered[d] += 1
            if d.weekday() in wds:
                out.append(d)
            d += timedelta(days=1)
    bad = [d for d in covered if covered[d] != 1] + [date(year, 1, 1) + timedelta(days=i) for i in range(366)
                                                       if (date(year, 1, 1) + timedelta(days=i)).year == year
                                                       and date(year, 1, 1) + timedelta(days=i) not in covered]
    return sorted(out), bad


def describe_weekly(seasons):
    parts = []
    for months, (kind, items) in seasons:
        span = month_span(months)
        if kind == "t":
            parts.append(f"{span}: " + " i ".join(INSTR[i] for i in items))
        else:
            nums = " i ".join((ORD_FEM if items[0][1] in (2, 5, 6) else ORD_TXT)[n] for n, _ in items)
            parts.append(f"{span}: {nums} {DAN[items[0][1]]} u mjesecu")
    return "; ".join(parts)


def month_span(months):
    """{1, 2, 3, 4, 10, 11, 12} -> 'siječanj–travanj i listopad–prosinac'."""
    ms = sorted(months)
    runs, start = [], ms[0]
    for a, b in zip(ms, ms[1:] + [None]):
        if b != a + 1:
            runs.append(MONTHS[start - 1] + ("" if a == start else "–" + MONTHS[a - 1]))
            start = b
    return " i ".join(runs)


def notices(page, year, problems):
    """{date: (new date, area)} from the company's notices linked on the page."""
    moves = {}
    for link in sorted(set(re.findall(r'href="(novosti/[^"]*odvoz[^"]*)"', page))):
        body = fetch(f"{SITE}/{link}").decode("utf-8", "replace")
        main = text(re.split(r"Kategor", body.split("<h1", 1)[-1])[0])
        m = NOTICE.search(main)
        if not m:
            problems.append(f"obavijest {link} nije razumljiva: {main[:200]!r}")
            continue
        d1, m1, y1, day1, day2, d2, m2, y2 = m.groups()
        area = "Prvić" if "Prvić" in main else None
        old, new = date(int(y1), int(m1), int(d1)), date(int(y2), int(m2), int(d2))
        if DAN[old.weekday()] != day1.lower() or DAN[new.weekday()] != day2.lower() or abs((new - old).days) > 6:
            problems.append(f"obavijest {link}: {old} -> {new} nije vjerojatan pomak")
        if old.year == year:
            moves[old] = (new, "Prvić" if area else None)
            print(f"Obavijest {link}: {old:%d.%m.} -> {new:%d.%m.} ({'Prvić' if area else 'sve zone'})")
    return moves


def zone_rows(year, mixed, recyc, moves, area, problems, label):
    """[(date, codes, moved)] with notice moves applied."""
    out = {}
    for code, ds in (("M", mixed), ("P", recyc)):
        for d in ds:
            new, moved = d, False
            if d in moves and moves[d][1] in (None, area):
                new, moved = moves[d][0], True
            codes, was = out.get(new, ("", False))
            if code in codes:
                problems.append(f"{label}: {code} dvaput {new}")
            out[new] = (codes + code, was or moved)
    return [(d, c, m) for d, (c, m) in sorted(out.items())]


def check(label, rows, allowed, monthly, problems):
    """allowed: {code: set of weekdays}; monthly: {code: (min, max)} collections per month."""
    per = Counter()
    for d, codes, moved in rows:
        for c in codes:
            per[c, d.month] += 1
            if not moved and d.weekday() not in allowed[c]:
                problems.append(f"{label}: {c} {d} nije {'/'.join(DAN[i] for i in sorted(allowed[c]))}")
    for (c, m), n in per.items():
        lo, hi = monthly[c]
        if not lo <= n <= hi:
            problems.append(f"{label}: {c} {n} puta u mjesecu {m}")
    for c in monthly:
        months = {m for (cc, m) in per if cc == c}
        if len(months) != 12:
            problems.append(f"{label}: {c} samo u {len(months)} mjeseci")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    page_m = fetch(MIXED).decode("utf-8", "replace")
    page_r = fetch(RECYC).decode("utf-8", "replace")
    plain_m, plain_r = text(page_m), text(page_r)
    for needle, where, plain in ((TOWN_TEXT, "MKO", plain_m), (ZALEDJE_TEXT, "MKO", plain_m),
                                 (PRVIC_TEXT, "MKO", plain_m), (PRVIC_RECYC_TEXT, "MKO", plain_m),
                                 (ZALEDJE_REC_TEXT, "reciklabilni", plain_r)):
        if re.sub(r"\s", "", needle) not in re.sub(r"\s", "", plain):  # tags may split words
            problems.append(f"tekst na stranici ({where}) se promijenio, provjeriti pravila u skripti: {needle[:70]!r}…")
    mixed, recyc = tables(page_m, problems), tables(page_r, problems)
    if list(mixed) != list(PLACES) or list(recyc) != list(PLACES):
        problems.append(f"tablice na stranicama: {list(mixed)} / {list(recyc)}")
    moves = notices(page_m, year, problems)

    by_jls = {"Vodice": [], "Tribunj": []}
    for place in PLACES:
        gm, gr = mixed.get(place, []), recyc.get(place, [])
        if len(gm) != len(gr):
            problems.append(f"{place}: {len(gm)} skupina ulica za MKO, {len(gr)} za reciklabilni")
            continue
        for (cell_m, seasons_m), (cell_r, seasons_r) in zip(gm, gr):
            ratio = difflib.SequenceMatcher(None, letters(cell_m), letters(cell_r)).ratio()
            if ratio < 0.97:
                problems.append(f"{place}: popisi ulica za MKO i reciklabilni se razlikuju ({ratio:.2f}): {cell_m[:60]!r}")
            for seasons in (seasons_m, seasons_r):
                ms = [m for months, _ in seasons for m in months]
                if sorted(ms) != list(range(1, 13)):
                    problems.append(f"{place}: sezone ne pokrivaju godinu točno jednom: {sorted(ms)}")
            summer = [months for months, (k, it) in seasons_m if len(it) == 2]
            if summer != [{5, 6, 7, 8, 9}]:
                problems.append(f"{place}: ljetni termin MKO {summer} ne odgovara tekstu (svibanj–rujan)")
            names = streets(cell_m)
            first_day = seasons_m[0][1][1][0]
            label = f"{place.capitalize()}, {DAN[first_day]} – " + ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
            rows = zone_rows(year, dates(year, seasons_m), dates(year, seasons_r), moves, place, problems, label)
            wds_m = {wd for _, (_, it) in seasons_m for wd in it}
            wds_r = {wd for _, (_, it) in seasons_r for _, wd in it}
            check(label, rows, {"M": wds_m, "P": wds_r}, {"M": (4, 10), "P": (1, 2)}, problems)
            by_jls[PLACES[place]].append({
                "jls": PLACES[place],
                "podrucje": label,
                "ulice": names,
                "napomena": f"Miješani otpad – {describe_weekly(seasons_m)}. Reciklabilni otpad – "
                            f"{describe_weekly(seasons_r)}.",
                "rows": rows,
            })

    # hinterland villages (Grad Vodice)
    mixed_z, gaps = periods(year, ZALEDJE["mixed"])
    recyc_z = dates(year, [(set(range(1, 13)), ("m", ZALEDJE["recyc"]))])
    rows = zone_rows(year, mixed_z, recyc_z, moves, "zaleđe", problems, "zaleđe")
    check("zaleđe", rows, {"M": {3, 6}, "P": {3}}, {"M": (4, 10), "P": (1, 1)}, problems)
    by_jls["Vodice"].append({
        "jls": "Vodice",
        "podrucje": "Zaleđe – " + ", ".join(ZALEDJE_NAMES),
        "ulice": ZALEDJE_NAMES,
        "napomena": "Miješani otpad – siječanj–lipanj i rujan–prosinac četvrtkom; srpanj i kolovoz četvrtkom i "
                    "nedjeljom. Reciklabilni otpad – prvi četvrtak u mjesecu.",
        "rows": rows,
    })
    # island of Prvić (Grad Vodice)
    mixed_p, bad = periods(year, PRVIC)
    if bad or gaps:
        problems.append(f"razdoblja za Prvić/zaleđe ne pokrivaju godinu točno jednom: {sorted(bad + gaps)[:5]}")
    rows = zone_rows(year, mixed_p, [], moves, "Prvić", problems, "Prvić")
    check("Prvić", rows, {"M": {0, 2, 4, 5}}, {"M": (4, 18)}, problems)
    by_jls["Vodice"].append({
        "jls": "Vodice",
        "podrucje": "Otok Prvić – Prvić Luka, Prvić Šepurine",
        "ulice": ["Prvić Luka", "Prvić Šepurine"],
        "napomena": "Miješani otpad po razdobljima: 1.1.–27.4. i 3.11.–31.12. petkom; 28.4.–8.6. i 29.9.–2.11. "
                    "ponedjeljkom i petkom; 9.6.–29.6. i 1.9.–28.9. ponedjeljkom, srijedom i petkom; 30.6.–31.8. "
                    "ponedjeljkom, srijedom, petkom i subotom. Reciklabilni otpad se na otoku ne odvozi: može se "
                    "samostalno dovesti u Vodice, Put Gaćeleza 3/b.",
        "rows": rows,
    })
    for d, (new, area) in moves.items():
        hit = any(r[0] == new and r[2] for z in by_jls.values() for zone in z for r in zone["rows"])
        if not hit:
            problems.append(f"obavijest {d} -> {new} ({area or 'sve'}): taj dan nema odvoza ni u jednoj zoni")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": NAPOMENE + [
        "Pomaci odvoza zbog blagdana nisu objavljeni kao pravilo; datumi su izračunati iz pravila, osim pomaka iz "
        "obavijesti tvrtke (označeni kao pomaknuti).",
        "Sezone: miješani otpad (Vodice, Srima, Tribunj) zimi siječanj–travanj i listopad–prosinac jednom tjedno, "
        "ljeti svibanj–rujan dvaput tjedno; reciklabilni otpad zimi siječanj–svibanj i listopad–prosinac jednom "
        "mjesečno, ljeti lipanj–rujan dvaput mjesečno.",
    ], "zone": {}}
    total = Counter()
    for zone in by_jls["Vodice"] + by_jls["Tribunj"]:
        key = str(len(data["zone"]) + 1)
        rows = zone.pop("rows")
        prev = old["zone"].get(key, {})
        keep = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} \
            if prev.get("ulice") == zone["ulice"] else {}
        zone["raw"] = {**keep, str(year): podaci.month_lines(rows)}
        data["zone"][key] = zone
        n = Counter(c for _, codes, _ in rows for c in codes)
        total.update(n)
        print(f"Zona {key} ({zone['jls']}): {zone['podrucje'][:60]} – {dict(n)}")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona, {dict(total)})")


if __name__ == "__main__":
    main()
