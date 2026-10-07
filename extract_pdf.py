#!/usr/bin/env python3
"""Read GOS zone schedule PDFs into the schedule data in odvoz.html.

    python3 extract_pdf.py 2027 1=RASPORED_2027_Zona_1.pdf 2=RASPORED_2027_Zona_2.pdf ...
    python3 extract_pdf.py 2027 1=... 2=... --write

Dates come from the PDF text (red date = moved because of a holiday that week), bin types
from the colour of the bin pictures under each date, sampled from a render of page 1.
Every date is checked against its printed weekday and every bin must belong to a date, so a
PDF in a different layout fails loudly instead of producing a wrong schedule.
Without --write it only prints what it found; with --write it stores the year's schedule
for those zones in odvoz.html (only if no zone had a problem).

Needs pdfplumber and Pillow (pip install pdfplumber pillow) and pdftoppm (poppler-utils).
"""
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

HTML = Path(__file__).with_name("odvoz.html")
DATA_RE = re.compile(r'(<script type="application/json" id="zones">\n)(.*?)(\n</script>)', re.S)
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj",
          "kolovoz", "rujan", "listopad", "studeni", "prosinac"]
DAYS = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DATE_RE = re.compile(r"^(\d{1,2})\.(" + "|".join(MONTHS) + r")$")
DPI = 200


def classify(img, box):
    """Bin type from the dominant colour inside an image's box: M black, B brown, P yellow, K blue."""
    x0, top, x1, bottom = (round(v * DPI / 72) for v in box)
    raw = img.crop((x0, top, x1, bottom)).tobytes()
    c = Counter()
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        if r > 200 and g > 180 and b < 110:
            c["P"] += 1
        elif b > 170 and r < 130 and g < 170:
            c["K"] += 1
        elif 80 < r < 180 and 40 < g < 120 and 20 < b < 100 and r - g > 25 and r - b > 35:
            c["B"] += 1
        elif r > 190 and g < 90 and b < 90:
            c["warn"] += 1  # red warning triangle in a moved cell
        elif r < 75 and g < 75 and b < 75:
            c["M"] += 1
    area = max(1, (x1 - x0) * (bottom - top))
    colour = {k: v for k, v in c.items() if k in "PKB" and v > 0.12 * area}
    if colour:
        return max(colour, key=colour.get)
    if c["warn"] > 0.05 * area:
        return "warn"
    if c["M"] > 0.25 * area:  # black wheels alone stay below this
        return "M"
    return "?"


def owner(cells, cx, top):
    """The date cell whose label is the closest one above (cx, top) in the same column."""
    best = None
    for d in cells:
        if abs(d["cx"] - cx) < 70 and d["bottom"] <= top + 1 and top - d["bottom"] < 40:
            if best is None or d["top"] > best["top"]:
                best = d
    return best


def extract(pdf_path, year):
    """Return ([(date, "MBPK" codes, moved)], problems) for one zone PDF."""
    import pdfplumber
    from PIL import Image

    page = pdfplumber.open(pdf_path).pages[0]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-r", str(DPI), "-png", "-singlefile",
                        str(pdf_path), f"{tmp}/p"], check=True)
        img = Image.open(f"{tmp}/p.png").convert("RGB")
    words = page.extract_words(extra_attrs=["non_stroking_color"])
    problems, cells = [], []
    for i, w in enumerate(words):
        m = DATE_RE.match(w["text"])
        if not m:
            continue
        nxt = words[i + 1] if i + 1 < len(words) else {"text": "", "top": -99, "x1": w["x1"]}
        weekday = nxt["text"].strip("()")
        d = date(year, MONTHS.index(m.group(2)) + 1, int(m.group(1)))
        if weekday not in DAYS or abs(nxt["top"] - w["top"]) > 2:
            problems.append(f"{d}: no weekday after {w['text']!r} (found {nxt['text']!r})")
        elif DAYS[d.weekday()] != weekday:
            problems.append(f"{d}: printed as {weekday}, but it is a {DAYS[d.weekday()]}")
        colour = tuple(round(float(v), 2) for v in (w["non_stroking_color"] or (0,)))
        red = colour[:3] == (1.0, 0.0, 0.0)
        if not red and any(colour):
            problems.append(f"{d}: unexpected text colour {colour}")
        cells.append({"date": d, "moved": red, "cx": (w["x0"] + nxt["x1"]) / 2,
                      "top": w["top"], "bottom": w["bottom"], "types": set(), "none": False})
    if not cells:
        return [], ["no dates found; is this a GOS zone schedule in the usual layout?"]
    for w in words:
        if w["text"] == "NEMA":  # "NEMA SAKUPLJANJA OTPADA" = no collection that day
            cell = owner(cells, (w["x0"] + w["x1"]) / 2, w["top"])
            if cell is None:
                problems.append(f"'NEMA' at {w['x0']:.0f},{w['top']:.0f} is under no date")
            else:
                cell["none"] = True
    first = min(c["top"] for c in cells) - 15
    pictures = [im for im in page.images if im["bottom"] >= first]  # skip logo and legend at the top
    # Small markers between the columns (e.g. a tiny bin with "2x" in Zona 5) are notes, not collections.
    widths = sorted(im["x1"] - im["x0"] for im in pictures)
    min_width = 0.6 * widths[len(widths) // 2] if widths else 0
    for im in pictures:
        if im["x1"] - im["x0"] < min_width:
            continue
        kind = classify(img, (im["x0"], im["top"], im["x1"], im["bottom"]))
        if kind == "warn":
            continue
        where = f"picture at {im['x0']:.0f},{im['top']:.0f}"
        cell = owner(cells, (im["x0"] + im["x1"]) / 2, im["top"])
        if kind == "?":
            problems.append(f"{where}: colour not recognised")
        elif cell is None:
            problems.append(f"{where} ({kind}) is under no date")
        elif kind in cell["types"]:
            problems.append(f"{cell['date']}: {kind} twice")
        else:
            cell["types"].add(kind)
    for c in cells:
        if c["types"] and c["none"]:
            problems.append(f"{c['date']}: bins and 'NEMA SAKUPLJANJA' in one cell")
        if not c["types"] and not c["none"]:
            problems.append(f"{c['date']}: no bins and no 'NEMA SAKUPLJANJA'")
    problems += [f"{d} listed {n} times" for d, n in Counter(c["date"] for c in cells).items() if n > 1]
    # Regular collection day: every date that is not red must fall on it, every red one must not.
    regular = Counter(DAYS[c["date"].weekday()] for c in cells if not c["moved"]).most_common(1)[0][0]
    for c in cells:
        on_regular = DAYS[c["date"].weekday()] == regular
        if c["moved"] == on_regular:
            problems.append(f"{c['date']}: {'red' if c['moved'] else 'black'} date on "
                            f"{DAYS[c['date'].weekday()]}, regular day is {regular}")
    rows = [(c["date"], "".join(t for t in "MBPK" if t in c["types"]), c["moved"])
            for c in sorted(cells, key=lambda c: c["date"]) if c["types"]]
    return rows, problems


def month_lines(rows):
    """["01-05 MP 01-12 BK ...", ...]: one string per month, "!" marks a moved date."""
    months = {}
    for d, codes, moved in rows:
        months.setdefault(d.month, []).append(f"{d:%m-%d} {codes}{'!' if moved else ''}")
    return [" ".join(v) for _, v in sorted(months.items())]


def format_data(data):
    """JSON for the data block in odvoz.html: one zone field per line, one month per line."""
    out = ["{", '  "zones": {']
    zones = sorted(data["zones"], key=int)
    for i, z in enumerate(zones):
        zone = data["zones"][z]
        out.append(f'    "{z}": {{')
        for k, v in zone.items():
            if k != "raw":
                out.append(f'      "{k}": {json.dumps(v, ensure_ascii=False)},')
        out.append('      "raw": {')
        years = sorted(zone["raw"])
        for j, y in enumerate(years):
            lines = zone["raw"][y]
            out.append(f'        "{y}": [')
            out += [f"          {json.dumps(l, ensure_ascii=False)}" + ("," if k < len(lines) - 1 else "")
                    for k, l in enumerate(lines)]
            out.append("        ]" + ("," if j < len(years) - 1 else ""))
        out.append("      }")
        out.append("    }" + ("," if i < len(zones) - 1 else ""))
    out += ["  }", "}"]
    text = "\n".join(out)
    assert json.loads(text) == data
    return text


def read_html():
    html = HTML.read_text(encoding="utf-8")
    return html, json.loads(DATA_RE.search(html).group(2))


def write_html(html, data):
    HTML.write_text(DATA_RE.sub(lambda m: m.group(1) + format_data(data) + m.group(3), html, count=1),
                    encoding="utf-8")


def main(argv):
    write = "--write" in argv
    args = [a for a in argv if a != "--write"]
    if len(args) < 2 or not args[0].isdigit() or not all("=" in a for a in args[1:]):
        sys.exit(__doc__)
    year = int(args[0])
    html, data = read_html()
    results, ok = {}, True
    for arg in args[1:]:
        zone, path = arg.split("=", 1)
        rows, problems = extract(Path(path), year)
        counts = Counter(t for _, codes, _ in rows for t in codes)
        print(f"Zona {zone}: {len(rows)} odvoza, pomaknutih {sum(m for *_, m in rows)}, "
              f"po vrsti {dict(sorted(counts.items()))}")
        for p in problems:
            print(f"   PROBLEM {p}")
        ok &= not problems
        results[zone] = month_lines(rows)
    if not ok:
        sys.exit("Ništa nije upisano: provjeri PROBLEM retke (drugačiji izgled PDF-a ili krivo odabrana godina).")
    if not write:
        print("Sve u redu. Ponovi s --write da se upiše u odvoz.html.")
        return
    for zone, lines in results.items():
        if zone not in data["zones"]:
            data["zones"][zone] = {"place": "TODO", "area": "TODO", "raw": {}}
            print(f"Zona {zone} je nova: upiši joj place i area u odvoz.html.")
        data["zones"][zone]["raw"][str(year)] = lines
    write_html(html, data)
    print(f"Upisano u {HTML.name}: {year}. za zone {', '.join(results)}. "
          "Provjeri napomene zona (note, noBioIn, pilotFrom) i pokreni gen_xlsx.py.")


if __name__ == "__main__":
    main(sys.argv[1:])
