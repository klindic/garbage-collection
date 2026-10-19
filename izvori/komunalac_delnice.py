"""Delnice, Brod Moravice, Lokve, Mrkopalj, Ravna Gora, Skrad: Komunalac d.o.o. Delnice (komunalac.hr).

    python3 -m izvori.komunalac_delnice [--year 2026]

Mixed waste: the page "Raspored odvoza komunalnog otpada" lists, per weekday and municipality, the streets
and settlements ("Ulice: - Sajmišna - Kamenita ...", "Naselja: - Lučice - ..."). It names only the weekday,
so mixed waste is taken as weekly on that day (an assumption, printed and noted). Paper, mixed packaging
(orange lid) and glass: the year page "Raspored odvoza YYYY. godina" links three PDFs, each a table of
area -> weekday and the monthly dates; the rows are read with pdfplumber's table finder. The three PDFs
group the areas differently ("Delnice, Lučice", "Općina Skrad", "ulice u kojima je odvoz komunalnog
otpada PONEDJELJKOM", "sve preostale ulice", house-number parts of Ulica Bajt), so every street and
settlement of the mixed-waste page is resolved against each PDF row (an explicit name beats a town-wide
or municipality-wide row) and every combination of mixed-waste day and recyclables dates is one zone.
Every date is checked against the row's weekday; a date off that weekday is kept as moved only when the
row says so ("osim 05.01.-ponedjeljak") or a holiday falls in that week, otherwise it is a typo and is
printed and skipped. No holiday rule is published for mixed waste: weekly dates are kept as computed.
"""
import argparse
import html
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalac-delnice"
SITE = "http://www.komunalac.hr"
MIXED = SITE + "/cistoca/12-smece/4-raspored-odvoza-komunalnog-otpada"
YEAR_PAGE = SITE + "/cistoca/raspored-odvoza-{year}-godina"
JLS = ["Delnice", "Brod Moravice", "Lokve", "Mrkopalj", "Ravna Gora", "Skrad"]
TOWNS = {"Delnice", "Ravna Gora"}  # municipalities whose "Ulice:" lists form the town
WEEKDAYS = ["ponedjelj", "utor", "srijed", "četvrt", "pet", "subot"]  # stems of the weekday words
DAY_WORD = r"ponedjelj(?:ak|kom)|utor(?:ak|kom)|srijed(?:a|om)|cetvrt(?:ak|kom)|pet(?:ak|kom)|subot(?:a|om)"
JLS_OF = {"Dedin": "Delnice"}  # listed under Ravna Gora on the mixed-waste page, a settlement of Grad Delnice
ALIASES = {"Japlenški": "Japlenški Vrh", "Turke": "Turki", "Satar Sušica": "Stara Sušica", "Brod n/K": "Brod na Kupi"}
PDFS = {"K": "papira", "P": "NARANCASTIM", "S": "staklene"}
TYPE_NAME = {"K": "papir", "P": "ambalaža", "S": "staklo"}
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Delnice",
    "web": SITE,
    "zupanija": "Primorsko-goranska",
    "jls": JLS,
    "nazivi": {"K": "Papir i karton (plavi spremnik)",
               "P": "Plastična i metalna ambalaža (spremnik s narančastim poklopcem)",
               "S": "Staklena ambalaža (prozirne vreće)"},
}
NAPOMENE = [
    "Raspored miješanog komunalnog otpada navodi samo dan u tjednu; prikazan je tjedni odvoz tim danom. "
    "Pomaci zbog blagdana za miješani otpad nisu objavljeni (datumi su prikazani bez pomaka).",
    "Papir, ambalaža (spremnik s narančastim poklopcem) i staklo odvoze se jednom mjesečno prema objavljenim "
    "datumima; staklo se od 1.2.2026. preuzima na adresi u prozirnim vrećama (paket vreća u Komunalcu, Supilova 173, "
    "ili staklo@komunalac.hr).",
    "Za naselja koja tablice papira, ambalaže ili stakla ne navode, ta vrsta otpada nije prikazana.",
    "Krupni (glomazni) otpad skuplja se organizirano dva puta godišnje (proljeće i jesen).",
    "Reciklažno dvorište Sović Laz: 051 829 354.",
]


def fold(s):
    s = s.lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def keys(name):
    """(full key, short key): 'Tina Ujevića' and 'T. Ujevića' share the short key 't ujevica'."""
    toks = re.findall(r"\d+|[^\W\d_]+", fold(name))
    return " ".join(toks), (toks[0][0] + " " + toks[-1]) if len(toks) > 1 else " ".join(toks)


def plain(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def mixed_units(page):
    """Mixed-waste page -> [unit]: {name, jls, day, kind ('ulica'|'naselje')}, in page order."""
    body = page.split('itemprop="articleBody"', 1)[-1].split("article-footer", 1)[0]
    units, day, jls = [], None, None
    for tag, inner in re.findall(r"<(h4|p)[^>]*>(.*?)</\1>", body, re.S):
        text = plain(inner)
        stem = next((i for i, s in enumerate(WEEKDAYS) if fold(text).startswith(fold(s))), None)
        if tag == "h4" and stem is not None:
            day = stem
            continue
        m = re.fullmatch(r"(?:Grad|Općin[ae]) (.+)", text)
        if m and m.group(1) in JLS:
            jls = m.group(1)
            continue
        for kind, items in re.findall(r"(Ulice|Naselja):(.*?)(?=Ulice:|Naselja:|$)", text):
            for item in re.split(r"\s*-\s+|\s+-\s*|^-", items):
                item = item.strip(" -")
                if not item or day is None or jls is None:
                    continue
                g = re.fullmatch(r"([A-ZČĆŽŠĐ]\w+) i ([A-ZČĆŽŠĐ]\w+) ([A-ZČĆŽŠĐ]\w+)", item)  # Gornje i Donje Tihovo
                for name in ([f"{g.group(1)} {g.group(3)}", f"{g.group(2)} {g.group(3)}"] if g else [item]):
                    name = ALIASES.get(name, name)
                    units.append({"name": name, "jls": JLS_OF.get(name, jls), "day": day,
                                  "kind": "ulica" if kind == "Ulice" else "naselje",
                                  "town": jls if kind == "Ulice" and jls in TOWNS else None})
    return units


def table_rows(pdf):
    """[(area text, rest text)] of the PDF table, header and notes left out."""
    out = []
    with pdfplumber.open(pdf) as doc:
        for page in doc.pages:
            for table in page.find_tables():
                for row in table.extract():
                    cells = [" ".join((c or "").split()) for c in row]
                    cells = [c for c in cells if c]
                    if len(cells) >= 2 and re.search(r"\d\d[.,]\d\d\.", cells[-1]):
                        out.append((cells[0], " ".join(cells[1:])))
    return out


def clean(text):
    for a, b in ALIASES.items():
        text = re.sub(rf"(?<!\w){re.escape(a)}(?!\w)", b, text)
    return " ".join(text.replace("( ", "(").replace(" ,", ",").split())


def parse_dates(rest, year, problems, typos, what):
    """'Utorkom (osim 05.01.-ponedjeljak): 05.01., 03.02., ...' -> ([(date, moved)], area words before the dates)."""
    exceptions = {}
    for d, m, wd in re.findall(r"osim (\d\d)\.(\d\d)\.\s*[-–]\s*(\w+)", rest):
        exceptions[date(year, int(m), int(d))] = next(i for i, s in enumerate(WEEKDAYS) if fold(wd).startswith(fold(s)))
    body = re.sub(r"\(\s*osim[^)]*\)", " ", rest)
    words = [w for w in re.findall(r"[^\W\d_]+", body) if re.fullmatch(DAY_WORD, fold(w))]
    day = next((i for i, s in enumerate(WEEKDAYS) if words and fold(words[0]).startswith(fold(s))), None)
    first = re.search(r"\d\d[.,]\d\d\.", body)
    lead = body[:first.start()] if first else body
    if day is None:
        problems.append(f"{what}: dan u tjednu nije naveden ({rest[:60]!r})")
        return [], lead, []
    hol = pravila.blagdani(year)
    out, notes = [], []
    for token in re.findall(r"\d\d[.,]\d\d\.", body):
        if "," in token:
            print(f"   {what}: '{token}' pročitano kao {token.replace(',', '.')}")
        d, m = int(token[:2]), int(token[3:5])
        try:
            x = date(year, m, d)
        except ValueError:
            typos.append(f"{what}: '{token}' nije datum")
            notes.append(f"{token} (nije datum)")
            continue
        regular = x - timedelta(days=x.weekday() - day)  # the row's weekday in the same week
        if x.weekday() == day:
            out.append((x, False))
        elif exceptions.get(x) == x.weekday() or (regular in hol and x.weekday() < 6):
            out.append((x, True))
        else:
            typos.append(f"{what}: '{token}' je {podaci.DAYS[x.weekday()]}, a red je za {podaci.DAYS[day]}")
            notes.append(f"{token} (nije {podaci.DAYS[day]})")
    for x, _ in out:
        if x in hol:  # kept as published
            print(f"UPOZORENJE {what}: {x:%d.%m.} je blagdan")
    # about every four weeks: at most two dates a month, 10-13 a year, in order
    months = Counter(x.month for x, _ in out)
    if any(n > 2 for n in months.values()) or not 10 <= len(out) <= 13 or [x for x, _ in out] != sorted(x for x, _ in out):
        problems.append(f"{what}: {len(out)} datuma, po mjesecima {dict(months)}")
    return out, lead, notes


def find_units(name, units, jls_hint=None):
    """Units for one name: exact full key, then the short key ('L. Draga'); [] if none."""
    f, s = keys(name)
    for i in (0, 1):
        hit = [u for u in units if keys(u["name"])[i] == (f, s)[i] and (jls_hint is None or u["jls"] == jls_hint)]
        if hit:
            return hit
    return []


def select(area, lead, units, problems, what, new_units):
    """PDF row -> {unit index: priority}. 0 municipality, 1 town or weekday-wide, 2 named, 3 house-number part."""
    out = {}

    def add(us, prio):
        for u in us:
            i = next(k for k, v in enumerate(units) if v is u)
            out[i] = max(prio, out.get(i, -1))

    area, lead = clean(area), clean(lead)
    text = f"{area} {lead}".strip()
    m = re.match(r"(?:Čitava )?Općina ([\w ]+?)(?:,? osim naselja ([\w ]+))?(?:\s*[–-]\s*sva naselja|\s*\(sva naselja\))?$", area)
    if m and m.group(1).strip() in JLS:
        jls = m.group(1).strip()
        skip = [id(u) for u in find_units(m.group(2), units, jls)] if m.group(2) else []
        add([u for u in units if u["jls"] == jls and id(u) not in skip and "parts" not in u], 0)
        return out
    items = [i.strip() for i in area.split(",") if i.strip()]
    town = items[0] if items and items[0] in TOWNS else None
    wd = re.search(r"ulice u kojima je odvoz komunalnog otpada (\w+)", lead, re.I)
    if wd:  # town streets with that mixed-waste day, plus the named settlements
        day = next(i for i, s in enumerate(WEEKDAYS) if fold(wd.group(1)).startswith(fold(s)))
        add([u for u in units if u["town"] == town and u["day"] == day and "parts" not in u], 1)
        items = items[1:]
    elif re.match(r"ulice:", lead):  # an explicit list of the town's streets
        for name in re.split(r",\s*", lead.split(":", 1)[1].strip(" -–")):
            hit = find_units(name, [u for u in units if u["town"] == town])
            if not hit:
                print(f"UPOZORENJE {what}: ulica '{name}' nije na rasporedu miješanog otpada")
                hit = [new_unit(new_units, units, name, town, "ulica", town)]
            add(hit, 2)
        return out
    elif re.match(r"sve preostale ulice", lead):  # the rest of the town (explicit rows win) + named settlements
        add([u for u in units if u["town"] == town and "parts" not in u], 1)
        items = re.findall(r"naselj[ea] ([\w ]+)", lead)
    elif town:
        add([u for u in units if u["town"] == town and "parts" not in u], 1)
        items = items[1:]
    for item in items:
        part = re.match(r"Ulica (\w+) \(kućni brojevi:? ([^)]*)\)", item)
        if part:
            hit = [u for u in units if u.get("part_of") == part.group(1) and u["nums"] == re.sub(r"\s", "", part.group(2))]
            add(hit, 3) if hit else problems.append(f"{what}: dio ulice '{item}' nije poznat")
            continue
        hit = find_units(item, units)
        if not hit:  # 'Mrzla Vodica Zelin Mrzlovodički' (comma missing) or a name the mixed page does not have
            words, found = item.split(), []
            while words:
                for n in range(len(words), 0, -1):
                    h = find_units(" ".join(words[:n]), units)
                    if h:
                        found += h
                        words = words[n:]
                        break
                else:
                    jls = Counter(units[i]["jls"] for i in out).most_common(1)
                    print(f"UPOZORENJE {what}: '{' '.join(words)}' nije na rasporedu miješanog otpada")
                    found.append(new_unit(new_units, units, " ".join(words), jls[0][0] if jls else None, "naselje"))
                    break
            hit = found
        add(hit, 2)
    if not out:
        problems.append(f"{what}: područje {text!r} nije prepoznato")
    return out


def new_unit(new_units, units, name, jls, kind, town=None):
    for u in new_units:
        if u["name"] == name:
            return u
    u = {"name": name, "jls": jls, "day": None, "kind": kind, "town": town}
    units.append(u)
    new_units.append(u)
    return u


def split_parts(rows_by_type, units):
    """'Ulica Bajt (kućni brojevi: 31-53 i 26-34)' in the PDFs: the street becomes one unit per part."""
    for rows in rows_by_type.values():
        for area, rest in rows:
            for street, nums in re.findall(r"Ulica (\w+) \(kućni brojevi:? ([^)]*)\)", clean(area)):
                parent = next((u for u in units if u["name"] == street and u["town"]), None)
                key = re.sub(r"\s", "", nums)
                nums = " ".join(re.sub(r"-\s+", "-", nums).split())
                if parent is None or any(u.get("part_of") == street and u["nums"] == key for u in units):
                    continue
                parent["parts"] = True
                units.append({**{k: v for k, v in parent.items() if k != "parts"},
                              "name": f"{street} (kućni brojevi {nums})", "part_of": street, "nums": key})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems, typos = [], []
    units = mixed_units(fetch(MIXED).decode("utf-8", "replace"))
    if Counter(u["jls"] for u in units).keys() != set(JLS) or len(units) < 100:
        problems.append(f"miješani otpad: {len(units)} ulica/naselja, općine {sorted({u['jls'] for u in units})}")
    print("PRETPOSTAVKA: miješani otpad odvozi se svaki tjedan na navedeni dan (stranica navodi samo dan).")
    year_page = YEAR_PAGE.format(year=year)
    links = [html.unescape(u) for u in re.findall(r'href="([^"]+\.pdf)"', fetch(year_page).decode("utf-8", "replace"))]
    rows_by_type = {}
    with tempfile.TemporaryDirectory() as tmp:
        for code, word in PDFS.items():
            url = next((u for u in links if word.lower() in fold(u.replace("%C5%BE", "ž")).lower() and str(year) in u), None)
            if not url:
                problems.append(f"{TYPE_NAME[code]}: PDF za {year} nije pronađen na {year_page}")
                continue
            pdf = Path(tmp) / f"{code}.pdf"
            fetch(url if url.startswith("http") else SITE + url, pdf)
            rows_by_type[code] = table_rows(pdf)
            if len(rows_by_type[code]) < 8:
                problems.append(f"{TYPE_NAME[code]}: samo {len(rows_by_type[code])} redaka tablice")
    split_parts(rows_by_type, units)

    new_units, picks = [], {}  # picks[code][unit index] = (priority, row number, dates)
    for code, rows in rows_by_type.items():
        best = {}
        for n, (area, rest) in enumerate(rows):
            what = f"{TYPE_NAME[code]} '{area[:40]}'"
            dates, lead, notes = parse_dates(rest, year, problems, typos, what)
            for i, prio in select(area, lead, units, problems, what, new_units).items():
                old = best.get(i)
                if old and old[0] == prio and old[2] != dates:
                    problems.append(f"{TYPE_NAME[code]}: '{units[i]['name']}' u dva retka iste razine ({rows[old[1]][0][:30]} / {area[:30]})")
                if not old or prio > old[0]:
                    best[i] = (prio, n, dates, [f"{TYPE_NAME[code]} {t}" for t in notes])
        picks[code] = best
    for t in typos:
        print(f"PRESKOČENO (tipfeler): {t}")

    # zones: units with the same municipality, mixed-waste day and recyclables dates
    groups = {}
    for i, u in enumerate(units):
        if u.get("parts"):
            continue
        sig = tuple(tuple(picks.get(c, {}).get(i, (0, 0, ()))[2]) for c in "KPS")
        u["typos"] = [t for c in "KPS" for t in picks.get(c, {}).get(i, (0, 0, (), []))[3]]
        groups.setdefault((JLS.index(u["jls"]) if u["jls"] in JLS else 99, u["day"] if u["day"] is not None else 9, sig), []).append(u)
    zones = []
    order = {id(u): i for i, u in enumerate(units)}
    for (j, day, sig), us in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], order[id(kv[1][0])])):
        if j == 99:
            problems.append(f"bez općine: {[u['name'] for u in us]}")
            continue
        rows = {d: ("M", False) for d in pravila.tjedno(year, list(pravila.DANI)[day])} if day < 9 else {}
        for code, dates in zip("KPS", sig):
            for d, moved in dates:
                c, m = rows.get(d, ("", False))
                rows[d] = (c + code, m or moved)
        rows = sorted((d, c, m) for d, (c, m) in rows.items())
        names = [u["name"] for u in us]
        head = podaci.DAYS[day].capitalize() if day < 9 else "Miješani otpad: dan nije naveden"
        missing = [TYPE_NAME[c] for c, dates in zip("KPS", sig) if not dates]
        note = []
        if day == 9:
            note.append("Ove ulice/naselja nisu na rasporedu miješanog otpada.")
        if missing:
            note.append("Tablice ne navode: " + ", ".join(missing) + ".")
        skipped = list(dict.fromkeys(t for u in us for t in u["typos"]))
        if skipped:
            note.append("Izostavljeni neispravni datumi iz rasporeda: " + "; ".join(skipped) + ".")
        zone = {"jls": JLS[j], "podrucje": f"{head} – " + ", ".join(names[:4]) + (", …" if len(names) > 4 else ""),
                "ulice": names, "rows": rows}
        if note:
            zone["napomena"] = " ".join(note)
        zones.append(zone)
        cnt = Counter((d.month, c) for d, codes, _ in rows for c in codes)
        if day < 9 and any(not 4 <= cnt.get((m, "M"), 0) <= 5 for m in range(1, 13)):
            problems.append(f"{zone['podrucje']}: broj odvoza miješanog otpada")
    if len(typos) > 8:
        problems.append(f"previše neispravnih datuma ({len(typos)})")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": MIXED, "napomene": NAPOMENE + [f"Papir, ambalaža i staklo: {year_page}"], "zone": {}}
    for i, z in enumerate(zones, 1):
        rows = z.pop("rows")
        prev = old.get(str(i), {})
        raw = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("podrucje") == z["podrucje"] else {}
        z["raw"] = {**raw, str(year): podaci.month_lines(rows)}
        data["zone"][str(i)] = z
        cnt = Counter(c for _, codes, _ in rows for c in codes)
        print(f"Zona {i} ({z['jls']}): {z['podrucje'][:70]}: " + ", ".join(f"{c} {cnt[c]}" for c in "MPKS" if cnt[c])
              + f", pomaknuto {sum(1 for *_, m in rows if m)}")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona, preskočeno {len(typos)} neispravnih datuma)")


if __name__ == "__main__":
    main()
