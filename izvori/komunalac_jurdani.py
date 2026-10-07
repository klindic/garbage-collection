"""Opatija, Matulji, Lovran, Mošćenička Draga: Komunalac d.o.o. Jurdani (komunalac-opatija.hr), colour calendars.

    python3 -m izvori.komunalac_jurdani [--year 2026]

The pages "Komunalni otpad" and the home page link one year calendar PDF per town plus two calendars of
extra recyclables rounds (part of Opatija, Lovran in summer). Every calendar is a 12-month grid with the
days of the neighbouring months greyed; each day is read from its row and column (the printed number
must agree), its fill colour says which area is collected that day (legend under the grid) and a thick
black frame marks the recyclables round, every other week; the odd/even ISO weeks stated in the text
are checked against the frames. Mixed waste goes on every day of the area's colour; biowaste follows
the legend's biowaste table (Opatija, Matulji: which coloured days in 1.10.-30.4. and 1.5.-30.9.) or the
printed text (Lovran, Mošćenička Draga); where the text says so, glass goes with the first recyclables
round of the month. Holidays are drawn in: a red cell is a non-working day and an arrow points to the
substitute day, which carries the colour of the moved round, so a day whose colour belongs to another
weekday is marked as moved (and a red day of that weekday must lie in the same week).
The extra recyclables calendar of Opatija (even weeks, same colours) gives five more zones (its streets:
the area's schedule plus the extra days); Lovran's (Tuesdays in summer, the whole area "ispod groblja")
adds its days to the Tuesday area and to the Thursday exceptions (Kali, Medveja, Zaheji, 43. Ist. Div.).
"""
import argparse
import calendar
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import MONTHS, colour

SLUG = "komunalac-jurdani"
SITE = "https://www.komunalac-opatija.hr"
PAGES = [SITE + "/komunalni-otpad/", SITE + "/"]
TOWNS = {"Opatija": "Opatija", "Matulji": "Matulji", "Lovran": "Lovran", "M-Draga": "Mošćenička Draga"}
HEADS = [["po", "ut", "sr", "če", "pe", "su", "ne"], ["mo", "tu", "we", "th", "fr", "sa", "su"],
         ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DAN_I = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
YELLOW, BLUE, ORANGE, LGREEN = (1.0, 1.0, 0.0), (0.0, 0.69, 0.941), (1.0, 0.753, 0.0), (0.573, 0.816, 0.314)
RED, WHITE = (1.0, 0.0, 0.0), (1.0, 1.0, 1.0)
KNOWN = {YELLOW, BLUE, ORANGE, LGREEN, RED, (0.0, 0.69, 0.314), (0.886, 0.635, 0.58), (0.867, 0.459, 0.788),
         (0.749, 0.749, 0.749), (0.894, 0.596, 0.882)}
SUMMER = (5, 6, 7, 8, 9)  # biowaste twice a week 1.5.-30.9.
GLASS = "Odvoz staklene ambalaže je prvi tjedan u mjesecu prilikom odvoza reciklabilnog otpada"
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Jurdani",
    "web": SITE,
    "zupanija": "Primorsko-goranska",
    "jls": list(TOWNS.values()),
    "nazivi": {"P": "Reciklabilni otpad (papir, karton, plastika, metal)",
               "S": "Staklena ambalaža (prozirne vrećice pored spremnika)"},
    "bioNapomena": "Biootpad se od 1.10. do 30.4. odvozi jednom, a od 1.5. do 30.9. dvaput tjedno.",
}


# ---- reading the calendar grid (also used by other scripts for 6-row month grids) ----

def head_rows(words):
    """Groups of 7 weekday headings (po..ne, Mo..Su, PON..NED) on one line, left to right."""
    heads = [w for w in words if any(w["text"].lower() in h for h in HEADS)]
    groups = []
    for top in sorted({round(w["top"]) for w in heads}):
        row = sorted((w for w in heads if abs(w["top"] - top) < 2), key=lambda w: w["x0"])
        texts, i = [w["text"].lower() for w in row], 0
        while i + 7 <= len(row):
            if texts[i:i + 7] in HEADS:
                if not any(abs(g[0]["top"] - row[i]["top"]) < 1 and abs(g[0]["x0"] - row[i]["x0"]) < 1 for g in groups):
                    groups.append(row[i:i + 7])
                i += 7
            else:
                i += 1
    return groups


def mreza(page, year, strict=True):
    """({date: cell}, problems) for a page with 12 month blocks (name, weekday row, up to 6 week rows).

    The date of a number comes from its row and column (week rows start on the Monday on or before the
    1st); the printed number must agree, so a changed layout fails. Days of the neighbouring months are
    skipped. cell = {"x", "y": centre of the number, "fill": colour of the topmost filled rect under it,
    "rect": that rect, "font": text colour, "printed": the number as printed}. With strict=False a
    misprinted number is kept (cell["printed"] != day) for the caller to judge instead of being a problem.
    """
    words = page.extract_words(extra_attrs=["non_stroking_color"])
    problems, months = [], {}
    for w in words:
        if w["text"].upper() in MONTHS:
            months.setdefault(MONTHS.index(w["text"].upper()) + 1, w)
    if len(months) != 12:
        return {}, [f"nazivi mjeseci: našao {sorted(months)}"]
    rows = head_rows(words)
    fills = [r for r in page.rects if r.get("fill") and colour(r)]
    cells = {}
    for m, h in sorted(months.items()):
        hx = (h["x0"] + h["x1"]) / 2
        below = [g for g in rows if 0 <= g[0]["top"] - h["bottom"] < 40]
        if not below:
            problems.append(f"{MONTHS[m - 1]}: nema reda s danima u tjednu")
            continue
        g = min(below, key=lambda g: (round(g[0]["top"]), abs((g[0]["x0"] + g[-1]["x1"]) / 2 - hx)))
        cols = [(w["x0"] + w["x1"]) / 2 for w in g]
        half = (cols[1] - cols[0]) / 2
        nums = [w for w in words if w["text"].isdigit() and w["top"] > g[0]["bottom"]
                and cols[0] - half <= (w["x0"] + w["x1"]) / 2 <= cols[-1] + half]
        lines = []  # week rows: lines of numbers right under the heading, up to a big gap
        for t in sorted({round(w["top"], 1) for w in nums}):
            if lines and t - lines[-1][-1] < 3:
                lines[-1].append(t)
            elif not lines and t - g[0]["bottom"] < 30 or lines and t - lines[-1][0] < 30:
                lines.append([t])
            else:
                break
        start = date(year, m, 1) - timedelta(days=date(year, m, 1).weekday())
        for i, line in enumerate(lines[:6]):
            for w in (w for w in nums if line[0] - 0.05 <= round(w["top"], 1) <= line[-1] + 0.05):
                cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
                d = start + timedelta(days=7 * i + min(range(7), key=lambda c: abs(cols[c] - cx)))
                if int(w["text"]) != d.day and (strict or d.month != m):
                    problems.append(f"{MONTHS[m - 1]}: na mjestu dana {d:%d.%m.} piše {w['text']}")
                    continue
                if d.month != m:
                    continue
                if d in cells:
                    problems.append(f"{d:%d.%m.} dvaput")
                under = [r for r in fills if r["x0"] - 0.5 <= cx <= r["x1"] + 0.5 and r["top"] - 0.5 <= cy <= r["bottom"] + 0.5]
                cells[d] = {"x": cx, "y": cy, "fill": colour(under[-1]) if under else None,
                            "rect": under[-1] if under else None, "printed": int(w["text"]),
                            "font": tuple(round(float(v), 3) for v in w["non_stroking_color"])}
    for m in range(1, 13):
        missing = [day for day in range(1, calendar.monthrange(year, m)[1] + 1) if date(year, m, day) not in cells]
        if missing:
            problems.append(f"{MONTHS[m - 1]}: nema dana {missing}")
    return cells, problems


def uokviren(x, y, lines, min_width=1.0):
    """True if the point lies inside a rectangle drawn with thick lines (frames may span several cells)."""
    thick = [l for l in lines if l["linewidth"] > min_width]
    vert = [l["x0"] for l in thick if l["x1"] - l["x0"] < 0.5 and l["top"] - 1 <= y <= l["bottom"] + 1]
    left, right = max((v for v in vert if v <= x), default=None), min((v for v in vert if v >= x), default=None)
    if left is None or right is None:
        return False

    def covered(side):
        reach = left
        for a, b in sorted((l["x0"], l["x1"]) for l in thick
                           if l["bottom"] - l["top"] < 0.5 and 2 < side * (y - l["top"]) < 10):
            if a <= reach + 1.5:
                reach = max(reach, b)
        return reach >= right - 1.5
    return covered(1) and covered(-1)


# ---- legend ----

def legend(page, cells):
    """[(colour, text, {"zima": [(colour, day name)], "ljeto": [...]})] for the swatches under the grid."""
    bottom = max(c["y"] for c in cells.values())
    words = [w for w in page.extract_words() if w["top"] > bottom + 5]
    first = next((w for w in words if w["text"] == "01.10."), None)  # biowaste table: 01.10. - 30.04. | 01.05. - 30.09.
    heads = {w["text"]: w for w in words if w["text"] in ("01.10.", "01.05.") and first and abs(w["top"] - first["top"]) < 2}
    split = (heads["01.10."]["x1"] + heads["01.05."]["x0"]) / 2 if len(heads) == 2 else None
    rects = [r for r in page.rects if r.get("fill") and colour(r) and r["top"] > bottom + 5]
    table_x = min((r["x0"] for r in rects if split and r["x1"] > first["x0"] and r["x1"] - r["x0"] >= 30),
                  default=page.width)
    out = []
    for s in sorted((r for r in rects if r["x1"] - r["x0"] < 30 and r["x0"] < table_x and colour(r) != WHITE),
                    key=lambda r: r["top"]):
        inside = [w for w in words if s["top"] - 1 <= (w["top"] + w["bottom"]) / 2 <= s["bottom"] + 1
                  and w["x0"] >= s["x1"] - 1 and w["x1"] < table_x + 1]
        text = " ".join(w["text"] for w in sorted(inside, key=lambda w: (round(w["top"]), w["x0"])))
        bio = {"zima": [], "ljeto": []}
        for r in (r for r in rects if r["x0"] >= table_x and s["top"] - 1 <= (r["top"] + r["bottom"]) / 2 <= s["bottom"] + 1):
            for side in bio:
                chars = [c for c in page.chars if r["x0"] <= (c["x0"] + c["x1"]) / 2 <= r["x1"]
                         and r["top"] <= (c["top"] + c["bottom"]) / 2 <= r["bottom"]
                         and ((c["x0"] + c["x1"]) / 2 < split) == (side == "zima")]
                name = "".join(c["text"] for c in sorted(chars, key=lambda c: c["x0"])).strip()
                if name:
                    bio[side].append((colour(r), name))
        text = " ".join(text.split())
        out.append((colour(s), text + ")" * (text.count("(") - text.count(")")), bio))
    return out


def split_list(text):
    """'A, B (x, y), C 1 a,b, D i E' -> ['A', 'B (x, y)', 'C 1 a,b', 'D', 'E'] (commas in brackets stay)."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth <= 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    out = []
    for p in (" ".join(p.split()).strip(" .") for p in parts + [cur]):
        if out and re.fullmatch(r"[a-z]", p):  # house number letters: "ul. 1 a,b,c,d"
            out[-1] += "," + p
        elif p:
            halves = p.split(" i ")
            out += halves if len(halves) == 2 and all(re.match(r"[A-ZČĆŠŽĐ0-9]", h) for h in halves) else [p]
    return list(dict.fromkeys(out))


def short(items, n=3):
    return ", ".join(items[:n]) + (" …" if len(items) > n else "")


# ---- one calendar ----

class Kalendar:
    """Cells, frames and regular weekday of every colour of one calendar PDF."""

    def __init__(self, page, year, name, problems):
        self.name, self.town = name, name.replace("extra-", "")
        self.cells, probs = mreza(page, year)
        problems += [f"{name}: {p}" for p in probs]
        self.text = " ".join((page.extract_text() or "").split())
        if not re.search(rf"\b{year}\b", self.text):
            problems.append(f"{name}: u kalendaru nema godine {year}")
        for d, c in sorted(self.cells.items()):
            c["frame"] = c["fill"] not in (None, WHITE) and uokviren(c["x"], c["y"], page.lines)
            if c["fill"] not in KNOWN | {None, WHITE}:
                problems.append(f"{name}: nepoznata boja {c['fill']} na {d:%d.%m.}")
        days = defaultdict(Counter)
        for d, c in self.cells.items():
            days[c["fill"]][d.weekday()] += 1
        self.weekday = {col: cnt.most_common(1)[0][0] for col, cnt in days.items() if col not in (None, WHITE, RED)}
        self.red = {d for d, c in self.cells.items() if c["fill"] == RED}
        self.legend = legend(page, self.cells) if self.cells else []

    def days(self, colours, framed=False):
        """[(date, moved)] of cells with one of the colours (only framed ones if asked)."""
        out = []
        for d, c in sorted(self.cells.items()):
            if c["fill"] in colours and (c["frame"] or not framed):
                out.append((d, d.weekday() != self.weekday[c["fill"]]))
        return out

    def check_moves(self, problems):
        """A colour off its weekday must replace a red (non-working) day of that weekday in the same week."""
        for d, c in sorted(self.cells.items()):
            wd = self.weekday.get(c["fill"])
            if wd is not None and d.weekday() != wd:
                monday = d - timedelta(days=d.weekday())
                if monday + timedelta(days=wd) not in self.red:
                    problems.append(f"{self.name}: {d:%d.%m.} ima boju za {DAN[wd]}, a tog tjedna nema neradnog dana")

    def check_frames(self, colours, parity, problems):
        """Frames only on area colours, only in weeks of the stated parity, and in every such week."""
        weeks = defaultdict(set)
        for d, c in self.cells.items():
            if c["frame"] and c["fill"] != RED:
                if c["fill"] not in colours:
                    problems.append(f"{self.name}: okvir na {d:%d.%m.} (boja {c['fill']})")
                elif d.isocalendar()[1] % 2 != parity:
                    problems.append(f"{self.name}: okvir na {d:%d.%m.}, a reciklabilni je u {'ne' if parity else ''}parnim tjednima")
                weeks[c["fill"]].add(d.isocalendar()[:2])
        for d, c in self.cells.items():
            if c["fill"] in colours and d.isocalendar()[1] % 2 == parity and d.isocalendar()[:2] not in weeks[c["fill"]]:
                problems.append(f"{self.name}: {d:%d.%m.} bez okvira, a tjedan je {'ne' if parity else ''}paran")


def bio_text(rule):
    """{"zima": [weekday], "ljeto": [weekday]} -> 'Biootpad: 1.10.–30.4. ponedjeljkom, 1.5.–30.9. ...'."""
    def days(ws):
        return " i ".join(DAN_I[w] for w in sorted(set(ws)))
    if rule["zima"] == rule["ljeto"]:
        return f"Biootpad {days(rule['zima'])}."
    return f"Biootpad: od 1.10. do 30.4. {days(rule['zima'])}, od 1.5. do 30.9. {days(rule['ljeto'])}."


def zone_rows(kal, m_cols, p_cols, bio, glass, extra=()):
    """[(date, codes, moved)] of one zone. bio: {"zima": colours, "ljeto": colours}."""
    rows = defaultdict(lambda: ["", False])
    for d, moved in kal.days(m_cols):
        rows[d][0] += "M"
        rows[d][1] |= moved
    regular = kal.days(p_cols, framed=True)
    for d, moved in regular + list(extra):  # extra rounds may fall on a regular recyclables day
        rows[d][0] += "" if "P" in rows[d][0] else "P"
        rows[d][1] |= moved
    for d, moved in kal.days(set(bio["zima"]) | set(bio["ljeto"])):
        if kal.cells[d]["fill"] in bio["ljeto" if d.month in SUMMER else "zima"]:
            rows[d][0] += "B"
            rows[d][1] |= moved
    if glass:
        firsts = {}
        for d, _ in regular:
            firsts.setdefault(d.month, d)
        for d in firsts.values():
            rows[d][0] += "S"
    return [(d, codes, moved) for d, (codes, moved) in sorted(rows.items())]


def check_zone(name, rows, year, problems, p_range=(1, 3)):
    per = defaultdict(Counter)
    for d, codes, _ in rows:
        if d.year != year:
            problems.append(f"{name}: datum {d} izvan godine")
        if len(set(codes)) != len(codes):
            problems.append(f"{name}: {d:%d.%m.} dvaput isti otpad {codes}")
        for c in codes:
            per[d.month][c] += 1
    for m in range(1, 13):
        n = per[m]
        if not 4 <= n["M"] <= 5:
            problems.append(f"{name}: {MONTHS[m - 1].lower()} miješani {n['M']} puta")
        if not p_range[0] <= n["P"] <= p_range[1]:
            problems.append(f"{name}: {MONTHS[m - 1].lower()} reciklabilni {n['P']} puta")
        if not (8 if m in SUMMER and n["B"] > 5 else 4) <= n["B"] <= 10:
            problems.append(f"{name}: {MONTHS[m - 1].lower()} biootpad {n['B']} puta")


# ---- towns ----

def town_table(kal, year, glass, problems):
    """Opatija, Matulji: one zone per legend row with a biowaste table; [(zone dict, rows)]."""
    out = []
    for col, text, bio in kal.legend:
        if not bio["zima"]:
            continue
        rule = {}
        for side, items in bio.items():
            rule[side] = []
            for c, name in items:
                wd = kal.weekday.get(c)
                if wd is None or DAN[wd] != name.lower():
                    problems.append(f"{kal.name}: u tablici biootpada '{name}' na boji dana {DAN[wd] if wd is not None else '?'}")
                rule[side].append(c)
        if col not in kal.weekday:
            problems.append(f"{kal.name}: boja područja '{text}' nije u kalendaru")
            continue
        wd = kal.weekday[col]
        rows = zone_rows(kal, {col}, {col}, rule, glass)
        days = {s: [kal.weekday[c] for c in cs] for s, cs in rule.items()}
        ulice = split_list(text)
        out.append(({"jls": TOWNS[kal.town], "podrucje": f"{DAN[wd].capitalize()} – {text}", "opis": text,
                     "ulice": ulice, "napomena": f"Miješani i reciklabilni otpad {DAN_I[wd]}. " + bio_text(days)},
                    rows, col))
    if len(out) != 5:
        problems.append(f"{kal.name}: {len(out)} područja u legendi umjesto 5")
    return out


def extra_zones(extra, kal, base, problems):
    """Opatija: areas of the extra recyclables calendar = the base zone of the same colour + extra days."""
    out = []
    for col, text, _ in extra.legend:
        if col == RED:
            continue
        match = [b for b in base if b[2] == col]
        if len(match) != 1 or extra.weekday.get(col) != kal.weekday.get(col):
            problems.append(f"{extra.name}: boja '{text[:40]}' ne odgovara području iz glavnog kalendara")
            continue
        zone, rows, _ = match[0]
        zone["napomena"] += " Dio ulica ovog područja ima i izvanredni odvoz reciklabilnog otpada (zasebna zona)."
        days = extra.days({col})
        regular = {d for d, c, _ in rows if "P" in c}
        for d, _ in days:
            if d in regular:
                problems.append(f"{extra.name}: {d:%d.%m.} je već redovni odvoz reciklabilnog otpada")
        merged = defaultdict(lambda: ["", False])
        for d, codes, moved in rows + [(d, "P", m) for d, m in days]:
            merged[d][0] += codes
            merged[d][1] |= moved
        ulice = split_list(text)
        wd = extra.weekday[col]
        out.append(({"jls": zone["jls"], "podrucje": f"{DAN[wd].capitalize()}, reciklabilni svaki tjedan – {short(ulice)}",
                     "opis": text, "ulice": ulice,
                     "napomena": zone["napomena"].rsplit(" Dio ulica", 1)[0] + " Uz redovni odvoz reciklabilnog otpada u neparnim tjednima i "
                                 "izvanredni odvoz u parnim tjednima (Kalendar izvanrednog odvoza reciklabilnog otpada)."},
                    [(d, c, m) for d, (c, m) in sorted(merged.items())], (3, 6)))
    if len(out) != 5:
        problems.append(f"{extra.name}: {len(out)} područja umjesto 5")
    return out


def lovran(kal, extra, problems):
    rows = {col: text for col, text, _ in kal.legend}
    expect = {YELLOW: "Lovran (sve ulice do zaključno lovranskog groblja)", BLUE: "Liganj",
              ORANGE: "Odvoz biootpada za cijelo područje Općine Lovran + MIJEŠANI OTPAD", LGREEN: "Dodatni, drugi odvoz biootpada"}
    for col, start in expect.items():
        if not rows.get(col, "").startswith(start):
            problems.append(f"{kal.name}: legenda se promijenila ({start!r})")
            return []
    if "četvrtkom" not in kal.text or "ponedjeljkom i četvrtkom" not in kal.text:
        problems.append(f"{kal.name}: tekst o biootpadu se promijenio")
    bio = {"zima": [ORANGE], "ljeto": [ORANGE, LGREEN]}
    glass = GLASS in kal.text
    exc = split_list(rows[ORANGE].split("MIJEŠANI OTPAD", 1)[1])
    extra_days, extra_streets = [], []
    if extra:
        extra_days = extra.days({YELLOW})
        text = next((t for c, t, _ in extra.legend if c == YELLOW), "")
        streets = extra.text.split("Ulice:", 1)[1].split("Odlaganje", 1)[0] if "Ulice:" in extra.text else ""
        extra_streets = [s for s in split_list(text) + split_list(streets)
                         if not any(s.startswith(e) for e in exc) and not s.startswith("Lovran")]
        for d, _ in extra_days:
            if d.weekday() != kal.weekday[YELLOW]:
                problems.append(f"{extra.name}: {d:%d.%m.} nije {DAN[kal.weekday[YELLOW]]}")
    area1 = rows[YELLOW].split(", Medveja")[0]
    note_extra = (" Od srpnja do listopada i izvanredni odvoz reciklabilnog otpada utorkom (Kalendar izvanrednog "
                  "prikupljanja reciklabilnog otpada, područje „ispod groblja”).") if extra_days else ""
    glass_note = " Staklena ambalaža uz prvi odvoz reciklabilnog otpada u mjesecu." if glass else ""
    out = [
        ({"jls": "Lovran", "podrucje": "Utorak – Lovran (do lovranskog groblja)", "opis": rows[YELLOW],
          "ulice": [area1] + extra_streets,
          "napomena": "Miješani i reciklabilni otpad utorkom, biootpad četvrtkom (od 1.5. do 30.9. i ponedjeljkom)."
                      + glass_note + note_extra + " Medveja, Kali, Zaheji i 43. Istarske divizije kod Kasarne: vidi zasebnu zonu."},
         zone_rows(kal, {YELLOW}, {YELLOW}, bio, glass, extra_days), (1, 6) if extra_days else (1, 3)),
        ({"jls": "Lovran", "podrucje": "Petak – Liganj, Tuliševica, Lovranska Draga (iznad groblja)", "opis": rows[BLUE],
          "ulice": split_list(rows[BLUE]),
          "napomena": "Miješani i reciklabilni otpad petkom, biootpad četvrtkom (od 1.5. do 30.9. i ponedjeljkom)." + glass_note},
         zone_rows(kal, {BLUE}, {BLUE}, bio, glass), (1, 3)),
        ({"jls": "Lovran", "podrucje": "Četvrtak – Kali, Medveja, Zaheji, 43. Ist. Div. kod Kasarne", "opis": rows[ORANGE],
          "ulice": exc,
          "napomena": "Miješani otpad i biootpad četvrtkom (biootpad od 1.5. do 30.9. i ponedjeljkom); reciklabilni otpad "
                      "utorkom kao područje Lovran (do groblja) i Medveja." + glass_note + note_extra},
         zone_rows(kal, {ORANGE}, {YELLOW}, bio, glass, extra_days), (1, 6) if extra_days else (1, 3)),
    ]
    return out


def draga(kal, problems):
    rows = {col: text for col, text, _ in kal.legend}
    if not rows.get(YELLOW, "").startswith("Dan redovnog prikupljanja otpada za sve frakcije") or \
            not rows.get(LGREEN, "").startswith("Dodatni, drugi odvoz biootpada"):
        problems.append(f"{kal.name}: legenda se promijenila")
        return []
    glass = GLASS in kal.text
    return [({"jls": "Mošćenička Draga", "podrucje": "Srijeda – cijelo područje Općine Mošćenička Draga",
              "opis": rows[YELLOW], "ulice": ["Mošćenička Draga"],
              "napomena": "Svi otpadi srijedom; biootpad od 1.5. do 30.9. i subotom."
                          + (" Staklena ambalaža uz prvi odvoz reciklabilnog otpada u mjesecu." if glass else "")},
             zone_rows(kal, {YELLOW}, {YELLOW}, {"zima": [YELLOW], "ljeto": [YELLOW, LGREEN]}, glass), (1, 3))]


def links(year):
    """{"Opatija": url, ..., "extra-Opatija": url, ...} from the provider's pages."""
    found = {}
    for page in PAGES:
        html = fetch(page).decode("utf-8", "replace")
        for url in re.findall(r'href="([^"]+\.pdf)"', html):
            name = url.rsplit("/", 1)[1]
            m = re.fullmatch(rf"Kalendar-odvoza-(Opatija|Matulji|Lovran|M-Draga)-{year}\.pdf", name)
            e = re.fullmatch(rf"Kalendar-odvoza-izvanrednog-reciklabilnog(?:-otpada)?-(\w+)-{year}\.pdf", name)
            if m:
                found[m.group(1)] = url
            elif e:
                found["extra-" + e.group(1)] = url
            elif re.fullmatch(rf"Raspored-postave-MRD-Opatija-{year}\.pdf", name):
                found["mrd"] = url
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    urls = links(year)
    missing = [t for t in TOWNS if t not in urls]
    if missing:
        sys.exit(f"Nema kalendara za {year}: {', '.join(missing)} (stranice {', '.join(PAGES)})")
    problems, kal = [], {}
    with tempfile.TemporaryDirectory() as tmp:
        for key in list(TOWNS) + [k for k in urls if k.startswith("extra-")]:
            path = Path(tmp) / f"{key}.pdf"
            fetch(urls[key], path)
            print(f"{key}: {urls[key]}")
            with pdfplumber.open(path) as pdf:
                if len(pdf.pages) != 1:
                    problems.append(f"{key}: {len(pdf.pages)} stranica")
                kal[key] = Kalendar(pdf.pages[0], year, key, problems)
    zones = []  # (zone, rows, (min, max) recyclables a month)
    for key in TOWNS:
        k = kal[key]
        k.check_moves(problems)
        m = re.search(r"na (neparne|parne) tjedne", k.text)
        if not m:
            problems.append(f"{key}: nema rečenice o parnim/neparnim tjednima")
            continue
        parity = 1 if m.group(1) == "neparne" else 0
        print(f"{key}: reciklabilni otpad na {m.group(1)} tjedne, staklo {'da' if GLASS in k.text else 'ne'}")
        if key in ("Opatija", "Matulji"):
            base = town_table(k, year, GLASS in k.text, problems)
            k.check_frames({c for _, _, c in base}, parity, problems)
            zones += [(z, r, (1, 3)) for z, r, _ in base]
            e = kal.get("extra-" + key)
            if e:
                e.check_moves(problems)
                for d, c in e.cells.items():
                    if c["fill"] not in (None, WHITE, RED) and d.isocalendar()[1] % 2 == parity:
                        problems.append(f"{e.name}: {d:%d.%m.} u tjednu redovnog odvoza")
                zones += extra_zones(e, k, base, problems)
        elif key == "Lovran":
            k.check_frames({YELLOW, BLUE}, parity, problems)
            zones += lovran(k, kal.get("extra-Lovran"), problems)
        else:
            k.check_frames({YELLOW}, parity, problems)
            zones += draga(k, problems)
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": PAGES[0], "napomene": napomene(urls), "zone": {}}
    for i, (zone, rows, p_range) in enumerate(zones, 1):
        check_zone(f"zona {i} ({zone['jls']})", rows, year, problems, p_range)
        if not zone["ulice"]:
            problems.append(f"zona {i}: nema ulica")
        prev = old.get(str(i), {})
        prev = prev.get("raw", {}) if prev.get("opis") == zone["opis"] else {}
        data["zone"][str(i)] = {**zone, "raw": {**prev, str(year): podaci.month_lines(rows)}}
        n = Counter(c for _, codes, _ in rows for c in codes)
        print(f"zona {i:2} {zone['jls']:17} {zone['podrucje'][:62]:62} " + " ".join(f"{c}{n[c]}" for c in "MBPS" if n[c]))
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


def napomene(urls):
    out = [
        "Miješani komunalni otpad odvozi se jednom tjedno na dan područja; reciklabilni otpad (papir, karton, plastika i "
        "metalna ambalaža u istom spremniku) jednom u dva tjedna – u Opatiji i Lovranu u neparnim, u Matuljima i "
        "Mošćeničkoj Dragi u parnim tjednima.",
        "U Lovranu, Matuljima i Mošćeničkoj Dragi staklena ambalaža odvozi se prvi tjedan u mjesecu, uz odvoz "
        "reciklabilnog otpada, u prozirnim vrećicama pored spremnika.",
        "Blagdani su ucrtani u kalendare: crveno polje je neradni dan bez odvoza, a odvoz se obavlja dan na koji pokazuje "
        "strelica (u ovim podacima označen kao pomaknut). Na ostale blagdane otpad se odvozi redovno.",
        "Otpad se odlaže na dan prikupljanja najkasnije do 5:00 sati u naseljima Opatija, Ičići i Ika, a drugdje do 7:00 "
        "sati, isključivo u spremnike ili vrećice davatelja usluge.",
        "Upiti: info@komunalac-opatija.hr, 051 505 201 (interno 2).",
    ]
    if "extra-Opatija" in urls:
        out.append("Na dijelu Grada Opatije reciklabilni otpad odvozi se svaki tjedan (Kalendar izvanrednog odvoza "
                   "reciklabilnog otpada: " + urls["extra-Opatija"] + "); te ulice imaju zasebne zone.")
    if "extra-Lovran" in urls:
        out.append("U Lovranu (područje „ispod groblja”) od srpnja do listopada reciklabilni otpad odvozi se i izvanredno "
                   "utorkom: " + urls["extra-Lovran"])
    if "mrd" in urls:
        out.append("Mobilno reciklažno dvorište Grada Opatije obilazi naselja po tjednima: " + urls["mrd"])
    return out


if __name__ == "__main__":
    main()
