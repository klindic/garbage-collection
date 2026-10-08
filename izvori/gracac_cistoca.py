"""Gračac: Gračac Čistoća d.o.o. (documents on gracac.hr), one PDF per half-year with dates per area.

    python3 -m izvori.gracac_cistoca [--year 2026]

The company's document list on gracac.hr links "Raspored_odvoza_<months>_<year>....pdf" for each half
of the year. Each PDF is a table: for four areas (Gračac first part, Gračac second part, Kijani and the
villages around it, Srb and the villages around it) and every month, the days of mixed waste ("12. i
26."), paper and plastic. The words are read with pdfplumber: month rows by the month name, the three
columns by the x position of the column headings, the area by the label left of its six month rows.
The street lists come from the text under the table. Holidays are built into the dates (the PDF lists
the holidays whose collection was moved to other days); a date that is not on the area's usual weekday
must be within three days of a public holiday and is marked as moved.
"""
import argparse
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

SLUG = "gracac-cistoca"
SITE = "https://www.gracac.hr"
PAGE = SITE + "/dokumenti.asp?id=13&n=6&g=2"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
ON_CALL = "099/168 3365, najkasnije dan prije datuma iz rasporeda"
# zone: (word in the area label, description (None: the settlements of the label), napomena)
ZONES = {
    "1": ("PRVI", "Gračac (prvi dio)", None),
    "2": ("DRUGI", "Gračac (drugi dio)", None),
    "3": ("KIJANI", None, f"Plastika i papir odvoze se samo na poziv: {ON_CALL}."),
    "4": ("SRB", None, f"U Tomingaju se miješani otpad, plastika i papir odvoze samo na poziv: {ON_CALL}."),
}
PROVIDER = {
    "davatelj": "Gračac Čistoća d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zadarska",
    "jls": ["Gračac"],
}
NAPOMENE = [
    "Spremnike iznijeti na javnu površinu 1-2 m od ruba kolnika najkasnije do 07:00 sati na dan odvoza.",
    "Pomaci zbog blagdana upisani su u datume rasporeda (datum izvan uobičajenog dana označen je kao pomaknut).",
    "Glomazni otpad (do 3 m³ bez naknade) prema rasporedu u PDF-u, uz zahtjev najkasnije 7 dana prije: "
    "023/773 925 (8-14 h).",
    "Kontakt: 023/773 925.",
]


def read_pdf(path):
    """((start, end), [(label, {month: {code: [days]}})], streets {key: [streets]}, problems)."""
    page = pdfplumber.open(path).pages[0]
    words = page.extract_words()
    text = " ".join(page.extract_text().split())
    problems = []
    m = re.search(r"OD (\d{1,2})\. ?(\d{1,2})\. ?(\d{4})\. DO (\d{1,2})\. ?(\d{1,2})\. ?(\d{4})\.", text)
    if not m:
        return None, [], {}, ["nema razdoblja u naslovu"]
    g = list(map(int, m.groups()))
    period = (date(g[2], g[1], g[0]), date(g[5], g[4], g[3]))
    head = next((w for w in words if w["text"] == "PLASTIKA"), None)
    line = sorted((v for v in words if head and abs(v["top"] - head["top"]) < 2), key=lambda v: v["x0"])
    groups = []
    for v in line:
        if groups and v["x0"] - groups[-1][-1]["x1"] < 20:
            groups[-1].append(v)
        else:
            groups.append([v])
    names = [" ".join(v["text"] for v in g) for g in groups]
    if names != ["MIJEŠANI KOMUNALNI OTPAD", "PAPIR I KARTON", "PLASTIKA"]:
        return period, [], {}, [f"zaglavlje tablice {names}"]
    hx = {code: (g[0]["x0"] + g[-1]["x1"]) / 2 for code, g in zip("MKP", groups)}
    rows = sorted((w for w in words if w["text"].rstrip(":") in MONTHS and w["top"] > head["top"]),
                  key=lambda w: w["top"])
    blocks = []
    for w in rows:
        month = MONTHS.index(w["text"].rstrip(":")) + 1
        if not blocks or month <= blocks[-1][-1][0]:
            blocks.append([])
        cells = {"M": [], "K": [], "P": []}
        for v in words:
            if abs(v["top"] - w["top"]) < 3 and v["x0"] > w["x1"]:
                code = min(hx, key=lambda c: abs(hx[c] - (v["x0"] + v["x1"]) / 2))
                cells[code] += [int(x) for x in re.findall(r"\d+", v["text"])]
        blocks[-1].append((month, w, cells))
    out = []
    for block in blocks:
        top, bottom = block[0][1]["top"] - 6, block[-1][1]["bottom"] + 6
        label = " ".join(v["text"] for v in sorted(words, key=lambda v: (round(v["top"]), v["x0"]))
                         if v["x1"] < block[0][1]["x0"] and top <= v["top"] <= bottom)
        out.append((label, {mo: cells for mo, _, cells in block}))
    streets = {}
    for key, part in (("PRVI", r"\(PRVI DIO\) [–-] (.*?)\.\s+GRAČAC \(DRUGI"),
                      ("DRUGI", r"\(DRUGI DIO\) [–-] (.*?)\.\s+(?:ODVOZ|Mobilno|VAŽNO|Korisnici)")):
        s = re.search(part, text, re.I)
        if not s:
            problems.append(f"nema popisa ulica za {key}")
            continue
        streets[key] = split_streets(s.group(1))
    return period, out, streets, problems


def split_streets(text):
    """'Pružni odvojci I, II i III, Lovinačka, ...' -> ['Pružni odvojci I, II i III', 'Lovinačka', ...]."""
    out = []
    for part in re.split(r",\s*", text.replace("sv.", "sv. ")):
        part = " ".join(part.split())
        if out and re.fullmatch(r"(I|II|III)( i (I|II|III))?", part):  # roman numerals of the street before
            out[-1] += ", " + part
        elif part:
            out.append(part)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = fetch(PAGE).decode("utf-8", "replace")
    # sijecanj-lipanj sorts before srpanj-prosinac
    links = sorted(set(re.findall(rf'href="(/?Dokumenti/Raspored_odvoza_[^"]*_{year}[^"]*\.pdf)"', page)))
    if not links:
        sys.exit(f"Na {PAGE} nema rasporeda za {year}")
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year - 1)) | set(pravila.blagdani(year + 1))
    problems, dates, streets, periods, places = [], {z: {} for z in ZONES}, {}, [], {}
    with tempfile.TemporaryDirectory() as tmp:
        for link in links:
            path = Path(tmp) / link.rsplit("/", 1)[1]
            fetch(SITE + "/" + link.lstrip("/"), path)
            period, areas, st, probs = read_pdf(path)
            problems += [f"{path.name}: {p}" for p in probs]
            if not period or period[0].year != year:
                continue
            periods.append(period)
            streets.update(st)  # the newer half-year's street lists win
            if len(areas) != len(ZONES):
                problems.append(f"{path.name}: {len(areas)} područja, očekivano {len(ZONES)}")
            for label, months in areas:
                zone = next((z for z, (key, *_) in ZONES.items() if re.search(rf"\b{key}\b", label)), None)
                if zone is None:
                    problems.append(f"{path.name}: nepoznato područje {label!r}")
                    continue
                for name in re.split(r", | I ", " ".join(label.split())):
                    if name.title() not in places.setdefault(zone, []):
                        places[zone].append(name.title())
                want = {m for m in range(period[0].month, period[1].month + 1)}
                if set(months) != want:
                    problems.append(f"{path.name} {label}: mjeseci {sorted(months)}")
                for month, cells in months.items():
                    for code, days in cells.items():
                        if not days:
                            problems.append(f"{label} {month:02d}: nema datuma za {code}")
                        for day in days:
                            try:
                                d = date(year, month, day)
                            except ValueError:
                                problems.append(f"{label} {month:02d}: nemoguć dan {day}")
                                continue
                            if code in dates[zone].get(d, ""):
                                problems.append(f"zona {zone} {d}: {code} dvaput")
                            dates[zone][d] = dates[zone].get(d, "") + code
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": list(NAPOMENE), "zone": {}}
    for zone, (key, desc, note) in ZONES.items():
        rows, moved_notes = [], []
        usual = {c: Counter(d.weekday() for d, codes in dates[zone].items() if c in codes).most_common(1)
                 for c in "MKP"}
        for d, codes in sorted(dates[zone].items()):
            off = [c for c in codes if usual[c] and d.weekday() != usual[c][0][0]]
            if off and not any(abs((d - h).days) <= 3 for h in hol):
                problems.append(f"zona {zone} {d}: {''.join(off)} nije na uobičajeni dan ({DAN[usual[off[0]][0][0]]}),"
                                " a nema blagdana blizu")
            if off:
                moved_notes.append(f"{d:%d.%m.} {''.join(off)}")
            rows.append((d, codes, bool(off)))
        for m in sorted({d.month for d in dates[zone]}):
            n = Counter(c for d, codes in dates[zone].items() if d.month == m for c in codes)
            if not 2 <= n["M"] <= 3 or n["K"] != 1 or n["P"] != 1:
                problems.append(f"zona {zone} {year}-{m:02d}: {dict(n)} odvoza")
        if not rows:
            problems.append(f"zona {zone}: nema datuma")
            continue
        ulice = streets.get(key) or places[zone]
        desc = desc or ", ".join(places[zone])
        data["zone"][zone] = {
            "jls": "Gračac", "podrucje": desc, "ulice": ulice, **({"napomena": note} if note else {}),
            "raw": {**old["zone"].get(zone, {}).get("raw", {}), str(year): podaci.month_lines(rows)},
        }
        print(f"zona {zone} ({desc}): {len(rows)} dana; pomaknuto: {', '.join(moved_notes) or '-'}")
    months = sorted({(a, b) for a, b in periods})
    if months and (months[0][0] > date(year, 1, 1) or months[-1][1] < date(year, 12, 31)):
        data["napomene"].insert(0, f"Za {year}. objavljen je raspored od {months[0][0]:%d.%m.} do "
                                   f"{months[-1][1]:%d.%m.%Y.}")
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
