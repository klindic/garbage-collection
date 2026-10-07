"""Duga Resa, Generalski Stol, Netretić, Barilović: Čistoća Duga Resa d.o.o. (cistoca-dugaresa.hr), PDF tables.

    python3 -m izvori.cistoca_duga_resa [--year 2026]

The page "Raspored odvoza komunalnog otpada" links one PDF per city or municipality (KALENDAR_<NAME>_<year>.pdf,
"Microsoft: Print To PDF" tables). Each landscape page is one zone ("DUGA RESA - 2", "NETRETIĆ LINIJA 1-1"):
a table with the months as columns and two rows, MIJEŠANI KOMUNALNI OTPAD and PLASTIKA I PAPIR (paper and
plastic together, PK), whose cells hold DD.MM. dates one per line; under it "RASPORED SE ODNOSI NA ..." with
the streets or settlements and sometimes a NAPOMENA. Words are put into rows by the table's horizontal
lines and into months by the x position of the month headings (pdfplumber). In Generalski Stol the
paper/plastic cells hold two dates, "26.01./28.01.": the first for houses on the main road, the second for
side roads (each line becomes two zones, the note says who uses which date). In "Naselja u okolici Duge
Rese - 1" the note puts Vikend naselje Osor on mixed waste every Monday and Friday from 1.6. to 15.9.; that
rule is applied in a zone of its own.
Holidays are built into the dates (Monday 05.01. instead of Tuesday 06.01.; the whole week of 1.5. one day
earlier): a date off the zone's usual weekday in a week with a public holiday is marked as moved.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cistoca-duga-resa"
SITE = "https://cistoca-dugaresa.hr"
PAGE = SITE + "/raspored-odvoza-komunalnog-otpada/"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
FILES = {"DUGA_RESA": "Duga Resa", "NETRETIC": "Netretić", "GENERALSKI_STOL": "Generalski Stol",
         "BARILOVIC": "Barilović"}
SINGLE = re.compile(r"(\d{1,2})\.(\d{1,2})\.?")
PAIR = re.compile(r"(\d{1,2}\.\d{1,2}\.?|-+)/(\d{1,2}\.\d{1,2}\.?|-+)")
DAYS = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "SRIJEDU": 2, "ČETVRTAK": 3, "PETAK": 4, "SUBOTU": 5}
LABELS = {"NASELJA U OKOLICI DUGE RESE": "Naselja u okolici Duge Rese", "DUGA RESA": "Duga Resa",
          "NETRETIĆ LINIJA": "Netretić, linija", "GENERALSKI STOL LINIJA": "Generalski Stol, linija",
          "BARILOVIĆ LINIJA": "Barilović, linija"}
# obvious typos in the street lists (missing or wrong separators)
FIXES = {
    "Gornje Bukovlje Donje Bukovlje": ["Gornje Bukovlje", "Donje Bukovlje"],
    "Okolnice. Mrežničke Poljice 17": ["Okolnice", "Mrežničke Poljice 17"],
    "Frketić Selo Vinski Vrh (dom za starije,motel ''AMARILIS'')": ["Frketić Selo",
                                                                    "Vinski Vrh (dom za starije, motel Amarilis)"],
}
PROVIDER = {
    "davatelj": "Čistoća Duga Resa d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Karlovačka",
    "jls": ["Duga Resa", "Generalski Stol", "Netretić", "Barilović"],
}
NAPOMENE = [
    "Plastika i papir odvoze se zajedno, jednom mjesečno.",
    "Pomaci zbog blagdana ugrađeni su u objavljene datume (npr. ponedjeljak 05.01. umjesto utorka 06.01.); "
    "datumi izvan uobičajenog dana zone u tjednu s blagdanom označeni su kao pomaknuti.",
    "Općina Barilović: Čistoća Duga Resa raspored objavljuje od 2026. godine.",
    "Kontakt: Čistoća Duga Resa d.o.o., Kolodvorska 1, Duga Resa, info@cistoca-dugaresa.hr.",
]


def naslov(name):
    """'BANA JOSIPA JELAČIĆA' -> 'Bana Josipa Jelačića'; mixed-case text stays."""
    if not name.isupper():
        return name
    words = [w[:1] + w[1:].lower() if w.isupper() and w not in ("I", "II") else w for w in name.split()]
    out = " ".join(w.lower() if i and w in ("Ulica", "Cesta", "Naselje", "Do", "Od", "Kbr.", "Kbr", "Nadalje")
                   else w for i, w in enumerate(words))
    return out.replace("S.s.", "S.S.")


def zone_label(title):
    """'NASELJA U OKOLICI DUGE RESE - 1' -> 'Naselja u okolici Duge Rese 1', 'NETRETIĆ LINIJA 1-1' -> 'Netretić,
    linija 1-1'."""
    for prefix, label in LABELS.items():
        if title.startswith(prefix):
            return label + " " + title[len(prefix):].strip(" -")
    return naslov(title)


def split_places(text):
    """'A, B (x, y); C 1, 2, 3, D I E' -> [A, B (x, y), C 1, 2, 3, D, E]."""
    items, depth, cur = [], 0, ""
    for ch in text + ",":
        depth += (ch == "(") - (ch == ")")
        if ch in ",;" and depth == 0:
            items.append(" ".join(cur.split()).strip(" ."))
            cur = ""
        else:
            cur += ch
    out = []
    for it in filter(None, items):
        if out and re.fullmatch(r"\d+[A-Z]?", it):
            out[-1] += ", " + it  # house numbers: "DOMOBRANSKA 43, 45, 47A"
        else:
            out.append(it)
    out = [naslov(o) for o in out]
    if out and " I " in out[-1].upper():
        left, right = re.split(r" [Ii] ", out[-1], maxsplit=1)
        if len(right.split()) <= len(left.split()):  # "Žabljak i Perjasica", not "Gornji i Donji Velemerić"
            out[-1:] = [left, right[:1].upper() + right[1:]]
    return [x for o in out for x in FIXES.get(o, [o])]


def cell_values(words):
    """Lines of one cell -> [str]: each line's words joined without spaces ("30.11./" "---" -> "30.11./---")."""
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0] - w["top"]) < 3:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    return ["".join(x["text"] for x in sorted(ws, key=lambda w: w["x0"])) for _, ws in lines]


def to_date(tok, month, year, problems, where):
    if tok.startswith("-"):
        return None
    m = SINGLE.fullmatch(tok)
    if not m:
        problems.append(f"{where}: ne razumijem {tok!r}")
        return None
    if int(m.group(2)) != month:
        problems.append(f"{where}: {tok} u stupcu {MONTHS[month - 1]}")
        return None
    try:
        return date(year, month, int(m.group(1)))
    except ValueError:
        problems.append(f"{where}: nemoguć datum {tok}")
        return None


def read_page(page, year, problems):
    """{title, rows: {"M": [date], "PK": [date] or [(first, second)]}, text} for one zone page."""
    lines = (page.extract_text() or "").splitlines()
    if len(lines) < 3 or f"ZA {year}. GODINU" not in lines[0]:
        problems.append(f"stranica nije raspored za {year}: {lines[:1]}")
        return None
    title = " ".join(lines[1].split())
    words = page.extract_words()
    heads = {MONTHS.index(w["text"]) + 1: (w["x0"] + w["x1"]) / 2 for w in words if w["text"] in MONTHS}
    if len(heads) != 12:
        problems.append(f"{title}: mjeseci {sorted(heads)}")
        return None
    left = heads[1] - (heads[2] - heads[1]) / 2  # left edge of the January column
    tops = sorted(r["top"] for r in page.rects if r["height"] < 2 and r["x0"] <= heads[1] <= r["x1"])
    bounds = [t for i, t in enumerate(tops) if i == 0 or t - tops[i - 1] > 2]
    labels = {"M": next((w for w in words if w["text"] == "MIJEŠANI" and w["x1"] < left), None),
              "PK": next((w for w in words if w["text"] == "PLASTIKA" and w["x1"] < left), None)}
    bands = {}
    for code, w in labels.items():
        cy = (w["top"] + w["bottom"]) / 2 if w else None
        band = next(((a, b) for a, b in zip(bounds, bounds[1:]) if cy and a < cy < b), None)
        if band is None:
            problems.append(f"{title}: nema retka {code}")
            return None
        bands[code] = band
    rows = {}
    for code, (a, b) in bands.items():
        cells = {}
        for w in words:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            if a < cy < b and w["x0"] > left - 2:
                cells.setdefault(min(heads, key=lambda k: abs(heads[k] - cx)), []).append(w)
        vals = []
        for month, ws in sorted(cells.items()):
            for v in cell_values(ws):
                where = f"{title} {code} {MONTHS[month - 1]}"
                pair = PAIR.fullmatch(v)
                if pair:
                    vals.append(tuple(to_date(t, month, year, problems, where) for t in pair.groups()))
                else:
                    vals.append(to_date(v, month, year, problems, where))
        rows[code] = vals
    # text box under the table, up to the waste-type instructions
    below = page.crop((0, bands["PK"][1] + 1, page.width, page.height)).extract_text() or ""
    text = []
    for line in below.splitlines():
        if "spremnike" in line or "sav otpad" in line:
            break
        text.append(line.strip())
    return {"title": title, "rows": rows, "text": " ".join(text)}


def moved_flags(dates, hol, where, series):
    """[(date, moved)]: a date off the usual weekday is moved when its week has a public holiday (Mon-Sat)."""
    usual = Counter(d.weekday() for d in dates).most_common(1)[0][0]
    out, odd = [], []
    for d in sorted(dates):
        if d.weekday() == usual:
            out.append((d, False))
            continue
        monday = d - timedelta(days=d.weekday())
        if any(monday <= h < monday + timedelta(days=6) for h in hol):
            out.append((d, True))
        else:
            odd.append(d)
            out.append((d, False))
            print(f"   UPOZORENJE {where} {series} {d:%d.%m.}: {podaci.DAYS[d.weekday()]} umjesto "
                  f"{podaci.DAYS[usual]}, a u tjednu nema blagdana (zadržano)")
    return out, odd


def area_parts(text):
    """'RASPORED SE ODNOSI NA ...: list. NAPOMENA ...' -> (list text, note or '')."""
    m = re.search(r"ODNOSI NA [^:]*:\s*(.*)", text)
    body = m.group(1) if m else ""
    parts = re.split(r"\.?\s*NAPOMENA\b\s*", body, maxsplit=1)
    return parts[0].strip(" ."), (parts[1].strip() if len(parts) > 1 else "")


def osor_rule(note, year, problems, where):
    """'IZ VIKEND NASELJA - OSOR, U PERIODU OD 01.06. - 15.09. MIJEŠANI ... SVAKI PONEDJELJAK I PETAK'."""
    m = re.search(r"IZ (.+?), U PERIODU OD (\d\d)\.(\d\d)\.\s*-\s*(\d\d)\.(\d\d)\. MIJEŠANI KOMUNALNI OTPAD "
                  r"ODVOZI SE SVAKI (\w+) I (\w+)", note)
    if not m or m.group(6) not in DAYS or m.group(7) not in DAYS:
        problems.append(f"{where}: napomena nije razumljiva: {note!r}")
        return None
    place = naslov(m.group(1).replace("VIKEND NASELJA", "VIKEND NASELJE"))
    start, end = date(year, int(m.group(3)), int(m.group(2))), date(year, int(m.group(5)), int(m.group(4)))
    days = {DAYS[m.group(6)], DAYS[m.group(7)]}
    return place, start, end, days, " i ".join(podaci.DAYS[d] for d in sorted(days))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    links = list(dict.fromkeys(re.findall(rf'href="([^"]*KALENDAR_([A-Z_]+)_{year}\.pdf)"', html)))
    found = {FILES[name]: url for url, name in links if name in FILES}
    missing = [j for j in FILES.values() if j not in found]
    if missing:
        sys.exit(f"Na {PAGE} nema kalendara za {year} za: {', '.join(missing)}. Ništa nije upisano.")
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))
    problems, zones, notes_extra = [], {}, []
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}

    def add(zone, rows):
        key = str(len(zones) + 1)
        prev = old.get(key, {})
        zone["raw"] = {**(prev.get("raw", {}) if prev.get("podrucje") == zone["podrucje"] else {}),
                       str(year): podaci.month_lines(rows)}
        zones[key] = zone
        cnt = Counter(c for _, codes, _ in rows for c in codes)
        print(f"Zona {key}: {zone['podrucje'][:90]}: M {cnt['M']}, PK {cnt['K']}, "
              f"pomaknuto {sum(1 for *_, mv in rows if mv)}, mjesta {len(zone['ulice'])}")
        return key

    with tempfile.TemporaryDirectory() as tmp:
        for jls, url in found.items():
            pdf = Path(tmp) / url.rsplit("/", 1)[1]
            fetch(url, pdf)
            with pdfplumber.open(pdf) as doc:
                pages = [read_page(p, year, problems) for p in doc.pages]
            titles = [p["title"] for p in pages if p]
            print(f"{jls}: {len(titles)} zona iz {url.rsplit('/', 1)[1]}: {', '.join(titles)}")
            # numbered zones missing from a series ("DUGA RESA - 1")
            series = {}
            for t in titles:
                m = re.fullmatch(r"(.+?)\s*-?\s*(\d+)(?:-\d+)?", t)
                if m:
                    series.setdefault(m.group(1).strip(" -"), set()).add(int(m.group(2)))
            for name, nums in series.items():
                gaps = sorted(set(range(1, max(nums) + 1)) - nums)
                if gaps:
                    print(f"   UPOZORENJE {jls}: nema stranice za {', '.join(f'{name} - {g}' for g in gaps)}")
                    notes_extra.append(f"{jls}: u kalendaru nema rasporeda za "
                                       f"{', '.join(f'{name} - {g}' for g in gaps)}.")
            for info in filter(None, pages):
                title = info["title"]
                plist, note = area_parts(info["text"])
                ulice = split_places(plist)
                if not ulice:
                    problems.append(f"{title}: nema popisa ulica/naselja ({info['text'][:60]!r})")
                m_dates = [d for d in info["rows"]["M"] if d]
                pk = info["rows"]["PK"]
                pairs = [v for v in pk if isinstance(v, tuple)]
                if pairs and len(pairs) != len(pk):
                    problems.append(f"{title}: papir i plastika miješano s jednim i dva datuma")
                m_rows, odd_m = moved_flags(m_dates, hol, title, "M")
                variants = [("", [d for d in pk if d])] if not pairs else [
                    ("prvi datum", [a for a, _ in pairs if a]), ("drugi datum", [b for _, b in pairs if b])]
                label = zone_label(title)
                osor = osor_rule(note, year, problems, title) if "U PERIODU OD" in note else None
                for variant, pk_dates in variants:
                    pk_rows, odd_p = moved_flags(pk_dates, hol, title, f"PK {variant}".strip())
                    rows = {d: ["M", mv] for d, mv in m_rows}
                    for d, mv in pk_rows:
                        r = rows.setdefault(d, ["", False])
                        r[0] += "PK"
                        r[1] = r[1] or mv
                    for code, ds in (("M", m_dates), ("PK", pk_dates)):
                        dup = sorted(d for d, n in Counter(ds).items() if n > 1)
                        if dup:
                            problems.append(f"{title} {code} {variant}: dvaput {dup}")
                    per_m = Counter(d.month for d in m_dates)
                    if not 24 <= len(m_dates) <= 53 or any(not 1 <= per_m[k] <= 5 for k in range(1, 13)):
                        problems.append(f"{title}: miješani {len(m_dates)}x, po mjesecima "
                                        f"{dict(sorted(per_m.items()))}")
                    if not 11 <= len(pk_dates) <= 14 or max(Counter(d.month for d in pk_dates).values()) > 2:
                        problems.append(f"{title} {variant}: papir i plastika {len(pk_dates)}x")
                    if len(odd_m) + len(odd_p) > 2:
                        problems.append(f"{title}: previše datuma izvan uobičajenog dana bez blagdana")
                    zulice = [u for u in ulice if not (osor and u == osor[0])]
                    head = label + (f" (papir i plastika: {variant})" if variant else "")
                    short = ", ".join(zulice[:3]) + (" …" if len(zulice) > 3 else "")
                    zone = {"jls": jls, "podrucje": f"{head} – {short}", "opis": info["text"], "ulice": zulice}
                    znotes = []
                    if variant:
                        znotes.append(f"Za papir i plastiku ova zona prikazuje {variant} iz kalendara. Napomena "
                                      f"u kalendaru: „{note.strip(' .:')}“.")
                    elif note and not osor:
                        znotes.append(f"Napomena u kalendaru: „{note.strip(' .:')}“.")
                    if osor:
                        znotes.append(f"{osor[0]}: od {osor[1]:%d.%m.} do {osor[2]:%d.%m.} miješani otpad svaki "
                                      f"{osor[4]} (zasebna zona).")
                    if znotes:
                        zone["napomena"] = " ".join(znotes)
                    base_rows = [(d, c, mv) for d, (c, mv) in rows.items()]
                    add(zone, base_rows)
                    if osor:
                        place, start, end, days, names = osor
                        extra = [start + timedelta(days=i) for i in range((end - start).days + 1)]
                        extra = pravila.primijeni_blagdane([d for d in extra if d.weekday() in days], "sljedeci")
                        orows = {d: [c, mv] for d, c, mv in base_rows}
                        for d, mv in extra:
                            r = orows.setdefault(d, ["", False])
                            if "M" not in r[0]:
                                r[0] = "M" + r[0]
                            r[1] = r[1] or mv
                        ozone = {"jls": jls, "podrucje": f"{label} – {place} (ljetni raspored)",
                                 "opis": info["text"], "ulice": [place],
                                 "napomena": f"Od {start:%d.%m.} do {end:%d.%m.} miješani otpad svaki {names} "
                                             f"(napomena u kalendaru zone {title}), inače kao ta zona."}
                        print(f"   PRAVILO {title}: {place} – {start:%d.%m.}–{end:%d.%m.} svaki {names}")
                        add(ozone, [(d, c, mv) for d, (c, mv) in orows.items()])
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "napomene": NAPOMENE + notes_extra, "zone": zones})
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
