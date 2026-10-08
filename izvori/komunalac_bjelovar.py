"""Bjelovar and 8 municipalities: Komunalac d.o.o. Bjelovar (komunalac-bj.hr).

    python3 -m izvori.komunalac_bjelovar [--year 2026]

City: the street table on /odvoz-otpada-ulice (a Craft Sprig component, read one letter category at a
time through its render endpoint) gives the mixed waste weekday of about 310 streets and settlements
(one street has two days). The year's PDFs linked from /obavijesti-komunalac-bjelovar/odvoz-otpada give
the paper and plastic dates of family houses in 10 street groups (a table: dates | streets) and the
biowaste dates of houses with a brown bin (every other week on the mixed waste weekday: a table of
weekday rows by month columns). Street names of the table and the paper/plastic groups are matched after
normalising (accents kept, punctuation, house-number ranges and "od … do …" parts dropped) plus an
alias list for different spellings; a street without a match gets no paper/plastic dates (printed).
Zones are streets with the same mixed waste day(s) and paper/plastic group.
Municipalities: the paper/plastic PDF gives one weekday and 12 dates per municipality (words split into
the date column and the "OPĆINA X: naselja" column, matched by vertical overlap), the bulky waste
leaflets give one or two Saturdays per group of settlements (one zone per group). The mixed waste day
of a municipality is printed only on the bills; it is used here only when a holiday notice states it.
Holidays: paper/plastic, bio and bulky dates are as published (a bio date off its weekday is marked as
moved). The holiday notices (PDFs of the "Obavijest o odvozu komunalnog otpada u vrijeme …" posts)
say per area and weekday whether a holiday is a normal collection day, moves to a Saturday, or (the
municipalities) is skipped until the next regular round; the notices of the year are applied, other
holidays keep the normal day in the city and are skipped in the municipalities (their constant rule).
"""
import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import DANI, blagdani, tjedno

SLUG = "komunalac-bjelovar"
SITE = "https://komunalac-bj.hr"
STREETS = SITE + "/odvoz-otpada-ulice"
DOCS = SITE + "/obavijesti-komunalac-bjelovar/odvoz-otpada"
RENDER = SITE + "/index.php?p=actions/sprig-core/components/render"
OPCINE = ["Kapela", "Nova Rača", "Rovišće", "Severin", "Šandrovac", "Velika Pisanica", "Veliko Trojstvo",
          "Zrinski Topolovac"]
WEEKDAYS = ["PONEDJELJ", "UTOR", "SRIJED", "ČETVRT", "PET"]  # stems: PONEDJELJAK/-KOM, SRIJEDA/-OM, ...
WEB_DAYS = {"pon": 0, "uto": 1, "sri": 2, "čet": 3, "pet": 4}
MONTHS = {"SIJEČ": 1, "VELJAČ": 2, "OŽUJ": 3, "TRAV": 4, "SVIB": 5, "LIP": 6, "SRP": 7, "KOLOVOZ": 8,
          "RUJ": 9, "LISTOPAD": 10, "STUDEN": 11, "PROSIN": 12}
# bulky leaflet spelling -> settlement name in the paper/plastic PDF
PLACE_ALIAS = {"Gornje Zdelice": "Gornje Zdjelice"}
# street table name (normalised) -> name in the paper/plastic PDF (normalised)
ALIAS = {
    "ANTA ANTIĆA": "ANTE ANTIĆA", "ADALBERTA OPITZA": "ADALBERTA OPPITZA",
    "ANTE STARČEVIĆA": "DR ANTE STARČEVIĆA", "BLAŽENOG G KOTORSKOG": "BLAŽENOG GRACIJA KOTORSKOG",
    "SPORTSKA": "ŠPORTSKA", "BREZOVAC GLAVNA CESTA": "GAREŠNIČKA",  # all of Brezovac is one group
    "DR LUKE STARČEVIĆA": "LUKE DR STARČEVIĆA", "FERDE GASSMANA": "FERDE GASSMANNA",
    "HRGOVLJANI": "HRGOVLJANI ANTUNA MATIJE RELJKOVIĆA", "IVANA SUPEKA": "AKADEMIKA IVANA SUPEKA",
    "JOSIPA JURJA STROSSMAYERA": "J J STROSSMAYERA", "MILANA ŠUFFLAYA": "MILANA ŠUFLLAYA",
    "MIHANOVIĆEVA": "ANTUNA MIHANOVIĆA", "PURIČANI": "PURIĆANI", "ŠETALIŠTE DR I LEBOVIĆA":
    "ŠETALIŠTE DR IVŠE LEBOVIĆA", "SVETE ANE PRILAZ": "PRILAZ SVETE ANE", "SVETE ANE ULICA": "SVETE ANE",
    "TOMAŠE GARIKA MASARYKA": "TOMAŠA G MASARYKA", "PLAVNIČKA ULICA": "PLAVNIČKA",
    "CRKVENA ULICA": "NOVOSELJANI CRKVENA",
}
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Bjelovar",
    "web": SITE,
    "izvor": STREETS,
    "zupanija": "Bjelovarsko-bilogorska",
    "jls": ["Bjelovar"] + OPCINE,
    "bioNapomena": "Biootpad iz obiteljskih kuća sa smeđim spremnikom odvozi se svaka dva tjedna, istog dana "
                   "kad i miješani otpad (u Gradu Bjelovaru). Stambene zgrade i korisnici vreća u općinama "
                   "imaju poseban raspored (" + DOCS + ").",
}
NAPOMENE = [
    "Raspored vrijedi za obiteljske kuće; stambene zgrade imaju vlastiti raspored (papir i plastika "
    "jednom tjedno, biootpad, glomazni otpad).",
    "Papir i plastika odvoze se istog dana (plavi i žuti spremnik).",
    "Glomazni otpad u Gradu Bjelovaru odvozi se subotom po skupinama ulica; raspored: " + DOCS + ".",
    "Općine: dan odvoza miješanog otpada otisnut je na računu; ovdje je upisan samo kad ga navodi neka "
    "obavijest davatelja o blagdanima. Prema tim obavijestima miješani se otpad u općinama na blagdan ne "
    "odvozi (sljedeći odvoz je u redovitom terminu), osim u Općini Severin (odvozi se redovito).",
    "Grad Bjelovar: za blagdane davatelj objavljuje posebne obavijesti; bez obavijesti je upisan redoviti dan.",
    "Reciklažna dvorišta: T. G. Masaryka 4b (043/332-112) i Prespa bb (043/242-450). Kompostana: Prespa bb.",
]


def norm(s):
    """'Ždralovi – Kralja Tomislava' -> 'ŽDRALOVI KRALJA TOMISLAVA' (upper case, accents kept)."""
    return " ".join(re.sub(r"[^\wČĆŽŠĐ]+", " ", s.upper()).split())


def base(name):
    """Street table name without settlement prefix, bracketed part, 'prilaz N' or house numbers."""
    k = norm(re.sub(r"\([^)]*\)", " ", name))
    k = re.sub(r"^(ŽDRALOVI|BREZOVAC) ", "", k)
    k = re.sub(r" (PRILAZ [IV]+|OD .*|\d+[A-Z]?( \d+[A-Z]?)*)$", "", k)
    k = re.sub(r"^SV ", "SVETE ", k)
    return ALIAS.get(k, k)


def pdf_text(path, layout=False):
    return subprocess.run(["pdftotext"] + (["-layout"] if layout else []) + [str(path), "-"],
                          capture_output=True, text=True, check=True).stdout


def weekday_of(word):
    w = word.upper()
    return next((i for i, s in enumerate(WEEKDAYS) if w.startswith(s)), None)


def street_table():
    """{street: [weekday, ...]} from every letter category of the Sprig street table."""
    page = fetch(STREETS).decode("utf-8", "replace")
    configs = [json.loads(html.unescape(v))["sprig:config"] for v in re.findall(r'data-hx-vals="([^"]*)"', page)]
    search = next(c for c in configs if "search.twig" in c)
    out, problems = {}, []
    for value, letter in re.findall(r'<option value="(\d+)"\s*>([^<]+)</option>', page):
        q = urllib.parse.urlencode({"sprig:config": search, "selectedCategoryId": value, "query": ""})
        part = fetch(RENDER + "&" + q).decode("utf-8", "replace")
        rows = [[html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
                for r in re.findall(r"<tr>(.*?)</tr>", part, re.S)]
        rows = [r for r in rows if r]
        if len(rows) >= 50:
            problems.append(f"slovo {letter}: {len(rows)} redaka (možda odrezano na 50)")
        for r in rows:
            days = [WEB_DAYS[c.lower()[:3]] for c in r[1:] if c]
            if len(r) != 6 or not days:
                problems.append(f"ulica {r}: neočekivan redak")
                continue
            out[" ".join(r[0].split())] = days
    return out, problems


def find_links(page, year):
    """Year's PDFs on the documents page and the holiday notice posts."""
    pdfs = {urllib.parse.unquote(h).lstrip("/") for h in re.findall(r'href="([^"]+\.pdf)"', page, re.I)}
    pick = lambda *keys: sorted(p for p in pdfs if all(k in p.upper() for k in keys) and str(year) in p)
    posts = sorted(set(re.findall(r'href="([^"]*/informacije/obavijest-o-odvozu-komunalnog-otpada-u-vrijeme[^"]*)"', page)))
    return {"pp": pick("PAPIRA-I-PLASTIKE", "GRADA-BJELOVARA"), "ppo": pick("PAPIRA-I-PLASTIKE", "OPĆINA"),
            "bio": pick("BIOOTPADA-ZA-OBITELJSKE-KUĆE-SPREMNICI"),
            "bulky": pick("LETAK-GLOMAZNI-OTPAD-OPĆINA")}, posts


def dates_in(text, year):
    """'05. 01._ 02. 02._ … i 07.12. 2026.' -> dates (the year itself is not a date)."""
    return [date(year, int(m), int(d)) for d, m in re.findall(r"(?<![\d.])(\d{1,2})\.\s*(\d{1,2})\.", text)]


def city_paper(path, year):
    """[(dates, [street, ...])] for the 10 groups of the city paper/plastic PDF."""
    out = []
    for page in pdfplumber.open(path).pages:
        for table in page.extract_tables():
            for row in table:
                if len(row) >= 2 and row[0] and row[1] and re.search(r"\d\.\s*\d{2}\.", row[0]):
                    names = [s.strip(" .") for s in re.split(r"[;,]", row[1].replace("\n", " ")) if s.strip(" .")]
                    out.append((dates_in(row[0], year), names))
    return out


def bio_table(path, year):
    """{weekday: [dates]} from the 'Svaki DRUGI PONEDJELJAK' rows by month columns."""
    words = pdfplumber.open(path).pages[0].extract_words()
    head = next(w for w in words if w["text"] == "Dani/Mjesec")
    cols = {round(w["x0"]): int(w["text"][:2]) for w in words
            if abs(w["top"] - head["top"]) < 3 and re.fullmatch(r"\d{2}\.", w["text"])}
    starts = sorted(w["top"] for w in words if w["text"] in ("Svaki", "Svaka") and w["x0"] < head["x1"]
                    and w["top"] > head["top"])
    out = {}
    for i, top in enumerate(starts):
        bottom = starts[i + 1] if i + 1 < len(starts) else top + 45
        band = [w for w in words if top - 2 <= w["top"] < bottom - 2]
        day = next(weekday_of(w["text"]) for w in band if weekday_of(w["text"]) is not None)
        ds = []
        for w in band:
            if re.fullmatch(r"\d{1,2}\.", w["text"]):
                x = min(cols, key=lambda c: abs(c - w["x0"]))
                if abs(x - w["x0"]) > 15:
                    raise ValueError(f"bio: broj {w['text']} izvan stupca")
                ds.append(date(year, cols[x], int(w["text"][:-1])))
        out[day] = sorted(ds)
    return out


def municipal_paper(path, year):
    """{općina: (dates, [settlements])}: the date column and the 'OPĆINA X: …' column by vertical overlap."""
    page = pdfplumber.open(path).pages[0]
    words = page.extract_words()
    head = next(w for w in words if w["text"] == "DATUM")
    words = [w for w in words if w["top"] > head["bottom"]]
    split = min(w["x0"] for w in words if w["text"] == "OPĆINA") - 5
    blocks, items = [], []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if w["x0"] < split:
            day = weekday_of(w["text"])
            if day is not None and w["text"].isupper() and len(w["text"]) > 4:
                blocks.append({"day": day, "top": w["top"], "bottom": w["bottom"], "text": ""})
            elif blocks:
                blocks[-1]["text"] += " " + w["text"]
                blocks[-1]["bottom"] = max(blocks[-1]["bottom"], w["bottom"])
        else:
            if w["text"] == "OPĆINA":
                items.append({"top": w["top"], "bottom": w["bottom"], "text": ""})
            elif items:
                items[-1]["text"] += " " + w["text"]
                items[-1]["bottom"] = w["bottom"]
    out = {}
    for it in items:
        b = max(blocks, key=lambda b: min(b["bottom"], it["bottom"]) - max(b["top"], it["top"]))
        name, _, rest = it["text"].strip().partition(":")
        ds = []
        for d, m in re.findall(r"(\d{1,2})\.\s*([A-ZČĆŽŠĐ]+)", b["text"]):
            month = next((v for k, v in MONTHS.items() if m.startswith(k)), None)
            if month:
                ds.append(date(year, month, int(d)))
        places = [p.strip(" .") for p in re.split(r",|\sI\s", rest) if p.strip(" .")]
        out[name.strip()] = (ds, places, b["day"])
    return out


def bulky(path, year):
    """[(date, [settlements])]: dates in order, settlement lists end with a full stop."""
    text = pdf_text(path, layout=True).split("PRIKUPLJAMO")[0]
    ds = [date(int(y), int(m), int(d)) for d, m, y in re.findall(r"Subota-\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text)]
    rest = re.sub(r"Subota-\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{4}\.?", " ", text)
    rest = " ".join(rest.split("odvoza", 1)[-1].replace("NE ", " ").split())
    groups = [g for g in re.split(r"(?<=[a-zčćžšđ])\.\s*", rest) if g.strip()]
    if len(groups) != len(ds):
        raise ValueError(f"{path.name}: {len(ds)} datuma, {len(groups)} skupina naselja")
    return [(d, [PLACE_ALIAS.get(p.strip(" ."), p.strip(" .")) for p in re.split(r",|\si\s", g) if p.strip(" .")])
            for d, g in zip(ds, groups)]


def notices(posts, tmp):
    """[(year, areas, weekday, holiday, outcome)], outcome 'redovito' | 'preskače' | date of the Saturday."""
    out = []
    for i, post in enumerate(posts):
        page = fetch(urllib.parse.urljoin(SITE, post)).decode("utf-8", "replace")
        pdfs = [h for h in re.findall(r'href="([^"]+\.pdf)"', page) if "OBAVIJEST-O-ODVOZU" in urllib.parse.unquote(h).upper()]
        if not pdfs:
            continue
        path = Path(tmp) / f"obavijest{i}.pdf"
        fetch(urllib.parse.urljoin(SITE, pdfs[0]), path)
        text = " ".join(pdf_text(path).split())
        for para in text.split("KORISNICIMA")[1:]:
            if "ZGRAD" in para.split("OD KOJIH")[0]:
                continue
            head = para.split("OD KOJIH")[0]
            areas = ["Bjelovar"] if "GRADA BJELOVARA" in head else []
            m = re.search(r"OPĆIN[AE] (.*)", head)
            if m:
                areas += [o for o in OPCINE if o.upper() in m.group(1)]
            dm = re.search(r"ODVOZI[^,]*?\b(PONEDJELJ|UTOR|SRIJED|ČETVRT|PET)\w*", para)
            hm = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", para)
            if not areas or not dm or not hm:
                continue
            hol = date(int(hm.group(3)), int(hm.group(2)), int(hm.group(1)))
            if "REDOVITO ODVOZITI" in para:
                outcome = "redovito"
            elif "NEĆE" in para and (sm := re.search(r"U SUBOTU (\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", para)):
                outcome = date(int(sm.group(3)), int(sm.group(2)), int(sm.group(1)))
            elif "NEĆE" in para and "REDOVITOG ODVOZA" in para:
                outcome = "preskače"
            else:
                continue
            out.append((hol.year, areas, weekday_of(dm.group(1)), hol, outcome))
    return out


def check_series(label, ds, problems, months=12, weekday=None):
    """One date per month (or `months` dates), all on `weekday`, no duplicates."""
    if len(ds) != months or len(set(ds)) != len(ds) or sorted(ds) != ds:
        problems.append(f"{label}: {len(ds)} datuma, očekivano {months} različitih po redu")
    days = Counter(d.weekday() for d in ds)
    if len(days) != 1 or (weekday is not None and weekday not in days):
        problems.append(f"{label}: dani u tjednu {dict(days)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    hol = [h for h in blagdani(year) if h.weekday() < 5]
    problems = []
    web, probs = street_table()
    problems += probs
    links, posts = find_links(fetch(DOCS).decode("utf-8", "replace"), year)
    for k, v in links.items():
        if not v or (k != "bulky" and len(v) > 1):
            sys.exit(f"Dokument '{k}' za {year}: {v}. Ništa nije upisano.")
    print(f"Ulica u tablici: {len(web)}; glomazni letci općina: {len(links['bulky'])}; obavijesti: {len(posts)}")

    with tempfile.TemporaryDirectory() as tmp:
        def pdf(rel):
            path = Path(tmp) / f"{len(list(Path(tmp).iterdir()))}.pdf"
            fetch(f"{SITE}/{rel}", path)
            return path
        groups = city_paper(pdf(links["pp"][0]), year)
        bio = bio_table(pdf(links["bio"][0]), year)
        muni = municipal_paper(pdf(links["ppo"][0]), year)
        leaflets = {}
        for rel in links["bulky"]:
            name = next((o for o in OPCINE if o.upper().replace(" ", "-") in rel.upper()), None)
            leaflets[name] = bulky(pdf(rel), year)
        said = notices(posts, tmp)

    # paper/plastic groups of the city: 12 dates on one weekday, label "PONEDJELJAK 1" etc.
    labels, seen = [], Counter()
    for i, (ds, names) in enumerate(groups):
        day = Counter(d.weekday() for d in ds).most_common(1)[0][0] if ds else 0
        seen[day] += 1
        labels.append(f"{podaci.DAYS[day]} {seen[day]}")
        check_series(f"papir/plastika {labels[-1]}", ds, problems)
    if len(groups) != 10:
        problems.append(f"papir/plastika grad: {len(groups)} skupina, očekivano 10")
    group_of = {}
    for i, (_, names) in enumerate(groups):
        for n in names:
            k = base(n)
            if group_of.get(k, i) != i:
                problems.append(f"papir/plastika: {n} u dvije skupine")
            group_of[k] = i
    # bio rows: every 14 days on the weekday, a date off the weekday is a holiday move
    for day, ds in bio.items():
        off = [d for d in ds if d.weekday() != day]
        if not 25 <= len(ds) <= 27 or any(not any(abs((d - h).days) <= 3 for h in blagdani(year)) for d in off):
            problems.append(f"biootpad {podaci.DAYS[day]}: {len(ds)} datuma, izvan dana {off}")
    if sorted(bio) != [0, 1, 2, 3, 4]:
        problems.append(f"biootpad: redovi {sorted(bio)}")

    # holiday notices: municipal weekdays and the city's holiday outcomes of the year
    muni_day, muni_rule, moves = {}, {}, {}
    for y, areas, day, h, outcome in sorted(said, key=lambda s: s[0]):
        for a in areas:
            if a != "Bjelovar":
                muni_day[a] = (day, y)
                muni_rule[a] = "preskače" if "preskače" in (outcome, muni_rule.get(a)) else "redovito"
            if y == year:
                moves[(a, day, h)] = outcome
    for (a, day, h), outcome in sorted(moves.items(), key=lambda x: x[0][2]):
        print(f"Obavijest {h:%d.%m.%Y.} ({podaci.DAYS[day]}): {a} {outcome}")

    def mixed(area, day, rule):
        """Weekly dates on `day` with the holiday notices of the year, else `rule` on weekday holidays."""
        rows = {}
        for d in tjedno(year, list(DANI)[day]):
            out = moves.get((area, day, d), rule) if d in hol else "redovito"
            if out != "preskače":
                rows[out if isinstance(out, date) else d] = ["M", isinstance(out, date)]
        return rows

    zones, unmatched = {}, []
    by_key = defaultdict(list)
    for street, days in web.items():
        g = group_of.get(base(street))
        if g is None:
            unmatched.append(street)
        by_key[(tuple(sorted(set(days))), g)].append(street)
    print(f"Ulice bez skupine papira/plastike: {unmatched}")
    n = 0
    for (days, g), streets in sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1] if kv[0][1] is not None else 99)):
        rows = {}
        for day in days:
            rows.update(mixed("Bjelovar", day, "redovito"))
        if len(days) == 1:
            for d in bio[days[0]]:
                r = rows.setdefault(d, ["", False])
                r[0] += "B"
                r[1] = r[1] or d.weekday() != days[0]
        if g is not None:
            for d in groups[g][0]:
                rows.setdefault(d, ["", False])[0] += "PK"
        n += 1
        day_txt = " i ".join(podaci.DAYS[d] for d in days)
        pp_txt = f"papir i plastika {labels[g]}" if g is not None else "bez rasporeda papira i plastike"
        zone = {"jls": "Bjelovar",
                "podrucje": f"Bjelovar, {day_txt}; {pp_txt}: " + ", ".join(sorted(streets)[:3]) + (", …" if len(streets) > 3 else ""),
                "ulice": sorted(streets)}
        notes = []
        if g is None:
            notes.append("Ove ulice nisu na popisu rasporeda papira i plastike; provjerite kod davatelja.")
        if len(days) > 1:
            notes.append("Dva dana odvoza miješanog otpada; dan odvoza biootpada nije jednoznačan pa nije upisan.")
        if notes:
            zone["napomena"] = " ".join(notes)
        zone["raw"] = {str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])}
        zones[str(n)] = zone

    # municipalities: one zone per bulky waste group
    upper = {o.upper(): o for o in OPCINE}
    if sorted(upper.get(k, k) for k in muni) != sorted(OPCINE):
        problems.append(f"papir/plastika općine: {sorted(muni)}")
    for name in OPCINE:
        ds, places, pday = next((v for k, v in muni.items() if upper.get(k) == name), ([], [], None))
        check_series(f"papir/plastika {name}", ds, problems, weekday=pday)
        mday = muni_day.get(name)
        if mday is None:
            print(f"{name}: dan miješanog otpada nije nigdje naveden, upisuje se samo papir/plastika i glomazni")
        else:
            print(f"{name}: miješani otpad {podaci.DAYS[mday[0]]} (obavijest {mday[1]}.), blagdan: {muni_rule[name]}")
        leaflet = leaflets.get(name) or []
        if not leaflet:
            problems.append(f"{name}: nema letka za glomazni otpad")
        listed = {norm(p) for _, ps in leaflet for p in ps}
        extra = [p for p in places if norm(p) not in listed]
        if extra:
            print(f"   {name}: naselja iz rasporeda papira/plastike kojih nema u letku glomaznog: {extra}")
        for gd, settlements in leaflet + ([(None, [p.title() for p in extra])] if extra else []):
            if gd and gd.weekday() != 5:
                problems.append(f"{name}: glomazni {gd} nije subota")
            rows = mixed(name, mday[0], muni_rule[name]) if mday else {}
            for d in ds:
                rows.setdefault(d, ["", False])[0] += "PK"
            if gd:
                rows.setdefault(gd, ["", False])[0] += "G"
            n += 1
            day_txt = f"miješani {podaci.DAYS[mday[0]]}" if mday else "miješani: vidi račun"
            zone = {"jls": name,
                    "podrucje": f"Općina {name} ({day_txt}): " + ", ".join(settlements[:4]) + (", …" if len(settlements) > 4 else ""),
                    "ulice": settlements}
            if mday is None:
                zone["napomena"] = ("Dan odvoza miješanog otpada nije javno objavljen (otisnut je na računu), "
                                    "pa ovdje nije upisan.")
            elif mday[1] < year:
                zone["napomena"] = (f"Dan odvoza miješanog otpada prema obavijesti davatelja iz {mday[1]}.; "
                                    "provjerite na računu.")
            zone["raw"] = {str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])}
            zones[str(n)] = zone

    # checks
    for z, zone in zones.items():
        rows = list(podaci.iter_dates(zone, year))
        cnt = Counter(d.month for d, c, _ in rows if "M" in c)
        mixed = [m for m in range(1, 13) if cnt[m] and not 3 <= cnt[m] <= 10]
        if mixed:
            problems.append(f"zona {z}: miješani po mjesecima {dict(cnt)}")
        if any(d.weekday() == 6 for d, _, _ in rows):
            problems.append(f"zona {z}: odvoz nedjeljom")
        print(f"Zona {z}: {zone['podrucje'][:90]} | " + ", ".join(f"{c} {sum(c in x for _, x, _ in rows)}" for c in "MBPKG"))
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "napomene": NAPOMENE, "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(zones)} zona)")


if __name__ == "__main__":
    main()
