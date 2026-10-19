"""Read colour-coded year calendars from PDFs (date in the text, waste type as the cell's fill colour).

Used by providers that publish a 12-month grid: Makarski komunalac, Zeleno i modro (Kaštela), ...
Each month block is found from its name and the weekday row under it (P U S Č P S N or PON UTO ...).
Every day number must sit in its own weekday column, every day of the year must appear exactly once,
and an unknown colour under a day is a problem, so a changed layout fails loudly instead of giving
wrong dates.
"""
import calendar
from collections import Counter
from datetime import date

MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ",
          "KOLOVOZ", "RUJAN", "LISTOPAD", "STUDENI", "PROSINAC"]
SHORT = ["P", "U", "S", "Č", "P", "S", "N"]
LONG = ["PON", "UTO", "SRI", "ČET", "PET", "SUB", "NED"]


def colour(obj):
    c = obj.get("non_stroking_color")
    if c is None:
        return None
    if isinstance(c, (int, float)):
        c = (c, c, c)
    c = tuple(round(float(v), 3) for v in c)
    return c if len(c) == 3 else None


def lookup(col, palette, tol=0.06):
    """Code for a fill colour, or "?" if it is not in the palette (within tol per channel)."""
    for rgb, code in palette.items():
        if all(abs(a - b) <= tol for a, b in zip(col, rgb)):
            return code
    return "?"


def weekday_rows(words):
    """Groups of 7 weekday-heading words (P..N or PON..NED) that sit on one line, left to right."""
    heads = [w for w in words if w["text"].upper() in set(SHORT) | set(LONG)]
    groups = []
    for row_top in sorted({round(w["top"]) for w in heads}):
        row = sorted((w for w in heads if abs(w["top"] - row_top) < 3), key=lambda w: w["x0"])
        texts = [w["text"].upper() for w in row]
        i = 0
        while i + 7 <= len(row):
            if texts[i:i + 7] in (SHORT, LONG):
                groups.append(row[i:i + 7])
                i += 7
            else:
                i += 1
    return groups


def read_page(page, year, palette, ignore=((1.0, 1.0, 1.0),), bbox=None):
    """({date: code}, problems) for the day cells of one page.

    palette: {(r, g, b): code}; code None means "not a collection" (e.g. red holiday cells).
    ignore: fills that mean "nothing that day" (white, grey headers...).
    bbox: optional (x0, top, x1, bottom) to read only part of the page.
    """
    if bbox:
        page = page.crop(bbox)
    words = page.extract_words()
    problems = []
    months = {w["text"].upper(): w for w in words if w["text"].upper() in MONTHS}
    if len(months) != 12:
        return {}, [f"found month headings {sorted(months)}, expected all 12"]
    rows = weekday_rows(words)
    blocks = []  # (month, weekday heading words)
    for name, h in months.items():
        hx = (h["x0"] + h["x1"]) / 2
        below = [g for g in rows if 0 <= g[0]["top"] - h["bottom"] < 40]
        if not below:
            problems.append(f"{name}: no weekday row under the heading")
            continue
        g = min(below, key=lambda g: (g[0]["top"], abs((g[0]["x0"] + g[-1]["x1"]) / 2 - hx)))
        same_line = [x for x in below if abs(x[0]["top"] - g[0]["top"]) < 3]
        g = min(same_line, key=lambda g: abs((g[0]["x0"] + g[-1]["x1"]) / 2 - hx))
        blocks.append((MONTHS.index(name) + 1, g))
    if problems:
        return {}, problems
    shapes = [s for s in page.rects + page.curves if s.get("fill") and colour(s)]
    found, seen = {}, Counter()
    for w in words:
        if not w["text"].isdigit():
            continue
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        inside = [(m, g) for m, g in blocks
                  if g[0]["bottom"] <= w["top"] and g[0]["x0"] - 10 <= cx <= g[-1]["x1"] + 10]
        if not inside:
            continue
        month, g = max(inside, key=lambda mg: mg[1][0]["top"])
        day = int(w["text"])
        if not 1 <= day <= calendar.monthrange(year, month)[1]:
            problems.append(f"{year}-{month:02d}: impossible day {day}")
            continue
        d = date(year, month, day)
        seen[d] += 1
        column = min(range(7), key=lambda i: abs((g[i]["x0"] + g[i]["x1"]) / 2 - cx))
        if column != d.weekday():
            problems.append(f"{d}: in column {LONG[column]}, but it is a {LONG[d.weekday()]}")
        under = [s for s in shapes if s["x0"] - 0.5 <= cx <= s["x1"] + 0.5 and s["top"] - 0.5 <= cy <= s["bottom"] + 0.5]
        if not under:
            continue
        col = colour(min(under, key=lambda s: (s["x1"] - s["x0"]) * (s["bottom"] - s["top"])))
        if col in ignore:
            continue
        code = lookup(col, palette)
        if code == "?":
            problems.append(f"{d}: unknown colour {col}")
        elif code:
            found[d] = code
    for m in range(1, 13):
        for day in range(1, calendar.monthrange(year, m)[1] + 1):
            if seen[date(year, m, day)] != 1:
                problems.append(f"{date(year, m, day)} appears {seen[date(year, m, day)]} times in the grid")
    return found, problems
