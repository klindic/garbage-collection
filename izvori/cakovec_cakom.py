"""Čakovec and surroundings: GKP Čakom d.o.o. (cakom.hr), one calendar PDF per route (24 routes).

    python3 -m izvori.cakovec_cakom [--year 2026]

The site's street search is WordPress data: posts of type "odvoz-otpada" titled "<route> – <settlement>"
with the settlement, its streets and the bin type (household bins, 1100 l containers, apartment
buildings) as taxonomy terms. The route calendars are media files named <route>.pdf; the newest upload
of a route wins for the months it covers, older uploads of the same year fill in the earlier months
(routes 20 and 22 changed in July 2026, route 24 started in July 2026).

The calendar page has month grids ("Pon Uto Sri Čet Pet Sub Ned"); the cell colour gives the waste:
grey mixed waste plus recyclables in bags (one collection, written as M + P), green biowaste, red a
public holiday (the collection moves to the cell coloured instead, marked as moved when that is not
the route's usual weekday), orange the mobile recycling yard (not a household collection, left out).
A cell drawn as a yellow gradient picture (pilot routes from July 2026) is mixed waste plus the new
yellow bin for plastic; a black ring around a red number is the day for bulky waste on request (G).

Checks: every day number in its own row and weekday column, every day of each covered month once, the
year in the title, every colour known and every legend sample matching its label, every coloured cell
holding a day, the months covering the year (or from the start of a new route), plausible counts per
type, and every route in the street search having a calendar. Otherwise nothing is written.
"""
import argparse
import calendar
import html
import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pdfplumber

import podaci
from izvori.sisak_gos import UA

SLUG = "cakovec-cakom"
SITE = "https://www.cakom.hr"
API = SITE + "/wp-json/wp/v2"
PAGE = SITE + "/raspored-odvoza-otpada/"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ",
          "KOLOVOZ", "RUJAN", "LISTOPAD", "STUDENI", "PROSINAC"]
WEEK = ["PON", "UTO", "SRI", "ČET", "PET", "SUB", "NED"]
# legend label words -> what a sample (colour family, picture or ring) must mean
LEGEND = [("grey", r"Miješani komunalni otpad,\s*reciklabilni"), ("green", r"Biootpad"), ("red", r"BLAGDANI"),
          ("orange", r"MOBILNO RECIKLAŽNO"), ("picture", r"AMBALAŽNU PLASTIKU"), ("ring", r"Glomazni otpad")]
CODES = {"grey": "MP", "green": "B", "picture": "MP", "ring": "G"}  # red and orange: no household collection
# collections per type in a full year
PER_YEAR = {"M": (24, 60), "P": (24, 60), "B": (20, 60), "G": (10, 13)}
PROVIDER = {
    "davatelj": "GKP Čakom d.o.o.",
    "web": SITE,
    "zupanija": "Međimurska",
    "jls": ["Čakovec", "Gornji Mihaljevec", "Mala Subotica", "Nedelišće", "Orehovica", "Strahoninec", "Šenkovec",
            "Štrigova"],
    "nazivi": {"P": "Reciklabilni otpad (vreće)", "B": "Biootpad (smeđa kanta i vreće)",
               "G": "Glomazni otpad (samo uz najavu)"},
    "napomene": [
        "Reciklabilni otpad (papir, plastika, staklo, metal, tetrapak) u namjenskim vrećama odvozi se u tjednu "
        "odvoza miješanog komunalnog otpada (crna kanta), istog dana.",
        "Biootpad u namjenskim vrećama odvozi se samo u tjednu odvoza biootpada (smeđa kanta).",
        "Glomazni otpad odvozi se na označeni dan samo uz najavu najkasnije 3 dana prije, na 0800 466 466 ili "
        "obrascem na www.cakom.hr; spremiti ga na privatnu površinu dostupnu vozilu.",
        "Odvoz od 6:00 do 16:00. Na blagdane nema odvoza; zamjenski dan je u kalendaru rute.",
        "Reciklažna dvorišta: Mihovljanska 10, Mihovljan (pon-pet 07-17) i Gospodarska 2, Totovec "
        "(pon-pet 06-15, sub 06-14). Otpadni tekstil predaje se u reciklažno dvorište.",
        "Čakom navodi i Općinu Sveti Juraj na Bregu, ali za nju nema rute u tražilici odvoza na cakom.hr, "
        "pa ni rasporeda ovdje.",
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


def rest(path, **params):
    """Every item of a WordPress REST collection (100 per page)."""
    out, page = [], 1
    while True:
        query = "&".join(f"{k}={v}" for k, v in {**params, "per_page": 100, "page": page}.items())
        try:
            items = json.loads(fetch(f"{API}/{path}?{query}").decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 400 and page > 1:  # past the last page
                break
            raise
        out += items
        if len(items) < 100:
            break
        page += 1
    return out


def family(col):
    """Colour family of a fill: legend samples and cells differ a little (grey 0.50 / 0.65, green 0/1/0 ...)."""
    if col is None:
        return None
    if isinstance(col, (int, float)):
        col = (col, col, col)
    if len(col) == 1:
        col = (col[0],) * 3
    if len(col) != 3:
        return "?"
    r, g, b = col
    if min(col) > 0.95:
        return "white"
    if max(col) - min(col) < 0.05 and 0.4 <= r <= 0.8:
        return "grey"
    if g > 0.8 and r < 0.35 and b < 0.35:
        return "green"
    if r > 0.9 and g < 0.2 and b < 0.2:
        return "red"
    if r > 0.9 and 0.6 < g < 0.85 and b < 0.25:
        return "orange"
    if max(col) < 0.05:
        return "black"
    return "?"


def calendar_page(pdf):
    pages = [p for p in pdf.pages if sum(m in (p.extract_text() or "") for m in MONTHS) >= 6]
    return pages[0] if len(pages) == 1 else None


def month_blocks(words, problems):
    """[(month, the 7 weekday heading words)]."""
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
        below = [g for g in rows if 0 <= g[0]["top"] - w["bottom"] < 30 and g[0]["x0"] - 10 <= cx <= g[-1]["x1"] + 10]
        if len(below) != 1:
            problems.append(f"{w['text']}: {len(below)} weekday rows under the heading")
            continue
        blocks.append((MONTHS.index(w["text"].upper()) + 1, below[0]))
    return blocks


def read_calendar(path, year):
    """(title, JLS heading, {date: kinds}, months, problems) of one route PDF.

    kinds: set of "grey", "green", "red", "orange", "picture", "ring" found at the day."""
    problems = []
    pdf = pdfplumber.open(path)
    page = calendar_page(pdf)
    if page is None:
        return "", "", {}, [], ["no single page with the month grids"]
    words = page.extract_words(extra_attrs=["non_stroking_color"])
    text = " ".join((page.extract_text() or "").split())
    m = re.match(r"(.*?)\s+(\d{4})\s+(?:" + "|".join(MONTHS) + ")", text)
    if not m or int(m.group(2)) != year:
        return "", "", {}, [], [f"no '{year}' over the calendar"]
    title = m.group(1)
    blocks = month_blocks(words, problems)
    months = sorted(mo for mo, _ in blocks)
    if months not in (list(range(1, 13)), list(range(7, 13))):
        problems.append(f"month grids {months}, expected the whole year or July to December")
    if problems:
        return title, "", {}, months, problems
    # the JLS: "Obavijest o sakupljanju komunalnog otpada na području Grada Čakovca – ..." on another page
    jm = re.search(r"na području\s+(Grada Čakovca|Općin[ae] [A-ZČĆŠŽĐ][\w ]*?)"
                   r"(?=\s*[–-]|\s+naselj|\s+Plan|\s*$)",
                   " ".join(" ".join((p.extract_text() or "").split()) for p in pdf.pages if p is not page))
    jls = "" if not jm else "Čakovec" if jm.group(1) == "Grada Čakovca" else re.sub(r"^Općin[ae] ", "", jm.group(1))
    fills = [s for s in page.rects + page.curves if s.get("fill")]
    rings = [s for s in page.curves if s.get("fill") and family(s.get("non_stroking_color")) == "black"
             and len(s.get("pts") or []) > 30 and 8 < s["x1"] - s["x0"] < 30]
    ring_parts = rings
    rings = list({(round(r["x0"]), round(r["top"])): r for r in rings  # the outer edge of each ring, once
                  if not any(o["x0"] < r["x0"] - 1 and r["x1"] < o["x1"] - 1 and o["top"] < r["top"] - 1
                             and r["bottom"] < o["bottom"] - 1 for o in rings)}.values())
    pictures = [i for i in page.images if 10 < i["x1"] - i["x0"] < 30 and 8 < i["bottom"] - i["top"] < 30]
    grid_bottom = 0
    cells, centres = {}, {}
    for mo, g in blocks:
        cols = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (cols[-1] - cols[0]) / 6
        inside = [w for w in words if w["text"].isdigit() and 0 < w["top"] - g[0]["bottom"] < 7 * pitch
                  and cols[0] - pitch / 2 <= (w["x0"] + w["x1"]) / 2 <= cols[-1] + pitch / 2]
        lines = []  # distinct week rows, top to bottom
        for w in sorted(inside, key=lambda w: w["top"]):
            if not lines or w["top"] - lines[-1] > 3:
                lines.append(w["top"])
        offset = date(year, mo, 1).weekday()
        for w in inside:
            cx = (w["x0"] + w["x1"]) / 2
            col = min(range(7), key=lambda i: abs(cols[i] - cx))
            row = min(range(len(lines)), key=lambda i: abs(lines[i] - w["top"]))
            day = row * 7 + col - offset + 1
            if (int(w["text"]) != day or not 1 <= day <= calendar.monthrange(year, mo)[1]
                    or abs(cols[col] - cx) > pitch / 3):
                problems.append(f"{year}-{mo:02d}: number {w['text']} in week row {row + 1}, column {WEEK[col]}")
                continue
            d = date(year, mo, day)
            cells.setdefault(d, []).append(w)
            centres[d] = (cx, (w["top"] + w["bottom"]) / 2)
            grid_bottom = max(grid_bottom, w["bottom"])
    found = {}
    for mo in months:
        for day in range(1, calendar.monthrange(year, mo)[1] + 1):
            d = date(year, mo, day)
            if len(cells.get(d, [])) != 1:
                problems.append(f"{d} appears {len(cells.get(d, []))} times in the grid")
                continue
            cx, cy = centres[d]
            under = [s for s in fills
                     if s["x0"] - 0.5 <= cx <= s["x1"] + 0.5 and s["top"] - 0.5 <= cy <= s["bottom"] + 0.5
                     and s["x1"] - s["x0"] < 60 and not any(s is r for r in ring_parts)]
            kinds = set()
            if under:  # the cell is the smallest filled rectangle around the number
                cell = min(under, key=lambda s: (s["x1"] - s["x0"]) * (s["bottom"] - s["top"]))
                fam = family(cell.get("non_stroking_color"))
                if fam == "?":
                    problems.append(f"{d}: unknown colour")
                elif fam != "white":
                    kinds.add(fam)
            if any(i["x0"] <= cx <= i["x1"] and i["top"] <= cy <= i["bottom"] for i in pictures):
                kinds.add("picture")
            if any(r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"] for r in rings):
                kinds.add("ring")
            if len(kinds - {"ring"}) > 1:
                problems.append(f"{d}: several fills {sorted(kinds)}")
            if kinds:
                found[d] = kinds
    # every ring and picture must be on a day or in the legend; legend samples must match their labels
    legend_top = grid_bottom + 5
    label_words = [w for w in words if w["top"] > legend_top]
    samples = [("ring", r) for r in rings] + [("picture", i) for i in pictures] + [
        (family(s.get("non_stroking_color")), s) for s in fills
        if 8 < s["x1"] - s["x0"] < 30 and 6 < s["bottom"] - s["top"] < 30 and s["top"] > legend_top
        and family(s.get("non_stroking_color")) in ("grey", "green", "red", "orange")]
    seen_legend = set()
    for kind, s in samples:
        on_day = any(s["x0"] <= cx <= s["x1"] and s["top"] <= cy <= s["bottom"] for cx, cy in centres.values())
        if s["top"] <= legend_top:
            if not on_day:
                problems.append(f"{kind} at {s['x0']:.0f},{s['top']:.0f} is not on a day")
            continue
        right = [w for w in label_words if s["x1"] < w["x0"] < s["x1"] + 60
                 and s["top"] - 3 < (w["top"] + w["bottom"]) / 2 < s["bottom"] + 3]
        first = min(right, key=lambda w: w["x0"]) if right else None
        label = " ".join(w["text"] for w in sorted(label_words, key=lambda w: w["x0"])
                         if first and abs(w["top"] - first["top"]) < 3 and w["x0"] >= first["x0"])
        want = [k for k, kw in LEGEND if re.search(kw, label, re.I)]
        if want != [kind]:
            problems.append(f"legend sample {kind} is labelled {label!r}")
        seen_legend.add(kind)
    used = {k for kinds in found.values() for k in kinds}
    if used - seen_legend:
        problems.append(f"cells {sorted(used - seen_legend)} without a legend sample")
    return title, jls, found, months, problems


def nice(name):
    """'BANA JOSIPA JELAČIĆA' -> 'Bana Josipa Jelačića', 'ULICA 1. SVIBNJA' -> 'Ulica 1. svibnja'."""
    small = {"I", "U", "NA", "OD", "DO", "ZA", "PL.", "ULICA", "CESTA", "TRG", "ODVOJAK", "PUT", "SVIBNJA",
             "RUJNA", "MAJA", "BOJNE", "BRANITELJA", "BORACA", "ŽRTAVA", "VELIKANA", "PARK"}
    out = []
    for i, word in enumerate(name.split()):
        if re.fullmatch(r"(II|III|IV|ZAVNOH-A)\.?", word):
            out.append(word)
        elif i and word in small:
            out.append(word.lower())
        else:
            out.append(re.sub(r"[^\W\d_]+", lambda m: m.group(0)[:1] + m.group(0)[1:].lower(), word))
    return " ".join(out)


def hr_key(text):
    return text.lower().translate(str.maketrans({"č": "c{", "ć": "c|", "đ": "d{", "š": "s{", "ž": "z{"}))


def regular_weekdays(rows):
    """Weekdays the route normally collects on (at least a third as often as the most common one)."""
    days = Counter(d.weekday() for d in rows)
    top = max(days.values(), default=0)
    return {wd for wd, n in days.items() if n * 3 >= top}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    posts = rest("odvoz-otpada", _fields="id,date,title,naselje,ulica,vrsta-kante")
    streets = {t["id"]: t["name"] for t in rest("ulica", _fields="id,name")}
    places = {t["id"]: t["name"] for t in rest("naselje", _fields="id,name")}
    bins = {t["id"]: t["name"] for t in rest("vrsta-kante", _fields="id,name")}
    media = rest("media", mime_type="application/pdf", after=f"{year - 1}-10-01T00:00:00", _fields="date,source_url")
    routes = {}
    for p in posts:
        title = html.unescape(p["title"]["rendered"])
        m = re.match(r"^(\d+)\s*[–-]\s*(.+)$", title)
        if not m:
            sys.exit(f"Ruta nije razumljiva iz naslova {title!r}")
        routes.setdefault(m.group(1), []).append(p)
    for m in media:
        n = re.fullmatch(r"(\d{1,3})(?:-\d+)?\.pdf", m["source_url"].rsplit("/", 1)[1])
        if n and n.group(1) not in routes and m["date"] >= f"{year - 1}-12":
            print(f"   {m['source_url']}: ruta {n.group(1)} nije u tražilici, preskočena")
    print(f"{len(posts)} unosa u tražilici, {len(routes)} ruta, {len(streets)} ulica, {len(places)} naselja; "
          f"{len(media)} PDF-ova od 10/{year - 1}")
    ok, zones, total = True, {}, Counter()
    with tempfile.TemporaryDirectory() as tmp:
        for route in sorted(routes, key=int):
            files = sorted(((m["date"], m["source_url"]) for m in media
                            if re.fullmatch(rf"{route}(-\d+)?\.pdf", m["source_url"].rsplit("/", 1)[1])), reverse=True)
            if not files:
                print(f"Ruta {route}: nema PDF-a")
                ok = False
                continue
            months, used, problems, jls, title = {}, [], [], None, None
            for i, (when, url) in enumerate(files):
                if len(months) == 12:
                    break
                name = "/".join(url.rsplit("/", 3)[-3:])
                dest = Path(tmp) / f"{route}-{i}.pdf"
                fetch(url, dest)
                t, j, found, mos, probs = read_calendar(dest, year)
                if probs:
                    if i == 0 and not re.match(r"no single page with the month grids|no '\d+' over", probs[0]):
                        problems += [f"{name}: {p}" for p in probs]
                        break
                    print(f"   {name}: preskočen ({probs[0]})")
                    continue
                if title is None:
                    title, jls = t, j
                new = [mo for mo in mos if mo not in months]
                for mo in new:
                    months[mo] = {d: k for d, k in found.items() if d.month == mo}
                if new:
                    used.append(f"{name} ({MONTHS[new[0] - 1].lower()}-{MONTHS[new[-1] - 1].lower()})")
            if problems or not months:
                print(f"Ruta {route}: PROBLEM")
                for p in problems[:15] or ["no calendar months"]:
                    print(f"   PROBLEM {p}")
                ok = False
                continue
            covered = sorted(months)
            created = min(datetime.fromisoformat(p["date"]).date() for p in routes[route])
            if covered != list(range(covered[0], 13)):
                problems.append(f"months covered {covered}")
            elif covered[0] != 1 and created < date(year, 1, 1):  # only a route new this year may start later
                problems.append(f"no calendar for {MONTHS[0].lower()}-{MONTHS[covered[0] - 2].lower()}, "
                                f"but the route exists since {created}")
            if jls not in PROVIDER["jls"]:
                problems.append(f"JLS {jls!r} from the PDF is not in the provider's list")
            kinds = {d: k for part in months.values() for d, k in part.items()}
            red = sorted(d for d, k in kinds.items() if "red" in k)
            rows = {d: "".join(CODES[k] for k in sorted(ks) if k in CODES) for d, ks in kinds.items()}
            rows = {d: c for d, c in rows.items() if c}
            regular = regular_weekdays([d for d, c in rows.items() if set(c) & set("MB")])
            moved = {d for d, c in rows.items() if set(c) & set("MB") and d.weekday() not in regular
                     and any(abs((d - h).days) <= 6 for h in red)}
            odd = [d for d, c in rows.items() if set(c) & set("MB") and d.weekday() not in regular and d not in moved]
            if odd:
                problems.append("collections off the usual weekdays without a holiday near: "
                                + ", ".join(f"{d:%d.%m.}" for d in odd))
            counts = Counter(t for c in rows.values() for t in c)
            share = len(months) / 12
            for t, n in counts.items():
                lo, hi = PER_YEAR[t]
                if not lo * share * 0.85 <= n <= hi * share * 1.15 + 1:
                    problems.append(f"{n}x {t} in {len(months)} months is implausible")
            if not counts.get("M"):
                problems.append("no mixed-waste collections")
            picture_days = sorted(d for d, k in kinds.items() if "picture" in k)
            orange = sorted(d for d, k in kinds.items() if "orange" in k)
            # streets and settlements from the street search
            settlements, names, kinds_of_bins = [], [], {}
            for p in routes[route]:
                for i in p["naselje"]:
                    settlements.append(nice(places[i]))
                for i in p["vrsta-kante"]:
                    kinds_of_bins.setdefault(bins[i], []).extend(nice(places[j]) for j in p["naselje"])
            single = len(set(settlements)) == 1
            for p in routes[route]:
                place = nice(places[p["naselje"][0]]) if p["naselje"] else ""
                for i in p["ulica"]:
                    s = nice(streets[i])
                    names.append(s if s == place or single or place == "Čakovec" else f"{s} ({place})")
            ulice = sorted(set(names) | set(settlements), key=hr_key)
            zone = {"jls": jls, "podrucje": f"Ruta {route}: {title}"}
            if set(kinds_of_bins) - {"Kante"}:  # containers or apartment buildings: say which settlement has what
                zone["podrucje"] += " (" + "; ".join(
                    f"{k.lower()}: {', '.join(dict.fromkeys(v))}" if len(kinds_of_bins) > 1 else k.lower()
                    for k, v in sorted(kinds_of_bins.items())) + ")"
            notes = []
            if sorted(months)[0] != 1:
                notes.append(f"Raspored ove rute objavljen je od {date(year, sorted(months)[0], 1):%d.%m.%Y.}")
            if picture_days:
                yellow = ", ".join(f"{d:%d.%m.}" for d in picture_days)
                notes.append("Pilot projekt: plastična ambalaža odlaže se u žutu kantu koja se odvozi jednom "
                             f"mjesečno, zajedno s miješanim otpadom: {yellow}")
            if orange:
                notes.append("Mobilno reciklažno dvorište: " + ", ".join(f"{d:%d.%m.}" for d in orange))
            if notes:
                zone["napomena"] = " ".join(notes)
            zone["ulice"] = ulice
            print(f"Ruta {route} ({jls}: {title}): {len(rows)} dana {dict(sorted(counts.items()))}, "
                  f"pomaknuto {len(moved)} ({', '.join(f'{d:%d.%m.}' for d in sorted(moved))}), blagdani {len(red)}, "
                  f"{len(ulice)} ulica/naselja; {', '.join(used)}")
            for p in problems[:15]:
                print(f"   PROBLEM {p}")
            if problems:
                ok = False
                continue
            total.update(counts)
            old = data["zone"].get(route, {})
            zone["raw"] = {**old.get("raw", {}), str(year): podaci.month_lines(
                [(d, c, d in moved) for d, c in rows.items()])}
            zones[route] = zone
    if len(zones) < 0.8 * len(data["zone"]):
        print(f"PROBLEM samo {len(zones)} ruta, a u {path.name} ih je {len(data['zone'])}")
        ok = False
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} ruta, odvoza {dict(sorted(total.items()))})")


if __name__ == "__main__":
    main()
