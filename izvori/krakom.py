"""Krapina and surroundings: Krakom d.o.o., Krapina (streets by weekday) and the municipalities Đurmanec, Radoboj,
Jesenje and Petrovsko (one weekday each).

    python3 -m izvori.krakom [--year 2026]

krakom.hr/raspored-odvoza/ links one calendar PDF per city or municipality ("KALENDAR ODVOZA KOMUNALNOG OTPADA
2026. GODINA"): 12 month grids (Ned Pon Uto Sri Čet Pet Sub) in which every collection week is filled in the
colour of the bin collected that week, green (mixed waste), yellow (plastic) or blue (paper), and a green
fill with a blue or yellow outline means both bins. A household is collected on its weekday (Krapina lists
the streets of each weekday under the calendar; each municipality has one weekday), so the week colour at
that weekday gives the bin. Public holidays have red day numbers and no fill; the PDF rule moves the round to
the previous Saturday (Monday/Tuesday holidays) or to the next Saturday (Wednesday to Friday), and the
calendar shows that Saturday in the colour of the moved round. The Saturday actually coloured wins (Krakom
deviates from the rule when two holidays are close, e.g. 6.1.2026 -> 10.1.); deviations are printed.
Krapina's biowaste goes every Tuesday (bin for households that do not compost) and moves with the Tuesday
round.

Checks: the year and the city/municipality in the title, every day number in its week row and Sunday-first
column, legend samples matching their labels, every non-holiday collection weekday filled, every coloured
Saturday explained by exactly one holiday, the holiday rule text present, 4-5 collections a month.
"""
import argparse
import calendar
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import MONTHS, colour

SLUG = "krakom"
SITE = "https://krakom.hr"
PAGE = SITE + "/raspored-odvoza/"
WEEK = ["NED", "PON", "UTO", "SRI", "ČET", "PET", "SUB"]  # Sunday first
FILL = {(0.0, 0.69, 0.314): "M", (1.0, 0.753, 0.0): "P", (0.0, 0.439, 0.753): "K"}
EDGE = {(0.0, 0.439, 0.753): "K", (1.0, 0.753, 0.0): "P"}  # outline colours of combined weeks
LEGEND = [("plavog i zelenog", "KM"), ("žutog i zelenog", "MP"), ("zelenog i žutog", "MP"),  # combined first
          ("zelenog spremnika", "M"), ("žutog spremnika", "P"), ("plavog spremnika", "K")]
DAYS = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "ČETVRTAK": 3, "PETAK": 4}
DAY_NAME = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak"]
PLACES = {"GRAD KRAPINA": "Krapina", "OPĆINA ĐURMANEC": "Đurmanec", "OPĆINA RADOBOJ": "Radoboj",
          "OPĆINA JESENJE": "Jesenje", "OPĆINA PETROVSKO": "Petrovsko"}
PROVIDER = {
    "davatelj": "Krakom d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Krapinsko-zagorska",
    "jls": ["Krapina", "Đurmanec", "Radoboj", "Jesenje", "Petrovsko"],
    "nazivi": {"M": "Miješani komunalni otpad (zeleni spremnik)", "P": "Plastika (žuti spremnik)",
               "K": "Papir, karton i tetrapak (plavi spremnik)"},
    "bioNapomena": "Biootpad (smeđi spremnik) u Krapini se preuzima svaki utorak, za korisnike koji ne kompostiraju "
                   "samostalno.",
    "napomene": [
        "Svaki tjedan odvozi se jedan spremnik (zeleni, žuti ili plavi, ponekad zeleni zajedno sa žutim ili "
        "plavim), na dan u tjednu određen za ulicu ili općinu.",
        "Ako je dan odvoza neradni dan, odvoz je u subotu: za ponedjeljak i utorak prethodnu, za srijedu do petka "
        "sljedeću; kad se dva praznika spoje, Krakom objavljuje promjenu (ovdje su upisane subote iz kalendara).",
        "Glomazni otpad: zasebni raspored po naseljima na krakom.hr (nije uključen).",
        "Kontakt: 049/382-700, krakom@krakom.hr.",
    ],
}


def near(col, rgb, tol=0.03):
    return col is not None and len(col) == len(rgb) and all(abs(a - b) <= tol for a, b in zip(col, rgb))


def code_of(col, table):
    return next((c for rgb, c in table.items() if near(col, rgb)), None)


def outlined(box, edges):
    """Outline colours drawn along at least 3 of the 4 edges of a cell box (x0, top, x1, bottom)."""
    x0, t, x1, b = box
    out = set()
    for code in set(EDGE.values()):
        lines = [e for e in edges if EDGE[e[1]] == code]
        hit = 0
        for horiz, pos in ((True, t), (True, b), (False, x0), (False, x1)):
            for (r, _) in lines:
                if horiz and r["bottom"] - r["top"] < 4 and r["top"] - 2 <= pos <= r["bottom"] + 2 \
                        and min(x1, r["x1"]) - max(x0, r["x0"]) > 0.6 * (x1 - x0):
                    hit += 1
                    break
                if not horiz and r["x1"] - r["x0"] < 4 and r["x0"] - 2 <= pos <= r["x1"] + 2 \
                        and min(b, r["bottom"]) - max(t, r["top"]) > 0.6 * (b - t):
                    hit += 1
                    break
        if hit >= 3:
            out.add(code)
    return out


def read_calendar(path, year, problems):
    """(place, {date: codes}, red days, rule text, street lists, bio note) of one calendar PDF."""
    page = pdfplumber.open(path).pages[0]
    words = page.extract_words(extra_attrs=["non_stroking_color"])
    text = page.extract_text() or ""
    flat = " ".join(text.split())
    if f"KALENDAR ODVOZA KOMUNALNOG OTPADA {year}. GODINA" not in flat:
        problems.append(f"{path.name}: no title for {year}")
    head = next((w for w in words if w["text"] == "KALENDAR"), None)
    spaced = " ".join(w["text"] for w in sorted((w for w in words if head and 2 < w["top"] - head["top"] < 25),
                                                 key=lambda w: w["x0"]))
    joined = re.sub(r"(?<=\S) (?=\S( |$))", "", spaced + " ")  # "G R A D K R A P I N A" -> "GRAD KRAPINA"
    place = next((v for k, v in PLACES.items() if k.replace(" ", "") == joined.replace(" ", "")), None)
    if place is None:
        problems.append(f"{path.name}: place {spaced!r} not known")
    fills = [(r, code_of(colour(r), FILL)) for r in page.rects if r.get("fill") and code_of(colour(r), FILL)
             and r["x1"] - r["x0"] > 5 and r["bottom"] - r["top"] > 5]
    edges = [(r, rgb) for r in page.rects if r.get("fill") and min(r["x1"] - r["x0"], r["bottom"] - r["top"]) < 4
             for rgb in EDGE if near(colour(r), rgb)]
    heads = [w for w in words if w["text"].upper() in WEEK]
    rows = []
    for top in sorted({round(w["top"]) for w in heads}):
        line = sorted((w for w in heads if abs(w["top"] - top) < 2), key=lambda w: w["x0"])
        for i in range(len(line) - 6):
            if [w["text"].upper() for w in line[i:i + 7]] == WEEK:
                rows.append(line[i:i + 7])
    rows = list({(round(g[0]["top"]), round(g[0]["x0"])): g for g in rows}.values())
    titles = [w for w in words if w["text"] in MONTHS]
    cells, red, grid_bottom = {}, set(), 0
    if len(titles) != 12:
        problems.append(f"{place}: month headings {[w['text'] for w in titles]}")
    for t in titles:
        mo = MONTHS.index(t["text"]) + 1
        tx = (t["x0"] + t["x1"]) / 2
        g = [g for g in rows if 0 < g[0]["top"] - t["bottom"] < 20 and g[0]["x0"] - 10 < tx < g[-1]["x1"] + 10]
        if len(g) != 1:
            problems.append(f"{place} {t['text']}: {len(g)} weekday rows")
            continue
        g = g[0]
        cx = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (cx[-1] - cx[0]) / 6
        first, ndays = (date(year, mo, 1).weekday() + 1) % 7, calendar.monthrange(year, mo)[1]
        nums = [w for w in words if w["text"].isdigit() and cx[0] - pitch / 2 < (w["x0"] + w["x1"]) / 2 < cx[-1] + pitch / 2
                and 0 < w["top"] - g[0]["bottom"] < 6.5 * pitch]
        lines = []
        for w in sorted(nums, key=lambda w: w["top"]):
            if not lines or w["top"] - lines[-1] > 4:
                lines.append(w["top"])
        rpitch = (lines[-1] - lines[0]) / max(1, len(lines) - 1)
        seen = Counter()
        for w in nums:
            wx = (w["x0"] + w["x1"]) / 2
            col = min(range(7), key=lambda i: abs(cx[i] - wx))
            row = min(range(len(lines)), key=lambda i: abs(lines[i] - w["top"]))
            day = row * 7 + col - first + 1
            if w["text"] != str(day) or not 1 <= day <= ndays:
                problems.append(f"{place} {year}-{mo:02d}: {w['text']} in week row {row + 1}, column {WEEK[col]}")
                continue
            d = date(year, mo, day)
            seen[d] += 1
            if near(w["non_stroking_color"], (1.0, 0.0, 0.0)):
                red.add(d)
            wy = (w["top"] + w["bottom"]) / 2
            box = (cx[col] - pitch / 2, wy - rpitch / 2, cx[col] + pitch / 2, wy + rpitch / 2)
            under = [(r, c) for r, c in fills if r["x0"] <= wx <= r["x1"] and r["top"] <= wy <= r["bottom"]]
            if under:
                codes = {min(under, key=lambda rc: (rc[0]["x1"] - rc[0]["x0"]) * (rc[0]["bottom"] - rc[0]["top"]))[1]}
                if codes == {"M"}:
                    codes |= outlined(box, edges)
                cells[d] = "".join(sorted(codes))
            grid_bottom = max(grid_bottom, w["bottom"])
        if len(seen) != ndays or max(seen.values()) != 1:
            problems.append(f"{place} {year}-{mo:02d}: {len(seen)} of {ndays} days")
    # legend: each sample (fill + outline) below the grid must match the label to its right
    found = set()
    for r, c in fills:
        if r["top"] < grid_bottom or r["x1"] - r["x0"] > 30:
            continue
        ry = (r["top"] + r["bottom"]) / 2
        right = sorted((w for w in words if r["x1"] < w["x0"] < r["x1"] + 160 and abs((w["top"] + w["bottom"]) / 2 - ry) < 4),
                       key=lambda w: w["x0"])
        label = " ".join(w["text"] for w in right).replace("odvozaplavog", "odvoza plavog")
        want = next((code for k, code in LEGEND if k in label), None)
        got = "".join(sorted({c} | (outlined((r["x0"], r["top"], r["x1"], r["bottom"]), edges) if c == "M" else set())))
        if want is None or got != "".join(sorted(want)):
            problems.append(f"{place}: legend sample {got} labelled {label!r}")
        found.add(got)
    used = set(cells.values())
    if used - found:
        problems.append(f"{place}: cells {sorted(used - found)} without a legend sample")
    rule = re.search(r"\* ?ukoliko neradni dan(.*?)(?:izuzev|$)", flat)
    streets = {}
    parts = re.split(r"\n\s*(PONEDJELJAK|UTORAK|SRIJEDA|ČETVRTAK|PETAK)\s*\n", text)
    for name, body in zip(parts[1::2], parts[2::2]):
        streets[DAYS[name]] = [s.strip() for s in re.split(r",\s*", " ".join(body.split())) if s.strip()]
    bio = re.search(r"biootpad vrši se svaki (\w+)", flat)
    return place, cells, red, rule.group(1) if rule else "", streets, bio.group(1) if bio else None


def rule_saturdays(rule, problems, place):
    """{weekday: 'prev'|'next'} from the PDF's holiday rule."""
    out = {}
    for m in re.finditer(r"pada u ([\w ,]+?)(?:,)? (?:odvoz otpada (?:će )?se (?:vrši|vršiti) )?(prethodnu|nadolazeću) subotu",
                         rule.replace("pada u pada u", "pada u")):
        for name, wd in (("ponedjeljak", 0), ("utorak", 1), ("srijed", 2), ("četvrtak", 3), ("petak", 4)):
            if name in m.group(1):
                out[wd] = "prev" if m.group(2) == "prethodnu" else "next"
    if not out:
        problems.append(f"{place}: holiday rule not understood: {rule!r}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    links = list(dict.fromkeys(u for u in re.findall(r'href="(https://krakom\.hr/wp-content/uploads/[^"]+\.pdf)"', html)
                               if "KALENDAR" in u.upper() and str(year) in u and "GLOMAZNI" not in u.upper()))
    print(f"{len(links)} kalendara na {PAGE}")
    problems, zones = [], []
    hol = set(pravila.blagdani(year))
    with tempfile.TemporaryDirectory() as tmp:
        for i, url in enumerate(links):
            path = Path(tmp) / f"{i}-{url.rsplit('/', 1)[1]}"
            fetch(url, path)
            place, cells, red, rule, streets, bio = read_calendar(path, year, problems)
            if place is None:
                continue
            moves = rule_saturdays(rule, problems, place)
            if red - hol - {d for d in red if d.weekday() == 6}:
                print(f"   {place}: crveni dani koji nisu blagdani: {sorted(red - hol)}")
            weekdays = sorted(streets) if place == "Krapina" else sorted(
                {d.weekday() for d in cells if d.weekday() < 5})
            if place != "Krapina" and len(weekdays) != 1:
                problems.append(f"{place}: collection weekdays {weekdays}, expected one")
            if place == "Krapina" and (weekdays != list(range(5)) or bio != "utorak"):
                problems.append(f"Krapina: street lists for {weekdays}, biowaste day {bio!r}")
            # coloured Saturdays host the rounds of holidays
            saturdays = {d: c for d, c in cells.items() if d.weekday() == 5}
            host, used, gaps = {}, set(), {}
            for h in sorted(d for d in hol if d.weekday() in weekdays and d not in cells):
                prev = h - timedelta(days=h.weekday() + 2)
                nxt = h + timedelta(days=5 - h.weekday())
                order = [prev, nxt] if moves.get(h.weekday()) == "prev" else [nxt, prev]
                if h.weekday() not in moves:
                    problems.append(f"{place}: no holiday rule for {DAY_NAME[h.weekday()]} ({h})")
                pick = next((s for s in order if s in saturdays and s not in used), None)
                if pick is None and order[0] in hol:  # two holidays in a row: Krakom announces the change later
                    print(f"   {place}: blagdan {h:%d.%m.} i subota {order[0]:%d.%m.} su neradni; zamjenskog dana nema u kalendaru")
                    gaps[h] = order[0]
                    continue
                if pick is None:
                    problems.append(f"{place}: no coloured Saturday for the holiday {h}")
                    continue
                if pick != order[0]:
                    print(f"   {place}: blagdan {h:%d.%m.} ({DAY_NAME[h.weekday()]}) -> subota {pick:%d.%m.} "
                          f"(po pravilu bi bila {order[0]:%d.%m.})")
                host[h], used = pick, used | {pick}
            if set(saturdays) - used:
                problems.append(f"{place}: coloured Saturdays without a holiday {sorted(set(saturdays) - used)}")
            for wd in weekdays:
                rows, notes = [], []
                for d in pravila.tjedno(year, ["pon", "uto", "sri", "čet", "pet"][wd]):
                    if d in hol and d in cells:  # the calendar shows a collection on the holiday itself
                        print(f"   {place}: blagdan {d:%d.%m.} je u kalendaru dan odvoza ({cells[d]}); upisano kako je objavljeno")
                        notes.append(f"{d:%d.%m.} je blagdan, a kalendar ga prikazuje kao dan odvoza; po pravilu bi "
                                     f"odvoz bio u subotu (provjeriti kod Krakoma).")
                        rows.append((d, cells[d] + ("B" if place == "Krapina" and wd == 1 else ""), False))
                    elif d in hol:
                        if d in gaps:
                            notes.append(f"{d:%d.%m.} je blagdan, a i subota {gaps[d]:%d.%m.}; kalendar ne navodi "
                                         f"zamjenski dan (Krakom ga objavljuje naknadno).")
                        if d in host:
                            rows.append((host[d], saturdays[host[d]] + ("B" if place == "Krapina" and wd == 1 else ""), True))
                    elif d in cells:
                        rows.append((d, cells[d] + ("B" if place == "Krapina" and wd == 1 else ""), False))
                    else:
                        problems.append(f"{place}: {d} ({DAY_NAME[wd]}) has no colour")
                if place == "Krapina" and wd != 1:  # biowaste every Tuesday also reaches the other weekday zones
                    tue = {r[0]: r for r in zones_tuesday(year, cells, hol, host)}
                    rows = merge_bio(rows, tue)
                for m in range(1, 13):
                    n = sum(1 for d, c, _ in rows if d.month == m and set(c) & set("MPK"))
                    if not 4 - sum(1 for h in gaps if h.month == m) <= n <= 5:
                        problems.append(f"{place} {DAY_NAME[wd]} {year}-{m:02d}: {n} collections")
                if place == "Krapina":
                    ulice = streets[wd]
                    zone = {"jls": place, "podrucje": f"{DAY_NAME[wd].capitalize()}: {', '.join(ulice[:4])}, …",
                            "ulice": ulice}
                    if wd != 1:
                        zone["napomena"] = "Biootpad se preuzima utorkom (za korisnike koji ne kompostiraju)."
                else:
                    zone = {"jls": place, "podrucje": f"Cijela općina ({DAY_NAME[wd]})", "ulice": []}
                zone["napomena"] = " ".join([zone.get("napomena", "")] + notes + [f"Izvor: {url}"]).strip()
                zones.append((zone, rows))
                cnt = Counter(c for _, cs, _ in rows for c in cs)
                mv = ", ".join(f"{d:%d.%m.}" for d, _, m in sorted(rows) if m)
                print(f"{place} {DAY_NAME[wd]}: {len(rows)} dana {dict(sorted(cnt.items()))}, pomaknuto: {mv}")
    order = PROVIDER["jls"]
    zones.sort(key=lambda zr: order.index(zr[0]["jls"]))
    if {z["jls"] for z, _ in zones} != set(order):
        problems.append(f"zones for {sorted({z['jls'] for z, _ in zones})}")
    for p in problems[:30]:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for n, (zone, rows) in enumerate(zones, start=1):
        prev = {y: v for y, v in old.get(str(n), {}).get("raw", {}).items() if y != str(year)}
        data["zone"][str(n)] = {**zone, "raw": {**prev, str(year): podaci.month_lines(rows)}}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


def zones_tuesday(year, cells, hol, host):
    """Krapina biowaste dates: every Tuesday, or the Saturday the Tuesday round moved to."""
    out = []
    for d in pravila.tjedno(year, "uto"):
        if d in hol:
            if d in host:
                out.append((host[d], "B", True))
        else:
            out.append((d, "B", False))
    return out


def merge_bio(rows, tue):
    by_date = {d: (c, m) for d, c, m in rows}
    for d, (_, c, m) in tue.items():
        old = by_date.get(d, ("", False))
        by_date[d] = (old[0] + c, old[1] or m)
    return [(d, c, m) for d, (c, m) in by_date.items()]


if __name__ == "__main__":
    main()
