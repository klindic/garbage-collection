"""Valpovo: Urbanizam d.o.o. Valpovo, five zones of Valpovo (two half-year PDFs), and from October 2026 two zones of
the municipality Koška.

    python3 -m izvori.urbanizam_valpovo [--year 2026]

The PDFs are found among the site's media files (WordPress REST): "RASPORED-ODVOZA-od-sijecnja-do-lipnja-<year>"
and "...od-srpnja-do-prosinca-<year>" (one page per zone with six month grids, the zone's streets and the
weekday of the mixed-waste round), and "Raspored-odvoza-otpada-Opcina-Koska-<year>" (one page per zone).
Valpovo: mixed waste goes every week on the weekday written on the zone's page; biowaste is a brown cell; a
cell drawn as a picture of two triangles holds two recyclables (yellow plastic + grey metal, or pink
tetrapak + blue paper; the picture's pixels are read), and a black ring around the day number is glass. Koška:
green cells mixed waste, dark blue paper, yellow plastic. Days of the neighbouring months are skipped.

Holidays: the calendars carry no shifts for mixed waste; Urbanizam announces changes in news posts
("Raspored odvoza miješanog komunalnog otpada za vrijeme božićnih i novogodišnjih praznika"), whose moves for
the year are applied (the original date's weekday names the zone) and marked as moved; other dates are kept.
Biowaste, recyclables and glass are taken as drawn.

Checks: every day of each month once at its position, legend samples matching their labels, picture colours
known, glass every four weeks, both half-years with the same five zones and streets, 4-5 mixed collections a
month, the legend bin pictures of Koška in the right colours.
"""
import argparse
import calendar
import io
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import MONTHS, colour

SLUG = "urbanizam-valpovo"
SITE = "https://www.urbanizam-valpovo.hr"
MEDIA = SITE + "/wp-json/wp/v2/media?search={q}&per_page=50&_fields=date,source_url"
POSTS = SITE + "/wp-json/wp/v2/posts?search={q}&per_page=20&after={after}&_fields=date,link,title,content"
WEEK = ["po", "ut", "sr", "če", "pe", "su", "ne"]
DAYS = {"PONEDJELJAK": "pon", "UTORAK": "uto", "SRIJEDU": "sri", "ČETVRTAK": "čet", "PETAK": "pet"}
DAY_NAME = {"pon": "ponedjeljak", "uto": "utorak", "sri": "srijeda", "čet": "četvrtak", "pet": "petak"}
BROWN = (0.6, 0.4, 0.0)
CELLS = {BROWN: "B", (0.247, 0.686, 0.274): "M", (0.165, 0.376, 0.6): "K", (1.0, 1.0, 0.0): "P"}
# legend samples (rect colour -> code) and the label to their right
LEGEND = {"AMBALAŽNA PLASTIKA": ((1.0, 1.0, 0.0), "P"), "METAL": ((0.8, 0.8, 0.8), "L"),
          "TETRAPAK": ((0.992, 0.721, 0.721), "K"), "PAPIR": ((0.0, 0.4, 1.0), "K")}
PICTURE = {(255, 242, 0): "P", (195, 195, 195): "L", (255, 174, 201): "K", (0, 162, 232): "K"}  # triangle pixels
PROVIDER = {
    "davatelj": "Urbanizam d.o.o. Valpovo",
    "web": SITE,
    "izvor": SITE + "/",
    "zupanija": "Osječko-baranjska",
    "jls": ["Valpovo", "Koška"],
    "nazivi": {"P": "Ambalažna plastika", "S": "Ambalažno staklo"},
    "napomene": [
        "Valpovo: miješani komunalni otpad odvozi se svaki tjedan na dan naveden za zonu; biootpad, plastika i "
        "metal, papir i tetrapak te staklo na dane označene u kalendaru.",
        "Tetrapak se odvozi istog dana kao papir, a metal istog dana kao plastika (odvojene vrećice).",
        "Kalendari nemaju pomake zbog blagdana; Urbanizam ih objavljuje u obavijestima na urbanizam-valpovo.hr "
        "(uključene su objavljene promjene za ovu godinu), ostali datumi su prema rasporedu.",
        "Spremnike i vrećice treba iznijeti najkasnije do 6:00 (ljeti od 5:00) na dan odvoza.",
        "Informacije: 031/656-078.",
    ],
}


def near(col, rgb, tol=0.03):
    return col is not None and len(col) == 3 and all(abs(a - b) <= tol for a, b in zip(col, rgb))


def media(year, problems):
    """{'H1': url, 'H2': url, 'KOSKA': url} - newest upload of each schedule."""
    found = {}
    for q in ("RASPORED-ODVOZA", "Koska"):
        for m in sorted(json.loads(fetch(MEDIA.format(q=q))), key=lambda m: m["date"]):
            u = m["source_url"]
            name = u.rsplit("/", 1)[1].upper()
            if str(year) not in name:
                continue
            if "SIJECNJA-DO-LIPNJA" in name:
                found["H1"] = u
            elif "SRPNJA-DO-PROSINCA" in name:
                found["H2"] = u
            elif "KOSKA" in name and name.startswith("RASPORED"):
                found["KOSKA"] = u
    if "H2" not in found:
        problems.append(f"nema PDF-a za drugo polugodište {year} među datotekama na {SITE}")
    if "H1" not in found:  # the first half-year may be taken down later in the year
        print(f"   nema PDF-a za prvo polugodište {year}; zadržavaju se ranije upisani mjeseci")
    return found


def moves_from_posts(year, problems):
    """{original date: new date} for mixed waste from the holiday notices of the year."""
    out = {}
    posts = json.loads(fetch(POSTS.format(q="odvoza+miješanog+komunalnog+otpada", after=f"{year - 1}-11-01T00:00:00")))
    for p in posts:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p["content"]["rendered"]))
        for m in re.finditer(r"predviđen\w* za \w+,? (\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\.? godine[^.]*?(?:\.[^.]*?)??"
                             r"vršit će se u \w+,? (\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", text):
            a = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            b = date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
            if a.year == year or b.year == year:
                if abs((a - b).days) > 7:
                    problems.append(f"obavijest {p['link']}: {a} -> {b} predaleko")
                out[a] = b
                print(f"   obavijest {p['date'][:10]}: miješani {a:%d.%m.%Y.} -> {b:%d.%m.%Y.}")
    return out


def picture_codes(im):
    """Codes of the two triangles of a split-cell picture (upper left, lower right)."""
    data = im["stream"].get_data()
    w, h = im["srcsize"]
    if len(data) != w * h * 3:
        return None
    px = Counter()
    for x0, x1, y0, y1 in ((3, w // 3, 3, h // 3), (2 * w // 3, w - 3, 2 * h // 3, h - 3)):
        part = Counter(tuple(data[(y * w + x) * 3:(y * w + x) * 3 + 3]) for x in range(x0, x1) for y in range(y0, y1))
        rgb = part.most_common(1)[0][0]
        code = next((c for k, c in PICTURE.items() if all(abs(a - b) <= 8 for a, b in zip(rgb, k))), None)
        if code is None:
            return None
        px[code] += 1
    return "".join(sorted(px))


def read_page(page, year, problems, name):
    """{date: codes} for the month grids of one zone page."""
    words = page.extract_words(extra_attrs=["non_stroking_color"])
    heads = [w for w in words if w["text"].lower() in WEEK]
    rows = []
    for top in sorted({round(w["top"]) for w in heads}):
        line = sorted((w for w in heads if abs(w["top"] - top) < 2), key=lambda w: w["x0"])
        for i in range(len(line) - 6):
            if [w["text"].lower() for w in line[i:i + 7]] == WEEK:
                rows.append(line[i:i + 7])
    fills = [(r, colour(r)) for r in page.rects if r.get("fill") and colour(r)]
    rings = [c for c in page.curves if c.get("stroke") and not c.get("fill") and 20 < c["x1"] - c["x0"] < 45]
    out, months = {}, []
    for t in (w for w in words if w["text"] in MONTHS):
        mo = MONTHS.index(t["text"]) + 1
        tx = (t["x0"] + t["x1"]) / 2
        g = [g for g in rows if 0 < g[0]["top"] - t["bottom"] < 15 and g[0]["x0"] - 10 < tx < g[-1]["x1"] + 10]
        if len(g) != 1:
            problems.append(f"{name} {t['text']}: {len(g)} weekday rows")
            continue
        g = g[0]
        months.append(mo)
        cols = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (cols[-1] - cols[0]) / 6
        nums = [w for w in words if w["text"].isdigit() and 0 < w["top"] - g[0]["bottom"] < 6.5 * 21.5
                and cols[0] - pitch / 2 < (w["x0"] + w["x1"]) / 2 < cols[-1] + pitch / 2]
        lines = []
        for w in sorted(nums, key=lambda w: w["top"]):
            if not lines or w["top"] - lines[-1] > 5:
                lines.append(w["top"])
        if len(lines) != 6:
            problems.append(f"{name} {t['text']}: {len(lines)} week rows")
            continue
        ndays = calendar.monthrange(year, mo)[1]
        prev_days = calendar.monthrange(year - (mo == 1), 12 if mo == 1 else mo - 1)[1]

        def number(w, first):
            col = min(range(7), key=lambda i: abs(cols[i] - (w["x0"] + w["x1"]) / 2))
            row = min(range(6), key=lambda i: abs(lines[i] - w["top"]))
            day = row * 7 + col - first + 1
            return col, row, day, day if 1 <= day <= ndays else prev_days + day if day < 1 else day - ndays
        # the 1st is in the first week row, or in the second when the month starts on a Monday
        first = min((date(year, mo, 1).weekday() + k for k in (0, 7)),
                    key=lambda f: sum(w["text"] != str(number(w, f)[3]) for w in nums))
        seen = Counter()
        for w in nums:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            col, row, day, want = number(w, first)
            if w["text"] != str(want):
                problems.append(f"{name} {year}-{mo:02d}: {w['text']} in week row {row + 1}, column {WEEK[col]}")
                continue
            if not 1 <= day <= ndays:
                continue
            d = date(year, mo, day)
            seen[d] += 1
            codes = ""
            under = [(r, c) for r, c in fills if r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"]
                     and r["x1"] - r["x0"] < 2 * pitch]
            if under:
                c = min(under, key=lambda rc: (rc[0]["x1"] - rc[0]["x0"]) * (rc[0]["bottom"] - rc[0]["top"]))[1]
                codes += next((v for k, v in CELLS.items() if near(c, k)), "")
            for im in page.images:
                if im["x0"] <= cx <= im["x1"] and im["top"] <= cy <= im["bottom"] and im["x1"] - im["x0"] < 2 * pitch:
                    got = picture_codes(im)
                    if got is None:
                        problems.append(f"{name} {d}: picture with unknown colours")
                    else:
                        codes += got
            if any(r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"] for r in rings):
                codes += "S"
            if codes:
                out[d] = codes
        if len(seen) != ndays or max(seen.values()) > 1:
            problems.append(f"{name} {year}-{mo:02d}: {len(seen)} of {ndays} days")
    return out, sorted(months)


def legend_problems(page, name):
    problems = []
    words = page.extract_words()
    fills = [(r, colour(r)) for r in page.rects if r.get("fill") and colour(r) and 20 < r["x1"] - r["x0"] < 40]
    for label, (rgb, _) in LEGEND.items():
        first = label.split()[0]
        w = next((w for w in words if w["text"] == first and label in " ".join(
            x["text"] for x in words if abs(x["top"] - w["top"]) < 2 and x["x0"] >= w["x0"])), None)
        cy = (w["top"] + w["bottom"]) / 2 if w else -1
        sample = [c for r, c in fills if w and w["x0"] - 15 < r["x1"] <= w["x0"] + 2 and r["top"] <= cy <= r["bottom"]]
        if len(sample) != 1 or not near(sample[0], rgb, 0.02):
            problems.append(f"{name}: legend {label}: sample {sample}")
    if "AMBALAŽNO STAKLO" not in " ".join(w["text"] for w in words):
        problems.append(f"{name}: no glass in the legend")
    return problems


def boxed_text(page, words, rgb):
    """Text inside the wide legend boxes of one colour (green mixed waste, brown biowaste)."""
    boxes = [r for r in page.rects if r.get("fill") and near(colour(r), rgb, 0.02) and r["x1"] - r["x0"] > 100]
    inside = [w for w in words if any(r["x0"] <= (w["x0"] + w["x1"]) / 2 <= r["x1"]
                                      and r["top"] <= (w["top"] + w["bottom"]) / 2 <= r["bottom"] for r in boxes)]
    return " ".join(w["text"] for w in sorted(inside, key=lambda w: (round(w["top"]), w["x0"])))


def zone_text(page):
    flat = " ".join((page.extract_text() or "").split())
    words = page.extract_words()
    z = re.search(r"ZONA ([IVX]+):\s*(Grad Valpovo|Općina Koška)\s*(.*)$", flat)
    days = "|".join(DAYS)
    mixed, bio = boxed_text(page, words, (0.0, 0.662, 0.2)), boxed_text(page, words, BROWN)
    mixed = re.search(rf"\b({days})\b", mixed) if "ODVOZI SE SVAK" in mixed else None
    bio = re.search(rf"\b({days})\b", bio) if "ODVOZI SE SVAK" in bio else None
    return z, mixed.group(1) if mixed else None, bio.group(1) if bio else None


def koska_legend(page, name):
    """The bin pictures above the labels must be green / yellow / blue."""
    problems = []
    words = page.extract_words()
    for label, want in (("MIJEŠANI", "green"), ("PLASTIKA", "yellow"), ("PAPIR", "blue")):
        w = next((w for w in words if w["text"] == label and w["top"] > 400), None)
        im = next((i for i in page.images if w and i["x0"] - 15 <= w["x0"] <= i["x1"] and i["top"] - 25 < w["top"] < i["top"] + 30
                   and i["bottom"] - i["top"] > 100), None)
        if im is None:
            problems.append(f"{name}: no bin picture for {label}")
            continue
        pic = Image.open(io.BytesIO(im["stream"].get_rawdata())).convert("RGB").resize((40, 56))
        px = [pic.getpixel((x, y)) for x in range(40) for y in range(56)]
        px = [p for p in px if max(p) - min(p) > 60]
        r, g, b = (sum(p[i] for p in px) / max(1, len(px)) for i in range(3))
        got = "yellow" if r > 150 and g > 150 and b < 100 else "green" if g > r and g > b else "blue" if b > r else "?"
        if got != want:
            problems.append(f"{name}: bin picture for {label} is {got}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    urls = media(year, problems)
    moves = moves_from_posts(year, problems)
    hol = set(pravila.blagdani(year))
    zones = {}  # roman numeral -> dict
    with tempfile.TemporaryDirectory() as tmp:
        for key in ("H1", "H2", "KOSKA"):
            if key not in urls:
                continue
            path = Path(tmp) / f"{key}.pdf"
            fetch(urls[key], path)
            for i, page in enumerate(pdfplumber.open(path).pages):
                z, mixed, bio = zone_text(page)
                if not z:
                    continue
                name = f"{key} zona {z.group(1)}"
                cells, months = read_page(page, year, problems, name)
                if key != "KOSKA":
                    problems += legend_problems(page, name)
                    if mixed not in DAYS:
                        problems.append(f"{name}: dan miješanog otpada {mixed!r}")
                        continue
                else:
                    problems += koska_legend(page, name) if i == 0 else []
                tail = z.group(3)
                m = re.search(r"Prigradsk\w+ naselj\w+:?\s*(.+)$", tail)
                streets = [s.strip(" .") for s in re.split(r",\s*", tail[:m.start()] if m else tail) if s.strip(" .")]
                suburbs = [s.strip() for s in re.split(r",\s*|\s+i\s+", m.group(1))] if m else []
                if z.group(2) == "Općina Koška":
                    streets, suburbs = [], streets
                zone = zones.setdefault(z.group(1), {"jls": "Koška" if key == "KOSKA" else "Valpovo", "mixed": mixed,
                                                     "streets": streets, "suburbs": suburbs, "cells": {}, "months": []})
                if (zone["mixed"], zone["streets"], zone["suburbs"]) != (mixed, streets, suburbs):
                    problems.append(f"{name}: dan ili ulice se razlikuju od drugog polugodišta")
                bio_days = {d.weekday() for d, c in cells.items() if "B" in c}
                if bio and bio_days - {["PONEDJELJAK", "UTORAK", "SRIJEDU", "ČETVRTAK", "PETAK"].index(bio)}:
                    problems.append(f"{name}: biootpad na danima {sorted(bio_days)}, a piše {bio}")

                zone["cells"].update(cells)
                zone["months"] += months
                print(f"{name}: {len(cells)} obojenih dana u mjesecima {months[0]}-{months[-1]}, miješani {mixed}, "
                      f"{len(streets)} ulica, naselja {suburbs}")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    out = {}
    for n, (roman, z) in enumerate(sorted(zones.items(), key=lambda kv: ["I", "II", "III", "IV", "V", "VI", "VII"].index(kv[0])), 1):
        months = sorted(z["months"])
        kept = {}
        if z["jls"] == "Valpovo" and months != list(range(1, 13)):
            prev = old.get(str(n), {})
            if months == list(range(7, 13)) and prev.get("podrucje", "").startswith(f"Zona {roman} "):
                kept = {d: [c, mv] for d, c, mv in podaci.iter_dates(prev, year) if d.month <= 6}
            if {d.month for d in kept} != set(range(1, 7)):
                problems.append(f"zona {roman}: mjeseci {months}, a ranije upisanih siječanj-lipanj nema")
        rows = dict(kept)
        for d, c in z["cells"].items():
            rows[d] = [c, False]
        if z["jls"] == "Valpovo":
            for d in pravila.tjedno(year, DAYS[z["mixed"]]):
                if d.month not in months:
                    continue
                if d in moves:
                    d = moves[d]
                    rows.setdefault(d, ["", True])[1] = True
                rows.setdefault(d, ["", False])[0] += "M"
        bio = Counter(d.weekday() for d, (c, _) in rows.items() if "B" in c)
        for d, cm in rows.items():
            if "B" in cm[0] and bio and d.weekday() != bio.most_common(1)[0][0]:
                if any(abs((d - h).days) <= 7 for h in hol):
                    cm[1] = True
                else:
                    problems.append(f"zona {roman}: biootpad {d} izvan uobičajenog dana bez blagdana blizu")
        rows = [(d, "".join(sorted(set(c))), mv) for d, (c, mv) in rows.items()]
        # checks
        glass = sorted(d for d, c, _ in rows if "S" in c)
        if any((b - a).days != 28 for a, b in zip(glass, glass[1:])):
            problems.append(f"zona {roman}: staklo nije svaka 4 tjedna: {[f'{d:%d.%m.}' for d in glass]}")
        for m in months:
            cnt = Counter(c for d, cs, _ in rows if d.month == m for c in cs)
            if not 4 <= cnt["M"] <= 5 and z["jls"] == "Valpovo":
                problems.append(f"zona {roman} {year}-{m:02d}: {cnt['M']}x M")
            if z["jls"] == "Koška" and not 1 <= cnt["M"] <= 3:
                problems.append(f"zona {roman} {year}-{m:02d}: {cnt['M']}x M")
            if cnt["P"] > 2 or cnt["K"] > 2:
                problems.append(f"zona {roman} {year}-{m:02d}: {dict(cnt)}")
        notes = []
        on_hol = [f"{d:%d.%m.} {c}" for d, c, mv in sorted(rows) if d in hol and not mv]
        if on_hol:
            print(f"   zona {roman}: odvozi na blagdane (upisano prema rasporedu): {', '.join(on_hol)}")
        if z["jls"] == "Koška":
            usual = Counter(d.weekday() for d, c, _ in rows).most_common(1)[0][0]
            rows = [(d, c, d.weekday() != usual) for d, c, _ in rows]
            odd = [f"{d:%d.%m.}" for d, c, mv in sorted(rows) if mv]
            notes.append(f"Urbanizam d.o.o. Valpovo za Općinu Koška od {min(d for d, _, _ in rows):%d.%m.%Y.} "
                         f"(raspored objavljen u listopadu 2026.); prije toga Eko-Flor Plus / Mull-Trans.")
            if odd:
                notes.append("Odvoz izvan uobičajenog dana prema rasporedu: " + ", ".join(odd))
        place = ", ".join(z["suburbs"])
        if z["jls"] == "Valpovo":
            podrucje = f"Zona {roman} ({DAY_NAME[DAYS[z['mixed']]]}): " + (", ".join(z["streets"][:4]) + ", …" if z["streets"] else "") \
                + (("; " if z["streets"] else "") + place if place else "")
        else:
            podrucje = f"Zona {roman}: {place}"
        zone = {"jls": z["jls"], "podrucje": podrucje, "ulice": z["streets"] + z["suburbs"]}
        if notes:
            zone["napomena"] = " ".join(notes)
        cnt = Counter(c for _, cs, _ in rows for c in cs)
        print(f"Zona {n} ({z['jls']} {roman}): {len(rows)} dana {dict(sorted(cnt.items()))}, "
              f"pomaknuto {', '.join(f'{d:%d.%m.}' for d, _, mv in sorted(rows) if mv)}")
        out[str(n)] = (zone, rows)
    if len([z for z in zones.values() if z["jls"] == "Valpovo"]) != 5:
        problems.append(f"Valpovo: {len(zones)} zona")
    for p in problems[:30]:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "napomene": PROVIDER["napomene"] + ["Izvori: " + ", ".join(urls.values())], "zone": {}}
    for key, (zone, rows) in out.items():
        prev = {y: v for y, v in old.get(key, {}).get("raw", {}).items() if y != str(year)}
        data["zone"][key] = {**zone, "raw": {**prev, str(year): podaci.month_lines(rows)}}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
