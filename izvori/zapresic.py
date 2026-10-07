"""Zaprešić and six municipalities: Zaprešić d.o.o. (komunalno.vio-zapresic.hr), rules per area.

    python3 -m izvori.zapresic [--year 2026]

Two WordPress pages (read through the REST API) hold the rules: mixed waste once a week per street
or settlement, and plastic (two weeks of the month, "1. i 3. ponedjeljak") and paper (one week,
"1. ponedjeljak") per area. The two pages group the areas differently and abbreviate street names in
different ways ("Al. Đ. Jelačića" / "Aleja Đure Jelačića"), so both are split into single streets and
settlements, matched by a normalised name (surname stem, initials must agree, a few written-out
aliases), and every street or settlement gets the rule of the most specific row that covers it
(street, settlement, "cijelo područje općine"). Places with the same rules form a zone; a place that
only one page lists keeps only that page's rules and is named in the zone note. Holiday shifts come
from the company's notices; a holiday without a notice follows the practice of the last notices
(Christmas and New Year's Day on the Saturday of that week, other holidays as usual).
"""
import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from datetime import date

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch, holiday_moves, plain, saturday

SLUG = "zapresic"
SITE = "https://komunalno.vio-zapresic.hr"
API = SITE + "/wp-json/wp/v2/pages?slug={slug}&_fields=id,modified,link,content"
MIXED = "raspored-odvoza-mijesanog-komunalnog-otpada"
USEFUL = "rapored-odvoza-papira-i-plastike"
POSTS = SITE + "/wp-json/wp/v2/posts?search={q}&per_page=50&after={after}&_fields=id,date,link,title,content"
DAYS = {"ponedjeljak": "pon", "utorak": "uto", "srijeda": "sri", "srijedu": "sri", "četvrtak": "čet",
        "petak": "pet", "subota": "sub", "subotu": "sub"}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
JLS = ["Zaprešić", "Bistra", "Brdovec", "Dubravica", "Luka", "Marija Gorica", "Pušća"]
ALL, REST = "*", "ostale ulice"
# same street or settlement written differently on the two pages (normalised spelling -> one name)
ALIAS = {
    "aleja đure jelačića": "aleja jelačića", "al. đ. jelačića": "aleja jelačića",
    "d. demetera": "d. demetra", "trakonšćanska": "trakošćanska",
    "lužnični odvojak": "lužnički odvojak", "ključ": "ključ brdovečki",
    "hruševec puščanski": "pušćanski hruševec", "pušćanski hruševac": "pušćanski hruševec",
    "kupljenski hruševac": "kupljenski hruševec",
    "d. pušća": "donja pušća", "g. pušća": "gornja pušća",
    "m. magdalena": "marija magdalena",
    # the two pages split this street differently (house numbers / "od, do Skurjenijeve"): no match
    "b. j. jelačića 1-102": "bana jelačića 1-102", "b. j. jelačića 139-165": "bana jelačića 139-165",
    "bana j. jelačića od skurijenijeve": "bana jelačića od skurjenijeve",
    "bana j. jelačića do skurjenijeve": "bana jelačića do skurjenijeve",
}
GENERIC = {"odvojak", "ulica", "vrh", "naselje", "brijeg", "selo", "put"}
PROVIDER = {
    "davatelj": "Zaprešić d.o.o.",
    "web": SITE,
    "zupanija": "Zagrebačka",
    "jls": JLS,
    "nazivi": {"P": "Plastika"},
}
NAPOMENE = [
    "Posude za otpad moraju biti spremne za odvoz najkasnije do 7 sati.",
    "Reciklažna dvorišta: Zaprešić, Bistra i Brdovec.",
]


def fold(s):
    s = s.lower().replace("đ", "dj")
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def stem(word):
    for end in ("eva", "ova", "a", "e", "i", "u"):
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[:-len(end)]
    return word


def norm(name):
    name = " ".join(name.replace("–", " ").split()).strip(" ,.")
    low = name.lower()
    return ALIAS.get(low, low)


def street_key(name):
    """(key, initials) of a street name: 'Lj. Vukotinovića' -> ('vukotinovic', {'l'})."""
    n = norm(name)
    if re.search(r"\b(od|do)\b", n) or n.startswith("bana jelačića"):
        return fold(n), set()  # part of a street ("od Nove ulice", "1-102"): matches only the same part
    words = re.findall(r"[^\W\d_]+\.?|\d+[a-z]?(?:-\d+[a-z]?)?", n)
    words = [w for w in words if not re.fullmatch(r"\d+[a-z]?-\d+[a-z]?", w)]  # house numbers
    full = [fold(w) for w in words if not w.endswith(".")]
    generic = [w for w in full if w in GENERIC or re.fullmatch(r"[ivx]+", w)]
    full = [w for w in full if w not in generic]
    if not full:
        return fold(n), set()
    key = stem(full[-1]) + "".join(" " + g for g in generic)
    initials = {fold(w)[0] for w in words[:-1] if w.endswith(".") and len(w) <= 3} | {w[0] for w in full[:-1]}
    if n.startswith("aleja"):
        key = "aleja " + key
    return key, initials


def settlement_key(name):
    return " ".join(sorted(stem(w) for w in fold(norm(name)).split()))


def split_list(text):
    """'A. Kovačića, Grmovćica i S. Stanišaka' -> ['A. Kovačića', 'Grmovćica', 'S. Stanišaka']."""
    text = re.sub(r"([a-zčćžšđ])([A-ZČĆŽŠĐ])", r"\1, \2", text)  # "Davora bašićaGalijaševićeva"
    text = re.sub(r"\(?\b((?:[IVX]+(?:,\s*|\s+i\s+))+[IVX]+)\b\)?",
                  lambda m: "/".join(re.split(r",\s*|\s+i\s+", m.group(1))), text)
    out = []
    for part in re.split(r",|\s+i\s+(?=[A-ZČĆŽŠĐa-zčćžšđ])", text):
        part = " ".join(part.split()).strip(" ,")
        m = re.fullmatch(r"(.+?) ((?:[IVX]+/)+[IVX]+)", part)
        if m:  # "Rakitovec I/II/III" -> Rakitovec I, Rakitovec II, Rakitovec III
            out += [f"{m.group(1)} {r}" for r in m.group(2).split("/")]
        elif part:
            out.append(part)
    return out


def title(name):
    return " ".join(w if w.isupper() and "." in w else w.capitalize() if w.isupper() else w for w in name.split())


def places(jls, text):
    """Location cell -> [(settlement or ALL, street / None / REST / ('osim', [...]))]."""
    t = " ".join(text.split())
    m = re.fullmatch(r"(?i)cijelo područje općine(?: \w+)?", t)
    if m:
        return [(ALL, None)]
    m = re.fullmatch(r"(?i)cijelo područje općine osim (.+)", t)
    if m:
        return [(ALL, ("osim", split_list(m.group(1))))]
    m = re.fullmatch(r"(?i)cijelo područje općine i (.+)", t)
    if m:
        return [(ALL, None)] + [(title(s), None) for s in split_list(m.group(1))]
    m = re.fullmatch(r"dio grada Zaprešića – ulice: (.+)", t)
    if m:
        return [("Zaprešić", s) for s in split_list(m.group(1))]
    m = re.fullmatch(r"dio naselja (\S+) – (.+)", t)
    if m:
        return [(title(m.group(1)), s) for s in split_list(m.group(2))]
    m = re.fullmatch(r"dio (Brdovca) – ulice: (.+)", t)
    if m:
        return [("Brdovec", s) for s in split_list(m.group(2))]
    out = []
    for part in [p.strip() for p in re.split(r",(?![^:]*$)", t)] if ":" in t else split_list(t):
        m = re.fullmatch(r"([A-ZČĆŽŠĐ. ]+?) ulice: (.+)", part)
        if m:  # "GORNJA BISTRA ulice: M. štreka, ..." / "PUŠĆA ulice: ... PUŠĆANSKI HRUŠEVAC"
            streets = split_list(m.group(2))
            tail = re.fullmatch(r"(.*?) ((?:[A-ZČĆŽŠĐ]{2,} ?)+)", streets[-1])
            if tail:
                streets[-1] = tail.group(1)
                out.append((title(tail.group(2).strip()), None))
            out += [(title(m.group(1)), s) for s in streets]
            continue
        m = re.fullmatch(r"(.+?) – ostale ulice", part)
        if m:
            out.append((title(m.group(1)), REST))
            continue
        for s in split_list(part) if ":" in t else [part]:
            out.append((title(s), None))
    return out


def read_page(slug, problems):
    """[(section title, [[cells]])], modified date, link."""
    page = json.loads(fetch(API.format(slug=slug)))
    if not page:
        sys.exit(f"Nema stranice {slug} na {SITE}")
    page = page[0]
    html = page["content"]["rendered"]
    out = []
    for m in re.finditer(r'class="elementor-accordion-title"[^>]*>(.*?)</a>(.*?)(?=class="elementor-accordion-title"|$)',
                         html, re.S):
        rows = []
        for tr in re.findall(r"<tr.*?</tr>", m.group(2), re.S):
            cells = [c for c in (plain(td) for td in re.findall(r"<td.*?</td>", tr, re.S)) if c]
            if cells and cells[0] != "DAN ODVOZA":
                rows.append(cells)
        out.append((plain(m.group(1)), rows))
    if not out:
        problems.append(f"{slug}: nema tablica")
    return out, page["modified"][:10], page["link"]


def section_jls(title_text):
    t = re.sub(r"(?i)\s*(-\s*ulice|-\s*naselja| odvoz (plastike|papira))\s*$", "", title_text)
    t = re.sub(r"(?i)^(grad|općina)\s+", "", t.strip(" -"))
    name = title(t.strip(" -").lower().title() if t.isupper() else t.strip(" -"))
    return next((j for j in JLS if fold(j) == fold(name)), None)


def nth_rule(text):
    """'1. i 3. ponedjeljak u mjesecu' -> ((1, 3), 'pon'); '3. ponedjeljak u mjesecu' -> ((3,), 'pon')."""
    m = re.fullmatch(r"(\d)\.\s*(?:i\s*(\d)\.\s*)?(\w+) u mjesecu", " ".join(text.lower().split()))
    if not m or m.group(3) not in DAYS:
        return None
    weeks = tuple(int(x) for x in (m.group(1), m.group(2)) if x)
    return weeks, DAYS[m.group(3)]


def read_notices(year, posts, problems):
    """{holiday: ("redovno", None) | ("pomak", date)} from the notices about collection on holidays."""
    hol = [h for h in pravila.blagdani(year) if h.weekday() < 5]
    out = {}
    for post in posts:
        text = plain(post["content"]["rendered"])
        spots = sorted((m.start(), h) for h in hol for m in re.finditer(rf"\b{h:%d\.%m\.%Y}", text))
        for i, (pos, h) in enumerate(spots):
            part = text[pos:spots[i + 1][0] if i + 1 < len(spots) else len(text)]
            m = re.search(r"izvršit\w*(?: će se)? u (\w+)\s*,?\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", part)
            if m:
                new = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
                if DAYS.get(m.group(1).lower()) != ["pon", "uto", "sri", "čet", "pet", "sub", "ned"][new.weekday()]:
                    problems.append(f"obavijest {post['link']}: {new} nije {m.group(1)}")
                    continue
                found = ("pomak", new)
            elif "po redovnom rasporedu" in part:
                found = ("redovno", None)
            elif "odvoz" in part:
                problems.append(f"obavijest {post['link']}: nepoznata formulacija za {h:%d.%m.%Y}: {part[:160]}")
                continue
            else:
                continue
            if out.get(h, found) != found:
                problems.append(f"obavijesti se ne slažu za {h:%d.%m.%Y}: {out[h]} / {found}")
            out[h] = found
    return out


def practice(h):
    """Last years' notices: Christmas and New Year's Day -> Saturday of that week, the rest as usual."""
    return saturday(h) if (h.month, h.day) in ((1, 1), (12, 25)) else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []

    mixed_page, mixed_mod, mixed_link = read_page(MIXED, problems)
    useful_page, useful_mod, useful_link = read_page(USEFUL, problems)

    # mixed waste: {(jls, settlement, item): weekday}
    mixed = {}
    for sec, rows in mixed_page:
        jls = section_jls(sec)
        if not jls:
            problems.append(f"MKO: nepoznata sekcija {sec!r}")
            continue
        for row in rows:
            day = DAYS.get(row[0].lower())
            if len(row) < 2 or not day:
                problems.append(f"MKO {jls}: nepoznat redak {row}")
                continue
            for s, item in places(jls, row[-1]):
                key = (jls, s, item if not isinstance(item, tuple) else ("osim", tuple(item[1])))
                if key in mixed and mixed[key] != day:
                    problems.append(f"MKO {jls}: {s} {item} ima dva dana")
                mixed[key] = day

    # plastic and paper: rows with the same location on both lists
    plastic, paper = {}, {}
    for sec, rows in useful_page:
        jls = section_jls(sec)
        kind = "P" if "plastike" in sec.lower() else "K" if "papira" in sec.lower() else None
        if not jls or not kind:
            problems.append(f"papir/plastika: nepoznata sekcija {sec!r}")
            continue
        for row in rows:
            r = nth_rule(row[0]) if len(row) >= 2 else None
            if not r or len(r[0]) != (2 if kind == "P" else 1):
                problems.append(f"{sec}: nepoznato pravilo {row}")
                continue
            loc = " ".join(row[-1].split())
            target = plastic if kind == "P" else paper
            if (jls, loc) in target:
                problems.append(f"{sec}: {loc} dvaput")
            target[(jls, loc)] = r
    useful = {}
    for k in sorted(set(plastic) | set(paper)):
        if k not in plastic or k not in paper:
            problems.append(f"{k[0]}: {k[1][:60]!r} je samo na popisu {'plastike' if k in plastic else 'papira'}")
            continue
        (pw, pd), (kw, kd) = plastic[k], paper[k]
        if pd != kd or kw[0] not in pw:
            problems.append(f"{k[0]}: {k[1][:60]!r} papir {kw} {kd} nije u tjednu plastike {pw} {pd}")
        for s, item in places(k[0], k[1]):
            key = (k[0], s, item if not isinstance(item, tuple) else ("osim", tuple(item[1])))
            if key in useful and useful[key] != (pw, pd, kw[0]):
                problems.append(f"papir/plastika {k[0]}: {s} {item} ima dva pravila")
            useful[key] = (pw, pd, kw[0])

    # match: every place gets the rule of the most specific row covering it on each page
    def index(table):
        streets, whole, rest, alls = defaultdict(list), {}, {}, []
        for (jls, s, item), rule in table.items():
            if s == ALL:
                alls.append((jls, item, rule))
            elif item is None:
                whole[(jls, settlement_key(s))] = rule
            elif item == REST:
                rest[(jls, settlement_key(s))] = rule
            else:
                k, ini = street_key(item)
                streets[(jls, settlement_key(s), k)].append((ini, item, rule))
        return streets, whole, rest, alls

    def lookup(idx, jls, s, item):
        streets, whole, rest, alls = idx
        sk = settlement_key(s) if s != ALL else ALL
        if item not in (None, REST) and s != ALL:
            k, ini = street_key(item)
            for ini2, other, rule in streets.get((jls, sk, k), []):
                if not ini or not ini2 or ini & ini2:
                    return rule
                problems.append(f"{jls}: {item!r} i {other!r} imaju isto prezime, a različite inicijale")
        if item == REST and (jls, sk) in rest:
            return rest[(jls, sk)]
        if (jls, sk) in whole and s != ALL:
            return whole[(jls, sk)]
        for j, extra, rule in alls:  # "cijelo područje općine" / "... osim Trstenika"
            if j == jls and (extra is None or s != ALL and sk not in {settlement_key(x) for x in extra[1]}):
                return rule
        return None

    mi, ui = index(mixed), index(useful)
    split_m = {(j, settlement_key(s)) for (j, s, it) in mixed if it not in (None,) and s != ALL}
    split_u = {(j, settlement_key(s)) for (j, s, it) in useful if it not in (None,) and s != ALL}
    atoms = {}
    for table, other in ((mixed, useful), (useful, mixed)):
        for (jls, s, item) in table:
            if s == ALL and (isinstance(item, tuple) or any(j == jls and x != ALL for j, x, _ in other)):
                continue  # "the whole municipality (except ...)" is covered by the places it contains
            if s != ALL and item is None and (jls, settlement_key(s)) in (split_u if table is mixed else split_m):
                continue  # the other page splits this settlement: its parts carry both rules
            if isinstance(item, tuple):
                continue
            name = (f"{jls} (cijela općina)" if s == ALL else s if item is None
                    else f"{s} ({item})" if item == REST else item if s == "Zaprešić" else f"{item} ({s})")
            ident = (jls, settlement_key(s) if s != ALL else ALL,
                     street_key(item)[0] if item not in (None, REST) else item)
            atoms.setdefault(ident, (jls, s, item, name))
    rows_by_zone = defaultdict(list)
    only = defaultdict(list)
    for ident, (jls, s, item, name) in atoms.items():
        m_rule, u_rule = lookup(mi, jls, s, item), lookup(ui, jls, s, item)
        if m_rule is None and u_rule is None:
            problems.append(f"{jls}: {name} nema nijedno pravilo")
            continue
        if m_rule is None or u_rule is None:
            only["miješani" if u_rule is None else "papir i plastika"].append(f"{name} ({jls})")
        part = (f"{jls} (cijela općina)" if s == ALL else s if item is None else f"{s} ({item})" if item == REST
                else "Zaprešić (dio grada)" if s == "Zaprešić" else f"{s} (dio naselja)")
        rows_by_zone[(jls, m_rule, u_rule)].append((name, part))
    # every row of both pages must have reached at least one place
    for table, idx_name in ((mixed, "MKO"), (useful, "papir/plastika")):
        for (jls, s, item), rule in table.items():
            if isinstance(item, tuple) or s == ALL:
                continue
            ident = (jls, settlement_key(s), street_key(item)[0] if item not in (None, REST) else item)
            if ident not in atoms and not any(a[0] == jls and a[1] == ident[1] for a in atoms):
                problems.append(f"{idx_name} {jls}: {s} {item or ''} nije ni u jednoj zoni")

    hol_problems = []
    after = f"{year - 1}-10-01T00:00:00"
    posts = {}
    for q in ("odvoz", "blagdan", "praznik"):
        for p in json.loads(fetch(POSTS.format(q=urllib.parse.quote(q), after=after))):
            posts[p["id"]] = p
    notices = read_notices(year, posts.values(), hol_problems)
    moves, notes = holiday_moves(year, notices, practice, hol_problems)
    problems += hol_problems

    zones = {}
    totals = Counter()
    order = sorted(rows_by_zone, key=lambda k: (JLS.index(k[0]), k[1] is None, k[1] and pravila.DANI[k[1]],
                                                k[2] is None, k[2] and (pravila.DANI[k[2][1]], k[2][0])))
    for i, (jls, m_rule, u_rule) in enumerate(order, 1):
        names = sorted({n for n, _ in rows_by_zone[(jls, m_rule, u_rule)]}, key=fold)
        settlements = sorted({p for _, p in rows_by_zone[(jls, m_rule, u_rule)]}, key=fold)
        rows, desc = [], []
        if m_rule:
            rows += [(d, "M") for d in pravila.tjedno(year, m_rule)]
            desc.append(f"miješani {DAN[pravila.DANI[m_rule]]}")
        if u_rule:
            pw, pd, kw = u_rule
            rows += [(d, "P") for w in pw for d in pravila.mjesecno(year, pd, w)]
            rows += [(d, "K") for d in pravila.mjesecno(year, pd, kw)]
            desc.append(f"plastika {min(pw)}. i {max(pw)}. {DAN[pravila.DANI[pd]]}, papir {kw}. {DAN[pravila.DANI[pd]]}")
        merged = defaultdict(lambda: ["", False])
        for d, code in rows:
            new = moves.get(d, d)
            if code in merged[new][0]:
                problems.append(f"zona {i}: {code} dvaput {new}")
            merged[new][0] += code
            merged[new][1] = merged[new][1] or d in moves
        for d, (codes, moved) in merged.items():
            want = {pravila.DANI[m_rule]} if "M" in codes and m_rule else set()
            want |= {pravila.DANI[u_rule[1]]} if set(codes) & set("PK") else set()
            if (d.weekday() not in want or len(want) > 1) and not (moved and d.weekday() == 5):
                problems.append(f"zona {i}: {d} {codes} nije na pravilan dan")
        got = Counter(c for codes, _ in merged.values() for c in codes)
        if m_rule and not 52 <= got["M"] <= 53 or u_rule and (got["P"] != 24 or got["K"] != 12):
            problems.append(f"zona {i}: neočekivan broj odvoza {dict(got)}")
        totals.update(got)
        label = ", ".join(settlements[:3]) + (" …" if len(settlements) > 3 else "")
        zone = {"jls": jls, "podrucje": f"{label} – " + "; ".join(desc), "ulice": names}
        if not m_rule:
            zone["napomena"] = ("Dan odvoza miješanog otpada za ova mjesta nije naveden u rasporedu "
                                "(navedena su samo na popisu za papir i plastiku).")
        elif not u_rule:
            zone["napomena"] = ("Dan odvoza papira i plastike za ova mjesta nije naveden (ili se ne može pouzdano "
                                "povezati) u rasporedu za papir i plastiku.")
        zone["raw"] = {str(year): podaci.month_lines([(d, c, m) for d, (c, m) in merged.items()])}
        zones[str(i)] = zone
    dup = Counter(u for z in zones.values() for u in z["ulice"])
    for u, n in dup.items():
        if n > 1:
            problems.append(f"{u!r} je u {n} zone")

    print(f"Mjesta (ulice/naselja): {len(atoms)}, zona: {len(zones)}")
    for k, v in sorted(only.items()):
        print(f"Samo {k}: {len(v)}: {', '.join(sorted(v, key=fold))}")
    for n in notes:
        print(f"Blagdan {n}")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")

    data = {**PROVIDER, "izvor": mixed_link, "napomene": NAPOMENE + [
        f"Izvori: {mixed_link} (miješani otpad) i {useful_link} (papir i plastika).",
        f"Rasporedi na stranicama nemaju godinu (zadnje izmjene: miješani otpad {date.fromisoformat(mixed_mod):%d.%m.%Y.}, "
        f"papir i plastika {date.fromisoformat(useful_mod):%d.%m.%Y.}); datumi za {year}. izračunati su iz pravila.",
        "Plastika se odvozi dva puta mjesečno, papir jednom (u jednom od tjedana plastike).",
        "Blagdani " + str(year) + ": odvoz je i na blagdane prema rasporedu, osim: " +
        "; ".join(n for n in notes if "→" in n) + ". Izvor: obavijesti na " + SITE + "/obavijesti/ (redovno prema "
        "obavijesti: " + ", ".join(n.split()[0] for n in notes if "redovno (obavijest)" in n) + ").",
        "Ulice i naselja navedeni samo na jednom od dva popisa imaju samo taj dio rasporeda (vidi napomenu zone).",
    ], "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years of zones that did not change
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("podrucje") == zone["podrucje"] and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    print(f"Odvoza {year} (zbroj po zonama): " + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
