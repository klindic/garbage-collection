"""Donji Miholjac and surroundings: Doroslov d.o.o., Donji Miholjac (4 areas), Marijanci, and from the second half
of 2026 Magadenovac, Podravska Moslavina and Viljevo.

    python3 -m izvori.doroslov [--year 2026]

Donji Miholjac: the year PDF linked from doroslov.hr/index.php/donji-miholjac/ is a 12-month grid in which
each day cell holds the day number and a small coloured box with a zone letter: I (Istok), Z (Zapad),
P (Prigradska naselja) or B (biowaste, all zones); the box colour gives the waste (dark green mixed, yellow
plastic and metal, light green glass, blue paper, brown biowaste, orange bulky waste). Mixed waste, plastic
and paper go by I/Z/P; glass and bulky waste only by I/Z, and the suburban settlements are split between
them, so the city has four areas here: Istok, Zapad, and the suburban settlements of each side. The legend
gives the streets and settlements. Holidays are built in (a red dot marks Sundays and holidays and the
collections of that week move on by a day); a collection off its usual weekday in a week with a holiday is
marked as moved.

Marijanci, Magadenovac, Podravska Moslavina, Viljevo: each municipality's page links a PDF table with the
explicit dates per waste type and month (holiday shifts already applied by the provider); a date off the
type's usual weekday in or right after a holiday week is marked as moved. Doroslov took over Magadenovac,
Podravska Moslavina and Viljevo on 1.7.2026 (before that Eko-Flor Plus / Mull-Trans), so only Doroslov's
published dates are written for them.

Checks: every day of each month once in its weekday column, the printed number matching the position
(known misprints are listed in TYPOS and printed), legend colours matching their labels, letters and
colours fitting the zone rules, every suburban settlement in exactly one glass/bulky zone, every table
date in the month column it is listed under and not on a Sunday, plausible counts per month.
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
from kalendar_boje import MONTHS, colour, weekday_rows

SLUG = "doroslov"
SITE = "https://doroslov.hr"
DM_PAGE = SITE + "/index.php/donji-miholjac/"
# JLS: (page, file name part, taken over from Eko-Flor Plus / Mull-Trans in 2026)
TABLES = {
    "Marijanci": ("/index.php/rasporedi-za-opcinu-marijanci/", "Marijanci", False),
    "Magadenovac": ("/index.php/rasporedi-za-opcinu-magadenovac/", "Magadenovac", True),
    "Podravska Moslavina": ("/index.php/rasporedi-za-opcinu-podravska-moslavina/", "Moslavina", True),
    "Viljevo": ("/index.php/rasporedi-za-opcinu-viljevo/", "Viljevo", True),
}
TAKEOVER = "Doroslov od 1.7.2026.; prije toga Eko-Flor/Mull-Trans."
# box colours in the Donji Miholjac grid
PALETTE = {(0.439, 0.678, 0.278): "M", (1.0, 1.0, 0.0): "P", (0.663, 0.816, 0.557): "S",
           (0.357, 0.608, 0.835): "K", (0.514, 0.235, 0.0471): "B", (0.929, 0.49, 0.192): "G"}
# legend label word -> code (its sample is a slightly different shade, so the nearest colour is used)
LEGEND = {"miješani": "M", "plastična": "P", "staklena": "S", "papir": "K", "biorazgradivi": "B", "glomazni": "G"}
# misprinted day numbers in the Donji Miholjac grid: (year, month, day): number printed
TYPOS = {(2026, 2, 9): "10", (2026, 9, 12): "13"}
ROW_TYPES = {"MIJEŠANI": "M", "PLASTIKA": "P", "PAPIR": "K", "STAKLO": "S", "GLOMAZNI": "G", "MOBILNO": "MRD"}
PER_MONTH = {"M": (1, 3), "P": (0, 2), "K": (0, 2), "S": (0, 2), "G": (0, 1), "B": (4, 5)}
PROVIDER = {
    "davatelj": "Doroslov d.o.o.",
    "web": SITE,
    "zupanija": "Osječko-baranjska",
    "jls": ["Donji Miholjac", "Marijanci", "Magadenovac", "Podravska Moslavina", "Viljevo"],
    "nazivi": {"P": "Plastična i metalna ambalaža", "S": "Staklena ambalaža (vrećica)",
               "G": "Glomazni otpad (samo uz najavu)"},
    "bioNapomena": "Biootpad se u Donjem Miholjcu odvozi jednom tjedno, samo korisnicima koji su preuzeli smeđi "
                   "spremnik.",
    "napomene": [
        "Miješani komunalni otpad odvozi se jednom u dva tjedna, plastika i metal, papir i staklo jednom mjesečno.",
        "Glomazni otpad odvozi se na označeni dan samo uz najavu najkasnije tri dana prije: 031/632-284 ili "
        "info@doroslov.hr.",
        "Pomaci zbog blagdana već su upisani u rasporede.",
        "Općine Magadenovac, Podravsku Moslavinu i Viljevo Doroslov preuzima od 1.7.2026.; ovdje su samo datumi "
        "iz Doroslovih rasporeda.",
        "Odlagalište i reciklažno dvorište Donji Miholjac: pon-pet 12-17 h.",
    ],
}


def pdf_link(html, year, part):
    """Newest upload of 'Raspored-odvoza-otpada-<year>-<part>[-n].pdf' linked from a page."""
    links = set(re.findall(rf"""href=["']?([^"' >]*Raspored-odvoza-otpada-{year}-{re.escape(part)}(?:-(\d+))?\.pdf)""",
                           html, re.I))
    return max(links, key=lambda l: (l[0].split("/uploads/")[-1][:7], int(l[1] or 0)))[0] if links else None


def nearest(col, palette):
    return min(palette.items(), key=lambda kv: sum((a - b) ** 2 for a, b in zip(col, kv[0])))


def usual_days(dates):
    days = Counter(d.weekday() for d in dates)
    top = max(days.values(), default=0)
    return {wd for wd, n in days.items() if n * 3 >= top}


def holiday_week(d, hol, back=0):
    """A public holiday on Monday..d of d's week (or of the `back` weeks before)."""
    monday = d - timedelta(days=d.weekday() + 7 * back)
    return any(monday <= h <= d for h in hol)


def nice_street(s):
    s = re.sub(r"\b([A-ZČĆŠŽĐ]) ([a-zčćšžđ]) ([a-zčćšžđ]{3,})", r"\1\2\3", s.strip())  # "K o lodvorska"
    return re.sub(r"\s+", " ", s).strip(" .")


def split_list(text):
    return [nice_street(x) for x in re.split(r",\s*|\s+i\s+", text) if nice_street(x)]


def read_grid(page, year, problems):
    """{date: [(letter, code)]} from the Donji Miholjac grid."""
    words = page.extract_words(extra_attrs=["non_stroking_color"])
    rows = weekday_rows(words)
    fills = [r for r in page.rects if r.get("fill") and colour(r)]
    out, seen = {}, Counter()
    heads = [w for w in words if w["text"] in MONTHS]
    if len(heads) != 12:
        problems.append(f"month headings {sorted(w['text'] for w in heads)}")
        return out
    for h in heads:
        mo = MONTHS.index(h["text"]) + 1
        hx = (h["x0"] + h["x1"]) / 2
        below = [g for g in rows if 0 <= g[0]["top"] - h["bottom"] < 25 and g[0]["x0"] - 15 <= hx <= g[-1]["x1"] + 15]
        if len(below) != 1:
            problems.append(f"{h['text']}: {len(below)} weekday rows")
            continue
        g = below[0]
        cols = [(w["x0"] + w["x1"]) / 2 for w in g]
        pitch = (cols[-1] - cols[0]) / 6
        nums = [w for w in words if w["text"].isdigit() and 0 < w["top"] - g[0]["bottom"] < 6 * 16
                and cols[0] - pitch / 2 <= w["x0"] <= cols[-1] + pitch / 2]
        lines = []
        for w in sorted(nums, key=lambda w: w["top"]):
            if not lines or w["top"] - lines[-1] > 4:
                lines.append(w["top"])
        if len(lines) != 6:
            problems.append(f"{h['text']}: {len(lines)} week rows")
            continue
        first, ndays = date(year, mo, 1).weekday(), calendar.monthrange(year, mo)[1]
        prev_days = calendar.monthrange(year - (mo == 1), 12 if mo == 1 else mo - 1)[1]
        letters = [w for w in words if w["text"] in ("I", "Z", "P", "B")]
        for w in nums:
            cx = (w["x0"] + w["x1"]) / 2
            col = min(range(7), key=lambda i: abs(cols[i] - cx))
            row = min(range(6), key=lambda i: abs(lines[i] - w["top"]))
            day = row * 7 + col - first + 1
            want = str(day if 1 <= day <= ndays else prev_days + day if day < 1 else day - ndays)
            if w["text"] != want and TYPOS.get((year, mo, day)) != w["text"]:
                problems.append(f"{year}-{mo:02d}: {w['text']} in week row {row + 1}, column {col + 1} (expected {want})")
                continue
            box = [l for l in letters if abs(l["top"] - w["top"]) < 3 and w["x1"] < l["x0"] < cols[col] + pitch / 2]
            if not 1 <= day <= ndays:
                if box:
                    problems.append(f"{year}-{mo:02d}: letter in the cell of another month's day {w['text']}")
                continue
            d = date(year, mo, day)
            if w["text"] != str(day):
                print(f"   NAPOMENA: {d:%d.%m.} je u PDF-u otisnut kao {w['text']} (položaj u mreži je jednoznačan)")
            seen[d] += 1
            for l in box:
                lx, ly = (l["x0"] + l["x1"]) / 2, (l["top"] + l["bottom"]) / 2
                under = [r for r in fills if r["x0"] <= lx <= r["x1"] and r["top"] <= ly <= r["bottom"]]
                col_ = colour(min(under, key=lambda r: (r["x1"] - r["x0"]) * (r["bottom"] - r["top"]))) if under else None
                code = next((c for rgb, c in PALETTE.items() if col_ and all(abs(a - b) < 0.03 for a, b in zip(col_, rgb))), "?")
                if code == "?":
                    problems.append(f"{d}: letter {l['text']} on unknown colour {col_}")
                out.setdefault(d, []).append((l["text"], code))
    for mo in range(1, 13):
        for day in range(1, calendar.monthrange(year, mo)[1] + 1):
            if seen[date(year, mo, day)] != 1:
                problems.append(f"{date(year, mo, day)} appears {seen[date(year, mo, day)]} times")
    return out


def read_dm(path, year, problems):
    """(zones {key: zone without raw}, rows {key: [(date, codes, moved)]}) for Donji Miholjac."""
    page = pdfplumber.open(path).pages[0]
    text = page.extract_text() or ""
    flat = " ".join(text.split())
    if f"{year}." not in flat or "DONJEG MIHOLJCA" not in flat:
        problems.append(f"PDF nema naslov za Donji Miholjac {year}.")
    # legend samples
    words = page.extract_words()
    fills = [r for r in page.rects if r.get("fill") and colour(r)]
    for label, code in LEGEND.items():
        w = next((w for w in words if w["text"].lower() == label), None)
        cy = (w["top"] + w["bottom"]) / 2 if w else 0
        sample = [r for r in fills if w and w["x0"] - 25 < r["x1"] < w["x0"] and r["top"] <= cy <= r["bottom"]
                  and r["x1"] - r["x0"] > 6 and r["bottom"] - r["top"] > 6 and 0.05 < sum(colour(r)) / 3 < 0.95]
        if len(sample) != 1 or nearest(colour(sample[0]), PALETTE)[1] != code:
            problems.append(f"legend {label}: no matching sample")
    cells = read_grid(page, year, problems)
    # areas
    m1 = re.search(r"ZONE PRIKUPLJANJA MIJEŠANOG.*?KARTONA(.*?)● ZONE PRIKUPLJANJA STAKLENE(.*?)$", flat)
    if not m1:
        problems.append("legend with the zones not found")
        return {}, {}
    first = dict(re.findall(r"(ISTOK|ZAPAD|PRIGRADSKA):\s*(.*?)(?=\s+(?:[IZP] ){2,3}[IZP]? ?[A-Z]+:|$)", m1.group(1)))
    second = dict(re.findall(r"(ISTOK|ZAPAD):\s*(.*?)(?=\s+[IZB] (?:[IZ] )?[A-Z]+|\s+B SVE|$)", m1.group(2)))
    if set(first) != {"ISTOK", "ZAPAD", "PRIGRADSKA"} or set(second) != {"ISTOK", "ZAPAD"}:
        problems.append(f"zone lists: {sorted(first)} / {sorted(second)}")
        return {}, {}
    suburbs = split_list(first["PRIGRADSKA"])
    side = {}
    for key in ("ISTOK", "ZAPAD"):
        for s in split_list(re.sub(r"Sve ulice zone \w+,?", "", second[key])):
            match = [x for x in suburbs if x.split()[-1] == s.split()[-1]]
            if len(match) != 1:
                problems.append(f"{key}: '{s}' is not one of the suburban settlements")
            for x in match:
                side.setdefault(x, []).append(key)
    for s in suburbs:
        if len(side.get(s, [])) != 1:
            problems.append(f"suburban settlement {s} is in glass/bulky zones {side.get(s, [])}")
    areas = {  # key: (letter for M/P/K, letter for S/G, podrucje, ulice)
        "1": ("I", "I", "Istok", split_list(first["ISTOK"])),
        "2": ("Z", "Z", "Zapad", split_list(first["ZAPAD"])),
        "3": ("P", "I", "Prigradska naselja (istok)", [s for s in suburbs if side.get(s) == ["ISTOK"]]),
        "4": ("P", "Z", "Prigradska naselja (zapad)", [s for s in suburbs if side.get(s) == ["ZAPAD"]]),
    }
    for d, marks in cells.items():
        for letter, code in marks:
            if (letter == "B") != (code == "B"):
                problems.append(f"{d}: letter {letter} with {code}")
            if letter == "P" and code in "SG":
                problems.append(f"{d}: suburban letter P with {code}")
    hol = set(pravila.blagdani(year))
    zones, rows = {}, {}
    for key, (main, glass, name, ulice) in areas.items():
        got = {}
        for d, marks in cells.items():
            for letter, code in marks:
                if letter == "B" or (letter == main and code in "MPK") or (letter == glass and code in "SG"):
                    got.setdefault(d, set()).add(code)
        usual = {c: usual_days([d for d, cs in got.items() if c in cs]) for c in "MPKSB"}
        moved = {d for d, cs in got.items() if any(d.weekday() not in usual[c] for c in cs if c in usual)
                 and holiday_week(d, hol)}
        odd = [f"{d:%d.%m.} {''.join(sorted(cs))}" for d, cs in sorted(got.items()) if d not in moved
               and any(d.weekday() not in usual[c] for c in cs if c in usual)]
        if odd:
            print(f"   {name}: neuobičajen dan u tjednu bez blagdana (upisano kako je objavljeno): {', '.join(odd)}")
        rows[key] = [(d, "".join(sorted(cs)), d in moved) for d, cs in got.items()]
        opis = first["ISTOK" if main == "I" else "ZAPAD" if main == "Z" else "PRIGRADSKA"]
        zones[key] = {"jls": "Donji Miholjac", "podrucje": f"{name}: {', '.join(ulice[:6])}"
                      + (", …" if len(ulice) > 6 else ""), "opis": nice_street(opis), "ulice": ulice}
        if main == "P":
            zones[key]["napomena"] = (f"Miješani otpad, plastika i papir: zona Prigradska (P); staklo i glomazni "
                                      f"otpad: zona {'Istok' if glass == 'I' else 'Zapad'}.")
    return zones, rows


def read_table(path, year, jls, problems):
    """([(date, code)], mobile recycling yard dates) from a municipality's date table."""
    page = pdfplumber.open(path).pages[0]
    flat = " ".join((page.extract_text() or "").split()).upper()
    if f"{year}." not in flat or not re.search(r"RASPORED ODVOZA KOMUNALNOG OTPADA NA PODRUČJU OPĆINE", flat):
        problems.append(f"{jls}: naslov tablice nije prepoznat")
    words = page.extract_words()
    labels = sorted(((w["top"], ROW_TYPES[w["text"]]) for w in words if w["text"] in ROW_TYPES))
    heads = [w for w in words if w["text"] in MONTHS]
    out, yard = [], []
    for w in words:
        m = re.fullmatch(r"\*?(\d{1,2})\.(\d{1,2})\.(\d{4})\.", w["text"])
        if not m:
            continue
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        above = [lab for top, lab in labels if top <= w["top"] + 3]
        cols = [h for h in heads if h["top"] < w["top"]]
        if not above or not cols:
            problems.append(f"{jls}: {d} outside the table")
            continue
        top = max(h["top"] for h in cols)
        cx = (w["x0"] + w["x1"]) / 2
        col = min((h for h in cols if abs(h["top"] - top) < 3), key=lambda h: abs((h["x0"] + h["x1"]) / 2 - cx))
        if d.year != year or MONTHS.index(col["text"]) + 1 != d.month:
            problems.append(f"{jls}: {d} listed under {col['text']}")
        if d.weekday() == 6:
            problems.append(f"{jls}: {d} is a Sunday")
        if above[-1] == "MRD":
            yard.append(d)
        else:
            out.append((d, above[-1]))
    return out, yard


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    problems, zones, sources = [], {}, []
    hol = set(pravila.blagdani(year))
    with tempfile.TemporaryDirectory() as tmp:
        url = pdf_link(fetch(DM_PAGE).decode("utf-8", "replace"), year, "D.Miholjac")
        if not url:
            sys.exit(f"Nema PDF-a za Donji Miholjac {year} na {DM_PAGE}")
        fetch(url, Path(tmp) / "dm.pdf")
        sources.append(url)
        dm_zones, dm_rows = read_dm(Path(tmp) / "dm.pdf", year, problems)
        for key, zone in dm_zones.items():
            zones[key] = (zone, dm_rows[key])
        for n, (jls, (page, part, new)) in enumerate(TABLES.items(), start=5):
            url = pdf_link(fetch(SITE + page).decode("utf-8", "replace"), year, part)
            if not url:
                problems.append(f"{jls}: nema PDF-a za {year} na {SITE + page}")
                continue
            dest = Path(tmp) / f"{part}.pdf"
            fetch(url, dest)
            sources.append(url)
            dates, yard = read_table(dest, year, jls, problems)
            got = {}
            for d, c in dates:
                if c in got.get(d, ""):
                    problems.append(f"{jls}: {d} {c} listed twice")
                got[d] = got.get(d, "") + c
            usual = {c: usual_days([d for d, cc in got.items() if c in cc]) for c in "MPKS"}
            moved = {d for d, cc in got.items() if any(d.weekday() not in usual[c] for c in cc if c in usual)
                     and (holiday_week(d, hol) or holiday_week(d, hol, back=1))}
            odd = [f"{d:%d.%m.} {cc}" for d, cc in sorted(got.items()) if d not in moved
                   and any(d.weekday() not in usual[c] for c in cc if c in usual)]
            if odd:
                print(f"   {jls}: neuobičajen dan u tjednu bez blagdana blizu (upisano kako je objavljeno): {', '.join(odd)}")
            on_holiday = [f"{d:%d.%m.}" for d in got if d in hol]
            if on_holiday:
                problems.append(f"{jls}: collection on a public holiday {on_holiday}")
            first = min(got)
            notes = []
            if new:
                notes.append(f"{TAKEOVER} Doroslovov raspored za ovu općinu objavljen je od {first:%d.%m.%Y.}")
            if yard:
                notes.append("Mobilno reciklažno dvorište: " + ", ".join(f"{d:%d.%m.}" for d in sorted(yard))
                             + " (lokacije i vrijeme u PDF-u rasporeda).")
            zone = {"jls": jls, "podrucje": "Cijela općina", "ulice": []}
            if notes:
                zone["napomena"] = " ".join(notes)
            zones[str(n)] = (zone, [(d, c, d in moved) for d, c in got.items()])
    # month counts
    for key, (zone, rows) in zones.items():
        months = sorted({d.month for d, _, _ in rows})
        span = range(months[0], 13) if months else []
        for m in span:
            cnt = Counter(c for d, cs, _ in rows if d.month == m for c in cs)
            for c, (lo, hi) in PER_MONTH.items():
                if c == "B" and "B" not in "".join(cs for _, cs, _ in rows):
                    continue
                if not lo <= cnt.get(c, 0) <= hi:
                    problems.append(f"{zone['jls']} {zone['podrucje'][:20]} {year}-{m:02d}: {cnt.get(c, 0)}x {c}")
        total = Counter(c for _, cs, _ in rows for c in cs)
        mv = sorted(f"{d:%d.%m.}" for d, _, m in rows if m)
        print(f"Zona {key} ({zone['jls']}: {zone['podrucje'][:45]}): {len(rows)} dana {dict(sorted(total.items()))}, "
              f"od {min(d for d, _, _ in rows):%d.%m.}, pomaknuto {len(mv)} ({', '.join(mv)}), "
              f"{len(zone['ulice'])} ulica/naselja")
    for p in problems[:30]:
        print(f"   PROBLEM {p}")
    if problems or len(zones) != 8:
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "izvor": DM_PAGE, "zone": {}}
    data["napomene"] = PROVIDER["napomene"] + ["Izvori: " + ", ".join(sources)]
    for key, (zone, rows) in zones.items():
        raw = {y: v for y, v in old.get(key, {}).get("raw", {}).items() if y != str(year)}
        data["zone"][key] = {**zone, "raw": {**raw, str(year): podaci.month_lines(rows)}}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
