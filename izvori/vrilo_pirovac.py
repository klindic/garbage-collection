"""Pirovac: Vrilo d.o.o. (vrilo.hr), mixed and useful waste by street lists, winter and summer timetables.

    python3 -m izvori.vrilo_pirovac [--year 2026]

Vrilo publishes every timetable as a news article ("ZIMSKI VOZNI RED - od 15. rujna 2025. do 30. svibnja
2026.", "LJETNI VOZNI RED - OD 01. LIPNJA 2026 DO 14. RUJNA 2026", ...; vijesti.php). Each article lists
"<days> - zona X" with the zone's streets and one line for useful waste ("ZONA 1 - ČETVRTAK ..."). The
zones differ between summer (C, 1, 2 i 3, 4) and winter (1, 2, 3), so every street is followed through all
timetables that touch the year and streets with the same rules in all of them form one zone. Each
timetable applies to the dates in its title exactly (on a day where two meet, both apply).
No holiday rule is published; holiday changes come as notices. The notices that touch the year are
listed in NOTICES with the sentence they were read from; a new notice about collection stops the script
until it is added there. Moved dates are marked as moved, cancelled ones are dropped.
"""
import argparse
import html
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "vrilo-pirovac"
SITE = "https://www.vrilo.hr"
PAGE = SITE + "/prikupljanje-komunalnog-otpada.php"
NEWS = SITE + "/vijesti.php"
DAYS = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK", "SUBOTA", "NEDJELJA"]
INSTR = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
GEN = ["siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja", "kolovoza", "rujna",
       "listopada", "studenoga", "prosinca"]
TITLE = re.compile(r"(ZIMSKI|LJETNI) (?:VOZNI RED|RASPORED PRIKUPLJANJA OTPADA)", re.I)
SPAN = re.compile(r"od (\d{1,2})\. (\w+) (\d{4})\.? do (\d{1,2})\. (\w+) (\d{4})", re.I)
# Notices (article id -> sentence that must be in the article, [(date, new date or None, codes)]).
NOTICES = {
    141: ("u sljedeća dva tjedna dolazi do privremene izmjene rasporeda prikupljanja otpada na kućnom pragu. "
          "Otpad će se prikupljati isključivo: ponedjeljak i utorak: 22. i 23. ponedjeljak i utorak: 29. i 30.",
          [(date(2026, 1, 1), None, "MP"), (date(2026, 1, 2), None, "MP")]),
    142: ("raspored prikupljanja komunalnog otpada za utorak, 06.01.2026., prebacuje na srijedu, 07.01.2026.",
          [(date(2026, 1, 6), date(2026, 1, 7), "M")]),
    144: ("u ponedjeljak, 06. travnja, neće vršiti odvoz otpada. Sav otpad koji je predviđen za odvoz toga dana "
          "bit će prikupljen u utorak, 07. travnja.", [(date(2026, 4, 6), date(2026, 4, 7), "MP")]),
    145: ("u četvrtak 23.04.2026. godine, nećemo vršiti uslugu prikupljanja otpada. Sve zone predviđene za odvoz "
          "u četvrtak bit će prikupljene u petak, 24.04.2026. godine.", [(date(2026, 4, 23), date(2026, 4, 24), "MP")]),
    146: ("redovni odvoz korisnog otpada predviđen za petak, 01.05., izvršiti dan ranije, u četvrtak 30.04.",
          [(date(2026, 5, 1), date(2026, 4, 30), "P")]),
}
PROVIDER = {
    "davatelj": "Vrilo d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Pirovac"],
    "nazivi": {"P": "Korisni otpad (spremnici i vrećice)"},
}
NAPOMENE = [
    "Spremnike i vrećice treba ostaviti na vidljivom i dostupnom mjestu; vozilo kreće zimi u 6 sati, ljeti u 5 sati.",
    "Glomazni otpad: informacije na tel. 022/466-155.",
    "Pomaci zbog blagdana nisu objavljeni kao pravilo; uključeni su pomaci iz obavijesti Vrila (pomaknuti odvozi su "
    "označeni, otkazani izostavljeni).",
]


def text(fragment):
    t = re.sub(r"<(br|/p|/div|/h\d)\b[^>]*>", "\n", fragment)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("\xa0", " ")
    return "\n".join(" ".join(line.split()) for line in t.splitlines() if line.strip())


def key(street):
    """Spelling-insensitive key: 'Petra Draganiča Vrančića (od broja 1 ...)' == 'Petra Draganića Vrančića (...)'."""
    s = unicodedata.normalize("NFKD", street.lower().replace("đ", "d"))
    return re.sub(r"[^a-z0-9]", "", s)


def articles(year):
    """[(id, date, title)] from the news list, newest first, back to June of the year before."""
    out = []
    for page in range(1, 6):
        body = fetch(NEWS + (f"?page={page}" if page > 1 else "")).decode("utf-8", "replace")
        older = False
        for a in body.split("<article")[1:]:
            i = re.search(r"clanak\.php\?id=(\d+)", a)
            d = re.search(r'class="day">(\d+)<', a)
            m = re.search(r'class="month">(\d+)<', a)
            y = re.search(r'fa-folder"></i> <a href="#">(\d{4})</a>', a)
            t = re.search(r"<h2[^>]*>\s*<a[^>]*>(.*?)</a>", a, re.S)
            if not (i and d and m and y and t):
                continue
            when = date(int(y.group(1)), int(m.group(1)), int(d.group(1)))
            out.append((int(i.group(1)), when, " ".join(html.unescape(t.group(1)).split())))
            older = older or when < date(year - 1, 6, 1)
        if older:
            break
    return out


def article(aid):
    body = fetch(f"{SITE}/clanak.php?id={aid}").decode("utf-8", "replace")
    part = body.split("Članak", 1)[-1].split("Share this Post")[0]
    return text(part)


def span(title, problems):
    m = SPAN.search(title)
    if not m or m.group(2).lower() not in GEN or m.group(5).lower() not in GEN:
        problems.append(f"ne čitam razdoblje iz naslova {title!r}")
        return None
    d0, m0, y0, d1, m1, y1 = m.groups()
    return date(int(y0), GEN.index(m0.lower()) + 1, int(d0)), date(int(y1), GEN.index(m1.lower()) + 1, int(d1))


def timetable(body, name, problems):
    """({zone label: (weekdays, [streets])}, {zone id: weekday of useful waste}) from an article's text."""
    zones, cur = {}, None
    head = re.compile(r"((?:%s)(?:\s*,\s*(?:%s))*)\s*[-–]\s*zona (.+)" % ("|".join(DAYS), "|".join(DAYS)), re.I)
    lines = body.splitlines()
    for i, line in enumerate(lines):
        m = head.fullmatch(line)
        if m:
            days = [DAYS.index(d.strip().upper()) for d in m.group(1).split(",")]
            cur = m.group(2).strip()
            zones[cur] = (days, [])
        elif line.startswith("_"):
            cur = None
        elif cur and not zones[cur][1]:
            zones[cur][1].extend(s.strip() for s in line.split(",") if s.strip())
        elif cur:
            problems.append(f"{name}: neočekivan redak {line!r}")
    m = re.search(r"korisnim otpadom[^\n]*\n(.*?)\n_", body, re.S)
    useful = {}
    for label, day in re.findall(r"ZONA ([\w ]+?)\s*-\s*(%s)" % "|".join(DAYS), m.group(1) if m else "", re.I):
        for z in re.split(r"\s+[iI]\s+", label.strip()):
            useful[z.upper()] = DAYS.index(day.upper())
    if not zones or not useful:
        problems.append(f"{name}: nema zona ili rasporeda korisnog otpada")
    return zones, useful


def zone_ids(label):
    """'C (centar)' -> ['C'], '2 i 3' -> ['2', '3']."""
    return [z.upper() for z in re.split(r"\s+i\s+", re.sub(r"\s*\(.*\)", "", label))]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    first, last = date(year, 1, 1), date(year, 12, 31)
    news = articles(year)
    sources = []  # (start, end, title, {street key: (label, weekdays, useful weekday)}, streets in order)
    for aid, when, title in news:
        if not TITLE.search(title):
            continue
        sp = span(title, problems)
        if not sp or sp[1] < first or sp[0] > last:
            continue
        body = article(aid)
        zones, useful = timetable(body, title, problems)
        streets = {}
        order = []
        for label, (days, names) in zones.items():
            udays = {useful.get(z) for z in zone_ids(label)}
            if len(udays) != 1 or None in udays:
                problems.append(f"{title}: zona {label} nema jedinstven dan korisnog otpada ({useful})")
                continue
            for n in names:
                if key(n) in streets:
                    problems.append(f"{title}: {n} dvaput")
                streets[key(n)] = (label, days, min(udays))
                order.append(n)
        sources.append((sp[0], sp[1], title, streets, order))
        print(f"{title}: {len(zones)} zona, {len(streets)} ulica (clanak.php?id={aid})")
    sources.sort()
    if not sources:
        sys.exit(f"Nema voznog reda za {year} na {NEWS}")
    covered = set()
    for s0, s1, *_ in sources:
        d = max(s0, first)
        while d <= min(s1, last):
            covered.add(d)
            d += timedelta(days=1)
    gaps = [first + timedelta(days=i) for i in range((last - first).days + 1) if first + timedelta(days=i) not in covered]
    if [d for d in gaps if d.weekday() != 6]:
        problems.append(f"dani bez voznog reda: {', '.join(f'{d:%d.%m.}' for d in gaps)}")
    elif gaps:
        print(f"Bez voznog reda (nedjelje, zimi nema odvoza): {', '.join(f'{d:%d.%m.}' for d in gaps)}")

    # streets with the same rules in every timetable form one zone
    keys = set(sources[0][3])
    for s in sources[1:]:
        if set(s[3]) != keys:
            problems.append(f"{s[2]}: popis ulica se razlikuje: {sorted(set(s[3]) ^ keys)}")
    names = {}
    for s in sources:  # last spelling wins (the newest timetable)
        names.update({key(n): n for n in s[4]})
    groups = defaultdict(list)
    for n in sources[-1][4]:
        k = key(n)
        if all(k in t[3] for t in sources):
            groups[tuple((tuple(t[3][k][1]), t[3][k][2]) for t in sources)].append(names[k])

    # notices
    moves = []
    for aid, when, title in news:
        if when < date(year - 1, 12, 1) or TITLE.search(title):
            continue
        if not re.search(r"raspored|odvoz|otpad|prikupljanj", title, re.I) or re.search(r"JAVNI POZIV", title):
            continue
        if aid not in NOTICES:
            problems.append(f"nova obavijest (clanak.php?id={aid}, {when:%d.%m.%Y.}): {title!r} – pregledati i "
                            "upisati u NOTICES")
            continue
        sentence, changes = NOTICES[aid]
        if re.sub(r"\s", "", sentence) not in re.sub(r"\s", "", article(aid)):
            problems.append(f"obavijest clanak.php?id={aid} se promijenila: {title!r}")
        moves += [c for c in changes if c[0].year == year]
    for aid in NOTICES:
        if aid not in {a for a, _, _ in news}:
            problems.append(f"obavijest clanak.php?id={aid} više nije na popisu vijesti")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": NAPOMENE + ["Vozni redovi: " + "; ".join(
        f"{s[2].split(' - ')[0].capitalize()} {s[0]:%d.%m.%Y.}–{s[1]:%d.%m.%Y.}" for s in sources)], "zone": {}}
    used = Counter()
    for rules in sorted(groups, key=lambda r: [list(s[3]).index(key(groups[r][0])) for s in sources]):
        streets_z = groups[rules]
        out = {}
        for (s0, s1, title, *_), (mko, useful) in zip(sources, rules):
            d = max(s0, first)
            while d <= min(s1, last):
                if d.weekday() in mko:
                    out.setdefault(d, set()).add("M")
                if d.weekday() == useful:
                    out.setdefault(d, set()).add("P")
                d += timedelta(days=1)
        rows = {d: ("".join(sorted(c, key="MP".index)), False) for d, c in out.items()}
        for old_d, new_d, codes in moves:
            have, was = rows.get(old_d, ("", False))
            moved = "".join(c for c in have if c in codes)
            if not moved:
                continue
            used[old_d] += 1
            rest = "".join(c for c in have if c not in codes)
            if rest:
                rows[old_d] = (rest, was)
            else:
                rows.pop(old_d)
            if new_d:
                have = rows.get(new_d, ("", False))[0]
                if set(have) & set(moved):
                    problems.append(f"{streets_z[0]}: {moved} već postoji {new_d}")
                rows[new_d] = ("".join(sorted(set(have + moved), key="MP".index)), True)
        season = {"zimski": "zimi", "ljetni": "ljeti"}
        labels = [f"{season[t[2].split(' ')[0].lower()]} zona {t[3][key(streets_z[0])][0]}" for t in sources]
        desc = []
        for (s0, s1, title, *_), (mko, useful) in zip(sources, rules):
            days = "svaki dan" if len(mko) == 7 else " i ".join(INSTR[i] for i in mko)
            desc.append(f"{s0:%d.%m.%Y.}–{s1:%d.%m.%Y.}: miješani {days}, korisni {INSTR[useful]}")
        # checks
        per = Counter()
        for d, (codes, moved) in rows.items():
            for c in codes:
                per[c, d.month] += 1
            day_rules = [r for (s0, s1, *_), r in zip(sources, rules) if s0 <= d <= s1]
            if not moved and not all(any(d.weekday() in (r[0] if c == "M" else [r[1]]) for r in day_rules)
                                     for c in codes):
                problems.append(f"{streets_z[0]}: {d} {codes} nije po voznom redu")
        for (c, m), n in per.items():
            if not (1 if c == "P" else 3) <= n <= (6 if c == "P" else 31):
                problems.append(f"{streets_z[0]}: {c} {n} puta u mjesecu {m}")
        k = str(len(data["zone"]) + 1)
        short = ", ".join(streets_z[:3]) + (" …" if len(streets_z) > 3 else "")
        area = "Kašić, Putičanje" if key(streets_z[0]) in ("kasic", "puticanje") else "Pirovac"
        zone = {
            "jls": "Pirovac",
            "podrucje": f"{area} ({', '.join(dict.fromkeys(labels))}) – {short}",
            "ulice": streets_z,
            "napomena": "; ".join(desc) + ".",
        }
        prev = old["zone"].get(k, {})
        keep = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} \
            if prev.get("ulice") == streets_z else {}
        zone["raw"] = {**keep, str(year): podaci.month_lines([(d, c, m) for d, (c, m) in rows.items()])}
        data["zone"][k] = zone
        n = Counter(c for codes, _ in rows.values() for c in codes)
        print(f"Zona {k}: {zone['podrucje'][:90]} – {len(streets_z)} ulica, {dict(n)}")
    for d, _, _ in moves:
        if not used[d]:
            problems.append(f"obavijest za {d}: tog dana nema odvoza ni u jednoj zoni")
    print("Obavijesti: " + ", ".join(f"{a:%d.%m.}->{f'{b:%d.%m.}' if b else 'nema odvoza'} ({c})" for a, b, c in moves))
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
