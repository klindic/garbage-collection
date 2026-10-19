"""Split, Solin, Podstrana, Klis, Dugopolje: Čistoća d.o.o. Split (cistoca-split.hr), standing weekday rules.

    python3 -m izvori.cistoca_split [--year 2026]

Čistoća publishes no dated calendar. The page "Raspored odvoza otpada po blokovima" (found from the site
menu) lists per area and block how often and on which weekdays the mixed waste of the listed streets and
settlements is collected ("3 PUTA TJEDNO (pon. sri. pet.): Visoka, Stobreč"), a box for the eastern
suburbs with paper and plastic every two weeks ("svako petnaest dana", anchored on dates from April 2025,
continued week by week into the year asked) and street lists with mixed waste on two weekdays and some with
biowaste on one ("PONEDJELJAK - ČETVRTAK : MKO" is read as those two days), and a box for Bačvice
(individual bins: plastic on Tuesday, paper on Thursday; mixed waste and biowaste without weekdays).
The page is UTF-8, but Đ and đ are stored as the entities &eth; (ð) and &ntilde; (ñ), the substitution
known from text copied out of PDFs set in old Croatian fonts; the script maps them back and stops on any
other unexpected letter. Places with the same rules form one zone per city/municipality. Streets emptied
daily or six times a week (shared containers in the centre) and rules without weekdays get no dates and
are listed in notes. No holiday rule or holiday notice is published, so the regular weekdays are kept and
a note says so. Bulky-waste days per city district (on request) come from "Raspored odvoza glomaznog
otpada" and go into the notes.
"""
import argparse
import csv
import html
import re
import sys
from collections import Counter, defaultdict
from datetime import date

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cistoca-split"
SITE = "https://www.cistoca-split.hr"
JLS = ["Split", "Solin", "Podstrana", "Klis", "Dugopolje"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
KEY = list(pravila.DANI)  # pon uto sri čet pet sub ned
ABBR = {"pon": "pon", "ut": "uto", "uto": "uto", "sri": "sri", "čet": "čet", "pet": "pet", "sub": "sub",
        "ponedjeljak": "pon", "utorak": "uto", "srijeda": "sri", "četvrtak": "čet", "petak": "pet", "subota": "sub"}
INSTR = {"ponedjeljkom": "pon", "utorkom": "uto", "srijedom": "sri", "četvrtkom": "čet", "petkom": "pet",
         "subotom": "sub"}
TYPE_WORDS = {"mko": "M", "biootpad": "B", "plastika": "P", "papir": "K"}
NAME = {"M": "miješani", "B": "biootpad", "P": "plastika", "K": "papir"}
# Places outside Grad Split (all places in the "SOLIN – DUGOPOLJE" part must be listed here).
JLS_OF = {
    "Podstrana": "Podstrana", "Klis": "Klis", "Konjsko": "Klis", "Prugovo": "Klis",
    "Dugopolje": "Dugopolje", "Koprivno": "Dugopolje",
    "Solin": "Solin", "Vranjic": "Solin", "Sv. Kajo": "Solin", "Starine": "Solin", "Mravince (Mravinci)": "Solin",
    "Kučine": "Solin", "Rupotine": "Solin", "TTTS": "Solin", "Solin (Građa – Pivovara)": "Solin",
    "Magistrala Solin": "Solin",
    "Split – Sućidar": "Split", "Ivaniševićeva": "Split", "Draganjina": "Split",
}
# Obvious typos in the provider's lists (Mravince: official name of the settlement, kept with the page's form).
FIXES = {"Kućine": "Kučine", "Antafogasta": "Antofagaste", "Hrvatske Mornarice": "Hrvatske mornarice",
         "Nikole Taveliće": "Nikole Tavelića", "Put Bonacinke": "Put Bonaćinke", "Mravinci": "Mravince (Mravinci)"}
LOWER = {"i", "put", "cesta", "ulica", "obala", "mira", "dragovoljaca", "vitezova"}  # common nouns in street names
LOCATIVE = {"Stobreču": "Stobreč"}
# Đ/đ arrive as ð/ñ; nothing else is affected (č ć š ž are correct).
REPAIR = str.maketrans({"ð": "Đ", "ñ": "đ"})
LETTERS = set("čćšžđČĆŠŽĐ")
PROVIDER = {
    "davatelj": "Čistoća d.o.o. Split",
    "web": SITE,
    "zupanija": "Splitsko-dalmatinska",
    "nazivi": {"P": "Plastika (žuta vrećica / spremnik)", "K": "Papir i karton (plava vrećica / spremnik)"},
}
NAPOMENE = [
    "Velik dio Splita koristi zajedničke spremnike (kontejnere i polupodzemne spremnike za miješani otpad, "
    "biootpad, papir i plastiku); ondje su upisani dani pražnjenja spremnika. Obiteljske kuće imaju kante za "
    "miješani otpad (i biootpad gdje je uveden), a papir i plastiku odvajaju u vrećice.",
    "Staklo se odlaže u zelena zvona na zelenim otocima te u mobilna i velika reciklažna dvorišta.",
    "Reciklažna dvorišta: Karepovac (Dračevac 122), Orišac (Put Orišca 9), Pujanke (Pujanke 67B), Kopilica "
    "(Kopilica 52). Info telefon 0800 0021, operativna služba 021 323 740.",
]
SOLIN_NOTE = ("Prema obavijesti Grada Solina od 8.2.2022. (solin.hr) u Kučinama se ulice Brig i Franina prazne "
              "utorkom, četvrtkom i subotom (individualni spremnici).")


def clean(fragment):
    """HTML fragment -> one line of text, with Đ/đ repaired."""
    t = html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ")
    return " ".join(t.translate(REPAIR).split())


def naslov(s):
    """'SV.IŽIDORA' -> 'Sv. Ižidora', 'DON FILIPA I ANTE' -> 'Don Filipa i Ante', 'STARI PUT' -> 'Stari put'."""
    words = re.sub(r"\.(?=\S)", ". ", s.strip()).lower().split()
    return " ".join(w if w in LOWER and k else w[:1].upper() + w[1:] for k, w in enumerate(words))


def blok(name):
    """'VI BLOK' -> 'VI. blok', 'VAROŠ – MEJE' -> 'Varoš – Meje'."""
    m = re.fullmatch(r"([IVX]+) BLOK(.*)", name)
    return f"{m.group(1)}. blok{naslov(m.group(2)) if m.group(2) else ''}" if m else naslov(name)


def places(fragment):
    """'Osječka, Vukovarska,<br />Šižgorićeva' -> ['Osječka', 'Vukovarska', 'Šižgorićeva'] (typos fixed)."""
    out = []
    for part in re.split(r",|<br\s*/?>", fragment):
        p = clean(part).strip(" ,.")
        p = FIXES.get(p, p)
        if p and p.lower() not in {o.lower() for o in out}:
            out.append(p)
    return out


def rule(text):
    """'3 PUTA TJEDNO (pon. sri. pet.):' -> (3, ['pon', 'sri', 'pet'], 'dani'); None if it is not a rule.

    kind: 'dani' (weekdays given), 'svaki' (6 or 7 times a week), 'bez' (no weekdays given).
    """
    m = re.fullmatch(r"(\d)\s*PUTA?\s+TJ[EA]DNO\s*(?:\((.*?)\))?\s*:?", text, re.I)
    if not m:
        return None
    n, inner = int(m.group(1)), (m.group(2) or "").strip().lower()
    if n >= 6:
        return n, None, "svaki"
    if not inner:
        return n, None, "bez"
    words = [w for w in re.split(r"[\s.,]+", inner) if w]
    days = [ABBR.get(w) for w in words]
    return n, days, "dani" if None not in days and len(days) == n else "?"


def page_links():
    """URLs of the blocks page and the bulky-waste page from the site menu."""
    home = fetch(SITE + "/").decode("utf-8", "replace")
    found = {}
    for key, part in (("blokovi", "raspored-odvoza-otpada-po-blokovima"),
                      ("glomazni", "raspored-odvoza-glomaznog-otpada")):
        m = re.search(rf'href="([^"]*/{part})"', home)
        if not m:
            sys.exit(f"Na naslovnici {SITE} nema poveznice na '{part}'.")
        found[key] = m.group(1) if m.group(1).startswith("http") else SITE + m.group(1)
    return found


def content(page, problems):
    """The page's article HTML (title to end of the text block)."""
    i = page.find('class="c-contents__content')
    j = page.find("</div>", i)
    if i < 0 or j < 0:
        problems.append("na stranici nema bloka s rasporedom")
        return ""
    return page[i:j]


def check_letters(text, where, problems):
    bad = Counter(ch for ch in text if ch.isalpha() and not ch.isascii() and ch not in LETTERS)
    if bad:
        problems.append(f"{where}: nepoznati znakovi (kodiranje?): {dict(bad)}")


def parse_east(inner, order, problems):
    """The 'ISTOK' box: paper/plastic groups and the streets with individual bins.

    Returns ([(order, names, {code: (day, anchor)}, text)], [(order, street, {code: days}, heading, text)]).
    """
    paras = [(p, clean(p)) for p in re.findall(r"<p[^>]*>(.*?)</p>", inner, re.S)]
    groups, streets = [], []
    days_of, header, pending = {}, "", None
    for raw, t in paras:
        if not t or t.upper() == "RASPORED PRIKUPLJANJA ODVOJENOG OTPADA":
            continue
        if pending:  # the street list after "PONEDJELJAK - ČETVRTAK : MKO"
            days, code, head = pending
            for s in (naslov(x) for x in t.split(",") if x.strip()):
                s = FIXES.get(s, s)
                streets.append((order + 0.5, s, {code: days}, head, f"{head} {t}"))
            pending = None
            continue
        m = re.fullmatch(r"([A-ZČĆŠŽĐ]+)(?:\s*-\s*([A-ZČĆŠŽĐ]+))?\s*:\s*(MKO|Biootpad)", t, re.I)
        if m:
            days = [ABBR.get(d.lower()) for d in m.groups()[:2] if d]
            if None in days:
                problems.append(f"istok: nepoznat dan u {t!r}")
            # "PONEDJELJAK - ČETVRTAK" is read as the two days (individual bins are emptied twice a week)
            pending = (days, "M" if m.group(3).upper() == "MKO" else "B", t)
            continue
        dates = list(re.finditer(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", t))
        if not dates:  # header: "naizmjenično, svako petnaest dana ponedjeljkom odvoz papira, a utorkom plastika"
            pairs = re.findall(r"(\w+?om)\s+(?:odvoz\s+)?(papir|plastik)", t, re.I)
            days_of = {("K" if w.lower() == "papir" else "P"): INSTR.get(d.lower()) for d, w in pairs}
            header = t
            if set(days_of) != {"K", "P"} or None in days_of.values() or "petnaest dana" not in t:
                problems.append(f"istok: ne razumijem pravilo {t!r}")
            continue
        strong = [clean(s) for s in re.findall(r"<strong>(.*?)</strong>", raw, re.S)]
        area = next((s for s in strong if re.match(r"(M\.O\.|G\.K\.)", s)), None)
        if not area or not days_of:
            problems.append(f"istok: odlomak bez područja ili pravila: {t!r}")
            continue
        names = []
        for part in re.sub(r"^(M\.O\.|G\.K\.)\s*", "", area).strip(" :").split("/"):
            part = part.strip(" :")
            m2 = re.fullmatch(r"(\w+) (\w+) i (\w+)", part)
            names += ([f"{m2.group(1)} {m2.group(2)}", f"{m2.group(1)} {m2.group(3)}"] if m2
                      else [LOCATIVE.get(part, part)])
        anchors = {}
        for k, d in enumerate(dates):
            clause = t[d.end():dates[k + 1].start() if k + 1 < len(dates) else len(t)]
            code = "K" if "papir" in clause else "P" if "plastik" in clause else None
            when = date(int(d.group(3)), int(d.group(2)), int(d.group(1)))
            if code is None or code in anchors:
                problems.append(f"istok: ne razumijem {t!r}")
                continue
            if KEY[when.weekday()] != days_of.get(code):
                problems.append(f"istok: {when} nije {days_of.get(code)} ({t!r})")
            anchors[code] = (days_of[code], when)
        if set(anchors) != {"K", "P"}:
            problems.append(f"istok: nema datuma za papir i plastiku u {t!r}")
            continue
        groups.append((order + len(groups) / 100, names, anchors, f"{header} {t}"))
    if pending:
        problems.append("istok: popis ulica nedostaje")
    # the areas of one header alternate week by week
    by_rule = defaultdict(list)
    for _, names, anchors, _ in groups:
        by_rule[tuple(sorted((c, d) for c, (d, _) in anchors.items()))].append(anchors["K"][1])
    for key, firsts in by_rule.items():
        if len(firsts) == 2 and abs((firsts[0] - firsts[1]).days) % 14 != 7:
            problems.append(f"istok: područja {key} ne izmjenjuju se tjedno ({firsts})")
    return groups, streets


def parse_blocks(html_text, problems):
    """Blocks page -> (occurrences: one per place and rule row, daily lists, paper/plastic groups, east streets)."""
    occ, daily, groups, east_streets, typed = [], [], [], [], {}
    section = block = pending = None
    for order, m in enumerate(re.finditer(r"<(h4|th|td)\b[^>]*>(.*?)</\1>", html_text, re.S)):
        tag, inner = m.groups()
        t = clean(inner)
        if tag == "h4":
            section, block = (t, None) if t else (section, block)
            continue
        if tag == "th":
            if "ODVOJENOG OTPADA" in t.upper():
                g, s = parse_east(inner, order, problems)
                groups += g
                east_streets += s
            elif t and rule(t):
                pending = (t, rule(t))
            elif t:
                block = t
            continue
        if pending is None:
            if t:
                problems.append(f"tekst bez pravila: {t!r}")
            continue
        (text, (n, days, kind)), pending = pending, None
        if kind == "?":
            problems.append(f"{block}: ne razumijem pravilo {text!r}")
            continue
        code = next((c for w, c in TYPE_WORDS.items() if t.lower().startswith(w)), None)
        if code:  # "2 PUTA TJEDNO | MKO individualne kante": a rule for the block itself (Bačvice)
            typed.setdefault(block, (order, []))[1].append((code, n, days, kind, f"{text} {t}"))
            continue
        names = places(inner)
        if kind == "svaki":
            daily.append((block, n, names))
            continue
        if kind == "bez":
            problems.append(f"{block}: pravilo bez dana {text!r} za {names}")
            continue
        for p in names:
            jls = JLS_OF.get(p)
            if jls is None and ((section or "").upper().startswith("SOLIN") or "solin" in p.lower()):
                problems.append(f"{block}: ne znam kojoj JLS pripada {p!r}")
                continue
            occ.append({"order": order, "jls": jls or "Split", "place": p, "rules": {"M": ("t", tuple(days))},
                        "src": [f"{block}: {text} {clean(inner)}"], "notes": []})
    # a block with one rule per waste type (Bačvice: individual bins)
    for blk, (order, rows) in typed.items():
        name = naslov(blk)
        dated = {c: ("t", tuple(d)) for c, n, d, kind, _ in rows if kind == "dani"}
        undated = [f"{NAME[c]} {n} put{'a' if n > 1 else ''} tjedno" for c, n, d, kind, _ in rows if kind == "bez"]
        note = []
        if undated:
            note.append(f"{name} (individualne kante): " + ", ".join(undated) + " – dani nisu objavljeni, pa ti "
                        "odvozi nisu upisani.")
        if not dated:
            problems.append(f"{blk}: nijedno pravilo s danima")
            continue
        occ.append({"order": order, "jls": JLS_OF.get(name, "Split"), "place": name, "rules": dated,
                    "src": [f"{blk}: " + "; ".join(r[4] for r in rows)], "notes": note,
                    "label": f"{name} (individualne kante)"})
    return occ, daily, groups, east_streets


def attach_east(occ, groups, streets, problems):
    """Add the two-weekly paper/plastic rules to the places they name; add the individual-bin streets."""
    for order, names, anchors, text in groups:
        for n in names:
            hits = [o for o in occ if o["place"] == n and set(o["rules"]) == {"M"}]
            if not hits or len({o["rules"]["M"] for o in hits}) != 1:
                problems.append(f"istok: {n!r} nije (jednoznačno) u rasporedu miješanog otpada")
                continue
            for o in hits:
                o["rules"].update({c: ("2t", d, a) for c, (d, a) in anchors.items()})
                o["order"] = order
                o["src"].append(text)
    by_street = {}
    for order, s, rules, head, text in streets:
        o = by_street.setdefault(s, {"order": order, "jls": "Split", "place": s, "rules": {}, "src": [], "notes": [],
                                     "east": True})
        code, days = next(iter(rules.items()))
        if code == "M" and len(days) == 2:
            two = " i ".join(DAN[KEY.index(d)] for d in days)
            o["notes"].append(f"Raspored piše „{head}”; upisana su ta dva dana ({two}).")
        if code in o["rules"]:
            if o["rules"][code] != ("t", tuple(days)):
                problems.append(f"istok: {s} ima dva pravila za {NAME[code]}")
            continue
        o["rules"][code] = ("t", tuple(days))
        o["src"].append(text)
    for s, o in by_street.items():
        if "M" not in o["rules"]:
            problems.append(f"istok: {s} ima biootpad, a nema miješanog otpada")
    occ += by_street.values()


def describe(rules):
    out = []
    for code in "MBKP":
        if code not in rules:
            continue
        r = rules[code]
        if r[0] == "t":
            out.append(f"{NAME[code]} " + ", ".join(DAN[KEY.index(d)] for d in r[1]))
        else:
            out.append(f"{NAME[code]} {DAN[KEY.index(r[1])]} svaki drugi tjedan")
    return "; ".join(out)


def dates_for(rules, year):
    """{date: codes} for one zone; (date, code) pairs that repeat are returned as problems."""
    out, dup = defaultdict(str), []
    for code, r in rules.items():
        if r[0] == "t":
            ds = [d for k in r[1] for d in pravila.tjedno(year, k)]
        else:
            first = next(d for d in pravila.tjedno(year, r[1]) if (d - r[2]).days % 14 == 0)
            ds = pravila.svaki_n_tjedan(year, r[1], 2, first)
        for d in ds:
            if code in out[d]:
                dup.append((d, code))
            out[d] += code
    return out, dup


def base(name):
    """Name without '(dio)', '– sjeverni dio' etc., for finding a street listed under several rules."""
    n = re.sub(r"\(.*?\)|\s+–\s+\w+ dio$", "", name)
    return " ".join(n.lower().split())


def bulky(url, problems):
    """'Glomazni otpad ...' note from the bulky-waste page (day of month per city district)."""
    page = fetch(url).decode("utf-8", "replace")
    body = content(page, problems)
    check_letters(clean(body), "glomazni", problems)
    by_area = defaultdict(list)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        cells = [c for c in cells if c]
        if len(cells) == 2 and cells[0].isdigit():
            area = "/".join(re.sub(r"\bMo\b", "MO", "-".join(naslov(w) for w in part.split("-")))
                            for part in re.split(r"\s*/\s*", cells[1]))
            by_area[area].append(int(cells[0]))
    days = sorted(d for v in by_area.values() for d in v)
    if days != list(range(1, len(days) + 1)) or len(days) < 20:
        problems.append(f"glomazni: neočekivani dani u mjesecu {days}")
    text = clean(body)
    other = re.search(r"Napomena:\s*-\s*(.+?\.)", text)
    start = re.search(r"Stupa na snagu od (\d{1,2}\.\d{1,2}\.\d{4})", text)
    return ("Glomazni otpad u Splitu odvozi se uz prethodnu najavu u gradskom kotaru ili web-zahtjev, dva dana u "
            "mjesecu po kotaru" + (f" (raspored od {start.group(1)}.)" if start else "") + ": "
            + "; ".join(f"{' i '.join(f'{d}.' for d in v)} {a}" for a, v in by_area.items()) + "."
            + (f" {other.group(1)}" if other else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    links = page_links()
    page = fetch(links["blokovi"]).decode("utf-8")
    body = content(page, problems)
    raw_letters = Counter(ch for ch in html.unescape(body) if ch in "ðñÐÑ")
    text = clean(body)
    check_letters(text, "raspored", problems)
    fixed = sorted({w for w in re.findall(r"\w*[Đđ]\w*", text)})
    print(f"Kodiranje: {sum(raw_letters.values())} znakova ð/ñ vraćeno u Đ/đ ({', '.join(fixed)})")
    for section in ("CENTAR", "ZAPAD", "ISTOK", "SOLIN"):
        if not re.search(rf"<h4[^>]*>\s*{section}", body):
            problems.append(f"na stranici nema dijela {section}")

    occ, daily, groups, streets = parse_blocks(body, problems)
    attach_east(occ, groups, streets, problems)
    print(f"Pravila: {len(occ)} navoda mjesta/ulica, {len(groups)} područja s papirom i plastikom, "
          f"{len(daily)} popisa sa svakodnevnim odvozom")
    print("Pretpostavke: 'PONEDJELJAK - ČETVRTAK' (i sl.) znači ta dva dana; 'svako petnaest dana' = svaki drugi "
          "tjedan, nastavljeno od datuma iz travnja 2025.; blagdani bez pomaka (pravilo nije objavljeno).")

    # zones: places of one city/municipality with the same rules
    zones_by = {}
    for o in sorted(occ, key=lambda o: o["order"]):
        key = (o["jls"], o.get("label"), o.get("east", False), tuple(sorted(o["rules"].items())))
        z = zones_by.setdefault(key, {"jls": o["jls"], "order": o["order"], "rules": o["rules"], "ulice": [],
                                      "src": [], "notes": [], "label": o.get("label"), "east": o.get("east", False)})
        if o["place"].lower() not in {u.lower() for u in z["ulice"]}:
            z["ulice"].append(o["place"])
        z["src"] += [s for s in o["src"] if s not in z["src"]]
        z["notes"] += [s for s in o["notes"] if s not in z["notes"]]
    ordered = sorted(zones_by.values(), key=lambda z: (JLS.index(z["jls"]), z["order"]))
    keys = {id(z): str(i) for i, z in enumerate(ordered, 1)}

    # streets named under several rules (other parts of the street or settlement)
    where = defaultdict(set)
    for z in ordered:
        for u in z["ulice"]:
            where[base(u)].add(keys[id(z)])
    daily_names = {base(p): "svakodnevni odvoz" if n == 7 else "6 puta tjedno" for _, n, ps in daily for p in ps}
    east = ", ".join(keys[id(z)] for z in ordered if z["east"])

    zones, totals = {}, Counter()
    hol = set(pravila.blagdani(year))
    for z in ordered:
        k = keys[id(z)]
        found, dup = dates_for(z["rules"], year)
        for d, code in dup:
            problems.append(f"zona {k}: {code} dvaput {d}")
        rows = sorted((d, codes, False) for d, codes in found.items())
        # checks: weekday, month counts
        for d, codes, _ in rows:
            for c in codes:
                r = z["rules"][c]
                allowed = r[1] if r[0] == "t" else (r[1],)
                if KEY[d.weekday()] not in allowed or d.year != year:
                    problems.append(f"zona {k}: {c} {d} nije po pravilu")
        for month in range(1, 13):
            for c, r in z["rules"].items():
                n = sum(1 for d, cs, _ in rows if d.month == month and c in cs)
                lo, hi = (4 * len(r[1]), 5 * len(r[1])) if r[0] == "t" else (2, 3)
                if not lo <= n <= hi:
                    problems.append(f"zona {k}: {c} u {month}. mjesecu {n} puta")
        totals.update(c for _, cs, _ in rows for c in cs)
        if not z["ulice"]:
            problems.append(f"zona {k}: nema mjesta ni ulica")
        notes = list(z["notes"])
        others = []
        for u in z["ulice"]:
            more = sorted(where[base(u)] - {k}, key=int)
            extra = (["zone " + ", ".join(more)] if more else []) + (
                [daily_names[base(u)]] if base(u) in daily_names else [])
            if extra:
                others.append(f"{u} ({'; '.join(extra)})")
        if others:
            notes.append("Navedeno i pod drugim rasporedom (drugi dio ulice ili naselja – raspored za svoj dio "
                         "provjerite kod Čistoće): " + ", ".join(others) + ".")
        if any(r[0] == "2t" for r in z["rules"].values()):
            notes.append("Papir i plastika: svaki drugi tjedan; datumi su izračunati nastavkom ciklusa od travnja "
                         "2025. (na stranici „svako petnaest dana”). Ulice koje Čistoća na istoku navodi posebno "
                         f"imaju svoj raspored miješanog otpada i biootpada (zone {east}).")
        if z["east"]:
            notes.append("Čistoća za ove ulice ne navodi područje odvojenog prikupljanja papira i plastike (svaki "
                         "drugi tjedan: Stobreč/Sirobuja pon/uto, Žrnovnica/Srinjine i Sitno sri/čet, "
                         "Visoka/Kamen/Šine pet/sub), pa papir i plastika ovdje nisu upisani.")
        if "Kučine" in z["ulice"]:
            notes.append(SOLIN_NOTE)
        if "Magistrala Solin" in z["ulice"]:
            notes.append("„Magistrala Solin” navedena je u VII. bloku Splita; pripisana je Solinu prema nazivu.")
        if z["rules"].get("M") == ("t", ("uto", "čet", "pet")):
            notes.append("Raspored piše „uto. čet. pet.” (drugdje uto. čet. sub.); upisano kako je objavljeno.")
        on_hol = sorted(d for d, _, _ in rows if d in hol)
        if on_hol:
            notes.append("Na blagdane (" + ", ".join(f"{d:%-d.%-m.}" for d in on_hol) + ") upisan je redovni odvoz; "
                         "pomaci nisu objavljeni.")
        head = (z["label"] or ", ".join(z["ulice"][:4]) + (" …" if len(z["ulice"]) > 4 else ""))
        if z["east"]:
            head += " (poseban popis ulica, istok)"
        zone = {"jls": z["jls"], "podrucje": f"{head} – {describe(z['rules'])}",
                "opis": " | ".join(z["src"]), "ulice": z["ulice"]}
        if notes:
            zone["napomena"] = " ".join(notes)
        zone["raw"] = {str(year): podaci.month_lines(rows)}
        zones[k] = zone
        print(f"Zona {k:>2} {z['jls']:<10} {describe(z['rules'])}: {len(z['ulice'])} mjesta/ulica, "
              + ", ".join(f"{c} {n}" for c, n in sorted(Counter(c for _, cs, _ in rows for c in cs).items())))
        if "M" not in z["rules"]:
            print(f"   (zona {k}: bez miješanog otpada – dani nisu objavljeni)")

    missing = [j for j in JLS if not any(z["jls"] == j for z in zones.values())]
    if missing:
        problems.append(f"nema zona za {missing}")
    with open(podaci.ROOT / "istrazivanje" / "jls_davatelj.csv", encoding="utf-8") as f:
        reg = {r["jls"]: r["davatelj"] for r in csv.DictReader(f)}
    for j in JLS:
        if j not in reg:
            problems.append(f"{j} nije u istrazivanje/jls_davatelj.csv")
        elif "čistoća d.o.o. split" not in reg[j].lower():
            print(f"   UPOZORENJE: registar za {j} navodi {reg[j]!r}")

    glom = bulky(links["glomazni"], problems)
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")

    first_anchor = min(a for _, _, anchors, _ in groups for _, a in anchors.values())
    every = "; ".join(f"{blok(b)} ({', '.join(n)})" for b, k, n in daily if k == 7)
    six = "; ".join(f"{blok(b)} ({', '.join(n)})" for b, k, n in daily if k == 6)
    napomene = [
        f"Čistoća ne objavljuje kalendar s datumima za {year}. Datumi su izračunati iz stalnih pravila sa stranice "
        "„Raspored odvoza otpada po blokovima” (dani u tjednu po bloku, području ili ulici, bez datuma važenja; "
        f"pravila za papir i plastiku na istoku Splita vrijede od {first_anchor:%-d.%-m.%Y}.). Stranica napominje "
        "da je raspored podložan promjenama i da je ažurirani raspored kod poslovođa Operativne službe.",
        "Pomaci zbog blagdana nisu objavljeni (ni pravilo ni obavijesti među novostima); upisani su redovni dani.",
        "Bez upisanih datuma (zajednički spremnici koji se prazne svakodnevno)."
        + (f" Svaki dan: {every}." if every else "")
        + (f" Šest puta tjedno, ponedjeljak – subota: {six}." if six else ""),
    ] + NAPOMENE + [glom]
    data = {**PROVIDER, "izvor": links["blokovi"], "jls": JLS, "napomene": napomene, "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years of zones that did not change
        old = podaci.load(path)
        for k, zone in zones.items():
            prev = old.get("zone", {}).get(k)
            if prev and prev.get("podrucje") == zone["podrucje"] and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    print(f"Zona: {len(zones)}; odvoza {year} (zbroj po zonama): "
          + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
