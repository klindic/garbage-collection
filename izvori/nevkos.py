"""Vinkovci and surroundings: Nevkoš d.o.o., Vinkovci (street lists per weekday, paper and plastic by blocks) and
the municipalities Ivankovo, Markušica, Nuštar, Tordinci, Privlaka, Šodolovci and Andrijaševci (year calendars).

    python3 -m izvori.nevkos [--year 2026]

Everything comes from nevkos.hr/raspored/. Municipalities: the page links one PDF per municipality or part of
one ("Godišnji raspored odvoza za općinu <JLS> (<settlements>)"), an Excel year calendar with a 6x7 grid per
month (days of the neighbouring months in light blue are skipped). The legend boxes at the top give the cell
colours (green mixed waste, brown biowaste, yellow or orange paper + plastic and metal + glass, grey public
holidays); a cell split diagonally holds two collections. When a legend has one recyclables box per group of
settlements (Nuštar), the municipality is split into those groups. Holiday shifts are already in the
calendars (a collection off its usual weekday next to a grey day is marked as moved). Cells in a colour that is
not in the legend are left out and listed in the zone's note.

Vinkovci: the page lists the streets for each weekday of the mixed-waste round (with sub-lists for the
settlement Mirkovci) and, separately, the paper and plastic round by blocks of the town ("prva srijeda u
mjesecu: Kanovci" ...) without saying which streets form a block. So the town gets one zone per weekday of the
mixed-waste round (weekly, the provider publishes no holiday shifts for it) and one zone per paper/plastic
block (n-th weekday of the month; a holiday moves it to the next working day or Saturday, as the page says).
Jarmina has only a price list on the site, no schedule, and is left out.
"""
import argparse
import calendar
import html as htmllib
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import MONTHS, colour

SLUG = "nevkos"
SITE = "https://nevkos.hr"
PAGE = SITE + "/raspored/"
WEEK = ["po", "ut", "sr", "če", "pe", "su", "ne"]
HOLIDAY = "X"
# legend label start -> code; recyclables: paper, plastic and metal, glass (glass is missing in Privlaka's label)
LABELS = [(r"^Miješani komunalni otpad", "M"), (r"^Biootpad", "B"), (r"^Papir, plastika i metal, staklo", "PKS"),
          (r"^Papir, plastika i metal", "PK"), (r"^Državni praznici", HOLIDAY)]
GREY = (0.851, 0.851, 0.851)  # holiday cells, also in calendars without a legend box for them
UNLISTED = {(0.753, 0.0, 0.0): "tamnocrvenom"}  # colours seen in cells but not in any legend
DAYS = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "ČETVRTAK": 3, "PETAK": 4}
DAY_KEYS = ["pon", "uto", "sri", "čet", "pet"]
TYPOS = {"9. Svibjna": "9. svibnja"}  # obvious misspellings in the street lists
ORDINAL = {"PRV": 1, "DRUG": 2, "TREĆ": 3, "ČETVRT": 4, "PET": 5}
PROVIDER = {
    "davatelj": "Nevkoš d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Vukovarsko-srijemska",
    "jls": ["Vinkovci", "Ivankovo", "Markušica", "Nuštar", "Tordinci", "Privlaka", "Šodolovci", "Andrijaševci"],
    "napomene": [
        "Vinkovci: miješani komunalni otpad odvozi se jednom tjedno na dan naveden za ulicu (zone po danima); "
        "zgrade i dijelovi ulica s više odvoza tjedno navedeni su u više zona. Nevkoš ne objavljuje pomake tog "
        "odvoza zbog blagdana, pa su datumi izračunati bez pomaka.",
        "Vinkovci: papir i plastika odvoze se jednom mjesečno po blokovima grada (zasebne zone); Nevkoš ne "
        "objavljuje koje ulice pripadaju kojem bloku. Ako odvoz padne na blagdan, obavlja se sljedeći radni dan "
        "ili u subotu.",
        "Općine: datumi su iz godišnjih kalendara na nevkos.hr; pomaci zbog blagdana već su upisani.",
        "Posudu (s vrećicom) treba iznijeti na dan odvoza najkasnije do 7:00, uz prometnicu.",
        "Općina Jarmina (također Nevkoš) nije uključena: na nevkos.hr za nju je objavljen samo cjenik, bez rasporeda.",
        "Kontakt: 032/306-130, info@nevkos.hr (pon-pet 7-15 h).",
    ],
}


def plain(fragment):
    text = re.sub(r"<br\s*/?>|</(?:p|h\d|div|li)>", "\n", fragment, flags=re.I)
    return [re.sub(r"\s+", " ", htmllib.unescape(l)).strip() for l in re.sub(r"<[^>]+>", "", text).split("\n")]


def title_case(s):
    small = {"I", "STRANA", "DESNA", "LIJEVA", "BLOK", "NASELJE", "SELO"}
    words = s.split()
    out = [w.lower() if i and w in small and not (w == "SELO" and words[i - 1] == "NOVO") else w[:1] + w[1:].lower()
           for i, w in enumerate(words)]
    return " ".join(out).replace("Novo selo", "Novo Selo")


# ---------------------------------------------------------------- Vinkovci (HTML)

def vinkovci(html, year, problems):
    """[(zone, rows)] for the town."""
    start = html.find("Raspored odvoza komunalnog otpada po danima")
    end = html.find("Radno vrijeme", start)
    m = re.search(r">\s*Plastika i papir\s*<", html)
    pk_start = m.end() if m else -1
    if start < 0 or end < 0 or pk_start < 0:
        problems.append("Vinkovci: na stranici nema popisa ulica po danima ili rasporeda papira i plastike")
        return []
    lists, day, place = {}, None, "Vinkovci"
    for line in plain(html[start:end])[1:]:
        if not line:
            continue
        if line.upper() in DAYS:
            day, place = DAYS[line.upper()], "Vinkovci"
        elif line.upper() == "MIRKOVCI":
            place = "Mirkovci"
        elif day is None:
            problems.append(f"Vinkovci: redak '{line}' prije prvog dana")
        else:
            lists.setdefault((day, place), []).append(line)
    if sorted({d for d, _ in lists}) != list(range(5)):
        problems.append(f"Vinkovci: dani u popisu ulica {sorted({d for d, _ in lists})}")
    out = []
    for (day, place), streets in sorted(lists.items(), key=lambda kv: (kv[0][0], kv[0][1] != "Vinkovci")):
        if len(set(streets)) < 5:
            problems.append(f"Vinkovci {DAY_KEYS[day]} {place}: samo {len(streets)} ulica")
        name = [k for k, v in DAYS.items() if v == day][0].lower()
        streets = [TYPOS.get(s, s) for s in streets]
        ulice = list(dict.fromkeys(f"{s} (Mirkovci)" if place == "Mirkovci" else s for s in streets))
        zone = {"jls": "Vinkovci", "podrucje": f"Miješani otpad {name}" + (" – Mirkovci" if place == "Mirkovci" else "")
                + ": " + ", ".join(streets[:4]) + ", …", "ulice": ulice,
                "napomena": "Papir i plastiku odvozi Nevkoš po blokovima grada, vidi zone 'Papir i plastika'; "
                            "Nevkoš ne objavljuje koje ulice pripadaju kojem bloku."}
        out.append((zone, [(d, "M", False) for d in pravila.tjedno(year, DAY_KEYS[day])]))
    pk_end = html.find("Napomena", pk_start)
    rules = [l for l in plain(html[pk_start:pk_end]) if "U MJESECU" in l.upper()]
    if not 8 <= len(rules) <= 16:
        problems.append(f"Vinkovci: {len(rules)} pravila za papir i plastiku")
    if "slijedeći radni dan ili subotu" not in html and "sljedeći radni dan ili subotu" not in html:
        problems.append("Vinkovci: pravilo za blagdane (sljedeći radni dan ili subota) više nije na stranici")
    for rule in rules:
        m = re.match(r"(PRV|DRUG|TREĆ|ČETVRT|PET)\w*\s+(PONEDJELJAK|UTORAK|SRIJED[AU]|ČETVRTAK|PETAK)\s+U MJESECU\s*:\s*(.+)",
                     rule.upper())
        if not m:
            problems.append(f"Vinkovci: pravilo nije razumljivo: {rule!r}")
            continue
        n, day = ORDINAL[m.group(1)], DAYS[m.group(2).replace("SRIJEDU", "SRIJEDA")]
        areas = [title_case(a.strip()) for a in re.split(r"\s*,\s*", m.group(3).replace("–", " – ")) if a.strip()]
        areas = [re.sub(r"\s+", " ", a) for a in areas]
        dates = pravila.mjesecno(year, DAY_KEYS[day], n)
        if len(dates) != 12:
            problems.append(f"Vinkovci: {rule!r} nema datum u svakom mjesecu")
        rows = [(d, "PK", moved) for d, moved in pravila.primijeni_blagdane(dates, "sljedeci", year)]
        rule_text = rule.split(":")[0].strip().capitalize()
        zone = {"jls": "Vinkovci", "podrucje": f"Papir i plastika ({rule_text.lower()}): {', '.join(areas)}",
                "ulice": areas,
                "napomena": "Samo papir i plastika; miješani otpad odvozi se prema popisu ulica po danima "
                            "(zone 'Miješani otpad')."}
        out.append((zone, rows))
    return out


# ---------------------------------------------------------------- municipalities (PDF calendars)

def near(col, rgb, tol=0.02):
    return col is not None and all(abs(a - b) <= tol for a, b in zip(col, rgb))


def legend(page, words, problems, name):
    """[(rgb, code, label)] from the boxes at the top, and the bottom of the legend area."""
    out, bottom, x0 = [], 0, page.width
    for r in page.rects:
        col = colour(r)
        if not r.get("fill") or col is None or r["top"] > 120 or r["x1"] - r["x0"] < 100:
            continue
        label = " ".join(w["text"] for w in words if r["x0"] <= (w["x0"] + w["x1"]) / 2 <= r["x1"]
                         and r["top"] <= (w["top"] + w["bottom"]) / 2 <= r["bottom"])
        if not label:
            continue
        code = next((c for pat, c in LABELS if re.search(pat, label)), None)
        if code is None:
            problems.append(f"{name}: legend label {label!r} not understood")
            continue
        out.append((col, code, label))
        bottom, x0 = max(bottom, r["bottom"]), min(x0, r["x0"])
    return out, bottom, x0


def read_calendar(path, year, problems, name):
    """(title words, legend, {date: [rgb, ...]}) of one municipality PDF."""
    page = pdfplumber.open(path).pages[0]
    words = page.extract_words(extra_attrs=["non_stroking_color"])
    leg, leg_bottom, leg_x0 = legend(page, words, problems, name)
    if not any(c == "M" for _, c, _ in leg):
        problems.append(f"{name}: no legend box for mixed waste")
    year_word = next((w for w in words if w["text"] == str(year) and w["top"] < 150), None)
    if not year_word:
        problems.append(f"{name}: no {year} above the calendar")
        return "", leg, {}
    title = " ".join(w["text"] for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
                     if w["x1"] < leg_x0 and w["bottom"] < year_word["top"])
    heads = [w for w in words if w["text"].lower() in WEEK]
    rows = []
    for top in sorted({round(w["top"]) for w in heads}):
        line = sorted((w for w in heads if abs(w["top"] - top) < 2), key=lambda w: w["x0"])
        for i in range(len(line) - 6):
            if [w["text"].lower() for w in line[i:i + 7]] == WEEK:
                rows.append(line[i:i + 7])
    rows = list({(round(g[0]["top"]), round(g[0]["x0"])): g for g in rows}.values())
    fills = [s for s in page.rects if s.get("fill") and colour(s) and s["top"] > leg_bottom]
    tris = [s for s in page.curves if s.get("fill") and colour(s) and s["top"] > leg_bottom]
    found, seen = {}, Counter()
    month_words = [w for w in words if w["text"] in MONTHS]
    if len(month_words) != 12:
        problems.append(f"{name}: month headings {[w['text'] for w in month_words]}")
    for h in month_words:
        mo = MONTHS.index(h["text"]) + 1
        hx = (h["x0"] + h["x1"]) / 2
        below = [g for g in rows if 0 < g[0]["top"] - h["bottom"] < 20 and g[0]["x0"] - 10 <= hx <= g[-1]["x1"] + 10]
        if len(below) != 1:
            problems.append(f"{name} {h['text']}: {len(below)} weekday rows")
            continue
        g = below[0]
        cols = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (cols[-1] - cols[0]) / 6
        nums = [w for w in words if w["text"].isdigit() and 0 < w["top"] - g[0]["bottom"] < 6 * 14
                and cols[0] - pitch / 2 < (w["x0"] + w["x1"]) / 2 < cols[-1] + pitch / 2]
        lines = []
        for w in sorted(nums, key=lambda w: w["top"]):
            if not lines or w["top"] - lines[-1] > 4:
                lines.append(w["top"])
        if len(lines) != 6:
            problems.append(f"{name} {h['text']}: {len(lines)} week rows")
            continue
        first, ndays = date(year, mo, 1).weekday(), calendar.monthrange(year, mo)[1]
        prev_days = calendar.monthrange(year - (mo == 1), 12 if mo == 1 else mo - 1)[1]
        for w in nums:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            col = min(range(7), key=lambda i: abs(cols[i] - cx))
            row = min(range(6), key=lambda i: abs(lines[i] - w["top"]))
            day = row * 7 + col - first + 1
            want = day if 1 <= day <= ndays else prev_days + day if day < 1 else day - ndays
            if w["text"] != str(want):
                problems.append(f"{name} {year}-{mo:02d}: {w['text']} in week row {row + 1}, column {WEEK[col]}")
                continue
            if not 1 <= day <= ndays:
                continue
            d = date(year, mo, day)
            seen[d] += 1
            under = [s for s in fills if s["x0"] - 0.5 <= cx <= s["x1"] + 0.5 and s["top"] - 0.5 <= cy <= s["bottom"] + 0.5
                     and s["x1"] - s["x0"] < 3 * pitch]
            if not under:
                continue
            cell = min(under, key=lambda s: (s["x1"] - s["x0"]) * (s["bottom"] - s["top"]))
            cols_here = [colour(cell)]
            for t in tris:  # the upper-left triangle of a split cell (fill rects may span several days)
                if t["x0"] - 1.5 <= cx <= t["x1"] + 1.5 and t["top"] - 1.5 <= cy <= t["bottom"] + 1.5:
                    cols_here.append(colour(t))
            found[d] = cols_here
    for mo in range(1, 13):
        for day in range(1, calendar.monthrange(year, mo)[1] + 1):
            if seen[date(year, mo, day)] != 1:
                problems.append(f"{name}: {date(year, mo, day)} appears {seen[date(year, mo, day)]} times")
    return title, leg, found


def parse_title(title, problems, name):
    """(JLS, [settlements]) from 'Godišnji raspored odvoza za općinu <JLS> (<settlements>)'."""
    m = re.search(r"za općinu\s+([A-ZČĆŠŽĐ][\wčćšžđ]+)\s*(?:\(\s*([^)]*)\))?", title)
    if not m:
        problems.append(f"{name}: title {title!r} not understood")
        return None, []
    places = [p.strip() for p in re.split(r",|\s+i\s+", m.group(2) or "") if p.strip()]
    return m.group(1), places or [m.group(1)]


def municipality(path, year, problems, name):
    """[(zone, rows)] of one municipality PDF."""
    title, leg, found = read_calendar(path, year, problems, name)
    jls, places = parse_title(title, problems, name)
    if jls is None:
        return []
    if jls not in PROVIDER["jls"]:
        problems.append(f"{name}: JLS {jls!r} is not in the provider's list")
    recycl = [(rgb, code, label) for rgb, code, label in leg if code in ("PKS", "PK")]
    groups = [None]
    if len(recycl) > 1:  # one recyclables colour per group of settlements: "... - Nuštar (4. utorak u mjesecu)"
        groups = []
        for rgb, code, label in recycl:
            m = re.search(r"-\s*([^(]+?)\s*\(", label)
            groups.append((rgb, [p.strip() for p in re.split(r",|\s+i\s+", m.group(1)) if p.strip()] if m else []))
        covered = sorted(p for _, ps in groups for p in ps)
        if covered != sorted(places):
            problems.append(f"{name}: recyclables groups {covered} do not cover {places}")
    palette = [(rgb, code) for rgb, code, _ in leg] + [(GREY, HOLIDAY)]
    cells, unlisted, unknown = {}, [], []
    for d, cols in found.items():
        for col in cols:
            if near(col, (1.0, 1.0, 1.0)):
                continue
            hit = [(rgb, code) for rgb, code in palette if near(col, rgb)]
            if hit:
                cells.setdefault(d, []).append(hit[0])
            elif any(near(col, rgb) for rgb in UNLISTED):
                unlisted.append((d, UNLISTED[next(rgb for rgb in UNLISTED if near(col, rgb))]))
            else:
                unknown.append(f"{d} {col}")
    if unknown:
        problems.append(f"{name}: unknown colours {unknown[:5]}")
    holidays = {d for d, cs in cells.items() if any(c == HOLIDAY for _, c in cs)}
    hol = set(pravila.blagdani(year))
    if holidays - hol:
        print(f"   {name}: sivo označeni dani koji nisu državni blagdani: {', '.join(f'{d:%d.%m.}' for d in sorted(holidays - hol))}")
    out = []
    for group in groups:
        rows = {}
        for d, cs in cells.items():
            for rgb, code in cs:
                if code == HOLIDAY or (group and code in ("PKS", "PK") and rgb != group[0]):
                    continue
                rows[d] = rows.get(d, "") + code
        usual = Counter(d.weekday() for d, c in rows.items() if "M" in c or "B" in c)
        top = usual.most_common(1)[0][0] if usual else None
        moved, odd = set(), []
        for d, c in rows.items():
            if "M" in c or "B" in c:
                if d.weekday() != top:
                    if any(abs((d - h).days) <= 6 for h in holidays | hol):
                        moved.add(d)
                    else:
                        odd.append(f"{d:%d.%m.}")
        if odd:
            problems.append(f"{name}: mixed/bio off the usual weekday without a holiday near: {', '.join(odd)}")
        on_hol = [f"{d:%d.%m.}" for d in rows if d in holidays]
        if on_hol:
            problems.append(f"{name}: collection on a grey holiday {on_hol}")
        sub = group[1] if group else places
        whole = not group and "(" not in title  # no settlement list in the title: the whole municipality
        zone = {"jls": jls, "podrucje": "Cijela općina" if whole else ", ".join(sub),
                "opis": " | ".join([" ".join(title.split())] + [label for _, _, label in leg]), "ulice": sub}
        notes = []
        if group:
            notes.append(f"Isti odvoz miješanog i biootpada za cijelu općinu; reciklabilni otpad za {', '.join(sub)} "
                         f"prema kalendaru općine.")
        lost = sorted({d for d, _ in unlisted})
        if lost:
            notes.append("U kalendaru su " + ", ".join(f"{d:%d.%m.}" for d in lost) + f" označeni {unlisted[0][1]} "
                         "bojom koje nema u legendi; ti datumi nisu upisani (provjeriti kod Nevkoša).")
        two = [label for _, _, label in recycl if re.search(r"\(\d\.\s*\w+ i \d\.", label)]
        if two:
            days = re.search(r"[(](.*)[)]", two[0]).group(1).replace("četrvrtak", "četvrtak")
            notes.append(f"Reciklabilni otpad: {days}, oba dana označena su u "
                         "kalendaru; Nevkoš ne navodi koji dio naselja odvozi koji dan.")
        if len({c for _, c, _ in recycl}) == 1 and recycl and recycl[0][1] == "PK":
            notes.append("Reciklabilni otpad ovdje bez stakla (papir, plastika i metal).")
        if notes:
            zone["napomena"] = " ".join(notes)
        out.append((zone, [(d, c, d in moved) for d, c in rows.items()]))
    return out


def check_counts(zone, rows, year, problems):
    label = f"{zone['jls']} {zone['podrucje'][:30]}"
    weekly = zone["podrucje"].startswith("Miješani")
    for m in range(1, 13):
        cnt = Counter(c for d, cs, _ in rows if d.month == m for c in cs)
        mb = sum(1 for d, cs, _ in rows if d.month == m and set(cs) & set("MB"))
        if (weekly and not 4 <= mb <= 5) or (not weekly and "M" in "".join(c for _, c, _ in rows) and not 2 <= mb <= 5):
            problems.append(f"{label} {year}-{m:02d}: {mb} odvoza miješanog/biootpada")
        if not weekly and not 1 <= cnt["P"] <= 2:
            problems.append(f"{label} {year}-{m:02d}: {cnt['P']} odvoza reciklabilnog otpada")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    problems = []
    parts = vinkovci(html, year, problems)
    pdfs = list(dict.fromkeys(re.findall(r'href="(https://nevkos\.hr/wp-content/uploads/[^"]+\.pdf)"', html)))
    pdfs = [u for u in pdfs if "raspored" in u.lower()]
    print(f"Vinkovci: {len(parts)} zona; {len(pdfs)} PDF-ova općina na {PAGE}")
    if len(pdfs) < 8:
        problems.append(f"only {len(pdfs)} municipality PDFs on {PAGE}")
    muni = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, url in enumerate(pdfs):
            name = url.rsplit("/", 1)[1]
            dest = Path(tmp) / f"{i}.pdf"
            fetch(url, dest)
            got = municipality(dest, year, problems, name)
            for zone, rows in got:
                zone["napomena"] = (zone.get("napomena", "") + f" Izvor: {url}").strip()
            muni += got
    order = list(dict.fromkeys(z["jls"] for z, _ in muni))
    muni.sort(key=lambda zr: order.index(zr[0]["jls"]))
    zones = {}
    for n, (zone, rows) in enumerate(parts + muni, start=1):
        check_counts(zone, rows, year, problems)
        dates = [d for d, _, _ in rows]
        if len(dates) != len(set(dates)) or any(d.year != year for d in dates):
            problems.append(f"zone {n}: duplicate or foreign dates")
        cnt = Counter(c for _, cs, _ in rows for c in cs)
        mv = [f"{d:%d.%m.}" for d, _, m in sorted(rows) if m]
        print(f"Zona {n} ({zone['jls']}: {zone['podrucje'][:50]}): {len(rows)} dana {dict(sorted(cnt.items()))}, "
              f"pomaknuto {len(mv)} ({', '.join(mv)})")
        zones[str(n)] = (zone, rows)
    missing = set(PROVIDER["jls"]) - {z["jls"] for z, _ in zones.values()}
    if missing:
        problems.append(f"no zones for {sorted(missing)}")
    for p in problems[:30]:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for key, (zone, rows) in zones.items():
        prev = old.get(key, {})
        raw = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("podrucje") == zone["podrucje"] else {}
        data["zone"][key] = {**zone, "raw": {**raw, str(year): podaci.month_lines(rows)}}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
