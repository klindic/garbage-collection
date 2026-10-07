"""Mull-Trans d.o.o. and the former Eko-Flor Plus d.o.o. municipalities: one colour calendar PDF per city/municipality.

    python3 -m izvori.mull_trans [--year 2026]

Eko-Flor Plus no longer publishes schedules; every calendar of both companies is linked from a municipality
page on mull-trans.hr (WordPress). One REST request lists all pages; on each, the PDF is the link around the
"Kalendar-odvoza" image (the newest page wins when a municipality has two). Each PDF is a 12-month grid
(Excel or Word export with a text layer). Day cells are read with pdfplumber: the date comes from the cell's
row and weekday column (the printed number is checked), the waste type from the cell's fill colour, matched
to the legend swatches under the calendar (mixed waste by "teren" weekday when there are several, paper,
plastic, glass, biowaste, black = bulky waste). Two-colour cells hold two types; a green missing from the
legend counts as mixed waste only when the legend has a single mixed-waste green; any other unknown colour
rejects the municipality. Text lines under the months add what is not coloured (paper/plastic date lists,
glass, bulky waste per teren); ranges of days ("09. - 13.") and MRD dates (double grey frame) go to the
zone's napomena. Paper, plastic, glass and bulky dates without a teren go to the teren whose weekday they
fall on when the month (or date list) has one date per teren, otherwise to every teren (noted as
ambiguous). Street lists per teren exist only for a few municipalities (STREETS, Tovarnik's legend).
Holiday shifts are drawn into the calendars (grey or boxed holiday, arrow to a substitute day, usually a
Saturday): a collection on a Saturday or off its teren's weekday is marked as moved and must lie within a
week of a public holiday. Schedule changes published later as posts ("Izmjena rasporeda ...") replace that
month's dates from the post's date on when they parse cleanly; other notices (one-off make-up collections)
are only listed. Months before a calendar's first coloured month (Jastrebarsko from June, Krašić from July)
keep the dates of an earlier podaci/mull-trans.json.
"""
import argparse
import calendar
import colorsys
import html
import json
import re
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "mull-trans"
SITE = "https://mull-trans.hr"
PAGES = SITE + "/wp-json/wp/v2/pages?per_page=100&_fields=id,slug,link,modified,title,content"
POSTS = SITE + "/wp-json/wp/v2/posts?per_page=100&after={after}&_fields=id,date,link,title,content"
PHONE = "049/587-837"
# JLS in the provider's area, grouped by county (zone order); names as in istrazivanje/jls_davatelj.csv
JLS = ["Bedenica", "Jakovlje", "Klinča Sela", "Krašić", "Rugvica", "Stupnik", "Žumberak", "Jastrebarsko",
       "Pisarovina", "Sveta Nedelja", "Donja Stubica", "Pregrada", "Gornja Stubica", "Stubičke Toplice",
       "Sveti Križ Začretje", "Oroslavje", "Krapinske Toplice", "Mihovljan", "Veliko Trgovišće", "Bosiljevo",
       "Breznica", "Čađavica", "Mikleuš", "Sopje", "Voćin", "Nova Bukovica", "Donji Andrijevci", "Garčin",
       "Gundinci", "Oprisavci", "Velika Kopanica", "Vrpolje", "Stankovci", "Drenje", "Koška", "Magadenovac",
       "Podravska Moslavina", "Punitovci", "Viljevo", "Lovas", "Nijemci", "Tovarnik", "Sveti Juraj na Bregu",
       "Štefanje"]
GRAD = {"Jastrebarsko", "Sveta Nedelja", "Donja Stubica", "Pregrada", "Oroslavje"}
# istrazivanje/jls_davatelj.csv (Evidencija 5.10.2026.) names another provider for these
REGISTRY = {"Mikleuš": "Papuk d.o.o.", "Donji Andrijevci": "Runolist d.o.o.", "Stankovci": "Michieli-Tomić d.o.o."}
# fill colours a calendar uses without a legend swatch: {jls: {cell colour: legend colour}}
ALIASES = {
    # Monday (PON) cells from August on are #F2F2F2, the legend swatch and earlier Mondays #D9D9D9
    "Rugvica": {(0.949, 0.949, 0.949): (0.852, 0.852, 0.852)},
}
# street / settlement lists per teren: {jls: (block heading regex with the weekday as group 1, end regex)}
STREETS = {
    "Sveta Nedelja": (r"ODVOZ (PONEDJELJKOM|UTORKOM|SRIJEDOM|ČETVRTKOM|PETKOM):", r"BIORAZGRADIVI OTPAD"),
    "Stupnik": (r"ODVOZ MIJEŠANOG KOMUNALNOG OTPADA (ČETVRTKOM|PETKOM):", r"Raspored sakupljanja"),
    "Rugvica": (r"Miješani komunalni otpad\s*[–-]?\s*(PONEDJELJKOM|UTORKOM|SRIJEDOM|ČETVRTKOM|PETKOM)\s*:",
                r"\nPapir\b"),
    "Pisarovina": (r"Komunalni otpad (UTO|SRI) [-–]", r"$"),
}

MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj",
          "kolovoz", "rujan", "listopad", "studeni", "prosinac"]
GENITIVE = ["siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja",
            "kolovoza", "rujna", "listopada", "studenoga", "prosinca"]
HEAD = ["P", "U", "S", "Č", "P", "S", "N"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
SHORT = ["PON", "UTO", "SRI", "ČET", "PET"]
WEEKDAY = re.compile(r"\b(ponedjelj\w*|pon|utor\w*|uto|srijed\w*|sri|četvrt\w*|čet|petk\w*|pet)\b")
NUM = re.compile(r"(\d{1,3})[\uf000-\uf8ff*]*")  # day number, maybe with a symbol glyph
WHITE = (1.0, 1.0, 1.0)
HOLIDAY = (0.855, 0.855, 0.855)  # #DADADA: holiday cells (also #D4D4D4, #C4C3C3 when not in the legend)
CODES = {"M": "miješani", "K": "papir", "P": "plastika", "S": "staklo", "B": "biootpad", "G": "glomazni"}
NAPOMENE = [
    "Rasporede za općine koje je ranije opsluživao Eko-Flor Plus d.o.o. sada objavljuje Mull-Trans d.o.o. "
    "na mull-trans.hr (izbornik Područje); neki PDF-ovi i dalje nose oznaku i kontakte Eko-Flora.",
    "Spremnike treba iznijeti najkasnije do 6:00 na dan odvoza, na vidljivo mjesto pored obračunskog mjesta.",
    "Glomazni otpad odvozi se na zahtjev podnesen najkasnije 3 radna dana prije termina: "
    f"{PHONE}, glomazni@mull-trans.hr (stariji PDF-ovi navode glomazni@eko-flor.hr).",
    "Blagdani: pomaci su ucrtani u kalendare (sivo ili uokvireno označen blagdan, strelica do zamjenskog dana, "
    "najčešće subote); takvi datumi označeni su kao pomaknuti.",
    "Mobilno reciklažno dvorište (MRD) nije odvoz s kućnog praga; datumi su u napomeni zone, lokacije i "
    "vrijeme u PDF-u općine.",
]


def norm(s):
    s = unicodedata.normalize("NFKD", s.replace("đ", "d").replace("Đ", "D"))
    return " ".join(s.encode("ascii", "ignore").decode().lower().split())


def colour(obj):
    c = obj.get("non_stroking_color")
    if isinstance(c, (int, float)):
        c = (c,)
    if not c or len(c) not in (1, 3):
        return None
    c = tuple(round(float(v), 3) for v in c)
    return c * 3 if len(c) == 1 else c


def fills(page):
    """Filled, non-white rectangles: [(x0, top, x1, bottom, rgb)]."""
    out = []
    for r in page.rects:
        c = colour(r) if r.get("fill") else None
        if c and c != WHITE:
            out.append((r["x0"], r["top"], r["x1"], r["bottom"], c))
    return out


def cell_colours(box, rects, share=0.2):
    """Fill colours covering at least `share` of a cell, left to right."""
    x0, t, x1, b = box
    area = (x1 - x0) * (b - t)
    acc, pos = defaultdict(float), {}
    for rx0, rt, rx1, rb, c in rects:
        w, h = min(x1, rx1) - max(x0, rx0), min(b, rb) - max(t, rt)
        if w >= 7 and h >= 7:  # thinner pieces are borders (holiday and MRD boxes) and stray strips
            acc[c] += w * h / area
            pos.setdefault(c, (max(x0, rx0) + min(x1, rx1)) / 2)
    return sorted((c for c, f in acc.items() if f >= share), key=lambda c: pos[c])


def is_green(c):
    h, s, v = colorsys.rgb_to_hsv(*c)
    return 70 <= h * 360 <= 170 and s > 0.3 and v > 0.3


def lines_of(words, gap=22, columns=()):
    """Words -> text segments [(x0, top, x1, text)]: one per line and column.

    A line is split at wide gaps and where a word starts in another month column (x ranges in `columns`).
    """
    col = lambda x: next((i for i, (a, b) in enumerate(columns) if a - 5 <= x <= b), None)
    rows = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1][1][-1]["top"] - w["top"]) < 2.5:
            rows[-1][1].append(w)
        else:
            rows.append([w["top"], [w]])
    out = []
    for top, ws in rows:
        ws.sort(key=lambda w: w["x0"])
        seg = [ws[0]]
        for w in ws[1:]:
            if w["x0"] - seg[-1]["x1"] > gap or None != col(w["x0"]) != col(seg[0]["x0"]) is not None:
                out.append((seg[0]["x0"], top, seg[-1]["x1"], " ".join(x["text"] for x in seg)))
                seg = []
            seg.append(w)
        out.append((seg[0]["x0"], top, seg[-1]["x1"], " ".join(x["text"] for x in seg)))
    return out


# ---------------------------------------------------------------- calendar grid

def grid(page, year, words):
    """Day cells of the month blocks on a page.

    Returns ({date: (x0, top, x1, bottom)}, {month: block}, [(month, problem)], warnings, number words,
    {date: [boxes of extra cells]}).
    The date of a cell comes from its week row and weekday column; the printed number must match
    (a single mismatch per month, e.g. a typo, is a warning).
    block = (x0, x1, grid bottom, next heading top, weekday row bottom).
    """
    heads = [(MONTHS.index(w["text"].lower()) + 1, w) for w in words if w["text"].lower() in MONTHS]
    letters = [w for w in words if w["text"] in ("P", "U", "S", "Č", "C", "N")]
    cells, blocks, problems, warnings, used, extra = {}, {}, [], [], [], defaultdict(list)
    for month, h in heads:
        name = MONTHS[month - 1]
        hx = (h["x0"] + h["x1"]) / 2
        row = sorted((w for w in letters if 0 < w["top"] - h["bottom"] < 25
                      and abs((w["x0"] + w["x1"]) / 2 - hx) < 110), key=lambda w: w["x0"])
        if [w["text"].replace("C", "Č") for w in row] != HEAD:
            problems.append((month, f"{name}: ispod naslova nema reda P U S Č P S N"))
            continue
        cx = [(w["x0"] + w["x1"]) / 2 for w in row]
        pitch = (cx[6] - cx[0]) / 6
        limit = min([w["top"] for m, w in heads if w["top"] > h["bottom"] + 20
                     and abs((w["x0"] + w["x1"]) / 2 - hx) < 60], default=page.height)
        x0, x1 = cx[0] - pitch / 2, cx[6] + pitch / 2
        blocks[month] = (x0, x1, row[0]["bottom"], limit, row[0]["bottom"])
        nums = [w for w in words if NUM.fullmatch(w["text"]) and row[0]["bottom"] < w["top"] < limit
                and x0 - 0.1 * pitch < (w["x0"] + w["x1"]) / 2 < x1 + 0.1 * pitch]
        rows = []  # [centre, [words]]
        for w in sorted(nums, key=lambda w: (w["top"] + w["bottom"]) / 2):
            cy = (w["top"] + w["bottom"]) / 2
            if rows and cy - rows[-1][0] < 5:
                rows[-1][1].append(w)
            else:
                rows.append([cy, [w]])
        first = date(year, month, 1).weekday()
        ndays = calendar.monthrange(year, month)[1]
        nrows = (first + ndays - 1) // 7 + 1
        if len(rows) < nrows:
            problems.append((month, f"{name}: {len(rows)} redaka s danima, očekivano {nrows}"))
            continue
        rows = rows[:nrows]
        centres = [sorted((w["top"] + w["bottom"]) / 2 for w in ws)[len(ws) // 2] for _, ws in rows]
        pitch_y = min(b - a for a, b in zip(centres, centres[1:]))
        blocks[month] = (x0, x1, centres[-1] + pitch_y / 2, limit, row[0]["bottom"])
        seen, bad = set(), []
        for r, (_, ws) in enumerate(rows):
            used += ws
            by_col = defaultdict(list)
            for w in ws:
                by_col[min(range(7), key=lambda k: abs(cx[k] - (w["x0"] + w["x1"]) / 2))].append(w)
            for c, cw in sorted(by_col.items()):
                day = r * 7 + c - first + 1
                chars = sorted((ch for w in cw for ch in w["chars"] if ch["text"].isdigit()), key=lambda ch: ch["x0"])
                text = "".join(ch["text"] for ch in chars)
                # hidden or overlapping glyphs ("2" + "20", "2" "6" "3"): one extra digit may be dropped
                ok = {text} | {text[:i] + text[i + 1:] for i in range(len(text))} if len(text) > 2 else {text}
                ok = {int(x) for x in ok if x}
                if not 1 <= day <= ndays:
                    box = (cx[c] - pitch / 2, centres[r] - pitch_y / 2, cx[c] + pitch / 2, centres[r] + pitch_y / 2)
                    num = int(text) if text and len(text) <= 2 else 0
                    if 1 <= num <= ndays:  # an extra cell for a day that has its own cell too (Veliko Trgovišće)
                        extra[date(year, month, num)].append(box)
                        warnings.append(f"{name}: ćelija '{text}' izvan mjeseca ({HEAD[c]}, {r + 1}. red); "
                                        f"njezina boja pribrojena je {num}.{month}.")
                    else:
                        warnings.append(f"{name}: ćelija '{text}' izvan mjeseca ({HEAD[c]}, {r + 1}. red) zanemarena")
                    continue
                seen.add(day)
                if day not in ok:
                    bad.append(f"{day}.{month}. (u ćeliji piše {text})")
        for day in range(1, ndays + 1):
            r, c = (first + day - 1) // 7, (first + day - 1) % 7
            cells[date(year, month, day)] = (cx[c] - pitch / 2, centres[r] - pitch_y / 2,
                                             cx[c] + pitch / 2, centres[r] + pitch_y / 2)
            if day not in seen:
                bad.append(f"{day}.{month}. (broj nedostaje)")
        if len(bad) > 1:
            problems.append((month, f"{name}: brojevi ne odgovaraju položaju: {', '.join(bad)}"))
        elif bad:
            warnings.append(f"{name}: {bad[0]}; datum je određen po položaju ćelije")
    return cells, blocks, problems, warnings, used, extra


def mrd_box(box, page):
    """True for a cell framed like the MRD legend swatch: a grey (#C0C0C0 / #606060) line on both sides."""
    x0, t, x1, b = box
    grey = [r["x0"] for r in page.rects if r["x1"] - r["x0"] < 1.5
            and min(b, r["bottom"]) - max(t, r["top"]) > 0.6 * (b - t)
            and (c := colour(r)) and max(c) - min(c) < 0.02 and 0.3 < c[0] < 0.8]
    return any(abs(x - x0) < 5 for x in grey) and any(abs(x - x1) < 5 for x in grey)


# ---------------------------------------------------------------- legend

def swatches(rects, top, words):
    """Legend swatches below `top`: [{box, colours (left to right), label, text}]."""
    rs = [r for r in rects if r[1] >= top and 6 < r[2] - r[0] < 150 and 6 < r[3] - r[1] < 30]
    groups = []
    for r in sorted(rs, key=lambda r: (r[1], r[0])):
        g = next((g for g in groups if r[0] < g[2] + 0.6 and r[2] > g[0] - 0.6
                  and r[1] < g[3] - 1 and r[3] > g[1] + 1), None)
        if g:
            g[:4] = [min(g[0], r[0]), min(g[1], r[1]), max(g[2], r[2]), max(g[3], r[3])]
            g[4].append(r)
        else:
            groups.append([r[0], r[1], r[2], r[3], [r]])
    out = []
    for x0, t, x1, b, parts in groups:
        cols = []
        for c in sorted({p[4] for p in parts}, key=lambda c: min((p[0] + p[2]) / 2 for p in parts if p[4] == c)):
            cols.append(c)
        label = " ".join(w["text"] for w in words if w["x0"] >= x0 - 1 and w["x1"] <= x1 + 1
                         and w["top"] >= t - 2 and w["bottom"] <= b + 2)
        nxt = min([g[0] for g in groups if g[0] > x1 and abs(g[1] - t) < 4], default=10 ** 4)
        right = sorted((w for w in words if x1 - 1 <= w["x0"] < nxt
                        and abs((w["top"] + w["bottom"]) / 2 - (t + b) / 2) < 7), key=lambda w: w["x0"])
        text, last = [], x1
        for w in right:
            if w["x0"] - last > (70 if not text else 20):
                break
            text.append(w["text"])
            last = w["x1"]
        out.append({"box": (x0, t, x1, b), "colours": cols, "label": label, "text": " ".join(text)})
    return out


def weekday_of(text):
    m = WEEKDAY.search(text.lower())
    return None if not m else ["pon", "uto", "sri", "čet", "pet"].index(m.group(1)[:3])


def classify(sw, page_text):
    """One legend swatch -> [(colour, code, teren weekday, teren label, settlement)], "MRD", None (no fill)
    or a str describing what is not understood."""
    label, text, cols = sw["label"], sw["text"], sw["colours"]
    t = f"{label} {text}".lower()
    if "mobiln" in t or label.strip() == "MRD":
        return "MRD"
    if "miješan" in t or (label.strip() in SHORT and not text.strip() and "MIJEŠANI KOMUNALNI OTPAD" in page_text):
        if len(cols) != 1:
            return f"legenda '{label} {text}': {len(cols)} boje za miješani otpad"
        name = label.strip() if re.fullmatch(r"[A-Z]{1,3}-\d", label.strip()) else None
        m = re.search(r"naselj[ea] ([A-ZČĆŽŠĐ][\wčćžšđ]+(?: [A-ZČĆŽŠĐ][\wčćžšđ]+)*)", text)
        return [(cols[0], "M", weekday_of(f"{label} {text}"), name, m.group(1) if m else None)]
    kinds = [(t.find(k), code) for k, code in (("papir", "K"), ("plastik", "P"), ("stakl", "S"),
                                                ("biorazgrad", "B"), ("glomazn", "G")) if k in t]
    kinds = [code for _, code in sorted(kinds)]
    if not kinds:
        return None if not cols else f"legenda '{label} {text}': nepoznato značenje"
    if len(kinds) == len(cols):
        teren = weekday_of(t.split("teren", 1)[1]) if "teren" in t else None
        return [(c, k, teren, None, None) for c, k in zip(cols, kinds)]
    if len(cols) == 1 and kinds == ["K", "P"]:
        return [(cols[0], "KP", None, None, None)]
    return f"legenda '{label} {text}': {len(cols)} boje za {kinds}"


# ---------------------------------------------------------------- text under the months

DATE = r"(\d{1,2})\.\s?(\d{1,2})\.?"


def text_dates(seg, month, year):
    """Collections named in one text segment: [(date, codes, teren, nominal date)], ranges, MRD dates.

    month: the month block the segment sits under (for day-only lists), or None.
    """
    s = " ".join(seg.replace("..", ".").split())
    s = re.sub(r"\b(\d) (\d)\.", r"\1\2.", s)  # "1 6." from a two-colour label
    low = s.lower()
    out, ranges, mrd = [], [], []

    def day(d, m=None):
        try:
            return date(year, int(m or month), int(d)) if (m or month) else None
        except ValueError:
            return None
    m = re.match(r"(papir\s*i\s*plastika|papir|plastika|staklo)\s*:\s*(.+)", low)
    if m:  # "PAPIR i PLASTIKA: 26., 27., ... i 19.(umjesto 25.)" -> one date per teren
        codes = {"papir": "K", "plastika": "P", "staklo": "S"}.get(m.group(1), "KP")
        for d, inst in re.findall(r"(\d{1,2})\.?\s*(?:\(umjesto (\d{1,2})\.?\))?", m.group(2)):
            if day(d):
                out.append((day(d), codes, None, day(inst) if inst else day(d)))
        return out, ranges, mrd
    m = re.match(r"(\d) ?(\d) odvoz papira i plastike \(umjesto (\d{1,2})\.(\d{1,2})\.\)", low)
    if m:  # "0 5 odvoz PAPIRA I PLASTIKE (umjesto 06.01.)"
        return [(day(m.group(1) + m.group(2), m.group(4)), "KP", None, day(m.group(3), m.group(4)))], ranges, mrd
    s_wo = re.sub(r"\(umjesto[^)]*\)", "", s)
    low = s_wo.lower()
    if "mobiln" in low:
        mrd += [day(d, mo) for d, mo in re.findall(DATE, s_wo) if day(d, mo)]
        for a, b, mo in re.findall(r"(\d{1,2})\./(\d{1,2})\.(\d{1,2})\.", s_wo):
            mrd += [x for x in (day(a, mo), day(b, mo)) if x]
        return out, ranges, mrd
    code = ("G" if "glomazn" in low else "S" if "stakl" in low else
            "KP" if "papir" in low and "plastik" in low else None)
    if not code:
        return out, ranges, mrd
    teren = weekday_of(low.split("teren", 1)[1]) if "teren" in low else None
    rng = re.match(r"(\d{1,2})\.\s*-\s*(\d{1,2})\.(?:(\d{1,2})\.)?", s_wo)
    if rng and (rng.group(3) or month):  # "09. - 13. - Odvoz glomaznog otpada" -> a range of days
        a, b = day(rng.group(1), rng.group(3)), day(rng.group(2), rng.group(3))
        if a and b:
            ranges.append((code, a, b, s))
        return out, ranges, mrd
    found = []
    pair = r"(?<![\d.])(\d{1,2})\.?\s*(?:/|i)\s*(\d{1,2})\.\s?/?\s?(\d{1,2})\."  # "16. i 22./07.", "01./03.09."
    for a, b, mo in re.findall(pair, s_wo):
        found += [day(a, mo), day(b, mo)]
    rest = re.sub(pair, " ", s_wo)
    found += [day(d, mo) for d, mo in re.findall(DATE + r"(?!\d)", rest) if mo]
    if not found and month:
        found += [day(a) for a in re.findall(r"(\d{1,2})\.\s*(?:i|/)", rest)]
        found += [day(a) for a in re.findall(r"(?:i|/)\s*(\d{1,2})\.", rest)]
    for d in dict.fromkeys(x for x in found if x):
        out.append((d, code, teren, d))
    return out, ranges, mrd


def stands_for(d, year):
    """The holiday a Saturday collection replaces (same week, else the next week), or the date itself."""
    if d.weekday() != 5:
        return d
    hol = [h for h in pravila.blagdani(year) if h.weekday() < 5]
    for lo, hi in ((-5, -1), (2, 6)):
        found = [h for h in hol if lo <= (h - d).days <= hi]
        if len(found) == 1:
            return found[0]
    return d


# ---------------------------------------------------------------- one PDF

def read_pdf(path, jls, year):
    """Zones of one municipality from its calendar PDF; problems mean the municipality is left out."""
    res = {"problems": [], "warnings": [], "notes": [], "mrd": set()}
    probs, warn = res["problems"], res["warnings"]
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages
        page = pages[0]
        words = page.extract_words(return_chars=True)
        text1 = page.extract_text() or ""
        alltext = "\n".join(p.extract_text() or "" for p in pages)
        if norm(jls) not in norm(text1) or str(year) not in text1:
            probs.append(f"naslov PDF-a nije '{jls}' za {year}.")
            return res
        rects = fills(page)
        cells, blocks, gprobs, gwarn, used, extra = grid(page, year, words)
        warn += gwarn
        if not blocks:
            probs.append("nema kalendara")
            return res
        coloured = {d: cell_colours(box, rects) + [c for b in extra[d] for c in cell_colours(b, rects)]
                    for d, box in cells.items()}
        # the schedule starts with the first month that has a coloured (not light grey) day
        months = sorted({d.month for d, cs in coloured.items()
                         if any(max(c) - min(c) > 0.02 or c[0] < 0.7 for c in cs)})
        if not months:
            probs.append("u kalendaru nema obojenih dana")
            return res
        start = months[0]
        for month, p in gprobs:
            b = blocks.get(month)
            if month >= start or not b or any(b[0] <= (r[0] + r[2]) / 2 <= b[1] and b[4] < r[1] < b[3] for r in rects
                                              if r[2] - r[0] >= 7 and r[3] - r[1] >= 7 and r[4] != HOLIDAY):
                probs.append(p)
            else:
                warn.append(f"{p} (prazan mjesec prije početka rasporeda)")
        gbottom = max(b[2] for b in blocks.values())

        # legend: under the calendar on page 1, else on page 2 (Rugvica)
        legend, legend_page = [], None
        for pg, top in ((page, gbottom), (pages[1] if len(pages) > 1 else None, 0)):
            if pg is None:
                continue
            pw = pg.extract_words()
            ptext = pg.extract_text() or ""
            # coloured labels of text lines under a month ("PAPIR i PLASTIKA: 26., 27., ...") are not legend
            legend = [(sw, classify(sw, ptext)) for sw in swatches(fills(pg), top, pw)
                      if not (re.match(r"\s*:|\s*\d{1,2}\.,", sw["text"]) or sw["label"].rstrip().endswith(":"))]
            legend_page = pg
            if any(isinstance(c, list) and c[0][1] == "M" for _, c in legend):
                break
        legend_top = min((sw["box"][1] for sw, c in legend if isinstance(c, list) and c[0][1] == "M"),
                         default=page.height)
        entries, has_mrd = [], False
        for sw, c in legend:
            if isinstance(c, str) and c != "MRD":
                warn.append(c + "; zanemareno")
            elif c == "MRD":
                has_mrd = True
            elif c:
                entries += c
        has_mrd = has_mrd or bool(re.search(r"\bMRD\b", text1))
        mixed = [e for e in entries if e[1] == "M"]
        # one teren per mixed-waste colour
        terens, seen_cols = [], set()
        for col, _, wd, name, place in mixed:
            if col not in seen_cols:
                seen_cols.add(col)
                terens.append({"wd": wd, "name": name, "place": place, "colour": col})
        if not terens:
            probs.append("legenda bez miješanog otpada")
            return res
        if len(terens) > 1 and any(t["wd"] is None for t in terens):
            probs.append("više boja miješanog otpada, a legenda ne kaže dan terena")
            return res
        terens.sort(key=lambda t: t["wd"] if t["wd"] is not None else 9)
        if len({t["wd"] for t in terens}) != len(terens):
            probs.append("dva terena s istim danom")
            return res
        palette = {}
        for col, code, wd, *_ in entries:
            key = (code, wd if code == "M" else None)
            if palette.get(col, key) != key:
                probs.append(f"boja {col} u legendi znači i {palette[col]} i {key}")
            palette[col] = key
        greens = [c for c, (code, _) in palette.items() if is_green(c)]
        holidays = set(pravila.blagdani(year))
        alias = ALIASES.get(jls, {})

        def meaning(c, d):
            c = (0.0, 0.0, 0.0) if max(c) < 0.1 else alias.get(c, c)
            near = min(list(palette) + [HOLIDAY], key=lambda p: max(abs(a - b) for a, b in zip(c, p)))
            if max(abs(a - b) for a, b in zip(c, near)) <= 0.03 and near in palette:
                return palette[near]
            if max(c) - min(c) <= 0.02 and 0.7 <= c[0] <= 0.9 and (d in holidays or d.weekday() >= 5):
                return "holiday"  # grey holiday / weekend cell
            if max(c) < 0.1 and not any(code == "G" for code, _ in palette.values()):
                warn.append("crna ćelija bez legende smatra se glomaznim otpadom (kao u ostalim kalendarima)")
                return ("G", None)
            if is_green(c) and len(greens) == 1 and palette[greens[0]][0] == "M":
                return palette[greens[0]]
            return None

        # cells -> (date, code, teren) ; teren = weekday of a mixed-waste teren, None = whole municipality
        found = defaultdict(set)  # (date, code) -> {teren or None}
        nominal = {}
        for d, cs in coloured.items():
            for c in cs:
                mng = meaning(c, d)
                if mng is None:
                    probs.append(f"{d}: nepoznata boja {c}")
                elif mng != "holiday":
                    for code in mng[0]:
                        found[(d, code)].add(mng[1])
            if d.weekday() == 6 and any(code != "holiday" for code in map(lambda c: meaning(c, d), cs)):
                probs.append(f"{d}: odvoz u nedjelju")
        # text lines under the months (and legend labels with dates)
        ranges = []
        nums = {id(w) for w in used}
        columns = sorted({(b[0], b[1]) for b in blocks.values()})
        # text lines above the legend, then each legend entry on its own ("25.05. Glomazni otpad")
        segs = [(x0, top, s) for x0, top, x1, s in lines_of([w for w in words if id(w) not in nums], columns=columns)
                if legend_page is not page or top < legend_top - 2]
        segs += [(None, None, f"{sw['label']} {sw['text']}") for sw, c in legend if isinstance(c, list)]
        group = {}  # (date, code) -> one date list ("PAPIR i PLASTIKA: 26., 27., ..."): one date per teren
        for i, (x0, top, s) in enumerate(segs):
            month = next((m for m, b in blocks.items() if x0 is not None and b[0] - 5 <= x0 <= b[1]
                          and b[2] - 2 <= top < b[3]), None)
            got, rg, mr = text_dates(s, month, year)
            ranges += rg
            res["mrd"].update(mr)
            for d, codes, teren, nom in got:
                for code in codes:
                    found[(d, code)].add(teren)
                    if re.match(r"(papir|plastika|staklo)[^:]*:", s.lower()):
                        group[(d, code)] = ("list", i)
                if nom != d:
                    nominal[d] = nom
        for p in pages[1:]:  # MRD date lists on page 2
            for s in (p.extract_text() or "").splitlines():
                if re.search(r"\d{2}\.\d{2}\.\d{4}\.\s*/", s):
                    res["mrd"].update(date(year, int(mo), int(d))
                                      for d, mo, y in re.findall(r"(\d{2})\.(\d{2})\.(\d{4})\.", s) if int(y) == year)
        if has_mrd:
            res["mrd"].update(d for d, box in cells.items() if mrd_box(box, page))
        # weekly biowaste rule (Sveta Nedelja)
        bio = re.search(r"BIORAZGRADIVI OTPAD\s+sakuplja se (\w+)", alltext)
        if bio:
            wd = weekday_of(bio.group(1))
            for d in pravila.tjedno(year, ["pon", "uto", "sri", "čet", "pet"][wd]):
                if d.month >= start:
                    found[(d, "B")].add(None)
            res["notes"].append(f"Biootpad: svaki {DAN[wd]} prema PDF-u; pomak zbog blagdana nije objavljen.")
        for code in dict.fromkeys(code for code, *_ in ranges):
            what = {"G": "Glomazni otpad", "S": "Staklo", "KP": "Papir i plastika"}[code]
            spans = [f"{a.day}.–{b.day}.{b.month}." + (re.sub(r".*?(naselja .*)", r" \1", s) if "naselja" in s
                                                        else "") for c, a, b, s in ranges if c == code]
            res["notes"].append(f"{what} (raspon dana; dan za pojedino naselje nije objavljen): "
                                + "; ".join(spans).rstrip(".") + ".")
        for t in terens:  # streets
            t["ulice"] = [t["place"]] if t["place"] else []
        if jls in STREETS:
            head, end = STREETS[jls]
            text = alltext if jls != "Pisarovina" else text1
            parts = re.split(head, text)
            for wd_text, body in zip(parts[1::2], parts[2::2]):
                body = re.split(end, body)[0]
                # missing commas: "Posavska Ulica. II. Trstenečki odvojak", "(Savska Ulica 1) Jandrovčeva ulica"
                body = re.sub(r"(?<=[Uu]lica)\. |(?<=\)) (?=[A-ZČĆŽŠĐ])", ", ", " ".join(body.split()))
                names = [re.sub(r"\bulia\b", "ulica", n).strip(" .") for n in body.split(",")]
                names = [n for n in dict.fromkeys(names) if n]
                t = next((t for t in terens if t["wd"] == weekday_of(wd_text)), None)
                if t is None or not names:
                    probs.append(f"popis ulica za '{wd_text}' ne odgovara terenu")
                else:
                    t["ulice"] = names
            if not all(t["ulice"] for t in terens):
                probs.append("popis ulica nije pronađen za svaki teren")

    # distribute whole-municipality dates
    rows = [defaultdict(set) for _ in terens]
    index = {t["wd"]: i for i, t in enumerate(terens)}
    free = defaultdict(list)
    for (d, code), ts in found.items():
        specific = {t for t in ts if t is not None}
        if code == "M":
            for t in ts:
                rows[index.get(t, 0) if t is not None or len(terens) == 1 else 0][d].add("M")
            if None in ts and len(terens) > 1:
                probs.append(f"{d}: miješani otpad bez terena")
        elif specific:
            for t in specific:
                if t not in index:
                    probs.append(f"{d}: {code} za teren {SHORT[t]}, a takvog terena nema")
                else:
                    rows[index[t]][d].add(code)
        else:
            nom = nominal.get(d) or stands_for(d, year)
            free[(code, group.get((d, code), nom.month))].append((d, nom))
    ambiguous = set()
    for (code, _), items in sorted(free.items(), key=str):
        items = sorted(set(items))
        if len(terens) > 1 and len(items) == len(terens):
            wds = [n.weekday() for _, n in items]
            regular = [w for w in wds if w in index]
            left = [w for w in index if w not in regular]
            sat = [d for d, n in items if n.weekday() not in index]
            if len(set(regular)) == len(regular) and len(sat) == len(left) <= 1 and all(d.weekday() == 5 for d in sat):
                for d, n in items:  # one date per teren; a Saturday stands in for the teren without a date
                    rows[index[n.weekday()] if n.weekday() in index else index[left[0]]][d].add(code)
                    if n != d:
                        nominal[d] = n
                continue
        for r in rows:
            for d, _ in items:
                r[d].add(code)
        if len(terens) > 1 and len(items) > 1:
            ambiguous.add(code)

    # moved dates and checks
    holidays = [h for h in pravila.blagdani(year) if h.weekday() < 5]
    near = lambda d: any(abs((d - h).days) <= 7 for h in holidays)
    for t, r in zip(terens, rows):
        tag = SHORT[t["wd"]] if t["wd"] is not None else "općina"
        mdays = Counter(d.weekday() for d, cs in r.items() if "M" in cs)
        if not mdays:
            probs.append(f"teren {tag}: nema miješanog otpada")
            continue
        wd = t["wd"] if t["wd"] is not None else mdays.most_common(1)[0][0]
        regular = {wd} | {w for w, n in mdays.items() if n >= 4 and w < 5}  # e.g. a second summer day
        t["regular"] = sorted(regular)
        t["moved"] = set()
        for d, cs in r.items():
            off = d.weekday() >= 5 or ("M" in cs and d.weekday() not in regular) or nominal.get(d, d) != d
            if off:
                if not near(d):
                    probs.append(f"teren {tag}: {d} ({DAN[d.weekday()]}) {''.join(sorted(cs))} izvan redovnog dana, "
                                 "a nema blagdana blizu")
                t["moved"].add(d)
        for m in range(start, 13):
            n = sum(1 for d, cs in r.items() if "M" in cs and d.month == m)
            if not 1 <= n <= 5 * len(regular) + 1:
                probs.append(f"teren {tag}: {MONTHS[m - 1]} {n} odvoza miješanog otpada")
        for code in "KPS":
            for m in range(start, 13):
                n = sum(1 for d, cs in r.items() if code in cs and d.month == m)
                if n > 5 or (code != "S" and n == 0):
                    probs.append(f"teren {tag}: {MONTHS[m - 1]} {n} odvoza ({CODES[code]})")
    res["terens"], res["rows"], res["start"], res["ambiguous"] = terens, rows, start, ambiguous
    return res


# ---------------------------------------------------------------- site, posts, zones

def discover(pages, year):
    """{jls: {url, page, modified}} of the newest page per municipality with a calendar PDF; skipped [(name, why)]."""
    names = {norm(n): n for n in JLS}
    found, skipped = {}, []
    for p in pages:
        title = html.unescape(p["title"]["rendered"])
        name = re.sub(r"^(grad|općina)\s+", "", title, flags=re.I).replace("Sv. ", "Sveti ")
        links = re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', p["content"]["rendered"], re.S)
        pdfs = [html.unescape(u) for u, inner in links if "Kalendar-odvoza" in inner]
        if not pdfs:
            continue
        jls = names.get(norm(name))
        if not jls:
            skipped.append((title, f"nije na popisu općina ({p['link']})"))
            continue
        cur = found.get(jls)
        if cur is None or p["modified"] > cur["modified"]:
            found[jls] = {"url": pdfs[0], "page": p["link"], "modified": p["modified"]}
    for jls, f in list(found.items()):
        if str(year) not in f["url"].rsplit("/", 1)[-1] and f"/{year}/" not in f["url"]:
            skipped.append((jls, f"na mull-trans.hr je samo stariji raspored, {f['url'].rsplit('/', 1)[-1]}"))
            del found[jls]
    for jls in JLS:
        if jls not in found and not any(s[0] == jls for s in skipped):
            skipped.append((jls, "nema stranice s kalendarom"))
    return found, skipped


def post_text(p):
    t = re.sub(r"<(?:br|/p|/h\d|/li)[^>]*>", "\n", p["content"]["rendered"])
    t = html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("\xa0", " ")
    return "\n".join(" ".join(line.split()) for line in t.splitlines() if line.strip())


def schedule_changes(posts, year):
    """Posts of the year -> changes [(jls, month, {code: [(date, teren weekday or None)]}, link, post date)]
    from "Izmjena rasporeda ..." posts, and other notices about missed or moved collections [(date, jls, title, link)].
    """
    changes, notices = [], []
    for p in posts:
        if not p["date"].startswith(str(year)):
            continue
        title = html.unescape(p["title"]["rendered"])
        text = post_text(p)
        jls = next((j for j in JLS if norm(j) in norm(title + " " + text[:300])), None)
        if "izmjen" not in norm(title) + norm(text[:200]) or not jls:
            if (jls and re.search(r"odvoz|sakupljanj|obavijest korisnicima", norm(title))
                    and re.search(r"odgod|nadoknad|neizvrs|nije bilo moguce|nece biti", norm(title + " " + text))):
                notices.append((p["date"][:10], jls, title, p["link"]))
            continue
        m = re.search(r"za (\w+) " + str(year), text)
        month = MONTHS.index(m.group(1).lower()) + 1 if m and m.group(1).lower() in MONTHS else None
        new, ok = defaultdict(list), month is not None
        lines = text.splitlines()
        for i, line in enumerate(lines):
            typed = re.match(r"(Miješani komunalni otpad|Selektivno prikupljen[ia] (papir|plastika))[^:]*:\s*(.*)",
                             line)
            if typed:  # "Miješani komunalni otpad (KO): 14. i 15. srpnja 2026. te 28. i 29. srpnja 2026."
                code = {"papir": "K", "plastika": "P"}.get(typed.group(2), "M")
                body = typed.group(3) or (lines[i + 1] if i + 1 < len(lines) else "")
                groups = re.findall(r"((?:\d{1,2}\.\s*(?:i\s+|,\s*)?)+)(\w+) " + str(year), body)
                for days, mon in groups:
                    if mon.lower() not in GENITIVE:
                        ok = False
                        continue
                    new[code] += [(date(year, GENITIVE.index(mon.lower()) + 1, int(d)), None)
                                  for d in re.findall(r"\d{1,2}", days)]
                ok = ok and bool(groups)
            wd = re.match(r"(\w+), (\d{1,2})\. (\w+) " + str(year) + r"\.\s*[–-]\s*područja na kojima se odvoz "
                          r"redovito obavlja (\w+)", line)
            if wd:  # "Ponedjeljak, 13. srpnja 2026. – područja na kojima se odvoz redovito obavlja ponedjeljkom"
                if wd.group(3).lower() not in GENITIVE:
                    ok = False
                    continue
                d = date(year, GENITIVE.index(wd.group(3).lower()) + 1, int(wd.group(2)))
                if not weekday_of(wd.group(1)) == weekday_of(wd.group(4)) == d.weekday():
                    ok = False
                new["M"].append((d, d.weekday()))
        if ok and new and all(d.month == month for v in new.values() for d, _ in v):
            changes.append((jls, month, dict(new), p["link"], date.fromisoformat(p["date"][:10])))
        else:
            notices.append((p["date"][:10], jls, title + " (nije primijenjeno: nije pročitano pouzdano)", p["link"]))
    return changes, notices


def apply_change(res, month, new, since):
    """Replace the dates of the given types in one month, from the post's date on; returns the replaced dates."""
    index = {t["wd"]: i for i, t in enumerate(res["terens"])}
    old = set()
    for code, items in new.items():
        for r in res["rows"]:
            for d in [d for d in r if d.month == month and d >= since and code in r[d]]:
                old.add((code, d))
                r[d].discard(code)
                if not r[d]:
                    del r[d]
        by_week = defaultdict(list)  # dates without a teren: one per teren weekday, else to every teren
        for d, wd in items:
            if wd is not None and wd in index:
                res["rows"][index[wd]][d].add(code)
            else:
                by_week[d.isocalendar()[:2]].append(d)
        for ds in by_week.values():
            if len(ds) == len(index) and sorted(d.weekday() for d in ds) == sorted(index):
                for d in ds:
                    res["rows"][index[d.weekday()]][d].add(code)
            else:
                for r in res["rows"]:
                    for d in ds:
                        r[d].add(code)
                if len(index) > 1 and len(ds) > 1:
                    res["ambiguous"].add(code)
    return old


def zone_entries(jls, res, src):
    """podaci zones of one municipality."""
    out = []
    multi = len(res["terens"]) > 1
    whole = "Cijeli grad" if jls in GRAD else "Cijela općina"
    for t, r in zip(res["terens"], res["rows"]):
        notes = []
        if multi:
            day = DAN[t["wd"]]
            label = f"Teren {t['name']}, {day}" if t["name"] else f"Teren {day}"
            if t["ulice"]:
                short = ", ".join(t["ulice"][:3]) + (" …" if len(t["ulice"]) > 3 else "")
                podrucje = f"{day.capitalize()} – {short}"
            else:
                podrucje = f"{label} (naselja po terenu nisu objavljena)"
                notes.append(f"PDF ne navodi naselja po terenima; svoj teren (dan odvoza miješanog otpada: "
                             f"{', '.join(DAN[x['wd']] for x in res['terens'])}) provjerite kod Mull-Transa "
                             f"({PHONE}, info@mull-trans.hr).")
        else:
            podrucje = whole if not t["ulice"] else ", ".join(t["ulice"][:3])
        if res["ambiguous"]:
            kinds = " i ".join(CODES[c] for c in "KPS" if c in res["ambiguous"])
            notes.append(f"{kinds.capitalize()}: PDF za {'cijeli grad' if jls in GRAD else 'cijelu općinu'} navodi "
                         "nekoliko uzastopnih dana; koji je dan za koji teren nije objavljeno, pa su navedeni svi.")
        notes += res["notes"]
        if res["mrd"]:
            ds = sorted(res["mrd"])
            notes.append("Mobilno reciklažno dvorište: " + ", ".join(f"{d.day}.{d.month}." for d in ds) +
                         " (lokacije i vrijeme u PDF-u).")
        if res["start"] > 1:
            notes.append(f"Objavljeni raspored počinje u mjesecu: {MONTHS[res['start'] - 1]} {res['year']}.")
        if jls == "Sveta Nedelja":
            notes.append("Grad Sveta Nedelja (Zagrebačka županija), ne Općina Sveta Nedelja u Istri.")
        if jls in REGISTRY:
            notes.append(f"U službenoj evidenciji davatelja usluge (stanje 5.10.2026.) za ovu općinu naveden je "
                         f"{REGISTRY[jls]}; Mull-Trans ipak objavljuje raspored za {res['year']}.")
        notes += res.get("changes", [])
        when = src.get("modified_pdf")
        when = f" (PDF izmijenjen {parsedate_to_datetime(when):%d.%m.%Y.})" if when else ""
        notes.append(f"Izvor: {src['url']}{when}")
        zone = {"jls": jls, "podrucje": podrucje, "ulice": t["ulice"], "napomena": " ".join(notes)}
        rows = [(d, "".join(sorted(cs)), d in t["moved"]) for d, cs in r.items() if cs]
        zone["raw"] = {str(res["year"]): podaci.month_lines(rows)}
        out.append(zone)
    return out


def wp_all(get, url):
    """All items of a WordPress REST list (100 per page)."""
    items, n = [], 1
    while True:
        batch = json.loads(get(url + (f"&page={n}" if n > 1 else "")))
        items += batch
        if len(batch) < 100:
            return items
        n += 1


def run(year, get):
    """Everything but writing: (data, problems). get(url, dest=None, meta=None) downloads."""
    problems = []
    pages = wp_all(get, PAGES)
    found, skipped = discover(pages, year)
    posts = wp_all(get, POSTS.format(after=f"{year}-01-01T00:00:00"))
    changes, notices = schedule_changes(posts, year)
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for jls in JLS:
            if jls not in found:
                continue
            src = found[jls]
            meta = {}
            path = Path(tmp) / f"{norm(jls).replace(' ', '-')}.pdf"
            get(src["url"], path, meta=meta)
            res = read_pdf(path, jls, year)
            res["year"], res["src"] = year, src
            src["modified_pdf"] = meta.get("Last-Modified")
            print(f"{jls}: {len(res.get('terens', []))} teren(a), od {MONTHS[res.get('start', 1) - 1]}, "
                  f"{src['url'].rsplit('/', 1)[-1]}")
            for w in dict.fromkeys(res["warnings"]):
                print(f"   upozorenje: {w}")
            for p in res["problems"]:
                print(f"   PROBLEM {p}")
                problems.append(f"{jls}: {p}")
            results[jls] = res
    for jls, month, new, link, since in changes:
        if jls in results and not results[jls]["problems"]:
            old = apply_change(results[jls], month, new, since)
            what = "; ".join(f"{CODES[c]} " + ", ".join(f"{d.day}.{d.month}." for d, _ in v) + " umjesto " +
                             (", ".join(f"{d.day}.{d.month}." for x, d in sorted(old) if x == c) or "–")
                             for c, v in new.items())
            results[jls].setdefault("changes", []).append(
                f"Izmjena rasporeda za {MONTHS[month - 1]} (obavijest {since.day}.{since.month}.{since.year}., "
                f"{link}): {what}")
            print(f"{jls}: primijenjena izmjena za {MONTHS[month - 1]}: {what}")
    for jls, why in skipped:
        print(f"Izostavljeno: {jls} – {why}")
    for d, jls, title, link in notices:
        print(f"Obavijest {d} ({jls}): {title} – {link}")
    zones = {}
    for jls in JLS:
        if jls in results and not results[jls]["problems"]:
            for z in zone_entries(jls, results[jls], results[jls]["src"]):
                zones[str(len(zones) + 1)] = z
    missing = [f"{j} ({why})" for j, why in skipped if j in JLS]
    data = {
        "davatelj": "Mull-Trans d.o.o. (i Eko-Flor Plus d.o.o.)",
        "web": SITE,
        "izvor": SITE + "/podrucje/",
        "zupanija": "Zagrebačka",
        "jls": [j for j in JLS if j in results and not results[j]["problems"]],
        "napomene": NAPOMENE + ([f"Nije uključeno: {'; '.join(missing)}."] if missing else []),
        "bioNapomena": "Biootpad se odvozi samo gdje je naveden u rasporedu; drugdje ga korisnici kompostiraju "
                       "ili predaju u zasebnom spremniku prema ugovoru.",
        "zone": zones,
    }
    return data, problems, results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    data, problems, results = run(year, fetch)
    if problems or not data["zone"]:
        sys.exit("Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep months before a calendar's window (Jastrebarsko, Krašić) and other years
        old = podaci.load(path)
        for z, zone in data["zone"].items():
            prev = old.get("zone", {}).get(z)
            if prev and prev["jls"] == zone["jls"] and prev["podrucje"] == zone["podrucje"]:
                start = results[zone["jls"]]["start"]
                raw = {**prev["raw"], **zone["raw"]}
                keep = [r for r in podaci.iter_dates(prev, year) if r[0].month < start]
                if keep:
                    raw[str(year)] = podaci.month_lines(keep + list(podaci.iter_dates(zone, year)))
                zone["raw"] = raw
    total = Counter(c for zone in data["zone"].values() for _, cs, _ in podaci.iter_dates(zone, year) for c in cs)
    print(f"Zona: {len(data['zone'])}, JLS: {len(data['jls'])}; odvoza {year}: "
          + ", ".join(f"{c} {n}" for c, n in sorted(total.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
