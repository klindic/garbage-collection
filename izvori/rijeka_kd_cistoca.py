"""Rijeka and surroundings: KD Čistoća d.o.o. (cistocarijeka.hr), one colour calendar per route and half-year.

    python3 -m izvori.rijeka_kd_cistoca [--year 2026]

The "od vrata do vrata" pages (WordPress REST: children of the Grad Rijeka page and of the municipalities'
parent page) link one calendar PDF per route for the current half-year, each followed by the route's
address list. The earlier calendars of the year are no longer linked but still online: they are found in
the media library under the same file name (WordPress appends -1, -2 ... to re-uploads); the newest
upload wins for each month the linked calendar does not cover.

Each calendar has month grids ("pon uto sri čet pet sub ned") with a coloured square behind every
collection day: green mixed waste, yellow plastic/metal/multilayer packaging, blue paper, brown biowaste
(Viškovo, bags); a day with two or three bins has the square split into triangles. Collection runs on
public holidays except where a note under the calendar says otherwise ("Zbog blagdana, 25.12.2026. neće
se vršiti odvoz ... Zamjenski termin odvoza bit će 30.12.2026."); the grid already shows the replacement
day, which is marked as moved.

Checks: every day number sits in its own row and weekday column, every day of each covered month appears
exactly once, the months match the "ZA RAZDOBLJE" line, every coloured square inside a grid holds a day,
every colour is known and the legend squares match their labels, the holiday notes agree with the grid,
the calendars cover the year without gaps (or from the date the route starts), the counts per type are
plausible, and every line of the address list is understood. Otherwise nothing is written.
"""
import argparse
import calendar
import html
import json
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pdfplumber

import podaci
from izvori.sisak_gos import UA

SLUG = "rijeka-kd-cistoca"
SITE = "https://cistocarijeka.hr"
API = SITE + "/wp-json/wp/v2"
PAGE = SITE + "/zbrinjavanje-otpada/prikupljanje-otpada-od-vrata-do-vrata/"
RIJEKA, MUNICIPALITIES = 17886, 11668  # parent pages: Grad Rijeka (mjesni odbori), gradovi i općine

MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ",
          "KOLOVOZ", "RUJAN", "LISTOPAD", "STUDENI", "PROSINAC"]
WEEK = ["PON", "UTO", "SRI", "ČET", "PET", "SUB", "NED"]
# Fill colours of the day and legend squares (RGB; CMYK in some files).
PALETTE = {
    (0.254, 0.679, 0.286): "M", (0.223, 0.708, 0.289): "M", (0.75, 0.0, 1.0, 0.0): "M", (0.75, 0.05, 1.0, 0.0): "M",
    (1.0, 0.805, 0.152): "P", (0.0, 0.19, 0.93, 0.0): "P",
    (0.0, 0.681, 0.938): "K", (0.0, 0.751, 0.916): "K", (1.0, 0.0, 0.0, 0.0): "K", (0.7, 0.0, 0.04, 0.0): "K",
    (0.614, 0.457, 0.28): "B",
}
WHITE = {(1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0)}
# Words in the legend labels and holiday notes that name a bin.
LEGEND = [("M", r"miješanog|zelenog"), ("P", r"plastične|žutog"), ("K", r"papira|plavog"),
          ("B", r"biootpad|biorazgradiv")]
NOTE_TYPES = [("M", r"miješanog|zeleni"), ("P", r"plastične|žuti"), ("K", r"papira|plavi"), ("B", r"biootpad")]
# Collections per type in half a year (26 weeks): mixed once to three times a week, plastic and paper
# every one to four weeks, biowaste weekly or every two weeks.
PER_HALF_YEAR = {"M": (24, 80), "P": (5, 30), "K": (5, 30), "B": (10, 60)}
SKIP_ROUTES = {"D Viktor Lenac": "kalendar za brodogradilište Viktor Lenac, nije za kućanstva",
               "D INA": "kalendar za INA rafineriju, nije za kućanstva"}
PROVIDER = {
    "davatelj": "KD Čistoća d.o.o.",
    "web": SITE,
    "zupanija": "Primorsko-goranska",
    "jls": ["Rijeka", "Bakar", "Kastav", "Kraljevica", "Kostrena", "Viškovo", "Čavle", "Jelenje", "Klana"],
    "nazivi": {"M": "Miješani komunalni otpad (zeleni spremnik)", "P": "Plastična, metalna i višeslojna ambalaža",
               "B": "Biootpad (biorazgradive vrećice)"},
    "napomene": [
        "Spremnik se na dan odvoza postavlja na dosadašnje mjesto primopredaje na javnoj površini "
        "najkasnije do 06:30.",
        "Odvoz se obavlja i na blagdane, osim kad je u kalendaru rute naveden zamjenski termin.",
        "U Rijeci raspored odvoza individualnih spremnika imaju mjesni odbori s rutama 1A do 7B (Draga, Orehovica, "
        "Pašac, Sveti Kuzam, Svilno, Grbci, Gornji Zamet, Pehlin, Srdoči, Škurinje, Zamet, Sveti Nikola, Kantrida, "
        "Drenova, Brašćine-Pulac, Turnić); ostali dijelovi grada, uključujući središte, koriste zajedničke "
        "spremnike na javnim površinama i nemaju raspored po kućanstvu.",
        "Staklo i tekstil predaju se u zajedničke spremnike na javnim površinama; glomazni i zeleni otpad odvoze se "
        "besplatno s adrese na zahtjev, a ostali otpad predaje se u reciklažno dvorište.",
        "Informacije: KD Čistoća, 0800 999 900, info@cistoca-ri.hr.",
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


def fetch_json(url):
    return json.loads(fetch(url).decode("utf-8"))


def ascii_name(text):
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^A-Za-z0-9]+", "-", text.encode("ascii", "ignore").decode()).strip("-")


def colour(obj):
    c = obj.get("non_stroking_color")
    if c is None:
        return None
    if isinstance(c, (int, float)):
        c = (c, c, c)
    return tuple(round(float(v), 3) for v in c)


def lookup(col, tol=0.04):
    """Code for a fill colour, "?" if it is not in the palette."""
    for known, code in PALETTE.items():
        if len(known) == len(col) and all(abs(a - b) <= tol for a, b in zip(col, known)):
            return code
    return "?"


def month_blocks(words, problems):
    """[(month, the 7 weekday heading words)]: each month name with the weekday row right under it."""
    heads = [w for w in words if w["text"].upper() in WEEK]
    rows = []
    for top in sorted({round(w["top"]) for w in heads}):
        row = sorted((w for w in heads if abs(w["top"] - top) < 3), key=lambda w: w["x0"])
        i = 0
        while i + 7 <= len(row):
            if [w["text"].upper() for w in row[i:i + 7]] == WEEK:
                rows.append(row[i:i + 7])
                i += 7
            else:
                i += 1
    rows = list({(round(g[0]["top"]), round(g[0]["x0"])): g for g in rows}.values())
    blocks = []
    for w in words:
        if w["text"].upper() not in MONTHS:
            continue
        cx = (w["x0"] + w["x1"]) / 2
        below = [g for g in rows if 0 <= g[0]["top"] - w["bottom"] < 40 and g[0]["x0"] - 10 <= cx <= g[-1]["x1"] + 10]
        if len(below) != 1:
            problems.append(f"{w['text']}: {len(below)} weekday rows under the heading")
            continue
        blocks.append((MONTHS.index(w["text"].upper()) + 1, below[0]))
    return blocks


def when(text):
    return datetime.strptime(text, "%d.%m.%Y").date()


def holiday_notes(text, problems):
    """[(days without collection, codes, [(replacement day, codes)])] from the notes under the grid."""
    out = []
    text = " ".join(re.sub(r"(\w)- (\w)", r"\1\2", text.replace("!!!", " ")).split())
    for m in re.finditer(r"Zbog (?:praznika|blagdana),(.*?)(?=Zbog (?:praznika|blagdana)|UPUTE ZA|$)", text):
        note = m.group(1).strip()
        n = re.match(r"((?:\d\d\.\d\d\.\d{4}\.?(?:\s*i\s*)?)+)\s*(?:godine\s*)?neće se vršiti odvoz (.*?)\. "
                     r"Zamjenski termin odvoza bit će (.*?)\.?\s*(?:godine\.)?\s*(?:Za odvoz (.*))?$", note)
        if not n:
            problems.append(f"holiday note not understood: {note!r}")
            continue
        days = [when(d) for d in re.findall(r"\d\d\.\d\d\.\d{4}", n.group(1))]
        codes = "".join(c for c, kw in NOTE_TYPES if re.search(kw, n.group(2)))
        repl = []
        for d, which in re.findall(r"(\d\d\.\d\d\.\d{4})\.?\s*(\([^)]*\))?", n.group(3)):
            repl.append((when(d), "".join(c for c, kw in NOTE_TYPES if re.search(kw, which)) if which else codes))
        if not days or not codes or not repl or any(not c for _, c in repl):
            problems.append(f"holiday note not understood: {note!r}")
            continue
        out.append((days, codes, repl))
        # "Za odvoz biootpada 25.12.2025. zamjenski termin je 27.12.2025., a 01.01.2026. nema odvoza biootpada."
        if n.group(4):
            b = re.match(r"biootpada (\S+?)\.? zamjenski termin je (\S+?)\.,? a (\S+?)\.? nema odvoza biootpada\.?$",
                         n.group(4).strip())
            if not b:
                problems.append(f"holiday note not understood: {n.group(4)!r}")
                continue
            out += [([when(b.group(1))], "B", [(when(b.group(2)), "B")]), ([when(b.group(3))], "B", [])]
    return out


def read_calendar(path, year):
    """(period (first, last), route text, {date: codes}, holiday notes, problems, info) of one calendar PDF."""
    problems, info = [], []
    pdf = pdfplumber.open(path)
    if len(pdf.pages) != 1:
        return None, "", {}, [], [f"{len(pdf.pages)} pages, expected 1"], info
    page = pdf.pages[0].dedupe_chars()  # bold text is printed twice, slightly shifted
    words = page.extract_words()
    text = " ".join((page.extract_text() or "").split())
    m = re.search(r"RAZDOBLJE:?\s*(\d\d\.\d\d\.\d{4})\.?\s*-\s*(\d\d\.\d\d\.\d{4})", text)
    if not m:
        return None, "", {}, [], ["no 'ZA RAZDOBLJE' line"], info
    period = (when(m.group(1)), when(m.group(2)))
    route = (re.search(r"\bRuta\s+(\S+)", text) or [None, ""])[1]
    blocks = month_blocks(words, problems)
    months = sorted(mo for mo, _ in blocks)
    expected = [mo for mo in range(1, 13) if date(year, mo, 1) <= period[1] and
                date(year, mo, calendar.monthrange(year, mo)[1]) >= period[0]]
    if not expected or months != expected:
        problems.append(f"month grids {months}, the period {period[0]} - {period[1]} needs {expected}")
    if problems:
        return period, route, {}, [], problems, info
    # geometry of each grid: column centres from the weekday row, rows one column pitch apart
    grids = []
    for mo, g in blocks:
        centres = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (centres[-1] - centres[0]) / 6
        grids.append((mo, g, centres, pitch))
    pitch = max(p for *_, p in grids)
    # filled shapes of cell size; a day with two bins has two triangles, with three bins three stripes
    shapes = [s for s in page.rects + page.curves
              if s.get("fill") and 0.25 * pitch <= s["x1"] - s["x0"] <= 1.3 * pitch
              and 0.25 * pitch <= s["bottom"] - s["top"] <= 1.3 * pitch and colour(s) and colour(s) not in WHITE]
    cells = {}  # date -> words; the number must sit in the date's own row and column
    for mo, g, centres, p in grids:
        inside = [w for w in words if w["text"].isdigit() and 0 < w["top"] - g[0]["bottom"] < 7 * p
                  and centres[0] - p / 2 <= (w["x0"] + w["x1"]) / 2 <= centres[-1] + p / 2]
        if not inside:
            continue
        first = min((w["top"] + w["bottom"]) / 2 for w in inside)
        offset = date(year, mo, 1).weekday()
        wrong = []
        for w in inside:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            col = min(range(7), key=lambda i: abs(centres[i] - cx))
            row = round((cy - first) / p)
            day = row * 7 + col - offset + 1
            if (int(w["text"]) == day and 1 <= day <= calendar.monthrange(year, mo)[1]
                    and abs(centres[col] - cx) < p / 3):
                cells.setdefault(date(year, mo, day), []).append(w)
            else:
                wrong.append((w, col, row))
        for w, col, row in wrong:  # a stray number printed under a correct one (hidden behind the square)
            if any(abs(v["top"] - w["top"]) < 2 and v["x0"] < w["x1"] and w["x0"] < v["x1"]
                   for vs in cells.values() for v in vs):
                info.append(f"{year}-{mo:02d}: ignored the number {w['text']} printed under another one")
            else:
                problems.append(f"{year}-{mo:02d}: number {w['text']} in row {row + 1}, column {WEEK[col]}")
    centre = {}
    for mo in months:
        for day in range(1, calendar.monthrange(year, mo)[1] + 1):
            d = date(year, mo, day)
            if len(cells.get(d, [])) != 1:
                problems.append(f"{d} appears {len(cells.get(d, []))} times in the grid")
                continue
            w = cells[d][0]
            centre[d] = ((w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2)
    codes, loose = {}, []
    for s in shapes:
        sx, sy = (s["x0"] + s["x1"]) / 2, (s["top"] + s["bottom"]) / 2
        near = [d for d, (cx, cy) in centre.items() if abs(cx - sx) < pitch / 2 and abs(cy - sy) < pitch / 2]
        if len(near) == 1:
            codes.setdefault(near[0], set()).add(lookup(colour(s)))
        elif near:
            problems.append(f"shape at {s['x0']:.0f},{s['top']:.0f} lies between {near}")
        else:
            loose.append(s)
    found = {}
    for d, cs in sorted(codes.items()):
        if "?" in cs:
            problems.append(f"{d}: unknown colour")
        elif not period[0] <= d <= period[1]:
            problems.append(f"{d}: coloured, but outside the period {period[0]} - {period[1]}")
        else:
            found[d] = "".join(sorted(cs))
    # shapes that are not behind a day: only the legend, under the grids
    grid_top = min(g[0]["top"] for _, g, _, _ in grids)
    grid_bottom = max(g[0]["bottom"] + 7 * p for _, g, _, p in grids)
    legend = []  # [[x0, x1, top, codes]]: the triangles or stripes of one legend square together
    for s in sorted(loose, key=lambda s: s["x0"]):
        if grid_top < s["top"] < grid_bottom - pitch:
            problems.append(f"coloured shape {colour(s)} at {s['x0']:.0f},{s['top']:.0f} without a day number")
            continue
        if not grid_bottom - pitch <= s["top"] < grid_bottom + 4 * pitch:
            continue
        for item in legend:
            if abs(item[2] - s["top"]) < 3 and s["x0"] <= item[1] + 1:
                item[1] = max(item[1], s["x1"])
                item[3].add(lookup(colour(s)))
                break
        else:
            legend.append([s["x0"], s["x1"], s["top"], {lookup(colour(s))}])
    label_page = page.filter(lambda o: o.get("object_type") != "char" or o["text"].strip())  # big spaces join lines
    label_words = label_page.extract_words()
    for x, x1, y, cs in legend:
        x_end = min([l[0] for l in legend if l[0] > x + 5 and abs(l[2] - y) < 25] + [x + 150])
        label = " ".join(w["text"] for w in label_words
                         if x1 < w["x0"] < x_end - 2 and y - 9 < w["top"] < y + pitch * 1.25)
        said = {c for c, kw in LEGEND if re.search(kw, label, re.I)}
        if said != cs:
            problems.append(f"legend: square {''.join(sorted(cs))} at {x:.0f},{y:.0f} is labelled {label!r}")
    if not legend:
        problems.append("no legend under the calendar")
    return period, route, found, holiday_notes(text, problems), problems, info


ADDR_HEAD = re.compile(r"^RUTA JLS (LOKACIJA|ADRESA KUĆNI BROJ)$")
# house numbers: 12, 12A, 9/1, 124/P1, 20B_VSZ, 15Č, 66A (B,C), BB, BB/(DO 61)
NUMBER = r"(?:\d+[A-ZČĆŠŽĐ]*|BB|B\.B\.)(?:[/_-][A-Z0-9ČĆŠŽĐ]+)*(?:/?\s*\([^)]*\))?"
LOWER = {"I", "U", "NA", "OD", "DO", "ZA", "S", "SA", "PO", "PRI", "KOD", "VA", "POD", "NAD", "PUT", "CESTA",
         "ULICA", "TRG", "OBALA", "ŠETALIŠTE", "PRILAZ", "USPON", "STUBE", "ODVOJAK", "PUTELJAK", "BORACA",
         "BRIGADE", "BRIGADA", "DIVIZIJE", "UDARNE", "SIJEČNJA", "VELJAČE", "OŽUJKA", "TRAVNJA", "SVIBNJA",
         "LIPNJA", "SRPNJA", "KOLOVOZA", "RUJNA", "LISTOPADA", "STUDENOGA", "STUDENOG", "PROSINCA"}
ROMAN = re.compile(r"^(II|III|IV|VI|VII|VIII|IX|XI|XII|XIII|HV|HVO|ZNG|NOB)\.?$")  # numerals, abbreviations


def nice(name):
    """'ŠKURINJSKIH BORACA' -> 'Škurinjskih boraca' (street words like put, cesta in lower case)."""
    out = []
    for i, word in enumerate(name.split()):
        if ROMAN.match(word):
            out.append(word)
        elif i and word in LOWER:
            out.append(word.lower())
        else:
            out.append(re.sub(r"[^\W\d_]+", lambda m: m.group(0)[:1] + m.group(0)[1:].lower(), word))
    return " ".join(out)


def hr_key(text):
    """Sort key in Croatian alphabetical order (č, ć after c, đ after d, š after s, ž after z)."""
    return text.lower().translate(str.maketrans({"č": "c{", "ć": "c|", "đ": "d{", "š": "s{", "ž": "z{"}))


def read_addresses(path, jls):
    """(route label, Counter of streets/settlements, problems) from lines 'RUTA JLS STREET NUMBER'.

    A few addresses over the border carry another JLS; their street gets the JLS name in brackets."""
    lines = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines += [" ".join(l.split()) for l in (page.extract_text() or "").splitlines()]
    places = "|".join(re.escape(j.upper()) for j in PROVIDER["jls"])
    labels, streets, problems = Counter(), Counter(), []
    for line in lines:
        if not line or line.isdigit() or ADDR_HEAD.match(line):
            continue
        m = re.match(rf"^(.+?) ({places}) (.+?)(?: {NUMBER})?$", line)
        if not m:
            problems.append(f"address line not understood: {line!r}")
            continue
        labels[m.group(1)] += 1
        street = nice(m.group(3))
        streets[street if m.group(2) == jls.upper() else f"{street} ({nice(m.group(2))})"] += 1
    if len(labels) != 1:
        problems.append(f"route labels in the address list: {dict(labels)}")
    if not streets:
        problems.append("no addresses")
    return (next(iter(labels)) if labels else ""), streets, problems


def page_links(content):
    """[(text, url)] of the PDF links in a page, in order, without repeats."""
    out = []
    for url, text in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', content, re.S):
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        if not any(u == url for _, u in out):
            out.append((text, url))
    return out


def routes_on_pages(year):
    """[dict(jls, area, route, start, calendar, addresses)] from the REST pages, in page order."""
    out = []
    for parent in (RIJEKA, MUNICIPALITIES):
        pages = fetch_json(f"{API}/pages?parent={parent}&per_page=100&orderby=menu_order&order=asc"
                           "&_fields=id,title,content,menu_order")
        for p in sorted(pages, key=lambda p: (p["menu_order"], p["id"])):
            if p["id"] == RIJEKA:
                continue
            title = " ".join(html.unescape(p["title"]["rendered"]).split())
            content = p["content"]["rendered"]
            jls = "Rijeka" if parent == RIJEKA else re.sub(r"^(Grad|Općina)\s+", "", title)
            # "Od 01.06.2026. godine ... započinje korištenje": routes may start during the year
            starts = [when(d) for d in re.findall(r"Od\s*(?:<[^>]+>|&nbsp;|\s)*(\d\d\.\d\d\.\d{4})\.",
                                                   html.unescape(content))]
            current = None
            for text, url in page_links(content):
                m = re.match(r"^(?:Kalendar rute|RIJEKA)\s+(.+)$", text, re.I)
                if m:
                    current = dict(jls=jls, area=title, route=m.group(1).strip(), calendar=url, addresses=None,
                                   start=max(starts, default=None))
                    out.append(current)
                elif re.match(r"^(Adrese rute|Popis adresa)", text, re.I) and current and not current["addresses"]:
                    current["addresses"] = url
    return out


def media_pdfs(year):
    """{file name without -N: [(upload time, url)]} of every PDF uploaded since November of the year before."""
    out, page = {}, 1
    while True:
        try:
            items = fetch_json(f"{API}/media?mime_type=application/pdf&per_page=100&page={page}"
                               f"&after={year - 1}-11-01T00:00:00&_fields=date,source_url")
        except urllib.error.HTTPError as e:
            if e.code == 400 and page > 1:  # past the last page
                break
            raise
        for it in items:
            out.setdefault(base(it["source_url"]), []).append((it["date"], it["source_url"]))
        if len(items) < 100:
            break
        page += 1
    return out


def base(url):
    """'.../2026/06/rijeka-7a-1.pdf' -> 'rijeka-7a' (WordPress appends -N to re-uploaded files)."""
    return re.sub(r"-\d+$", "", url.rsplit("/", 1)[1].rsplit(".", 1)[0])


def same_route(short, text):
    """The route printed in the PDF ('Ruta 2A', sometimes garbled as 'A6' or 'DB') contains the linked route."""
    want, got = Counter(short.upper().replace("-", "")), Counter(text.upper().replace("-", ""))
    return not want - got


def months_of(period, year):
    return [mo for mo in range(1, 13) if date(year, mo, 1) <= period[1]
            and date(year, mo, calendar.monthrange(year, mo)[1]) >= period[0]]


def route_schedule(r, media, get, year):
    """({date: (codes, moved)}, first date, [files used], problems) of one route for the year."""
    short = r["route"].split()[0]
    linked = r["calendar"]
    others = sorted(((t, u) for t, u in media.get(base(linked), []) if u != linked), reverse=True)
    months, used, problems = {}, [], []
    for t, url in [(None, linked)] + others:
        if len(months) == 12:
            break
        name = "/".join(url.rsplit("/", 3)[-3:])
        try:
            period, rtext, found, notes, probs, info = read_calendar(get(url), year)
        except (urllib.error.URLError, OSError) as e:
            if t is None:
                raise
            print(f"   {name}: preskočen ({e})")
            continue
        if not probs and not same_route(short, rtext):
            probs = [f"route '{rtext}' in the PDF, '{short}' on the page"]
        if probs:
            if t is None:
                problems += [f"{name}: {p}" for p in probs]
                break
            print(f"   {name}: preskočen ({probs[0]})")
            continue
        new = [mo for mo in months_of(period, year) if mo not in months]
        if not new:
            continue
        for p in info:
            print(f"   {name}: {p}")
        rows = {d: [c, False] for d, c in found.items() if d.month in new}
        for days, codes, repl in notes:  # the grid already has the replacement days; check and mark them
            for d in days:
                if d.year == year and d.month in new and set(codes) & set(rows.get(d, [""])[0]):
                    problems.append(f"{name}: {d} is coloured, but the note says no collection that day")
            for d, rc in repl:
                if d.year == year and d.month in new:
                    if not set(rc) <= set(rows.get(d, [""])[0]):
                        problems.append(f"{name}: {d} is the replacement day for {rc}, but not coloured so")
                    else:
                        rows[d][1] = True
        for mo in new:
            months[mo] = period
        part = (max(period[0], date(year, 1, 1)), min(period[1], date(year, 12, 31)))
        halves = ((part[1] - part[0]).days + 1) / 182.5
        counts = Counter(code for c, _ in rows.values() for code in c)
        for code, n in counts.items():
            lo, hi = PER_HALF_YEAR[code]
            if not lo * halves * 0.9 <= n <= hi * halves * 1.1:
                problems.append(f"{name}: {n}x {code} in {part[0]} - {part[1]} is implausible")
        if not counts.get("M"):
            problems.append(f"{name}: no mixed-waste collections")
        used.append((period, name, rows))
    if problems:
        return {}, None, [], problems
    rows = {d: tuple(v) for _, _, part in used for d, v in part.items()}
    covered = sorted(months)
    first = min(p[0] for p, _, _ in used) if used else None
    if not covered or covered[-1] != 12 or covered != list(range(covered[0], 13)):
        problems.append(f"months covered {covered}, expected every month up to December")
    elif first > date(year, 1, 1) and first.day == 1 and first.month in (1, 7) and not (
            r["start"] and r["start"].year == year):
        problems.append(f"no calendar before {first} found, and the page does not say the route starts in {year}")
    return rows, first, [f"{name} ({p[0]:%d.%m.}-{p[1]:%d.%m.})" for p, name, _ in used], problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    routes = routes_on_pages(year)
    media = media_pdfs(year)
    print(f"{len(routes)} kalendara ruta na stranicama, {sum(map(len, media.values()))} PDF-ova u medijima od "
          f"11/{year - 1}")
    if not routes:
        sys.exit(f"Nema kalendara ruta na {PAGE}")
    ok, zones, total = True, {}, Counter()
    with tempfile.TemporaryDirectory() as tmp:
        cache = {}

        def get(url):
            if url not in cache:
                cache[url] = Path(tmp) / f"{len(cache)}.pdf"
                fetch(url, cache[url])
            return cache[url]

        for r in routes:
            label = f"{r['jls']} {r['route']}"
            if r["route"] in SKIP_ROUTES:
                print(f"{label}: preskočena ({SKIP_ROUTES[r['route']]})")
                continue
            short = r["route"].split()[0]
            key = short if r["jls"] == "Rijeka" else f"{ascii_name(r['jls'])}-{short}"
            if key in zones:
                print(f"{label}: zona {key} se ponavlja")
                ok = False
                continue
            rows, first, used, problems = route_schedule(r, media, get, year)
            counts = Counter(t for c, _ in rows.values() for t in c)
            area = re.sub(r"\s*\(ulice:?[^)]*\)", "", r["area"])
            zone = {"jls": r["jls"],
                    "podrucje": f"{area}, ruta {short}" if r["jls"] == "Rijeka" else f"Ruta {r['route']}"}
            if r["jls"] == "Rijeka" and area != r["area"]:
                zone["opis"] = r["area"]
            if first and first > date(year, 1, 1):
                zone["napomena"] = f"Raspored ove rute objavljen je od {first:%d.%m.%Y.}"
            addr = "bez popisa adresa"
            if r["addresses"]:
                rlabel, streets, probs = read_addresses(get(r["addresses"]), r["jls"])
                problems += [f"{r['addresses'].rsplit('/', 1)[1]}: {p}" for p in probs]
                addr = f"{sum(streets.values())} adresa, {len(streets)} ulica/naselja ({rlabel})"
                if streets:
                    zone["ulice"] = sorted(streets, key=hr_key)
                    if r["jls"] != "Rijeka":
                        top = [s for s, _ in streets.most_common(4)]
                        zone["podrucje"] += ": " + ", ".join(top) + (" …" if len(streets) > 4 else "")
            print(f"{label} -> zona {key}: {len(rows)} dana {dict(sorted(counts.items()))}, "
                  f"{sum(m for _, m in rows.values())} pomaknuto; {addr}; " + ", ".join(used))
            for p in problems[:15]:
                print(f"   PROBLEM {p}")
            if problems:
                ok = False
                continue
            total.update(counts)
            old = data["zone"].get(key, {})
            zone["raw"] = {**old.get("raw", {}),
                           str(year): podaci.month_lines([(d, c, m) for d, (c, m) in rows.items()])}
            zones[key] = zone
    if len(zones) < 0.8 * len(data["zone"]):
        print(f"PROBLEM samo {len(zones)} zona, a u {path.name} ih je {len(data['zone'])}")
        ok = False
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona, odvoza {dict(sorted(total.items()))})")


if __name__ == "__main__":
    main()
