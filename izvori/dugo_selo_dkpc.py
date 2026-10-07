"""Dugo Selo: Dugoselski komunalni i poduzetnički centar d.o.o. (dkpc.hr), city-wide calendar + street tables.

    python3 -m izvori.dugo_selo_dkpc [--year 2026]

Three official sources are combined, each read from the site on every run:
1. The leaflet "Letak_kalendar-odvoza-<year>.pdf" (linked on the home page), page 1: twelve month grids
   ("po ut sr če pe su ne") where every week row says what is collected that week: green mixed waste,
   cyan paper, yellow plastic (the upper half of the cell from Wednesday on), and orange in the lower
   half on Wednesday to Friday for biowaste. Greyed days belong to the neighbouring months and are
   skipped. A coloured Saturday next to "Umjesto dd.mm.yyyy." replaces that holiday.
2. The table "Raspored odvoza" on dkpc.hr/djelatnosti/komunalni-otpad/: the weekday (Monday to Friday,
   in two "relacije") on which each street is collected; mixed waste, paper and plastic come on that
   weekday in the weeks the calendar shows for them.
3. The leaflet page 2: family houses get biowaste on Thursday (northern part) or Friday (southern
   part), with a street list for each.

So a zone is "weekday of the street" + "biowaste day of the street". Street names are matched between
the two lists after normalising (case, diacritics, "ulica", initials); spelling variants are matched by
similarity and printed. Checks: every day number in its own row and column, every day of the year once,
each week one type on Monday to Friday and biowaste on Wednesday to Friday, Saturdays only as notes say,
no unknown colour, every household street of the table matched to one biowaste list or reported, the
counts per type plausible. Otherwise nothing is written. Apartment buildings (mixed waste as needed,
biowaste on Wednesday) and business users have no household schedule.
"""
import argparse
import difflib
import html
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.sisak_gos import UA

SLUG = "dugo-selo-dkpc"
SITE = "https://dkpc.hr"
PAGE = SITE + "/djelatnosti/komunalni-otpad/"
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
WEEK = ["po", "ut", "sr", "če", "pe", "su", "ne"]
DAYS = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak"]
KEYS = ["PON", "UTO", "SRI", "CET", "PET"]
DAT = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom"]
PALETTE = {(0.573, 0.816, 0.314): "M", (0.0, 1.0, 1.0): "K", (1.0, 1.0, 0.0): "P", (0.894, 0.424, 0.039): "B"}
WHITE = {(1.0, 1.0, 1.0)}
# The leaflet prints a wrong number in a cell (the cell's place in the grid gives the date).
MISPRINTS = {date(2026, 1, 29): "23"}
# Entries of the street table that are not households (shopping centres, schools, the town hall, ...).
NOT_HOUSEHOLDS = re.compile(r"trgovački centri|osnovna škola|srednja škola|^općina$|groblje|kontejneri|dalekovod|"
                            r"– zgrade", re.I)
PER_YEAR = {"M": (24, 28), "K": (11, 14), "P": (11, 14), "B": (50, 53)}
PROVIDER = {
    "davatelj": "Dugoselski komunalni i poduzetnički centar d.o.o.",
    "web": SITE,
    "zupanija": "Zagrebačka",
    "jls": ["Dugo Selo"],
    "nazivi": {"M": "Miješani komunalni otpad (zelena kanta)", "K": "Papir (plava kanta)",
               "P": "Plastika (žuta kanta)",
               "B": "Biootpad (smeđa kanta)"},
    "bioNapomena": "Biootpad za obiteljske kuće: četvrtkom sjeverni dio, petkom južni dio grada "
                   "(popis ulica u letku).",
    "napomene": [
        "Raspored za obiteljske kuće: miješani otpad svaki drugi tjedan, papir i plastika jednom mjesečno, na dan "
        "odvoza ulice; biootpad svaki tjedan.",
        "Spremnike pripremiti za pražnjenje prije 07:00.",
        "Na Božić i Novu godinu odvoz se prebacuje na najbližu subotu; ostalim blagdanima odvoz je redovan.",
        "Višestambene zgrade: miješani otpad prema potrebi, biootpad srijedom; nemaju raspored po ulici.",
        "Staklo, metal i tekstil predaju se u spremnike na zelenim otocima, ostalo u reciklažno dvorište Andrilovec "
        "(Ulica Poljana 10; pon-pet 07-15, uto do 17:30, sub 07-14). Informacije: 01/2750-621, dkpc@dkpc.hr.",
    ],
}

_last = [0.0]


def fetch(url, dest=None, tries=4):
    """GET with the project User-Agent, at most 2 requests a second, retries with backoff."""
    for attempt in range(tries):
        wait = 0.5 - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                body = r.read()
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == tries - 1 or getattr(e, "code", 500) in (400, 403, 404, 410):
                raise
            time.sleep(2 ** (attempt + 1))
    if dest:
        Path(dest).write_bytes(body)
    return body


def colour(obj):
    c = obj.get("non_stroking_color")
    if c is None:
        return None
    if isinstance(c, (int, float)):
        c = (c,)
    c = tuple(round(float(v), 3) for v in c)
    return c * 3 if len(c) == 1 else c


def lookup(col, tol=0.05):
    for known, code in PALETTE.items():
        if all(abs(a - b) <= tol for a, b in zip(col, known)):
            return code
    return "?"


def read_calendar(page, year):
    """({date: (upper code, lower code)}, {replacement Saturday: holiday}, problems).

    Codes are "M", "K", "P", "B" or "" (white); the upper and lower halves of a cell are read separately."""
    problems = []
    words = page.extract_words()
    text = " ".join((page.extract_text() or "").split())
    if f"za {year}. godinu" not in text:
        return {}, {}, [f"no 'za {year}. godinu' in the title"]
    heads = [w for w in words if w["text"] in WEEK]
    blocks = []
    for w in words:
        if w["text"] not in MONTHS:
            continue
        cx = (w["x0"] + w["x1"]) / 2
        row = sorted((h for h in heads if 0 < h["top"] - w["bottom"] < 25 and abs((h["x0"] + h["x1"]) / 2 - cx) < 70),
                     key=lambda h: h["x0"])
        if [h["text"] for h in row] != WEEK:
            problems.append(f"{w['text']}: weekday row {[h['text'] for h in row]}")
            continue
        blocks.append((MONTHS.index(w["text"]) + 1, row))
    if sorted(mo for mo, _ in blocks) != list(range(1, 13)):
        return {}, {}, problems + [f"month grids {sorted(mo for mo, _ in blocks)}"]
    digits = [c for c in page.chars if c["text"].isdigit()]
    # filled rectangles in drawing order; a later one covers an earlier one (some coloured cells of
    # neighbouring months are painted over in white)
    fills = [s for s in page.rects
             if s.get("fill") and colour(s) and s["x1"] - s["x0"] > 2 and s["bottom"] - s["top"] > 2]
    hidden = {i for i, s in enumerate(fills) if any(
        o["x0"] <= s["x0"] + 0.6 and s["x1"] <= o["x1"] + 0.6
        and o["top"] <= s["top"] + 0.6 and s["bottom"] <= o["bottom"] + 0.6
        for o in fills[i + 1:])}
    # offset between a number's middle and its cell's middle, from the full-height coloured cells
    numbers = [w for w in words if w["text"].isdigit()]
    offsets = []
    for s in fills:
        if 14 < s["bottom"] - s["top"] < 18 and 15 < s["x1"] - s["x0"] < 21 and colour(s) not in WHITE:
            cy = (s["top"] + s["bottom"]) / 2
            near = [w for w in numbers if s["x0"] <= (w["x0"] + w["x1"]) / 2 <= s["x1"]
                    and abs((w["top"] + w["bottom"]) / 2 - cy) < 9]
            if near:
                offsets.append(cy - (near[0]["top"] + near[0]["bottom"]) / 2)
    if len(offsets) < 50 or max(offsets) - min(offsets) > 1.5:
        return {}, {}, problems + [f"cells and numbers do not line up ({len(offsets)} cells)"]
    shift = sorted(offsets)[len(offsets) // 2]
    cells, boxes = {}, []
    for mo, g in blocks:
        cols = [(h["x0"] + h["x1"]) / 2 for h in g]
        pitch = (cols[-1] - cols[0]) / 6
        mine = [c for c in digits if 0 < c["top"] - g[0]["bottom"] < 6.6 * pitch * 0.95
                and cols[0] - pitch / 2 < (c["x0"] + c["x1"]) / 2 < cols[-1] + pitch / 2]
        lines = []
        for c in sorted(mine, key=lambda c: c["top"]):
            if not lines or c["top"] - lines[-1] > 4:
                lines.append(c["top"])
        if len(lines) != 6:
            problems.append(f"{MONTHS[mo - 1]}: {len(lines)} week rows, expected 6")
            continue
        grid = {}
        for c in mine:
            col = min(range(7), key=lambda i: abs(cols[i] - (c["x0"] + c["x1"]) / 2))
            row = min(range(6), key=lambda i: abs(lines[i] - c["top"]))
            grid.setdefault((row, col), []).append(c)
        # the first row is the week of the 1st, or the week before when the month starts on a Monday
        first = date(year, mo, 1) - timedelta(days=date(year, mo, 1).weekday() or 7)
        for (row, col), chars in grid.items():
            d = first + timedelta(days=row * 7 + col)
            label = "".join(c["text"] for c in sorted(chars, key=lambda c: c["x0"]))
            grey = all(max(colour(c)) - min(colour(c)) < 0.05 and 0.6 < colour(c)[0] < 0.9 for c in chars)
            if label != str(d.day) and MISPRINTS.get(d) != label:
                problems.append(f"{MONTHS[mo - 1]} row {row + 1} column {WEEK[col]}: '{label}', expected {d.day}")
                continue
            if grey != (d.month != mo):
                problems.append(f"{d}: {'grey' if grey else 'not grey'} in the {MONTHS[mo - 1]} grid")
                continue
            if grey:
                continue
            top = min(c["top"] for c in chars)
            bottom = max(c["bottom"] for c in chars)
            cx, cy = cols[col], (top + bottom) / 2 + shift  # the cell's middle (the text sits a little lower)
            step = (lines[-1] - lines[0]) / 5
            boxes.append((d, cx - pitch / 2, cx + pitch / 2, cy - step / 2, cy + step / 2))
            halves = []
            for y in (cy - 0.25 * step, cy + 0.25 * step):  # upper and lower half of the cell
                under = [i for i, s in enumerate(fills) if s["x0"] <= cx <= s["x1"] and s["top"] <= y <= s["bottom"]]
                if not under:
                    halves.append("")
                    continue
                i = max(under)  # the one drawn last is the one you see
                halves.append("" if colour(fills[i]) in WHITE else lookup(colour(fills[i])))
            if "?" in halves:
                problems.append(f"{d}: unknown colour")
            cells.setdefault(d, []).append(tuple(halves))
    found = {}
    for d in (date(year, 1, 1) + timedelta(days=i) for i in range(366)):
        if d.year != year:
            continue
        if len(cells.get(d, [])) != 1:
            problems.append(f"{d} appears {len(cells.get(d, []))} times")
        else:
            found[d] = cells[d][0]
    # every coloured rectangle, where it is not painted over, lies on a day of the month and shows a colour read there
    bottom_of_grid = max(y1 for *_, y1 in boxes)
    for i, s in enumerate(fills):
        code = lookup(colour(s))
        if code == "?" or i in hidden or s["x1"] - s["x0"] > 25:
            continue
        seen = []  # sample points where this rectangle is the visible one
        for fx in range(6):
            for fy in range(6):
                x = s["x0"] + (fx + 0.5) / 6 * (s["x1"] - s["x0"])
                y = s["top"] + (fy + 0.5) / 6 * (s["bottom"] - s["top"])
                if y <= bottom_of_grid and not any(o["x0"] <= x <= o["x1"] and o["top"] <= y <= o["bottom"]
                                                   for o in fills[i + 1:]):
                    seen.append((x, y))
        if len(seen) < 11:  # painted over, a sliver between cells, or the legend
            continue
        for x, y in seen:
            near = [((x - (x0 + x1) / 2) ** 2 + (y - (y0 + y1) / 2) ** 2, d) for d, x0, x1, y0, y1 in boxes
                    if x0 - 1 <= x <= x1 + 1 and y0 - 1.5 <= y <= y1 + 1.5]
            on = [min(near)[1]] if near else []
            if not on or code not in found.get(on[0], ()):
                problems.append(f"{code} colour at {x:.0f},{y:.0f} is not on a day read with it ({on})")
                break
    notes = [datetime.strptime(d, "%d.%m.%Y").date() for d in re.findall(r"Umjesto (\d\d\.\d\d\.\d{4})\.", text)]
    replaced = {}
    for d, halves in found.items():
        if d.weekday() >= 5 and any(halves):
            monday = d - timedelta(days=d.weekday())
            hol = [n for n in notes if monday <= n < monday + timedelta(days=5)]
            if len(hol) != 1:
                problems.append(f"{d} is coloured, but no single 'Umjesto' note in that week ({hol})")
            else:
                replaced[d] = hol[0]
    for n in notes:
        if n.year == year and n not in replaced.values():
            problems.append(f"note 'Umjesto {n:%d.%m.%Y.}' without a coloured Saturday")
        if n.year == year and any(found.get(n, ("",))):
            problems.append(f"{n} is coloured, but the note moves it to a Saturday")
    # each week: one type (M, K or P) on Monday to Friday, biowaste in the lower half Wednesday to Friday
    for monday in sorted({d - timedelta(days=d.weekday()) for d in found}):
        days = [monday + timedelta(days=i) for i in range(5)]
        days = [replaced_by(d, replaced) or d for d in days]
        upper = {found[d][0] for d in days if d in found and any(found[d])}
        lower = [found[d][1] for d in days[2:] if d in found and any(found[d])]
        full = [found[d][1] == found[d][0] for d in days[:2] if d in found and any(found[d])]
        if len(upper) > 1 or upper & {"B"} or not all(full) or any(x != "B" for x in lower):
            problems.append(f"week of {monday}: upper {sorted(upper)}, lower Wed-Fri {lower}")
    return found, replaced, problems


def replaced_by(d, replaced):
    """The Saturday that replaces holiday d, if any."""
    return next((s for s, h in replaced.items() if h == d), None)


def fold(text):
    return unicodedata.normalize("NFKD", text.lower().replace("đ", "d")).encode("ascii", "ignore").decode()


def key(item):
    """'Odvojak  J. Dalmatinca' -> 'odvojak dalmatinca', 'Ul. Trsje' -> 'trsje', 'Bunarska (Prozorje)' -> 'bunarska'.

    Only for matching the two street lists; the names shown are the ones in the weekday table."""
    s = re.sub(r"\([^)]*\)", " ", fold(item))
    s = re.sub(r"\b(ii|i)\. (?=\w+ski odvojak)", lambda m: str(len(m.group(1))) + " ", s)  # 'II. Savski odvojak'
    s = re.sub(r"\bhr\. ", "hrvatskih ", s)  # 'Hr. Branitelja'
    s = re.sub(r" do [a-z]+$", "", s)  # 'Šaškovečka do Bunčića': part of the street
    s = re.sub(r"\b(ulica|ul)\b\.?", " ", s)
    s = re.sub(r"\b(?:[a-z]{1,2}\.)+", " ", s)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


# settlement headings: 'KOZINŠČAK:', 'DUGO SELO-CENTAR:', 'Goričino naselje:', 'ANDRILOVEC – cijelo naselje'
HEAD = re.compile(r"((?:[A-ZČĆŠŽĐ]{2,}[ -])*[A-ZČĆŠŽĐ]{3,}|[A-ZČĆŠŽĐ][a-zčćšžđ]+ naselje)"
                  r"\s*(?::|\s[–-]\s*)")


def street_items(text):
    """[(settlement heading, item)] from 'KOZINŠČAK: Carski put, M. Držića, ... ANDRILOVEC – cijelo naselje'."""
    pieces, place, pos = [], "", 0
    for m in HEAD.finditer(text):
        pieces.append((place, text[pos:m.start()]))
        place, pos = m.group(1), m.end()
    pieces.append((place, text[pos:]))
    out = []
    for place, chunk in pieces:
        place = place.title().replace("Berg", "Breg")
        if re.match(r"\s*cijelo naselje", chunk):
            out.append((place, place))
            chunk = re.sub(r"^\s*cijelo naselje", "", chunk)
        chunk = re.sub(r"\([^)]*\)", lambda m: m.group(0).replace(",", ";"), chunk)
        for item in chunk.split(","):
            item = " ".join(item.split()).strip(" .;")
            if item and key(item):
                out.append((place, item))
    return out


def weekday_table(html_text):
    """[(relacija, weekday 0-4, settlement, street)] from the 'Raspored odvoza' toggles."""
    parts = re.split(r"(\d)\.(?:\s|&nbsp;|\xa0)*RELACIJA", html_text)
    out = []
    for n, part in zip(parts[1::2], parts[2::2]):
        for day, body in re.findall(r"<h4[^>]*>\s*(\w+)\s*</h4>\s*<div[^>]*>(.*?)</div>", part, re.S):
            if day in DAYS:
                text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())
                out += [(int(n), DAYS.index(day), p, i) for p, i in street_items(text)]
    return out


def bio_lists(page_text):
    """{3: [(settlement, street)], 4: [...]} from 'ČETVRTAK (SJEVERNI DIO): ...' and 'PETAK (JUŽNI DIO): ...'."""
    text = " ".join(page_text.split())
    north = re.search(r"ČETVRTAK \(SJEVERNI DIO\):(.*?)PETAK \(JUŽNI DIO\):", text)
    south = re.search(r"PETAK \(JUŽNI DIO\):(.*)$", text)
    if not north or not south:
        return None
    return {3: street_items(north.group(1)), 4: street_items(south.group(1))}


def match(table, bio):
    """{index in table: bio weekday}, notes, problems. Exact key first, then a unique similar name."""
    bio_keys = {}
    for wd, items in bio.items():
        for place, item in items:
            bio_keys.setdefault(key(item), set()).add(wd)
    found, notes, problems, used = {}, [], [], set()
    for i, (n, wd, place, item) in enumerate(table):
        if NOT_HOUSEHOLDS.search(item):
            continue
        k = key(item)
        if k in bio_keys:
            found[i] = bio_keys[k]
            used.add(k)
    for i, (n, wd, place, item) in enumerate(table):
        if NOT_HOUSEHOLDS.search(item) or i in found:
            continue
        k = key(item)
        cands = [b for b in bio_keys if b not in used and (
            difflib.SequenceMatcher(None, k, b).ratio() >= 0.85 or k.endswith(" " + b) or b.endswith(" " + k))]
        if len(cands) == 1:
            found[i] = bio_keys[cands[0]]
            used.add(cands[0])
            notes.append(f"'{item}' = '{cands[0]}'")
    for b in bio_keys:
        if b not in used:
            notes.append(f"biootpad lista: '{b}' nije u tablici dana odvoza")
    return found, notes, problems


def town(place):
    """Settlement of a heading: 'Dugo Selo-Centar' and 'Dugo Selo-Novo Naselje' are Dugo Selo."""
    return "Dugo Selo" if not place or place.startswith("Dugo Selo") else place


def nice_item(place, item):
    item = re.sub(r"\bul\.?$", "ulica", re.sub(r"^Ul\. ", "Ulica ", item))
    return item if place in ("", "Dugo Selo", "Dugo Selo-Centar", "Dugo Selo-Novo Naselje") or item == place \
        else f"{item} ({place})"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    home = fetch(SITE + "/").decode("utf-8", "replace")
    m = re.search(rf'href="(https?://[^"]+/Letak_kalendar-odvoza-{year}[^"]*\.pdf)"', home)
    if not m:
        sys.exit(f"Nema letka s kalendarom za {year} na {SITE}")
    table = weekday_table(fetch(PAGE).decode("utf-8", "replace"))
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "letak.pdf"
        fetch(m.group(1), pdf_path)
        pdf = pdfplumber.open(pdf_path)
        found, replaced, probs = read_calendar(pdf.pages[0], year)
        problems += [f"kalendar: {p}" for p in probs]
        bio = bio_lists(pdf.pages[1].extract_text() or "") if len(pdf.pages) > 1 else None
    if bio is None:
        problems.append("no biowaste street lists on page 2 of the leaflet")
    if len({(n, wd) for n, wd, _, _ in table}) != 10:
        problems.append(f"street table: {len({(n, wd) for n, wd, _, _ in table})} relacija/weekday lists, expected 10")
    if problems:
        for p in problems[:20]:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    print(f"Letak: {m.group(1).rsplit('/', 1)[1]}; zamjenske subote: "
          + ", ".join(f"{s:%d.%m.} umjesto {h:%d.%m.}" for s, h in sorted(replaced.items())))
    weeks = Counter(found[d][0] for d in found if d.weekday() == 0 and found[d][0])
    print(f"Tjedni po vrsti (ponedjeljci): {dict(sorted(weeks.items()))}")
    bio_of, notes, probs = match(table, bio)
    problems += probs
    for n in notes:
        print(f"   spojeno/napomena: {n}")
    zones, skipped, no_bio = {}, [], []
    for i, (n, wd, place, item) in enumerate(table):
        if NOT_HOUSEHOLDS.search(item):
            skipped.append(f"{DAYS[wd]}: {item}")
            continue
        bios = bio_of.get(i)
        if bios and len(bios) > 1:
            problems.append(f"'{item}' is on both biowaste lists")
            continue
        bwd = next(iter(bios)) if bios else None
        if bwd is None:
            no_bio.append(f"{item} ({DAYS[wd].lower()})")
        zk = KEYS[wd] + (f"-{KEYS[bwd]}" if bwd is not None else "")
        zones.setdefault(zk, {"wd": wd, "bio": bwd, "ulice": []})["ulice"].append(nice_item(place, item))
    households = sum(1 for *_, i in table if not NOT_HOUSEHOLDS.search(i))
    if len(no_bio) > 0.1 * households:
        problems.append(f"{len(no_bio)} of {households} streets have no biowaste day: the lists no longer match")
    unmatched = [n for n in notes if n.startswith("biootpad lista")]
    if len(unmatched) > 5:
        problems.append(f"{len(unmatched)} streets of the biowaste lists are not in the weekday table")
    print(f"Preskočeno (nisu kućanstva): {', '.join(skipped)}")
    if no_bio:
        print(f"Bez dana za biootpad (nisu na popisu u letku): {', '.join(no_bio)}")
    days_of = {}  # the same street of the same settlement under several weekdays
    for _, wd, p, i in table:
        if not NOT_HOUSEHOLDS.search(i):
            days_of.setdefault((town(p), key(i)), set()).add(wd)
    twice = {nice_item(p, i): " i ".join(DAT[w] for w in sorted(days_of[(town(p), key(i))]))
             for _, _, p, i in table if len(days_of.get((town(p), key(i)), ())) > 1}
    out, total = {}, Counter()
    for zk, z in sorted(zones.items(), key=lambda kv: (kv[1]["wd"], kv[1]["bio"] if kv[1]["bio"] is not None else 9)):
        rows = {}
        for d in sorted(found):
            for wd, half, codes in ((z["wd"], 0, "MKP"), (z["bio"], 1, "B")):
                if wd is None or d.weekday() != wd:
                    continue
                sat = replaced_by(d, replaced)
                day, moved = (sat, True) if sat else (d, False)
                c = found[day][half]
                if c and c in codes:
                    old = rows.get(day, ("", False))
                    rows[day] = (old[0] + c, old[1] or moved)
        counts = Counter(t for c, _ in rows.values() for t in c)
        for t in "MKP" + ("B" if z["bio"] is not None else ""):
            lo, hi = PER_YEAR[t]
            if not lo <= counts.get(t, 0) <= hi:
                problems.append(f"zone {zk}: {counts.get(t, 0)}x {t} is implausible")
        podrucje = f"Miješani, papir i plastika {DAT[z['wd']]}" + (
            f", biootpad {DAT[z['bio']]}" if z["bio"] is not None else ", dan za biootpad nije naveden")
        entry = {"jls": "Dugo Selo", "podrucje": podrucje, "ulice": z["ulice"]}
        if z["bio"] is None:
            entry["napomena"] = ("Ove ulice nisu na popisu za biootpad u letku (četvrtak/petak); "
                                 "dan za biootpad provjerite kod DKPC-a.")
            entry["bezBioU"] = "ovim ulicama (nisu na popisu u letku)"
        dup = [f"{u} ({twice[u]})" for u in z["ulice"] if u in twice]
        if dup:
            entry["napomena"] = (entry.get("napomena", "") + " " if entry.get("napomena") else "") + \
                "Ulice navedene u tablici za više dana odvoza: " + ", ".join(dup) + \
                "; koji dan vrijedi za vašu adresu, provjerite kod DKPC-a."
        entry["raw"] = {**data["zone"].get(zk, {}).get("raw", {}),
                        str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])}
        out[zk] = entry
        total.update(counts)
        print(f"Zona {zk} ({podrucje}): {len(rows)} dana {dict(sorted(counts.items()))}, "
              f"pomaknuto {sum(mv for _, mv in rows.values())}, {len(z['ulice'])} ulica")
    for p in problems[:20]:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    data["zone"] = out
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(out)} zona, odvoza {dict(sorted(total.items()))})")


if __name__ == "__main__":
    main()
