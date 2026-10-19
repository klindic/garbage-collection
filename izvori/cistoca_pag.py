"""Pag: Čistoća Pag d.o.o. (cistocapag.hr), quarterly PDFs for four areas of the town of Pag.

    python3 -m izvori.cistoca_pag [--year 2026]

The page "Prikupljanje otpada" links only the current quarter (one PDF per area: Grad Pag; Šimuni,
Košljun, Proboj; Miškovići, Dinjiška, Vlašići, Gorica; Bošana, Dubrava, Sv. Marija, Sv. Marko); older
quarters are found through the WordPress media API. Each PDF is a table month x weekday whose cells read
"DD.MM. – miješani komunalni otpad" (or "D." with the type below it in the older files). The words are
read with pdfplumber, every cell is put in its weekday column (the date must match that weekday) and
its text gives the type: mixed waste, "selektivni otpad" (plastic, paper, glass and metal together, code
P) or biowaste. Holidays are built into the dates: "NEMA ODVOZA" cells are skipped, a cell marked
"(Zamjena za 06.04.)" or the date named in "ODVOZ ĆE SE IZVRŠITI ... 07.01.2026." is marked as moved,
and so is a type on a weekday it otherwise never has, within three days after a public holiday.
When quarters overlap, the newer upload wins; dates already in podaci/cistoca-pag.json outside the
published quarters are kept.
"""
import argparse
import json
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

SLUG = "cistoca-pag"
SITE = "https://cistocapag.hr"
PAGE = SITE + "/prikupljanje-otpada/"
MEDIA = SITE + "/wp-json/wp/v2/media?mime_type=application/pdf&per_page=100&page={page}&after={after}&before={before}" \
               "&_fields=date,source_url"
# file name prefix: (zone, area heading in the PDF, description, settlements)
AREAS = {
    "GRAD-PAG": ("1", "GRAD PAG", "Grad Pag", ["Pag"]),
    "SIMUNI": ("2", "ŠIMUNI, KOŠLJUN, PROBOJ", "Šimuni, Košljun, Proboj", ["Šimuni", "Košljun", "Proboj"]),
    "MISKOVICI": ("3", "MIŠKOVIĆI, DINJIŠKA, VLAŠIĆI, GORICA", "Miškovići, Dinjiška, Vlašići, Gorica",
                  ["Miškovići", "Dinjiška", "Vlašići", "Gorica"]),
    "BOSANA": ("4", "BOŠANA, DUBRAVA, MARIJA, MARKO", "Bošana, Dubrava, Sv. Marija, Sv. Marko",
               ["Bošana", "Dubrava", "Sveta Marija", "Sveti Marko"]),
}
MONTHS = ["Siječanj", "Veljača", "Ožujak", "Travanj", "Svibanj", "Lipanj", "Srpanj", "Kolovoz", "Rujan",
          "Listopad", "Studeni", "Prosinac"]
DAYS = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak", "Subota", "Nedjelja"]
TYPES = (("miješani", "M"), ("selektivni", "P"), ("biootpad", "B"), ("biorazgradivi", "B"))
PROVIDER = {
    "davatelj": "Čistoća Pag d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zadarska",
    "jls": ["Pag"],
    "nazivi": {"P": "Selektivni otpad (plastika, papir, staklo, metali)"},
}
NAPOMENE = [
    "Raspored se objavljuje tromjesečno po područjima; pomaci zbog blagdana upisani su u raspored "
    "(označeni kao pomaknuti), a na dane s napomenom \"NEMA ODVOZA\" nema odvoza.",
    "Odvoz otpada počinje u 06:00 sati; spremnike iznijeti na javnu površinu najkasnije do 06:00 sati.",
    "Reciklažno dvorište Sveti Kuzam bb, Pag: pon 08:00-14:30, uto 10:00-16:30, sri-pet 08:00-14:30, "
    "sub 08:00-13:00; nedjeljom i blagdanom zatvoreno.",
    "Kontakt: 023/600-265, info@cistocapag.hr.",
]


def find_files(year):
    """[(upload date, url)] of the area PDFs: links on the page and uploads around the year."""
    found = {u: "" for u in re.findall(r'href="(https?://[^"]+\.pdf)"', fetch(PAGE).decode("utf-8", "replace"))}
    for page in range(1, 10):
        items = json.loads(fetch(MEDIA.format(page=page, after=f"{year - 1}-10-01T00:00:00",
                                              before=f"{year + 1}-02-01T00:00:00")))
        found.update({x["source_url"]: x["date"] for x in items})
        if len(items) < 100:
            break
    out = []
    for url, when in found.items():
        name = url.rsplit("/", 1)[1]
        prefix = next((p for p in AREAS if name.upper().startswith(p)), None)
        if prefix:
            m = re.search(r"/uploads/(\d{4})/(\d\d)/", url)
            out.append((when or (f"{m.group(1)}-{m.group(2)}" if m else ""), url, prefix))
    return sorted(out)


def joined(words):
    """Words with the pieces a font split ("Poned" "jeljak") joined again."""
    out = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if out and abs(out[-1]["top"] - w["top"]) < 1 and 0 <= w["x0"] - out[-1]["x1"] < 1:
            out[-1] = {**out[-1], "text": out[-1]["text"] + w["text"], "x1": w["x1"]}
        else:
            out.append(dict(w))
    return out


def line_centre(words, w):
    """Centre x of the text line that starts with word w (words on the same line, small gaps)."""
    row = sorted((v for v in words if abs(v["top"] - w["top"]) < 2 and v["x0"] >= w["x0"]), key=lambda v: v["x0"])
    x1 = w["x1"]
    for v in row[1:]:
        if v["x0"] - x1 > 8:
            break
        x1 = v["x1"]
    return (w["x0"] + x1) / 2


def read_pdf(path, year):
    """(area heading, (start, end), {date: (codes, moved)}, moved targets, problems)."""
    page = pdfplumber.open(path).pages[0]
    words = page.extract_words()
    text = " ".join(page.extract_text().split())
    problems = []
    area = re.search(r">\s*(.+?)\s*RASPORED PRIKUPLJANJA", text)
    m = re.search(r"\((\d\d)\.(\d\d)\.(\d{4})\.? [–-] (\d\d)\.(\d\d)\.(\d{4})\.?\)", text)
    if not area or not m:
        return None, None, {}, set(), ["nema naslova s područjem i razdobljem"]
    g = list(map(int, m.groups()))
    period = (date(g[2], g[1], g[0]), date(g[5], g[4], g[3]))
    heads = [w for w in words if w["text"] in MONTHS]
    found, targets = {}, set()
    for i, h in enumerate(heads):
        nxt = words[words.index(h) + 1]["text"]
        my = int(nxt.rstrip(".")) if nxt.rstrip(".").isdigit() else None
        month = MONTHS.index(h["text"]) + 1
        bottom = heads[i + 1]["top"] if i + 1 < len(heads) else page.height
        block = [w for w in words if h["bottom"] < w["top"] < bottom - 1]
        heads_row = joined(block)
        cols = {DAYS.index(w["text"]): (w["x0"] + w["x1"]) / 2 for w in heads_row if w["text"] in DAYS}
        if sorted(cols) != list(range(5)) or my is None:
            problems.append(f"{h['text']}: nema godine ili zaglavlja dana (pon-pet)")
            continue
        head_bottom = max(w["bottom"] for w in heads_row if w["text"] in DAYS)
        stop = min([w["top"] for w in block if w["text"] == "Napomena:"] + [bottom])
        block = [w for w in block if head_bottom < w["top"] < stop - 1]

        def column(x):
            return min(cols, key=lambda c: abs(cols[c] - x))

        anchors = []
        for k, w in enumerate(block):
            left = [v for v in block if abs(v["top"] - w["top"]) < 2 and v["x1"] <= w["x0"] and w["x0"] - v["x1"] < 8]
            if any(v["text"].lower() == "za" for v in left):  # "(Zamjena za 06.04.)"
                continue
            if re.fullmatch(r"\d\d\.\d\d\.[–-]?", w["text"]):
                day, mon = int(w["text"][:2]), int(w["text"][3:5])
            elif re.fullmatch(r"\d{1,2}\.[–-]?", w["text"]):
                day, mon = int(w["text"].split(".")[0]), month
            else:
                continue
            anchors.append((w, day, mon))
        tops = sorted({round(w["top"]) for w, _, _ in anchors})
        rows = [t for i, t in enumerate(tops) if i == 0 or t - tops[i - 1] > 4]
        for w, day, mon in anchors:
            try:
                d = date(my, mon, day)
            except ValueError:
                problems.append(f"{h['text']} {my}: nemoguć datum {w['text']}")
                continue
            col = column(line_centre(block, w))
            r = max(i for i, t in enumerate(rows) if t <= round(w["top"]) + 4)
            lo, hi = rows[r] - 4, (rows[r + 1] - 4 if r + 1 < len(rows) else stop)
            cell = " ".join(v["text"] for v in sorted(block, key=lambda v: (round(v["top"]), v["x0"]))
                            if lo <= v["top"] < hi and column((v["x0"] + v["x1"]) / 2) == col)
            low = cell.lower()
            if mon != month:
                problems.append(f"{d}: u bloku za {h['text']}")
            if col != d.weekday():
                problems.append(f"{d}: u stupcu {DAYS[col]}, a to je {DAYS[d.weekday()]}")
            if d in found:
                problems.append(f"{d}: dvaput u rasporedu")
            codes = "".join(sorted({c for word, c in TYPES if word in low}))
            moved = "zamjena za" in low
            for t in re.findall(r"izvršiti.*?(\d\d)\.(\d\d)\.(\d{4})", low):
                targets.add(date(int(t[2]), int(t[1]), int(t[0])))
            if codes:
                found[d] = (codes, moved)
            elif "nema odvoza" not in low and "izvršiti" not in low and "-----" not in low:
                problems.append(f"{d}: nepoznat sadržaj ćelije {cell!r}")
    return area.group(1), period, found, targets, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    files = find_files(year)
    problems, per_zone = [], {z: ({}, []) for z, *_ in AREAS.values()}
    with tempfile.TemporaryDirectory() as tmp:
        for when, url, prefix in files:
            path = Path(tmp) / url.rsplit("/", 1)[1]
            fetch(url, path)
            area, period, found, targets, probs = read_pdf(path, year)
            zone, heading = AREAS[prefix][:2]
            if area and not all(n in area for n in heading.split(", ")):
                probs.append(f"područje {area!r}, očekivano {heading!r}")
            if not period:
                problems += [f"{path.name}: {p}" for p in probs]
                continue
            if not period[0].year <= year <= period[1].year:
                continue
            for d in targets:
                if d in found:
                    found[d] = (found[d][0], True)
                elif d.year == year:
                    probs.append(f"pomak na {d} bez odvoza tog dana")
            for d in found:
                if not period[0] <= d <= period[1]:
                    probs.append(f"{d} izvan razdoblja {period[0]:%d.%m.%Y.}-{period[1]:%d.%m.%Y.}")
            problems += [f"{path.name}: {p}" for p in probs]
            dates, periods = per_zone[zone]
            for d in [d for d in dates if period[0] <= d <= period[1]]:  # newer upload replaces the quarter
                del dates[d]
            dates.update(found)
            periods.append(period)
            print(f"{path.name}: {period[0]:%d.%m.%Y.}-{period[1]:%d.%m.%Y.}, {len(found)} dana odvoza")
    slug_path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(slug_path) if slug_path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": list(NAPOMENE), "zone": {}}
    covered, hol = [], set(pravila.blagdani(year))
    for prefix, (zone, heading, desc, places) in AREAS.items():
        dates, periods = per_zone[zone]
        if not periods:
            problems.append(f"zona {zone} ({desc}): nema rasporeda za {year}")
            continue
        rows = {d: v for d, v in dates.items() if d.year == year}
        # keep dates of the year from earlier runs that no published quarter covers any more
        for d, codes, moved in podaci.iter_dates(old["zone"].get(zone, {"raw": {}}), year):
            if not any(a <= d <= b for a, b in periods):
                rows[d] = (codes, moved)
        # a type on a weekday it is otherwise never collected on, right after a public holiday, is a shift
        # even without a "zamjena" note (e.g. 19.11. after "18.11. NEMA ODVOZA")
        usual = Counter((c, d.weekday()) for d, (codes, _) in rows.items() for c in codes)
        for d, (codes, moved) in sorted(rows.items()):
            if not moved and any(usual[(c, d.weekday())] < 3 for c in codes) \
                    and any(d - timedelta(days=k) in hol for k in range(1, 4)):
                rows[d] = (codes, True)
                print(f"   zona {zone}: {d:%d.%m.} {codes} neuobičajen dan nakon blagdana – označeno kao pomak")
        months = sorted({d.month for d in rows})
        for m in months:
            n = Counter(c for d, (codes, _) in rows.items() if d.month == m for c in codes)
            if not 4 <= n["M"] <= 14:
                problems.append(f"zona {zone} {year}-{m:02d}: {n['M']} odvoza miješanog otpada")
        covered.append((min(rows), max(rows)))
        raw = {**old["zone"].get(zone, {}).get("raw", {}),
               str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])}
        data["zone"][zone] = {"jls": "Pag", "podrucje": desc, "ulice": places, "raw": raw}
        print(f"zona {zone} ({desc}): {len(rows)} dana, {min(rows):%d.%m.}-{max(rows):%d.%m.%Y.}")
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    first, last = min(a for a, _ in covered), max(b for _, b in covered)
    if first > date(year, 1, 7) or last < date(year, 12, 24):
        data["napomene"].insert(0, f"Za {year}. objavljen je raspored od {first:%d.%m.} do {last:%d.%m.%Y.} "
                                   "(raspored za iduće tromjesečje izlazi početkom razdoblja).")
    podaci.save(SLUG, data)
    print(f"Upisano: {slug_path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
