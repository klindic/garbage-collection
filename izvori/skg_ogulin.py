"""Ogulin: Stambeno komunalno gospodarstvo d.o.o. Ogulin (skg-ogulin.hr), Grad Ogulin.

    python3 -m izvori.skg_ogulin [--year 2026]

The year post "Raspored odvoza komunalnog otpada za YYYY. godinu" (WordPress REST) links the annexes:
Prilog 1 gives the weekly weekday of mixed waste per street in town (a rule: every Monday ... Friday),
Prilog 3 "grad" and "ruralni dio" give explicit monthly dates of plastic (yellow bin) and paper (blue
bin) per local committee. Those tables (Excel printed to PDF) have a date row above a type row in every
cell and area names that wrap over several lines, so they are read with pdfplumber word positions: area
bands are cut at the horizontal rules that cross the name column. The rural settlements get mixed waste
every second Friday; their dates come from the "Raspored sakupljanja MKO YYYY" spreadsheet (found in the
WordPress media library, published in June with dates from July on).
The town's streets are listed per weekday and the recyclables per local committee, with no list of which
street belongs to which committee, so the town has one zone per mixed-waste weekday and one zone per
recyclables area; the rural settlements, which both lists name the same way, are joined into one zone.
No holiday rule is published: weekly dates are computed and kept as they are (napomena says so); the
only off-day date in the rural spreadsheet (after Christmas) is marked as moved.
"""
import argparse
import html
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import openpyxl
import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "skg-ogulin"
SITE = "https://skg-ogulin.hr"
POSTS = SITE + "/wp-json/wp/v2/posts?search=raspored&per_page=50&_fields=id,date,link,title,content"
MEDIA = SITE + "/wp-json/wp/v2/media?search={q}&per_page=50&_fields=id,date,source_url"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ",
          "KOLOVOZ", "RUJAN", "LISTOPAD", "STUDENI", "PROSINAC"]
WEEKDAYS = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "ČETVRTAK": 3, "PETAK": 4}
TYPES = {"plastika": "P", "papir": "K"}
DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(20\d\d)\.?")
PROVIDER = {
    "davatelj": "Stambeno komunalno gospodarstvo d.o.o. Ogulin",
    "web": SITE,
    "zupanija": "Karlovačka",
    "jls": ["Ogulin"],
    "nazivi": {"P": "Plastična i metalna ambalaža (žuta kanta)", "K": "Papir i karton (plava kanta)"},
}
NAPOMENE = [
    "Miješani komunalni otpad u gradu odvozi se jednom tjedno prema danu u tjednu za ulicu (Prilog 1), "
    "a papir i plastika jednom mjesečno po mjesnim odborima (Prilog 3). Popis ulica po mjesnim odborima nije "
    "objavljen, pa su to odvojene zone: zona ulice (miješani otpad) i zona mjesnog odbora (papir i plastika).",
    "Raspored ne navodi pomake zbog blagdana: tjedni datumi miješanog otpada izračunati su prema danu u tjednu "
    "i prikazani bez pomaka. Za odvoz na blagdan provjerite kod davatelja (047 522 215, kućni broj 3).",
    "Spremnike za papir i plastiku iznijeti na javnu površinu na dan odvoza najkasnije do 7:00 sati.",
    "Reciklažno dvorište: Dražice 33, Ogulin (pon, uto, čet, pet 8-15, sri 11-18, sub 8-13). "
    "Mobilno reciklažno dvorište prema Prilogu 4.",
    "Glomazni otpad: na zahtjev kod davatelja usluge.",
]


def get_json(url):
    return json.loads(fetch(url))


def year_post(year):
    """(post link, {annex key: pdf url}) of the 'Raspored odvoza ... za YYYY. godinu' post."""
    for post in get_json(POSTS):
        title = html.unescape(post["title"]["rendered"])
        links = [html.unescape(u) for u in re.findall(r'href="([^"]+\.pdf)"', post["content"]["rendered"])]
        if f"{year}. godinu" not in title or not any("Prilog-1" in u for u in links):
            continue
        found = {}
        for u in links:
            name = u.rsplit("/", 1)[1]
            if name.startswith("Prilog-1"):
                found["mko"] = u
            elif name.startswith("Prilog-3") and "ruraln" in name:
                found["rec_ruralno"] = u
            elif name.startswith("Prilog-3"):
                found["rec_grad"] = u
        return post["link"], found
    return None, {}


def rural_sheet(year):
    """Newest 'Raspored-sakupljanja-MKO-<year>-*.xlsx' in the media library, or None."""
    items = get_json(MEDIA.format(q=f"Raspored sakupljanja MKO {year}"))
    urls = sorted((m["date"], m["source_url"]) for m in items
                  if m["source_url"].endswith(".xlsx") and f"MKO-{year}" in m["source_url"])
    return urls[-1][1] if urls else None


def weekday_lists(pdf):
    """Prilog 1: {weekday 0-4: [streets]} and [[settlements]] of the 'every second Friday' groups."""
    text = "\n".join(p.extract_text() or "" for p in pdfplumber.open(pdf).pages)
    parts = re.split(r"^(PONEDJELJAK|UTORAK|SRIJEDA|ČETVRTAK|PETAK|ODVOZ SVAKI DRUGI PETAK):", text, flags=re.M)
    days, rural = {}, []
    for head, body in zip(parts[1::2], parts[2::2]):
        if head in WEEKDAYS:
            days[WEEKDAYS[head]] = split_list(body)
        else:
            rural = [split_list(g) for g in re.split(r"^\s*-\s*", body, flags=re.M) if g.strip()]
    return days, rural


def split_list(body):
    out = []
    for s in (" ".join(x.split()).strip(" .") for x in " ".join(body.split()).split(",")):
        m = re.fullmatch(r"([A-ZČĆŽŠĐ]\w+) i ([A-ZČĆŽŠĐ]\w+) ([A-ZČĆŽŠĐ]\w+)", s)  # "Gornje i Donje Zagorje"
        out += [f"{m.group(1)} {m.group(3)}", f"{m.group(2)} {m.group(3)}"] if m else [s] if s else []
    return out


def area_name(lines):
    """Wrapped name lines -> one name: a line ending in ',' or with an open '(' continues, others are list items."""
    out = ""
    for line in lines:
        if not out:
            out = line
        elif out.endswith(",") or out.count("(") > out.count(")"):
            out += " " + line
        else:
            out += ", " + line
    return out


def settlements(name):
    """'Desmerice, Zagorje, Ribarići, Šegani i Stabarnica' -> names for search; '(bez ...)' is dropped."""
    out = []
    for part in split_list(re.sub(r"\([^)]*\)", "", name)):
        out += [s.strip() for s in re.split(r" - | i ", part) if s.strip()]
    return out


def read_recyclables(pdf, year):
    """Prilog 3 table -> [(area name, [(date, code)])], problems.

    Area bands are cut at horizontal rules that cross the name column; each date word takes the type word
    ('plastika'/'papir') printed just below it in the same column, and its month from the column header."""
    page = pdfplumber.open(pdf).pages[0]
    words = page.extract_words()
    problems = []
    heads = {w["text"]: (w["x0"] + w["x1"]) / 2 for w in words if w["text"] in MONTHS}
    if len(heads) != 12:
        return [], [f"{pdf.name}: zaglavlja mjeseci: {sorted(heads)}"]
    head_bottom = max(w["bottom"] for w in words if w["text"] in MONTHS)
    first_col = min(heads.values()) - 30
    name_x = first_col / 2
    rules = sorted(r["top"] for r in page.rects if r["height"] < 2 and r["x0"] < name_x < r["x1"])
    bounds = []
    for y in rules:
        if y > head_bottom and (not bounds or y - bounds[-1] > 3):
            bounds.append(y)
    end = min((w["top"] for w in words if w["text"] == "NAPOMENA:"), default=page.height)
    areas = []
    for top, bottom in zip(bounds, bounds[1:]):
        if top >= end:
            break
        band = [w for w in words if top < (w["top"] + w["bottom"]) / 2 < bottom]
        lines = {}
        for w in sorted((w for w in band if w["x1"] < first_col), key=lambda w: (round(w["top"]), w["x0"])):
            lines.setdefault(round(w["top"]), []).append(w["text"])
        name = area_name([" ".join(v) for _, v in sorted(lines.items())])
        rows = []
        for w in band:
            m = DATE.fullmatch(w["text"])
            if not m:
                if w["x0"] > first_col and w["text"] not in TYPES and w["text"] != "/":
                    problems.append(f"{name}: nepoznata riječ {w['text']!r}")
                continue
            day, month, y = map(int, m.groups())
            under = [t for t in band if t["text"] in TYPES and abs(t["x0"] - w["x0"]) < 6 and 0 < t["top"] - w["top"] < 20]
            if len(under) != 1:
                problems.append(f"{name} {w['text']}: vrsta otpada nije jednoznačna")
                continue
            head = min(heads, key=lambda h: abs(heads[h] - (w["x0"] + w["x1"]) / 2))
            if y != year or MONTHS.index(head) + 1 != month:
                problems.append(f"{name} {w['text']}: stoji u stupcu {head} {year}")
                continue
            rows.append((date(y, month, day), TYPES[under[0]["text"]]))
        if not name or not rows:
            problems.append(f"pojas {top:.0f}-{bottom:.0f}: naziv {name!r}, {len(rows)} datuma")
            continue
        areas.append((name, rows))
    return areas, problems


def read_rural_sheet(xlsx, year):
    """'Raspored sakupljanja MKO' spreadsheet -> [([settlements], [dates])], problems."""
    ws = openpyxl.load_workbook(xlsx, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    problems, out = [], []
    head = next((i for i, r in enumerate(rows) if r and "SIJEČANJ" in [str(c).strip() for c in r if c]), None)
    if head is None:
        return [], ["MKO tablica: nema zaglavlja mjeseci"]
    cols = {j: MONTHS.index(str(c).strip()) + 1 for j, c in enumerate(rows[head]) if c and str(c).strip() in MONTHS}
    for r in rows[head + 1:]:
        if not r or not r[0]:
            continue
        names = [" ".join(s.split()) for s in str(r[0]).splitlines() if s.strip()]
        dates = []
        for j, month in cols.items():
            for m in DATE.finditer(str(r[j] or "")):
                d, mo, y = map(int, m.groups())
                if y != year or mo != month:
                    problems.append(f"MKO {names[0]}: {m.group(0)} u stupcu {MONTHS[month - 1]}")
                    continue
                dates.append(date(y, mo, d))
        out.append((names, sorted(dates)))
    return out, problems


def check_recyclables(name, rows, problems):
    hol = set(pravila.blagdani(rows[0][0].year))
    n = Counter((d.month, c) for d, c in rows)
    for (month, code), k in n.items():
        if k > 1:
            problems.append(f"{name}: {k}x {code} u mjesecu {month}")
    for d, c in rows:
        if d.weekday() > 4:
            problems.append(f"{name}: {d:%d.%m.} ({podaci.DAYS[d.weekday()]}) nije radni dan")
        elif d in hol:
            print(f"   UPOZORENJE {name}: {c} {d:%d.%m.} je blagdan")


def biweekly(dates, problems, name):
    """Every second Friday; a date off the Friday right after a holiday is kept as moved."""
    hol = pravila.blagdani(dates[0].year)
    out = []
    for i, d in enumerate(dates):
        moved = d.weekday() != 4
        if moved and not any(0 < (d - h).days <= 7 for h in hol):
            problems.append(f"{name}: {d:%d.%m.} nije petak, a nema blagdana prije")
        if i and not moved and dates[i - 1].weekday() == 4 and (d - dates[i - 1]).days != 14:
            problems.append(f"{name}: {dates[i - 1]:%d.%m.} -> {d:%d.%m.} nije razmak od dva tjedna")
        out.append((d, "M", moved))
    return out


def merge(*parts):
    out = {}
    for rows in parts:
        for d, codes, moved in rows:
            c, m = out.get(d, ("", False))
            out[d] = (c + codes, m or moved)
    return sorted((d, c, m) for d, (c, m) in out.items())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    link, pdfs = year_post(year)
    if not link or set(pdfs) != {"mko", "rec_grad", "rec_ruralno"}:
        sys.exit(f"Objava s rasporedom za {year}. nije pronađena ili nema sve priloge ({sorted(pdfs)}). Ništa nije upisano.")
    sheet = rural_sheet(year)
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for key, url in list(pdfs.items()) + ([("mko_ruralno", sheet)] if sheet else []):
            files[key] = Path(tmp) / url.rsplit("/", 1)[1]
            fetch(url, files[key])
            if key != "mko_ruralno" and not files[key].read_bytes().startswith(b"%PDF"):
                sys.exit(f"{url}: nije PDF. Ništa nije upisano.")
        days, rural_groups = weekday_lists(files["mko"])
        town, p1 = read_recyclables(files["rec_grad"], year)
        rural, p2 = read_recyclables(files["rec_ruralno"], year)
        sheet_rows, p3 = read_rural_sheet(files["mko_ruralno"], year) if sheet else ([], [])
    problems += p1 + p2 + p3
    if sorted(days) != [0, 1, 2, 3, 4] or any(len(v) < 5 for v in days.values()):
        problems.append(f"Prilog 1: dani {sorted(days)} s {[len(v) for v in days.values()]} ulica")
    if not sheet:
        print(f"UPOZORENJE: tablica 'Raspored sakupljanja MKO {year}' za ruralna naselja nije pronađena")

    zones = []
    # town: one zone per weekday of mixed waste
    for wd, streets in sorted(days.items()):
        rows = [(d, "M", False) for d in pravila.tjedno(year, list(pravila.DANI)[wd])]
        zones.append({"jls": "Ogulin", "podrucje": f"{podaci.DAYS[wd].capitalize()} – miješani otpad: "
                      + ", ".join(streets[:4]) + ", …", "ulice": streets,
                      "napomena": "Miješani komunalni otpad svaki tjedan. Papir i plastika: zona mjesnog odbora "
                                  "(\"Papir i plastika – …\").", "rows": rows})
    # rural settlements: recyclables area + mixed waste spreadsheet + Prilog 1 group, matched by names
    for name, rows in rural:
        check_recyclables(name, rows, problems)
        names = settlements(name)
        key = {n.lower() for n in names}
        mixed = [dates for sn, dates in sheet_rows if {s.lower() for s in sn} == key]
        group = next((g for g in rural_groups if len(key & {s.lower() for s in sum((settlements(x) for x in g), [])}) >= 2), [])
        extra = [s for g in group for s in settlements(g) if s.lower() not in key]
        mrows = biweekly(mixed[0], problems, name) if mixed and mixed[0] else []
        if not mrows:
            print(f"UPOZORENJE {name}: nema datuma miješanog otpada")
        first = min((d for d, *_ in mrows), default=None)
        note = ["Miješani komunalni otpad svaki drugi petak"
                + (f"; objavljeni su datumi od {first:%d.%m.%Y.}, raniji nisu objavljeni." if first else
                   "; datumi nisu objavljeni.")]
        if extra:
            note.append(f"{', '.join(extra)}: u Prilogu 1 u istoj skupini za miješani otpad; tablice papira, plastike "
                        f"i miješanog otpada od srpnja navode samo {', '.join(names)}.")
        note.append("U siječnju se reciklabilni otpad skupljao u dosad preuzetim vrećama.")
        zones.append({"jls": "Ogulin", "podrucje": f"{name} (ruralno područje)", "ulice": names + extra,
                      "napomena": " ".join(note), "rows": merge(mrows, [(d, c, False) for d, c in rows]),
                      "window": first})
    for names, dates in sheet_rows:
        if not any({s.lower() for s in names} == {n.lower() for n in settlements(a)} for a, _ in rural):
            problems.append(f"MKO tablica: skupina {names} nema svoje područje u Prilogu 3 (ruralni dio)")
    # town recyclables: one zone per local committee
    for name, rows in town:
        check_recyclables(name, rows, problems)
        zones.append({"jls": "Ogulin", "podrucje": f"Papir i plastika – {name}", "ulice": settlements(name),
                      "opis": f"Mjesni odbor: {name}",
                      "napomena": "Samo papir i plastika (mjesni odbor). Miješani otpad: zona ulice prema danu u "
                                  "tjednu. U siječnju se reciklabilni otpad skupljao u dosad preuzetim vrećama.",
                      "rows": [(d, c, False) for d, c in rows]})

    for i, z in enumerate(zones, 1):
        n = Counter((d.month, c) for d, codes, _ in z["rows"] for c in codes)
        for (month, code), k in n.items():
            if code == "M" and not (4 <= k <= 5 if "ruralno" not in z["podrucje"] else 1 <= k <= 3):
                problems.append(f"zona {i}: {k}x M u mjesecu {month}")
        if len({d for d, *_ in z["rows"]}) != len(z["rows"]):
            problems.append(f"zona {i}: datum dvaput")
        if not z["ulice"]:
            problems.append(f"zona {i}: nema ulica ni naselja")
    if len(town) < 8 or len(rural) < 2:
        problems.append(f"područja papira i plastike: grad {len(town)}, ruralno {len(rural)} (očekivano 10 i 2)")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": link, "napomene": NAPOMENE, "zone": {}}
    for i, z in enumerate(zones, 1):
        rows, window = z.pop("rows"), z.pop("window", None)
        prev = old.get(str(i), {})
        raw = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("podrucje") == z["podrucje"] else {}
        if window and prev.get("podrucje") == z["podrucje"]:
            # the rural mixed-waste table shows a window: keep earlier mixed-waste dates already in the file
            rows = merge(rows, [(d, "M", m) for d, c, m in podaci.iter_dates(prev, year) if "M" in c and d < window])
        z["raw"] = {**raw, str(year): podaci.month_lines(rows)}
        data["zone"][str(i)] = z
        cnt = Counter(c for _, codes, _ in rows for c in codes)
        print(f"Zona {i}: {z['podrucje'][:70]}: " + ", ".join(f"{c} {cnt[c]}" for c in "MPK" if cnt[c])
              + f", pomaknuto {sum(1 for *_, m in rows if m)}, naselja/ulica {len(z['ulice'])}")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
