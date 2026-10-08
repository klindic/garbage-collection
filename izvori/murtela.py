"""Murter-Kornati: Murtela d.o.o. (murtela-murter.hr), two areas of Murter, mixed waste by weekday, recyclables by calendar.

    python3 -m izvori.murtela [--year 2026]

The post "Godišnji raspored odvoza komunalnog otpada za <year>. godinu" (found with the WordPress REST
API) links the year PDF. Page 1 has the mixed waste tables: for the winter and the summer period (dates
in the PDF, e.g. 18.05.-26.09.) and each area ("za predio: naselje Murter, područje: ...") an X under
the collection weekdays. Page 2 is a year calendar of recyclables for the whole municipality: yellow
cells plastic, blue cells paper (filled curves); its characters are placed one by one, so they are
joined into words here and read with kalendar_boje.read_page (each day in its weekday column).
Holidays: the calendar of recyclables already has its dates; for mixed waste no holiday rule is
published, so the weekday dates are kept. The company's notices of the year (WordPress search) are
checked: "sukladno rasporedu" notices confirm a normal collection, any other notice of a change stops
the script until it is written into HANDLED.
"""
import argparse
import json
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch, plain
from kalendar_boje import read_page

SLUG = "murtela"
SITE = "https://murtela-murter.hr"
POSTS = SITE + "/wp-json/wp/v2/posts?search={q}&after={after}&per_page=50&_fields=id,date,link,title,content"
HEAD = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
WEEKDAYS = {"PO": "PON", "UT": "UTO", "SR": "SRI", "ČE": "ČET", "PE": "PET", "SU": "SUB", "NE": "NED"}
PALETTE = {(1.0, 1.0, 0.0): "P", (0.0, 0.439, 0.753): "K"}
HANDLED = {}  # notice id: what was done with it (notices that change the schedule)
PROVIDER = {
    "davatelj": "Murtela d.o.o.",
    "web": SITE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Murter-Kornati"],
    "nazivi": {"P": "Plastika (žuti spremnik)", "K": "Papir (plavi spremnik)"},
}
NAPOMENE = [
    "Miješani otpad: zimi (prema rasporedu npr. 01.01.-17.05. i 28.09.-31.12.) jednom tjedno, ljeti dvaput tjedno.",
    "Plastika (žuti spremnik) i papir (plavi spremnik) odvoze se za cijelu općinu prema godišnjem kalendaru.",
    "Za naselje Kornati termin odvoza uređen je posebno ugovorom s brodarom (nije objavljen).",
    "O glomaznom otpadu i izmjenama rasporeda Murtela obavještava na murtela-murter.hr.",
]


class Words:
    """A pdfplumber page whose words are given (kalendar_boje.read_page asks only for words and shapes)."""

    def __init__(self, page, words):
        self.page, self.words = page, words

    def extract_words(self):
        return self.words

    @property
    def rects(self):
        return self.page.rects

    @property
    def curves(self):
        return self.page.curves


def char_words(page):
    """Words built from single characters (gap under 2.5 pt, kerning may overlap), split at '-', weekday
    headings as PON..NED."""
    out = []
    for c in sorted(page.chars, key=lambda c: (round(c["top"]), c["x0"])):
        if not c["text"].strip():
            continue
        if out and abs(out[-1]["top"] - c["top"]) < 1 and -2 < c["x0"] - out[-1]["x1"] < 2.5:
            out[-1].update(text=out[-1]["text"] + c["text"], x1=c["x1"])
        else:
            out.append(dict(text=c["text"], x0=c["x0"], x1=c["x1"], top=c["top"], bottom=c["bottom"]))
    words = []
    for w in out:
        for part in filter(None, w["text"].split("-")):
            words.append({**w, "text": WEEKDAYS.get(part, part)})
    return words


def lines(words):
    rows = {}
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        key = next((k for k in rows if abs(k - w["top"]) < 3), w["top"])
        rows.setdefault(key, []).append(w)
    return [sorted(ws, key=lambda v: v["x0"]) for _, ws in sorted(rows.items())]


def mixed(page, year):
    """({area text: {season: day keys}}, {season: [(start, end)]}, problems) from page 1."""
    areas, periods, problems = {}, {"zima": [], "ljeto": []}, []
    season, area, heads = None, None, None
    for ws in lines(page.extract_words()):
        text = " ".join(w["text"] for w in ws)
        if "ZIMSKOM" in text or "LJETNOM" in text:
            season = "zima" if "ZIMSKOM" in text else "ljeto"
        for a, b, c, d, y in re.findall(r"(\d\d)\.(\d\d)\.-(\d\d)\.(\d\d)\.(\d{4})\.", text):
            if season and int(y) == year:
                periods[season].append((date(year, int(b), int(a)), date(year, int(d), int(c))))
        if text.startswith("za predio:"):
            area = text.split("područje:", 1)[1].strip()
        elif text.startswith("vrsta otpada"):
            heads = {w["text"]: (w["x0"] + w["x1"]) / 2 for w in ws if w["text"] in HEAD}
            if list(heads) != HEAD:
                problems.append(f"str. 1: zaglavlje dana {list(heads)}")
        elif text.startswith("MIJEŠANI") and area and heads and season:
            days = [min(heads, key=lambda k: abs(heads[k] - (w["x0"] + w["x1"]) / 2)) for w in ws if w["text"] == "X"]
            if not days:
                problems.append(f"str. 1: {area[:30]}: nema dana")
            areas.setdefault(area, {})[season] = " ".join(days)
            area = None
        elif area and ws[0]["x0"] > 140 and not text.startswith(("RASPORED", "U ", "IV.", "III.")):
            area += " " + text
    return areas, periods, problems


def notices(year, holidays, problems):
    """Notes about the company's notices of the year; a notice of a change not in HANDLED is a problem."""
    notes = []
    posts = json.loads(fetch(POSTS.format(q="odvoz", after=f"{year - 1}-12-01T00:00:00")))
    for p in posts:
        title, text = plain(p["title"]["rendered"]), plain(p["content"]["rendered"])
        if "Godišnji raspored" in title or str(year) not in text:
            continue
        dates = [date(int(y), int(m), int(d)) for d, m, y in re.findall(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)]
        if "sukladno rasporedu" in title.lower() or "sukladno rasporedu" in text.lower():
            notes += [f"{d:%d.%m.} odvoz po rasporedu (obavijest)" for d in dates if d in holidays]
        elif p["id"] in HANDLED:
            notes.append(HANDLED[p["id"]])
        else:
            problems.append(f"obavijest o izmjeni nije ugrađena u skriptu: {title} ({p['link']})")
    return notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    posts = json.loads(fetch(POSTS.format(q="raspored", after=f"{year - 2}-10-01T00:00:00")))
    post = next((p for p in posts if re.search(rf"Godišnji raspored odvoza.*za {year}\.", plain(p["title"]["rendered"]))),
                None)
    if not post:
        sys.exit(f"Nema objave s godišnjim rasporedom za {year}")
    pdf_url = re.search(r'href="([^"]+\.pdf)"', post["content"]["rendered"])
    if not pdf_url:
        sys.exit(f"Objava {post['link']} nema PDF")
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raspored.pdf"
        fetch(pdf_url.group(1), path)
        pdf = pdfplumber.open(path)
        areas, periods, probs = mixed(pdf.pages[0], year)
        problems += probs
        page = pdf.pages[1]
        words = char_words(page)
        legend = min((w["top"] for w in words if w["text"] in ("ŽUTI", "PLAVI")), default=page.height)
        cal, probs = read_page(Words(page, [w for w in words if w["bottom"] < legend]), year, PALETTE)
        problems += [f"kalendar: {p}" for p in probs]
    hol = set(pravila.blagdani(year))
    notes = notices(year, hol, problems)
    season_of = {}
    for season, spans in periods.items():
        for a, b in spans:
            d = a
            while d <= b:
                if d in season_of:
                    problems.append(f"{d} je u oba razdoblja")
                season_of[d] = season
                d += timedelta(days=1)
    d = date(year, 1, 1)
    while d.year == year:  # every day but Sundays must be in a period
        if d not in season_of and d.weekday() != 6:
            problems.append(f"{d} nije ni u zimskom ni u ljetnom razdoblju")
        d += timedelta(days=1)
    if len(areas) != 2:
        problems.append(f"str. 1: {len(areas)} područja, očekivano 2")
    for month in range(1, 13):
        n = sum(1 for d in cal if d.month == month)
        if not 3 <= n <= 6:
            problems.append(f"kalendar {year}-{month:02d}: {n} odvoza reciklabilnog")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "izvor": post["link"], "napomene": list(NAPOMENE), "zone": {}}
    hol_notes = set()
    for z, (area, days) in enumerate(areas.items(), 1):
        z = str(z)
        if set(days) != {"zima", "ljeto"}:
            problems.append(f"zona {z}: nema dana za oba razdoblja ({days})")
            continue
        rows = {d: c for d, c in cal.items()}
        for d, season in season_of.items():
            if HEAD[d.weekday()] in days[season].split():
                rows[d] = rows.get(d, "") + "M"
                if d in hol:
                    hol_notes.add(d)
        for m in range(1, 13):
            n = sum("M" in c for d, c in rows.items() if d.month == m)
            if not 4 <= n <= 10:
                problems.append(f"zona {z} {year}-{m:02d}: {n} odvoza miješanog otpada")
        names = [x.strip() for x in area.split(",")]
        name_days = {s: " i ".join(podaci.DAYS[pravila.DANI[k]] for k in v.split()) for s, v in days.items()}
        data["zone"][z] = {
            "jls": "Murter-Kornati",
            "podrucje": f"Murter (zimi {name_days['zima']}, ljeti {name_days['ljeto']}) – {', '.join(names[:3])}, …",
            "opis": f"naselje Murter, područje: {area}", "ulice": names,
            "raw": {**old["zone"].get(z, {}).get("raw", {}),
                    str(year): podaci.month_lines([(d, c, False) for d, c in rows.items()])},
        }
        print(f"zona {z} ({names[0]}, …; zimi {days['zima']}, ljeti {days['ljeto']}): {len(rows)} dana")
    confirmed = {n[:6] for n in notes}
    left = [f"{d:%d.%m.}" for d in sorted(hol_notes) if f"{d:%d.%m.}" not in confirmed]
    for n in notes:
        print(f"   {n}")
    if left:
        print(f"   blagdani na dan odvoza miješanog otpada bez obavijesti (zadržano po rasporedu): {', '.join(left)}")
        data["napomene"].append("Za blagdane na dan odvoza miješanog otpada pomak nije objavljen ("
                                + ", ".join(left) + "); upisani su datumi po rasporedu. Pratiti obavijesti "
                                "na murtela-murter.hr.")
    if notes:
        data["napomene"].append("Obavijesti: " + "; ".join(notes) + ".")
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
