"""Đakovo and surroundings: Univerzal d.o.o., Grad Đakovo (6 areas for mixed waste, paper and plastic, 5 groups
for biowaste) and the municipalities Strizivojna, Satnica Đakovačka, Viškovci, Trnava, Gorjani, Levanjska Varoš.

    python3 -m izvori.univerzal_djakovo [--year 2026]

All files are linked from univerzal-djakovo.hr/sakupljanje-otpada/.

Grad Đakovo: "Kalendar-sakupljanja-komunalnoga-otpada_Grad-Djakovo-<year>.pdf" has two areas per page, each
with its streets (by settlement) in a pink panel and 12 month grids whose cells are green (mixed waste), blue
(paper) or yellow (plastic). "Kalendar-sakupljanja-biootpada_Grad-Djakovo-<year>.pdf" has the same layout for
the five biowaste groups (one weekday each, tan cells). The biowaste groups cut across the other areas and
the street names are abbreviated differently, so biowaste gets zones of its own. Glass and metal bags go
three times a year ("Sakupljanje-stakla-i-metala-<year>.pdf", a day for the suburban settlements and the next
day for the town); that PDF has its text only as outlines, so its six dates are kept here, tied to the file's
sha256.

Municipalities: one calendar each ("<Općina>-<year>.pdf"). These PDFs draw every number and word as
outlines (no text layer, and some viewers show them blank), so the grid is read from the glyph shapes: the
weekday headings are found as twelve runs of seven grey labels, the day numbers as runs of dark/red digit
glyphs, and every number must sit in the week row and column of its date with as many glyphs as it has
digits. The cell colours are green mixed waste, blue paper, yellow plastic (orange and lilac are the mobile
recycling yard, not a household collection). The legend labels cannot be read, so the colour meanings were
checked by eye and are tied to each file's sha256: a changed file stops the script until it is checked again.

Holidays: the calendars show public holidays with red numbers; Univerzal colours collection days on some of
them, so dates are taken as drawn (holidays with a collection are printed and noted).
"""
import argparse
import calendar
import hashlib
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

SLUG = "univerzal-djakovo"
SITE = "https://univerzal-djakovo.hr"
PAGE = SITE + "/sakupljanje-otpada/"
WEEK = ["PO", "UT", "SR", "ČE", "PE", "SU", "NE"]
KOM = {(0.776, 0.878, 0.706): "M", (0.741, 0.843, 0.933): "K", (1.0, 1.0, 0.49): "P"}
BIO = {(0.859, 0.714, 0.569): "B"}
KOM_BAR, BIO_BAR = (0.898, 0.0353, 0.498), (0.859, 0.714, 0.569)
KOM_LEGEND = {"miješani": "M", "papir": "K", "plastika": "P"}
# municipal calendars (outlines only): colours checked by eye against the legend, per file
MUNI_COLOURS = {(0.843, 0.929, 0.745): "M", (0.839, 0.929, 0.741): "M", (0.718, 0.929, 1.0): "K",
                (0.718, 0.925, 1.0): "K", (1.0, 1.0, 0.506): "P",
                (0.973, 0.796, 0.678): "MRD 7-14", (0.886, 0.812, 0.945): "MRD 10-17"}
MUNI = {  # file name part: (JLS, sha256 of the checked file)
    "Strizivojna": ("Strizivojna", "e6790910e40de94341858461b199e3cdead964ff8dcd0cde3ce51c0718f212b0"),
    "Satnica-Djakovacka": ("Satnica Đakovačka", "a88bab297cd9734059a0df2e2f6d605ae14de503fac150c8dc558c2e069cfbac"),
    "Viskovci": ("Viškovci", "5b047d107fe96450d450da88061e11d02a10be99f9b62fa062f59854248e4bfe"),
    "Trnava": ("Trnava", "320a1e5110871ffc75245b1964989744433c27a7a9724d3fe4387001f27006dc"),
    "Gorjani": ("Gorjani", "35d0efac8ce5feb4cee6c6d73772c8481339f2feef6fa9f847dea9b3598aeb52"),
    "Levanjska-Varos": ("Levanjska Varoš", "a3c5937b989c69c4ebd71b3c38d222fbfed44c42c235ad13bb4bfe0f2908ad3d"),
}
DARK, GREY = (0.0883, 0.08101, 0.07515), (0.45882, 0.44314, 0.44314)
REDS = [(1.0, 0.0, 0.0), (0.89061, 0.0141, 0.0652)]
# glass and metal (outlines only), transcribed from the PDF with this sha256: (suburbs, town) per type
GLASS_METAL = {
    "sha256": "bb0ace10e4ce8c0ba9dc0b879100e7fe32e93b48d5778c52d716e0a38475a244", "year": 2026,
    "S": [(date(2026, 4, 21), date(2026, 4, 22)), (date(2026, 9, 22), date(2026, 9, 23))],
    "L": [(date(2026, 6, 23), date(2026, 6, 24))],
}
PROVIDER = {
    "davatelj": "Univerzal d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Osječko-baranjska",
    "jls": ["Đakovo", "Strizivojna", "Satnica Đakovačka", "Viškovci", "Trnava", "Gorjani", "Levanjska Varoš"],
    "nazivi": {"P": "Plastika i plastična ambalaža", "S": "Staklena ambalaža (vreća)", "L": "Metalna ambalaža (vreća)"},
    "bioNapomena": "Biootpad u Gradu Đakovu odvozi se po zasebnim skupinama ulica (zone 'Biootpad'); općine nemaju "
                   "odvoz biootpada u ovom rasporedu.",
    "napomene": [
        "Grad Đakovo: miješani otpad, papir i plastika po područjima (zone 1-6), biootpad po skupinama ulica "
        "(zone 'Biootpad'); za puni raspored treba pogledati obje zone svoje ulice.",
        "Grad Đakovo: staklena i metalna ambalaža u posebnim vrećama tri puta godišnje (prigradska naselja dan "
        "ranije od grada).",
        "Kalendari prikazuju blagdane crvenim brojevima; dani odvoza su obojeni i kad padnu na blagdan, pa su "
        "datumi upisani kako su objavljeni.",
        "Glomazni otpad i mobilno reciklažno dvorište imaju zasebne kalendare na univerzal-djakovo.hr (nisu uključeni).",
        "Reciklažna dvorišta: Ljudevita Gaja 31, Đakovo; Vitika (Ulica Ive Lole Ribara 125, Budrovci). "
        "Kontakt: 031/811-018.",
    ],
}


def near(col, rgb, tol=0.03):
    return col is not None and len(col) == len(rgb) and all(abs(a - b) <= tol for a, b in zip(col, rgb))


def code_of(col, table):
    return next((c for rgb, c in table.items() if near(col, rgb)), None)


# ---------------------------------------------------------------- town calendars (text)

def month_cells(page, words, fills, year, y0, y1, problems, name):
    """{date: code or None} for the 12 month grids between y0 and y1."""
    out, seen = {}, Counter()
    heads = [w for w in words if w["text"].upper() in WEEK and y0 < w["top"] < y1]
    rows = []
    for top in sorted({round(w["top"]) for w in heads}):
        line = sorted((w for w in heads if abs(w["top"] - top) < 2), key=lambda w: w["x0"])
        for i in range(len(line) - 6):
            if [w["text"].upper() for w in line[i:i + 7]] == WEEK:
                rows.append(line[i:i + 7])
    rows = list({(round(g[0]["top"]), round(g[0]["x0"])): g for g in rows}.values())
    titles = [w for w in words if w["text"].upper() in MONTHS and y0 < w["top"] < y1]
    if len(titles) != 12:
        problems.append(f"{name}: month titles {[w['text'] for w in titles]}")
    for t in titles:
        mo = MONTHS.index(t["text"].upper()) + 1
        tx = (t["x0"] + t["x1"]) / 2
        g = [g for g in rows if 0 < g[0]["top"] - t["bottom"] < 12 and g[0]["x0"] - 10 < tx < g[-1]["x1"] + 10]
        if len(g) != 1:
            problems.append(f"{name} {t['text']}: {len(g)} weekday rows")
            continue
        g = g[0]
        cx = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (cx[-1] - cx[0]) / 6
        below = [o["top"] for o in titles if o["top"] > t["top"] + 5 and abs((o["x0"] + o["x1"]) / 2 - tx) < 3 * pitch]
        limit = min(below + [y1])
        first, ndays = date(year, mo, 1).weekday(), calendar.monthrange(year, mo)[1]
        nums = [w for w in words if w["text"].isdigit() and g[0]["bottom"] < w["top"] < limit
                and cx[0] - pitch / 2 < (w["x0"] + w["x1"]) / 2 < cx[-1] + pitch / 2]
        lines = []
        for w in sorted(nums, key=lambda w: w["top"]):
            if not lines or w["top"] - lines[-1] > 3:
                lines.append(w["top"])
        for w in nums:
            wx, wy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            v = int(w["text"])
            col = min(range(7), key=lambda i: abs(cx[i] - wx))
            row = min(range(len(lines)), key=lambda i: abs(lines[i] - w["top"]))
            if not 1 <= v <= ndays or col != date(year, mo, v).weekday() or row != (v + first - 1) // 7:
                problems.append(f"{name} {year}-{mo:02d}: {w['text']} in week row {row + 1}, column {WEEK[col]}")
                continue
            d = date(year, mo, v)
            seen[d] += 1
            under = [(r, c) for r, c in fills if r["x0"] <= wx <= r["x1"] and r["top"] <= wy <= r["bottom"]]
            code = min(under, key=lambda rc: (rc[0]["x1"] - rc[0]["x0"]) * (rc[0]["bottom"] - rc[0]["top"]))[1] if under else None
            out[d] = (code, near(w["non_stroking_color"], (1.0, 0.0, 0.0)))
        missing = [day for day in range(1, ndays + 1) if seen[date(year, mo, day)] != 1]
        if missing:
            problems.append(f"{name} {year}-{mo:02d}: days {missing} missing or twice")
    return out


def panel(page, x0, x1, y0, y1, starts):
    """[(column index, text, bold)] lines of the street panel; chars go to the column their centre is in."""
    chars = [c for c in page.chars if x0 <= (c["x0"] + c["x1"]) / 2 <= x1 and y0 <= c["top"] < y1]
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else x1
        col = sorted((c for c in chars if start - 1 <= (c["x0"] + c["x1"]) / 2 < end - 1), key=lambda c: (round(c["top"]), c["x0"]))
        lines = {}
        for c in col:
            key = next((k for k in lines if abs(k - c["top"]) < 2), c["top"])
            lines.setdefault(key, []).append(c)
        for top in sorted(lines):
            cs = sorted(lines[top], key=lambda c: c["x0"])
            text = ""
            for a, b in zip([None] + cs, cs):
                if a is not None and b["x0"] - a["x1"] > 1.2 and not text.endswith(" "):
                    text += " "
                text += b["text"]
            text = " ".join(text.replace("\xad", "-").split())
            if text:
                out.append((i, text, all("Bold" in c["fontname"] for c in cs if c["text"].strip())))
    return out


def column_starts(page, x0, x1, y0, y1):
    words = [w for w in page.extract_words() if x0 <= w["x0"] < x1 and y0 <= w["top"] < y1]
    starts = Counter()
    for top in {round(w["top"]) for w in words}:
        line = sorted((w for w in words if round(w["top"]) == top), key=lambda w: w["x0"])
        for a, b in zip([None] + line, line):
            if a is None or b["x0"] - a["x1"] > 6:
                starts[round(b["x0"])] += 1
    keep = sorted(x for x, n in starts.items() if n >= 3)
    out = []
    for x in keep:
        if not out or x - out[-1] > 30:
            out.append(x)
    return out


def town_pdf(path, year, problems, kind):
    """[(names, [(settlement, street)], {date: (code, red)})] for each section of a town calendar."""
    pdf = pdfplumber.open(path)
    table, bar_rgb = (KOM, KOM_BAR) if kind == "kom" else (BIO, BIO_BAR)
    sections = []
    for pi, page in enumerate(pdf.pages):
        words = page.extract_words(extra_attrs=["non_stroking_color", "fontname"])
        if pi == 0:  # legend samples at the top
            fills = [r for r in page.rects if r.get("fill") and colour(r) and r["top"] < 70]
            legend = KOM_LEGEND if kind == "kom" else {"biootpad": "B"}
            for label, code in legend.items():
                w = next((w for w in words if w["text"] == label and w["top"] < 70), None)
                sample = [r for r in fills if w and w["x0"] - 20 < r["x1"] <= w["x0"] + 1 and r["top"] <= w["top"] + 3 <= r["bottom"]
                          and code_of(colour(r), table)]
                if not sample or code_of(colour(sample[0]), table) != code:
                    problems.append(f"{path.name}: legend {label}")
        fills = [(r, code_of(colour(r), table)) for r in page.rects if r.get("fill") and colour(r)
                 and code_of(colour(r), table) and r["x1"] - r["x0"] < 40 and r["top"] > 70]
        bars = sorted((r for r in page.rects if r.get("fill") and near(colour(r), bar_rgb, 0.01) and r["x1"] - r["x0"] > 200),
                      key=lambda r: r["top"])
        if not 1 <= len(bars) <= 2:
            problems.append(f"{path.name} p.{pi + 1}: {len(bars)} sections")
        cal_x0 = min((w["x0"] for w in words if w["text"].upper() == "PO" and w["top"] > 70), default=page.width)
        for i, bar in enumerate(bars):
            y1 = bars[i + 1]["top"] if i + 1 < len(bars) else page.height
            bar_words = sorted((w for w in words if bar["top"] - 1 <= w["top"] <= bar["bottom"] and bar["x0"] <= w["x0"] < bar["x1"]),
                               key=lambda w: w["x0"])
            names = []
            for w in bar_words:
                if names and w["x0"] - names[-1][2] < 10:
                    names[-1] = (names[-1][0], names[-1][1] + " " + w["text"], w["x1"])
                else:
                    names.append((w["x0"], w["text"], w["x1"]))
            cells = month_cells(page, words, fills, year, bar["top"] - 30, y1, problems, f"{path.name} p.{pi + 1}/{i + 1}")
            if kind == "kom" and len(names) > 1:
                starts = [round(x) for x, _, _ in names]
            else:
                starts = column_starts(page, bar["x0"], cal_x0 - 5, bar["bottom"], y1)
            lines = panel(page, bar["x0"], cal_x0 - 5, bar["bottom"] + 1, y1, starts)
            pairs, current = [], None  # biowaste lists run on from column to column under bold settlement names
            for col, text, bold in lines:
                if kind == "kom":
                    settlement = names[col][1] if len(names) > 1 else names[0][1]
                    pairs.append((settlement, text))
                elif bold:
                    current = text
                    pairs.append((text, None))
                else:
                    if current is None:
                        problems.append(f"{path.name}: street {text!r} before a settlement heading")
                    pairs.append((current, text))
            sections.append(([n for _, n, _ in names], pairs, cells))
    return sections


TYPOS = {"Tina Ujuevića": "Tina Ujevića", "Otokora Keršovanija": "Otokara Keršovanija"}  # obvious misprints


def ulice_of(pairs):
    out = []
    for settlement, street in pairs:
        street = TYPOS.get(street, street)
        if street is None or street == settlement:
            out.append(settlement)
        elif settlement == "Đakovo":
            out.append(street)
        else:
            out += [settlement, f"{street} ({settlement})"]
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- municipal calendars (outlines)

def runs(glyphs, gap, ytol, low=-1e9):
    """Glyphs grouped into lines (by vertical centre) and into runs along each line (x gap from `low` to `gap`)."""
    lines = []
    for g in sorted(glyphs, key=lambda g: (g["top"] + g["bottom"]) / 2):
        cy = (g["top"] + g["bottom"]) / 2
        if lines and cy - lines[-1][0] < ytol:
            lines[-1][1].append(g)
        else:
            lines.append((cy, [g]))
    out = []
    for _, line in lines:
        cur, right = [], None
        for g in sorted(line, key=lambda g: g["x0"]):
            if cur and low < g["x0"] - right < gap:
                cur.append(g)
                right = max(right, g["x1"])
            else:
                if cur:
                    out.append(cur)
                cur, right = [g], g["x1"]
        out.append(cur)
    return out


def outline_calendar(path, year, problems, name):
    """{date: (rgb or None, red)} from a calendar drawn as outlines."""
    page = pdfplumber.open(path).pages[0]
    curves = [c for c in page.curves if c.get("fill")]
    grey = [c for c in curves if near(colour(c), GREY) and c["bottom"] - c["top"] > 4]
    labels = sorted(runs(grey, 6, 4), key=lambda l: (round((l[0]["top"] + l[0]["bottom"]) / 8), min(g["x0"] for g in l)))
    heads, block = [], []
    for lab in labels:
        if block and (abs(lab[0]["top"] - block[-1][0]["top"]) > 4 or min(g["x0"] for g in lab) - max(g["x1"] for g in block[-1]) > 30):
            heads.append(block)
            block = []
        block.append(lab)
    heads = [h for h in heads + [block] if len(h) == 7]
    if len(heads) != 12:
        problems.append(f"{name}: {len(heads)} weekday heading rows, expected 12")
        return {}
    heads.sort(key=lambda h: (round(h[0][0]["top"] / 10), h[0][0]["x0"]))
    digits = [c for c in curves if (near(colour(c), DARK) or any(near(colour(c), r) for r in REDS))
              and 7.6 <= c["bottom"] - c["top"] <= 8.15 and 2 < c["x1"] - c["x0"] <= 6.0]
    fills = [r for r in page.rects if r.get("fill") and colour(r) and 15 < r["x1"] - r["x0"] < 40]
    out = {}
    for mo, h in enumerate(heads, 1):
        cx = [(min(g["x0"] for g in lab) + max(g["x1"] for g in lab)) / 2 for lab in h]
        pitch = (cx[-1] - cx[0]) / 6
        if any(abs((b - a) - pitch) > 2 for a, b in zip(cx, cx[1:])):
            problems.append(f"{name} month {mo}: uneven weekday columns")
        top = max(g["bottom"] for lab in h for g in lab)
        nums = runs([d for d in digits if top < d["top"] < top + 7 * 20
                     and cx[0] - pitch / 2 < (d["x0"] + d["x1"]) / 2 < cx[-1] + pitch / 2], 3.5, 2, low=-1)
        rows = []
        for n in sorted(nums, key=lambda n: n[0]["top"]):
            if not rows or n[0]["top"] - rows[-1] > 5:
                rows.append(n[0]["top"])
        first, ndays = date(year, mo, 1).weekday(), calendar.monthrange(year, mo)[1]
        seen = Counter()
        for n in nums:
            x = (min(g["x0"] for g in n) + max(g["x1"] for g in n)) / 2
            y = (n[0]["top"] + n[0]["bottom"]) / 2
            col = min(range(7), key=lambda i: abs(cx[i] - x))
            row = min(range(len(rows)), key=lambda i: abs(rows[i] - n[0]["top"]))
            if row > (first + ndays - 1) // 7:  # below the last week row: the title of the next month
                continue
            day = row * 7 + col - first + 1
            if not 1 <= day <= ndays or len(str(day)) != len(n) or abs(cx[col] - x) > pitch / 3:
                problems.append(f"{name} {year}-{mo:02d}: {len(n)} digit(s) in week row {row + 1}, column {WEEK[col]}")
                continue
            d = date(year, mo, day)
            seen[d] += 1
            under = [r for r in fills if r["x0"] <= x <= r["x1"] and r["top"] <= y <= r["bottom"]]
            rgb = colour(min(under, key=lambda r: (r["x1"] - r["x0"]) * (r["bottom"] - r["top"]))) if under else None
            out[d] = (rgb, any(near(colour(g), r) for g in n for r in REDS))
        missing = [day for day in range(1, ndays + 1) if seen[date(year, mo, day)] != 1]
        if missing:
            problems.append(f"{name} {year}-{mo:02d}: days {missing} not found once")
    return out


# ---------------------------------------------------------------- main

def check_months(label, rows, year, problems, mixed=(2, 5)):
    for m in range(1, 13):
        cnt = Counter(c for d, cs, _ in rows if d.month == m for c in cs)
        if mixed and not mixed[0] <= cnt["M"] <= mixed[1]:
            problems.append(f"{label} {year}-{m:02d}: {cnt['M']}x M")
        if cnt["K"] > 2 or cnt["P"] > 2:
            problems.append(f"{label} {year}-{m:02d}: {dict(cnt)}")


def holidays_used(label, cells, hol):
    used = [f"{d:%d.%m.}" for d, (c, red) in sorted(cells.items()) if c and d in hol]
    if used:
        print(f"   {label}: odvoz na blagdan prema kalendaru: {', '.join(used)}")
    return used


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    links = list(dict.fromkeys(re.findall(r'href="(https://univerzal-djakovo\.hr/wp-content/uploads/[^"]+\.pdf)"', html)))

    def link(pattern):
        found = [u for u in links if re.search(pattern, u.rsplit("/", 1)[1], re.I)]
        return found[-1] if found else None

    problems, zones = [], []
    hol = set(pravila.blagdani(year))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        kom_url = link(rf"^Kalendar-sakupljanja-komunalnoga-otpada_Grad-Djakovo-{year}\.pdf$")
        bio_url = link(rf"^Kalendar-sakupljanja-biootpada_Grad-Djakovo-{year}\.pdf$")
        sm_url = link(rf"^Sakupljanje-stakla-i-metala-{year}\.pdf$")
        if not (kom_url and bio_url):
            sys.exit(f"Nema kalendara Grada Đakova za {year} na {PAGE}")
        glass_metal = {}
        if sm_url:
            fetch(sm_url, tmp / "sm.pdf")
            if GLASS_METAL["year"] != year:
                print(f"   staklo i metal: prepisani datumi su za {GLASS_METAL['year']}., nisu upisani")
            elif hashlib.sha256((tmp / "sm.pdf").read_bytes()).hexdigest() != GLASS_METAL["sha256"]:
                problems.append(f"PDF stakla i metala se promijenio ({sm_url}); datume treba ponovo prepisati u GLASS_METAL")
            else:
                glass_metal = {c: GLASS_METAL[c] for c in "SL"}
        else:
            print(f"   nema PDF-a stakla i metala za {year}")
        fetch(kom_url, tmp / "kom.pdf")
        fetch(bio_url, tmp / "bio.pdf")
        kom = town_pdf(tmp / "kom.pdf", year, problems, "kom")
        bio = town_pdf(tmp / "bio.pdf", year, problems, "bio")
        if len(kom) != 6 or len(bio) != 5:
            problems.append(f"Đakovo: {len(kom)} područja i {len(bio)} skupina biootpada (očekivano 6 i 5)")
        for i, (names, pairs, cells) in enumerate(kom, 1):
            town = names == ["Đakovo"]
            rows = {d: c for d, (c, red) in cells.items() if c}
            for code, pairs_ in glass_metal.items():
                for suburb_day, town_day in pairs_:
                    d = town_day if town else suburb_day
                    rows[d] = rows.get(d, "") + code
            rows = [(d, c, False) for d, c in rows.items()]
            label = f"Đakovo područje {i} ({', '.join(names)})"
            check_months(label, rows, year, problems)
            used = holidays_used(label, cells, hol)
            ulice = ulice_of(pairs)
            if len(ulice) < (8 if town else 2):
                problems.append(f"{label}: only {len(ulice)} streets")
            streets = [s for _, s in pairs if s][:4]
            zone = {"jls": "Đakovo",
                    "podrucje": f"Miješani, papir, plastika – {'Đakovo' if town else ', '.join(names)}: {', '.join(streets)}, …",
                    "ulice": ulice,
                    "napomena": "Biootpad: vidi zone 'Biootpad' za svoju ulicu. Staklo i metal: "
                                + ("dani za grad." if town else "dani za prigradska naselja.")
                                + (f" Odvoz i na blagdan prema kalendaru: {', '.join(used)}" if used else "")}
            zones.append((zone, rows))
        for names, pairs, cells in bio:
            rows = [(d, c, False) for d, (c, red) in cells.items() if c]
            day = names[0].lower() if names else "?"
            wd = Counter(d.weekday() for d, _, _ in rows)
            label = f"Đakovo biootpad {day}"
            if len(wd) != 1:
                problems.append(f"{label}: weekdays {dict(wd)}")
            check_months(label, rows, year, problems, mixed=None)
            used = holidays_used(label, cells, hol)
            places = list(dict.fromkeys(s for s, _ in pairs if s))
            zone = {"jls": "Đakovo", "podrucje": f"Biootpad ({day}): {', '.join(places)}", "ulice": ulice_of(pairs),
                    "napomena": "Samo biootpad; miješani otpad, papir i plastika prema zonama područja (1-6)."
                                + (f" Odvoz i na blagdan prema kalendaru: {', '.join(used)}" if used else "")}
            zones.append((zone, rows))
        for part, (jls, sha) in MUNI.items():
            url = link(rf"^{part}-{year}\.pdf$")
            if not url:
                problems.append(f"{jls}: nema kalendara za {year} na {PAGE}")
                continue
            path = tmp / f"{part}.pdf"
            fetch(url, path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                problems.append(f"{jls}: PDF se promijenio ({url}); boje legende treba ponovo provjeriti i "
                                f"upisati novi sha256 u MUNI")
                continue
            cells = outline_calendar(path, year, problems, jls)
            rows, yard, unknown = [], [], []
            for d, (rgb, red) in sorted(cells.items()):
                if rgb is None or near(rgb, (1.0, 1.0, 1.0)):
                    continue
                code = code_of(rgb, MUNI_COLOURS)
                if code is None:
                    unknown.append(f"{d} {rgb}")
                elif code.startswith("MRD"):
                    yard.append(f"{d:%d.%m.} ({code[4:]} h)")
                else:
                    rows.append((d, code, False))
            if unknown:
                problems.append(f"{jls}: unknown colours {unknown[:4]}")
            wd = Counter(d.weekday() for d, c, _ in rows if c == "M")
            check_months(jls, rows, year, problems)
            used = holidays_used(jls, {d: (c, False) for d, c, _ in rows}, hol)
            day = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"][wd.most_common(1)[0][0]] if wd else "?"
            notes = [f"Izvor: {url}"]
            if used:
                notes.insert(0, f"Odvoz i na blagdan prema kalendaru: {', '.join(used)}")
            if yard:
                notes.insert(0, "Mobilno reciklažno dvorište: " + ", ".join(yard))
            zones.append(({"jls": jls, "podrucje": f"Cijela općina (miješani otpad: {day})", "ulice": [],
                           "napomena": " ".join(notes)}, rows))
    for n, (zone, rows) in enumerate(zones, 1):
        cnt = Counter(c for _, cs, _ in rows for c in cs)
        print(f"Zona {n} ({zone['jls']}: {zone['podrucje'][:60]}): {len(rows)} dana {dict(sorted(cnt.items()))}, "
              f"{len(zone['ulice'])} ulica/naselja")
    if {z["jls"] for z, _ in zones} != set(PROVIDER["jls"]):
        problems.append(f"zones for {sorted({z['jls'] for z, _ in zones})}")
    for p in problems[:30]:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for n, (zone, rows) in enumerate(zones, 1):
        prev = {y: v for y, v in old.get(str(n), {}).get("raw", {}).items() if y != str(year)}
        data["zone"][str(n)] = {**zone, "raw": {**prev, str(year): podaci.month_lines(rows)}}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
