"""Prelog and 13 municipalities in Međimurje and Varaždin County: GKP Pre-Kom d.o.o. (pre-kom.hr).

    python3 -m izvori.pre_kom [--year 2026]

Every municipality has a static page (pre-kom.hr/<page>.html, "SAKUPLJANJE OTPADA > TERMINI SAKUPLJANJA")
with one section per area: the settlements or streets and the rules as text ("CRNO – miješani komunalni
otpad (svaki 2. ponedjeljak)", "SMEĐE – biootpad (svaki 2. ponedjeljak)", "ZELENO – papir, PET, metalna
ambalaža, tetrapak i staklo (3. utorak u mjesecu)", "CRVENO – glomazni otpad (2. utorak u mjesecu),
na najavu"), followed by a year calendar image (JPG) where every collection day is a box outline in
that colour (two boxes nested when two collections fall on one day) and holidays are red digits.
The dates are computed from the rules with pravila.py. The image gives the start of the two-week
black/brown cycle and the holiday shifts (no box on the holiday, the box is on another day around it);
both are kept in IMAGES below together with the image's sha256, so a changed image stops the script.
The boxes are read from the image (outline colour on all sides of every day cell; the grid comes from
the month underlines and every day number must sit in its weekday column) and must match the computed
dates exactly, bulky waste included; bulky waste is on request, so it goes into the zone note, not
into the dates.
"""
import argparse
import calendar
import hashlib
import html
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "pre-kom"
SITE = "https://www.pre-kom.hr"
PAGES = {  # page: JLS, in the order of the site's menu
    "grad-prelog": "Prelog", "op-ina-belica": "Belica", "op-ina-dekanovec": "Dekanovec",
    "op-ina-domasinec": "Domašinec", "op-ina-d.-dubrava": "Donja Dubrava", "op-ina-d.-kraljevec": "Donji Kraljevec",
    "op-ina-d.-vidovec": "Donji Vidovec", "op-ina-gori-an": "Goričan", "op-ina-jalzabet": "Jalžabet",
    "op-ina-kotoriba": "Kotoriba", "op-ina-martijanec": "Martijanec", "op-ina-podturen": "Podturen",
    "op-ina-pribislavec": "Pribislavec", "op-ina-sv.-marija": "Sveta Marija",
}
COLOURS = {"CRNO": "M", "SMEĐE": "B", "ZELENO": "P", "CRVENO": "G"}
DAYS = {"ponedjeljak": "pon", "utorak": "uto", "srijeda": "sri", "srijedu": "sri", "srijede": "sri",
        "četvrtak": "čet", "petak": "pet", "subota": "sub", "subotu": "sub"}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DANI_MN = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
# Checked by hand against each image: sha256, first black (mixed waste) day of the two-week cycle,
# holiday shifts seen in the image (rule date -> collection day, for every bin that falls on it).
IMAGES = {
    "Kalendar-Prelog--etvrtak-2026.jpg": ("727f0cc9630f7bcf976ce9f8642b40264b64a665f6db5f5ea0aed3768996993e",
        "2026-01-01", "2026-01-01>2026-01-03 2026-04-06>2026-03-30 2026-06-04>2026-06-06 "
                      "2026-06-22>2026-06-29"),
    "Kalendar-Prelog-petak-2026.jpg": ("a13d4e63e0d2b183eaa451ccee394f184ffb96abdfa0df3b8fc500c69928abd6",
        "2026-01-02", "2026-04-06>2026-03-30 2026-05-01>2026-04-30 2026-06-22>2026-06-29 2026-12-25>2026-12-29 "
                      "2027-01-01>2026-12-31"),
    "Kalendar-Prelog-naselja-2026.jpg": ("f9214bec8dc4c5ba0d1805ce2409cddd74e024f6b6bf4516d7e603901ffc0ad2",
        "2026-01-14", "2026-08-05>2026-08-03 2026-11-18>2026-11-21"),
    "Kalendar-Belica-2026.jpg": ("aa1dfbf1831a710a5c7cb4697bda5c3703caf714e74eb389b18e56a7ef8e1226",
        "2026-01-12", "2026-04-06>2026-04-03 2026-06-22>2026-06-20"),
    "Kalendar-Dekanovec-2026.jpg": ("a56a484f46d472f7cca2b23bde65d06b22efd399b75d72fffbf89eef81515bca",
        "2026-01-14", "2026-08-05>2026-08-03 2026-11-18>2026-11-21"),
    "Kalendar-Domasinec-2026.jpg": ("85af1ee4fff26b8e8be864fac687af642b770449835f0011a614bc1eab646bde",
        "2026-01-14", "2026-01-01>2026-01-03 2026-06-04>2026-06-06 2026-08-05>2026-08-03 2026-11-18>2026-11-21"),
    "Kalendar-D.-Dubrava-2026.jpg": ("82ff0cf28290602ef0fb9cace81150341be31cbbb0ba35cbc33bdb01c9b29384",
        "2026-01-07", "2026-08-05>2026-07-31 2026-11-18>2026-11-21"),
    "Kalendar-D.-Kraljevec,-Hodosan-2026.jpg": ("2c89fc0140c4078e00571ba9b7d572aeb50cf28d7829af568e5ba2c16b3ce403",
        "2026-01-07", "2026-01-06>2026-01-10 2026-08-05>2026-07-31 2026-11-18>2026-11-21"),
    "Kalendar-D.-Kraljevec-naselja-2026.jpg": ("58e2ee8825a8d4f1ea259aad0bfc6f81494c62bc089566f37cef7241e123e5ae",
        "2026-01-06", "2026-01-06>2026-01-10"),
    "Kalendar-D.-Vidovec-2026.jpg": ("137722b6ee5029bfeaecb45121132d79a473311ce0990a4c3d71490fe1157860",
        "2026-01-06", "2026-01-06>2026-01-10"),
    "Kalendar-Gori-an-2026.jpg": ("22f6db1118b70a0c4cced8a4c923c3775d55fde96c217357fc4b108ead396480",
        "2026-01-09", "2026-05-01>2026-04-30 2026-12-25>2026-12-29 2027-01-01>2026-12-31"),
    "Kalendar-Jalzabet-2026.jpg": ("65e5206e20f66e513ca9d7037f8c4ba31a44fc9d0aea48a8597bb747054d3881",
        "2026-01-07", "2026-01-06>2026-01-10 2026-04-06>2026-04-02 2026-08-05>2026-07-31 2026-11-18>2026-11-21"),
    "Kalendar-Kotoriba-2026.jpg": ("2bafa2e7d38bfc89ecff18c1c7c42dda9dfa104d94a0faf8d78029fea4f72960",
        "2026-01-01", "2026-01-01>2026-01-03 2026-06-04>2026-06-06 2026-06-22>2026-06-20"),
    "Kalendar-Martijanec-2026.jpg": ("4d20ea93dcfb7c90096ed4fa470af099f8f01e67f0c0cdf190b921207d8dd920",
        "2026-01-02", "2026-05-01>2026-04-30 2026-12-25>2026-12-29 2027-01-01>2026-12-31"),
    "Kalendar-Podturen-2026.jpg": ("7786fd750b0efb3699f13db083cdec8cbabe4b742384e8e9468baea2e4f7758d",
        "2026-01-07", "2026-08-05>2026-07-31 2026-11-18>2026-11-21"),
    "Kalendar-Pribislavec-2026.jpg": ("251c2ea0dd1bea9e4fe3dcc87b417826b31e9fd68289824ec6a7416fdead3026",
        "2026-01-06", "2026-01-06>2026-01-10"),
    "Kalendar-Sveta-Marija-2026.jpg": ("2d08ee6c01f2ec9541000829e420faf2f49b3baec8edd8568a9c6b8dae50521c",
        "2026-01-08", "2026-01-01>2026-01-03 2026-06-04>2026-06-06"),
}
EXTRA_NOTES = {  # notes printed in red on the calendar image (transcribed)
    "Kalendar-Sveta-Marija-2026.jpg": "Na kalendaru: naselje Donji Mihaljevec – odvoz glomaznog otpada prebačen "
                                      "s 12.06. na 15.06.; naselje Sveta Marija – odvoz glomaznog otpada prebačen "
                                      "s 14.08. na 17.08.",
}
PROVIDER = {
    "davatelj": "GKP Pre-Kom d.o.o.",
    "web": SITE,
    "izvor": SITE + "/grad-prelog.html",
    "zupanija": "Međimurska",
    "jls": list(dict.fromkeys(PAGES.values())),
    "nazivi": {"P": "Reciklabilni otpad (papir, plastika, metal, tetrapak, staklo)"},
    "napomene": [
        "Prikupljanje počinje od 7 sati.",
        "Zeleno (jednom mjesečno): papir, PET, metalna ambalaža, tetrapak i staklo zajedno.",
        "Glomazni otpad odvozi se jednom mjesečno na najavu: naručuje se najmanje 3 radna dana prije odvoza "
        "osobno, na telefon 040 321 246 ili e-mailom glomazni.otpad@pre-kom.hr (termin je u napomeni područja).",
        "Mobilno reciklažno dvorište prema planu rada mobilnog reciklažnog dvorišta.",
        "Datumi pomaknuti zbog blagdana preuzeti su s kalendara na stranicama općina.",
    ],
}


# ---------------------------------------------------------------- page text

def sections(page_html):
    """[(text lines, image url)] in page order: the text before each calendar image is its section."""
    body = page_html[page_html.find("<main"):]
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    out, pos = [], 0
    for m in re.finditer(r'<img[^>]*src="(images/Kalendar-[^"]+\.jpg)"', body):
        chunk = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", body[pos:m.start()])
        text = html.unescape(re.sub(r"<[^>]+>", "", chunk))
        lines = [" ".join(l.split()) for l in text.splitlines() if l.strip()]
        out.append((lines, SITE + "/" + m.group(1)))
        pos = m.end()
    return out


def parse_rule(text):
    """'svaki 2. ponedjeljak' -> ('2t', 'pon'); '3. utorak u mjesecu' / '3. petak' -> ('m', 3, 'pet')."""
    t = " ".join(text.lower().replace(".", ". ").split())
    m = re.fullmatch(r"svak[iuea] 2\. (\w+)", t)
    if m and m.group(1) in DAYS:
        return ("2t", DAYS[m.group(1)])
    m = re.fullmatch(r"(\d)\. (\w+)(?: u mjesecu)?", t)
    if m and m.group(2) in DAYS:
        return ("m", int(m.group(1)), DAYS[m.group(2)])
    return None


def parse_section(lines, problems, where):
    """{code: rule}, area line, bulky text from the lines of one section."""
    rules, area, bulky = {}, None, ""
    for line in lines:
        m = re.match(r"(CRNO|SMEĐE|ZELENO|CRVENO)\b", line)
        if m:
            code = COLOURS[m.group(1)]
            par = [p for p in re.findall(r"\(([^()]*)\)", line) if parse_rule(p)]
            if len(par) != 1:
                problems.append(f"{where}: pravilo nije razumljivo: {line!r}")
                continue
            rules[code] = parse_rule(par[0])
            if code == "G":
                bulky = par[0].strip()
        elif re.match(r"(Prelog ulice|Naselja|naselja)\b", line):
            area = line
    for code in "MBPG":
        if code not in rules:
            problems.append(f"{where}: nema pravila za {code}")
    if rules.get("M", ("",))[0] != "2t" or rules.get("B", ("",))[0] != "2t" or \
            rules.get("M", (0, 0))[1:] != rules.get("B", (0, 0))[1:]:
        problems.append(f"{where}: miješani i bio nisu isti dan svaki drugi tjedan: {rules}")
    return rules, area, bulky


def area_names(area, jls):
    """'Prelog ulice: A, B, Jug I, II, III' -> ['A', 'B', 'Jug I', 'Jug II', 'Jug III']."""
    if not area:
        return [jls]
    m = re.match(r"Naselja (.+) i (.+)$", area)
    if m:
        return [m.group(1).strip(), m.group(2).strip()]
    text = area.split(":", 1)[1].strip().rstrip(")")
    out = []
    for part in (p.strip() for p in text.split(",")):
        if re.fullmatch(r"[IVX]+", part) and out:
            part = out[-1].rsplit(" ", 1)[0] + " " + part
        if part:
            out.append(re.sub(r"\.(?=\w)", ". ", part))
    return out


# ---------------------------------------------------------------- rules -> dates

def rule_dates(rule, year, first_black=None, black=True):
    """Rule dates from December of the year before to January of the year after."""
    out = []
    for y in (year - 1, year, year + 1):
        if rule[0] == "m":
            out += pravila.mjesecno(y, rule[2], rule[1])
        else:
            out += pravila.tjedno(y, rule[1])
    out = [d for d in out if date(year - 1, 12, 1) <= d <= date(year + 1, 1, 31)]
    if rule[0] == "2t":
        start = first_black if black else first_black + timedelta(days=7)
        out = [d for d in out if (d - start).days % 14 == 0]
    return out


def expected(rules, year, first_black, moves):
    """{date: (codes, moved)} in `year` after the holiday shifts."""
    out = {}
    for code, rule in rules.items():
        for d in rule_dates(rule, year, first_black, code == "M"):
            new = moves.get(d, d)
            if new.year != year:
                continue
            codes, moved = out.get(new, ("", False))
            out[new] = (codes + code, moved or new != d)
    return out


# ---------------------------------------------------------------- calendar image

def colour_masks(a):
    r, g, b = (a[..., i].astype(float) for i in range(3))
    mx, mn = a.max(axis=2).astype(float), a.min(axis=2).astype(float)
    c = np.maximum(mx - mn, 1)
    h = np.where(mx == r, ((g - b) / c) % 6, np.where(mx == g, (b - r) / c + 2, (r - g) / c + 4)) * 60
    h = np.where(h > 300, h - 360, h)
    chroma = mx - mn
    return {"M": mx < 100,
            "G": (chroma > 60) & (h >= -25) & (h <= 10) & (mx >= 100),
            "B": (chroma > 25) & (chroma < 90) & (h >= 8) & (h <= 45) & (mx >= 110) & (mx <= 215),
            "P": (chroma > 35) & (h >= 90) & (h <= 175)}


def underlines(mask):
    """Long thin horizontal runs (the line under every month name): [(x0, x1, y)], merged per line."""
    H, W = mask.shape
    found = []
    for y in range(H):
        d = np.diff(np.concatenate(([0], mask[y].view(np.int8), [0])))
        for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1):
            if e - s > 0.18 * W:
                found.append((int(s), int(e), y))
    lines = []
    for s, e, y in found:
        hit = next((l for l in lines if abs(l[0] - s) < 10 and y - l[3] <= 2), None)
        if hit:
            hit[1], hit[3] = max(hit[1], e), y
        else:
            lines.append([s, e, y, y])
    return [(s, e, y1) for s, e, y0, y1 in lines if y1 - y0 < 0.012 * H]


def row_grid(dark, lines):
    """(offset of the first day row below the underline, row pitch, problems), fitted to the centres of
    the text rows (weekday letters and day numbers) in the middle of every day column."""
    cw0 = float(np.median([(l[1] - l[0] + 1) / 7 for l in lines]))
    off0, pitch0 = 1.26 * cw0, 0.843 * cw0  # the template's proportions
    pts = []
    for x0, x1, y in lines:
        cw = (x1 - x0 + 1) / 7
        cols = np.concatenate([np.arange(int(x0 + (c + 0.35) * cw), int(x0 + (c + 0.65) * cw)) for c in range(7)])
        top = y + 3
        prof = dark[top:int(y + off0 + 5.6 * pitch0), cols].any(axis=1).tolist() + [False]
        start = None
        for i, v in enumerate(prof):
            if v and start is None:
                start = i
            elif not v and start is not None:
                if 0.3 * pitch0 <= i - start <= 0.7 * pitch0:
                    c = top + (start + i - 1) / 2 - y
                    pts.append((round((c - off0) / pitch0), c))
                start = None
    pitch, off = pitch0, off0
    for _ in range(2):  # fit, drop stray text rows (notes, titles), fit again
        good = [p for p in pts if abs(p[1] - off - p[0] * pitch) <= 0.15 * pitch0]
        k, c = np.array(good, dtype=float).T
        pitch, off = np.polyfit(k, c, 1)
    bad = len(pts) - len(good)
    problems = [f"redovi kalendara nisu pravilni ({bad} od {len(pts)})"] if bad > 3 or len(good) < 60 else []
    return float(off), float(pitch), problems


def line_cover(m, x0, x1, y0, y1, vertical):
    """Best fraction of mask pixels along one line (3 px wide) across the strip."""
    sub = m[int(round(y0)):int(round(y1)) + 1, int(round(x0)):int(round(x1)) + 1]
    if vertical:
        sub = sub.T
    if sub.shape[0] < 3 or not sub.size:
        return 0.0
    return float((sub[:-2] | sub[1:-1] | sub[2:]).mean(axis=1).max())


def sides(m, cx, cy, cw, p):
    """Line coverage on the four sides of a cell (a box outline 0.75..1.4 cells wide)."""
    lo, hi = 0.36, 0.72
    return [line_cover(m, cx - hi * cw, cx - lo * cw, cy - 0.35 * p, cy + 0.35 * p, True),
            line_cover(m, cx + lo * cw, cx + hi * cw, cy - 0.35 * p, cy + 0.35 * p, True),
            line_cover(m, cx - 0.25 * cw, cx + 0.25 * cw, cy - hi * p, cy - lo * p, False),
            line_cover(m, cx - 0.25 * cw, cx + 0.25 * cw, cy + lo * p, cy + hi * p, False)]


def read_image(path, year):
    """({date: codes}, red-digit dates, problems) from a Pre-Kom calendar image."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    ms = colour_masks(a)
    lines = sorted(underlines(ms["M"]), key=lambda l: l[2])
    if len(lines) != 12:
        return {}, set(), [f"{len(lines)} crta ispod naziva mjeseci umjesto 12"]
    lines = [l for i in range(0, 12, 3) for l in sorted(lines[i:i + 3])]
    dark = a.max(axis=2) < 150
    reddish = (a[..., 0] - a[..., 1] > 35) & (a[..., 0] - a[..., 2] > 25)
    off, pitch, problems = row_grid(dark, lines)
    ink = dark | reddish
    anyink = a.min(axis=2) < 215
    found, reds = {}, set()
    for m, (x0, x1, y) in enumerate(lines, 1):
        cw = (x1 - x0 + 1) / 7
        weeks = calendar.Calendar().monthdayscalendar(year, m)
        for k in range(6):
            for col in range(7):
                day = weeks[k][col] if k < len(weeks) else 0
                cx, cy = x0 + (col + 0.5) * cw, y + off + k * pitch
                box = (slice(int(cy - 0.2 * cw), int(cy + 0.2 * cw)), slice(int(cx - 0.3 * cw), int(cx + 0.3 * cw)))
                n = int(ink[box].sum()) if day else int(ms["M"][box].sum())
                if bool(day) != (n > 0.008 * cw * cw):
                    problems.append(f"slika: {m}. mjesec, {k + 1}. red, {DAN[col]}: "
                                    f"{'dan ' + str(day) + ' bez znamenki' if day else 'znamenke izvan mjeseca'}")
                if not day:
                    continue
                d = date(year, m, day)
                if reddish[box].sum() > 0.5 * n:
                    reds.add(d)
                for code, mask in ms.items():
                    s = sides(mask, cx, cy, cw, pitch)
                    weakest = min(range(4), key=lambda i: s[i])
                    # an edge shared with a box of another colour loses its colour in the JPEG
                    if s[weakest] < 0.8 and sides(anyink, cx, cy, cw, pitch)[weakest] >= 0.8:
                        s[weakest] = 1.0
                    if min(s) >= 0.8:
                        found[d] = found.get(d, "") + code
    return found, reds, problems


def suggest(rules, boxes, year):
    """Cycle start and holiday shifts that explain the image (printed when the image is not checked yet)."""
    wd = pravila.DANI[rules["M"][1]]
    first = date(year, 1, 1) + timedelta(days=(wd - date(year, 1, 1).weekday()) % 7)
    cands = [first, first + timedelta(days=7)]
    score = [sum("M" in boxes.get(d, "") for d in rule_dates(rules["M"], year, c)) for c in cands]
    start = cands[score.index(max(score))]
    exp = expected(rules, year, start, {})
    moves = {}
    for d, (codes, _) in sorted(exp.items()):
        if set(boxes.get(d, "")) == set(codes):
            continue
        near = [n for n in sorted(boxes, key=lambda n: abs((n - d).days))
                if 0 < abs((n - d).days) <= 7 and set(codes) <= set(boxes[n])
                and set(exp.get(n, ("",))[0]) != set(boxes[n])]
        if near:
            moves[d] = near[0]
    for y in (year + 1,):  # next January's rule dates moved back into December
        for code, rule in rules.items():
            for d in rule_dates(rule, year, start, code == "M"):
                if d.year == y and d in pravila.blagdani(y):
                    near = [n for n in boxes if n.year == year and 0 < (d - n).days <= 7 and code in boxes[n]]
                    if near:
                        moves[d] = max(near)
    return start, moves


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "zone": {}}
    problems = []
    hol = set(pravila.blagdani(year))
    with tempfile.TemporaryDirectory() as tmp:
        for page, jls in PAGES.items():
            url = f"{SITE}/{page}.html"
            secs = sections(fetch(url).decode("utf-8", "replace"))
            if not secs:
                problems.append(f"{jls}: nema kalendara na {url}")
            for lines, img_url in secs:
                name = img_url.rsplit("/", 1)[1]
                where = f"{jls} ({name})"
                if f"-{year}.jpg" not in name:
                    problems.append(f"{where}: kalendar nije za {year}.")
                    continue
                rules, area, bulky = parse_section(lines, problems, where)
                if len(rules) != 4:
                    continue
                img = Path(tmp) / "kalendar.jpg"
                fetch(img_url, img)
                sha = hashlib.sha256(img.read_bytes()).hexdigest()
                boxes, reds, img_problems = read_image(img, year)
                problems += [f"{where}: {p}" for p in img_problems]
                known = IMAGES.get(name)
                if not known or known[0] != sha:
                    start, moves = suggest(rules, boxes, year)
                    problems.append(f"{where}: slika se promijenila ili nije provjerena (sha256 {sha}); prijedlog: "
                                    f'"{name}": ("{sha}", "{start}", '
                                    f'"{" ".join(f"{a}>{b}" for a, b in sorted(moves.items()))}"),')
                    continue
                start = date.fromisoformat(known[1])
                moves = dict(tuple(map(date.fromisoformat, m.split(">"))) for m in known[2].split())
                for a, b in moves.items():
                    if a not in set(pravila.blagdani(a.year)) or not 0 < abs((a - b).days) <= 7:
                        problems.append(f"{where}: pomak {a} -> {b} nije pomak blagdana")
                exp = expected(rules, year, start, moves)
                for d in sorted(set(exp) | set(boxes)):
                    want, got = "".join(sorted(exp.get(d, ("",))[0])), "".join(sorted(boxes.get(d, "")))
                    if want != got:
                        problems.append(f"{where}: {d:%d.%m.} pravilo {want or '-'}, slika {got or '-'}")
                for d in sorted(hol ^ reds):
                    print(f"   {where}: {d:%d.%m.} {'blagdan nije crven' if d in hol else 'crveni datum nije blagdan'}"
                          " na slici")
                rows = [(d, codes.replace("G", ""), moved) for d, (codes, moved) in exp.items()
                        if codes.replace("G", "")]
                for d, codes, moved in rows:
                    if not moved and any(d.weekday() != pravila.DANI[rules[c][-1]] for c in codes):
                        problems.append(f"{where}: {d} nije dan iz pravila")
                    if d in hol:
                        problems.append(f"{where}: odvoz na blagdan {d}")
                for code, lo, hi in (("M", 25, 28), ("B", 25, 28), ("P", 12, 13)):
                    n = sum(code in c for _, c, _ in rows)
                    if not lo <= n <= hi:
                        problems.append(f"{where}: {code} {n} puta u godini")
                for mth in range(1, 13):
                    n = sum("M" in c for d, c, _ in rows if d.month == mth)
                    if not 1 <= n <= 3:
                        problems.append(f"{where}: {mth}. mjesec miješani {n} puta")
                names = area_names(area, jls)
                wd = pravila.DANI[rules["M"][1]]
                rec = rules["P"]
                rec_text = f"{rec[1]}. {DAN[pravila.DANI[rec[2]]]} u mjesecu"
                label = ", ".join(names[:3]) + (" …" if len(names) > 3 else "") if area else f"Općina {jls}"
                zone = {
                    "jls": jls,
                    "podrucje": f"{label} – miješani i biootpad {DANI_MN[wd]} naizmjence svaki drugi tjedan, "
                                f"reciklabilni {rec_text}",
                }
                if area:
                    zone["opis"] = area
                zone["ulice"] = names
                notes = [f"Glomazni otpad: {bulky}, na najavu (040 321 246, glomazni.otpad@pre-kom.hr)."]
                if name in EXTRA_NOTES:
                    notes.append(EXTRA_NOTES[name])
                zone["napomena"] = " ".join(notes)
                key = str(len(data["zone"]) + 1)
                prev = old["zone"].get(key, {})
                zone["raw"] = {**({k: v for k, v in prev.get("raw", {}).items() if k != str(year)}
                                  if prev.get("jls") == jls else {}),
                               str(year): podaci.month_lines(rows)}
                data["zone"][key] = zone
                moved = [f"{a:%d.%m.}→{b:%d.%m.}" for a, b in sorted(moves.items())]
                print(f"{where}: {len(rows)} odvoza, ciklus od {start:%d.%m.}, pomaci: {', '.join(moved) or '-'}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} područja)")


if __name__ == "__main__":
    main()
