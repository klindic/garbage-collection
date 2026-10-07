"""Karlovac: Čistoća d.o.o. Karlovac (cistocaka.hr), one PDF per city district or local board.

    python3 -m izvori.karlovac_cistoca [--year 2026]

The schedule page (read through the WordPress API) links one PDF per gradska četvrt and mjesni odbor
for each year. A PDF is one table: rows are waste types, columns are months, cells hold dd.mm. dates,
and dates printed in red are moved because of a holiday. Districts with blocks of flats colour the
cells (legend under the table): purple dates are for houses and buildings, peach dates for buildings
only, so those districts become two zones, "kuće" and "zgrade". In Drežnik-Hrnetić and Turanj biowaste
is collected from buildings only, so they are split the same way. The footer gives the streets and
settlements, house numbers with their own weekly round, and the mobile recycling yard dates.
Općina Draganić (also served by Čistoća) publishes JPG images only and is not covered.
"""
import argparse
import html
import json
import re
import sys
import tempfile
import time
import urllib.error
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
from izvori.sisak_gos import fetch
from pravila import blagdani

SLUG = "karlovac-cistoca"
SITE = "https://cistocaka.hr"
PAGE = SITE + "/raspored-odvoza-u-primjeni-od-svibnja/"
API = SITE + "/wp-json/wp/v2/pages?slug=raspored-odvoza-u-primjeni-od-svibnja"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ",
          "KOLOVOZ", "RUJAN", "LISTOPAD", "STUDENI", "PROSINAC"]
ROWS = [("MIJEŠANI", "M"), ("PLASTIKA", "P"), ("PAPIR", "K"), ("BIO", "B"), ("MOBILNO", "R")]
RED, BLACK, WHITE = (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
# collections per zone and year: (min, max); "M2" is mixed waste for buildings in districts with two rounds
COUNTS = {"M": (50, 56), "M2": (100, 112), "P": (11, 28), "K": (11, 28), "B": (50, 56)}
PROVIDER = {
    "davatelj": "Čistoća d.o.o. Karlovac",
    "web": SITE,
    "zupanija": "Karlovačka",
    "jls": ["Karlovac"],
    "napomene": [
        "Datumi označeni kao pomaknuti su dani odvoza zbog blagdana ili praznika.",
        "U gradskim četvrtima sa zgradama zgrade (kolektivno stanovanje) imaju dodatne odvoze; "
        "obiteljske kuće imaju samo zajedničke odvoze (zona \"kuće\").",
        "Mobilno reciklažno dvorište obilazi mjesne odbore prema rasporedu u napomeni zone.",
        "Općina Draganić (također Čistoća d.o.o.) objavljuje raspored samo kao slike i nije uključena.",
    ],
}

_last = [0.0]


def get(url, dest=None, tries=4):
    """fetch() with at most 2 requests per second and retries with backoff."""
    for i in range(tries):
        time.sleep(max(0.0, _last[0] + 0.5 - time.monotonic()))
        _last[0] = time.monotonic()
        try:
            return fetch(url, dest)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if i == tries - 1 or getattr(e, "code", 500) < 500:
                raise
            time.sleep(2 * 2 ** i)


def year_pdfs(year):
    """PDF URLs listed under the page's 'ZA <year>. GODINU' heading, in page order."""
    content = json.loads(get(API))[0]["content"]["rendered"]
    parts = re.split(r"(\d{4})\.\s*GODINU", content)
    urls = []
    for y, section in zip(parts[1::2], parts[2::2]):
        if int(y) == year:
            for url in re.findall(r'href="([^"]+\.pdf)"', section):
                url = html.unescape(url).replace("http://", "https://")
                if url not in urls:
                    urls.append(url)
    return urls


def col(w):
    c = w.get("non_stroking_color") or (0,)
    c = (c * 3) if len(c) == 1 else c
    return tuple(round(float(v), 3) for v in c[:3])


def read_pdf(path, year):
    """{title, rows: [(date, code, moved, who)], footer: [paragraphs], legend} and problems.

    who is "both" (houses and buildings) or "zgrade" (buildings only)."""
    page = pdfplumber.open(path).pages[0]
    words = page.extract_words(extra_attrs=["non_stroking_color"])
    text = page.extract_text() or ""
    problems = []
    if f"ZA {year}. GODINU" not in " ".join(text.split()):
        problems.append(f"no 'ZA {year}. GODINU' in the heading")
    m = re.search(r"^(GRADSKA ČETVRT|MJESNI ODBOR) ([A-ZČĆŽŠĐ][A-ZČĆŽŠĐ \-–]+)$", text, re.M)
    title = (m.group(1).capitalize() + " " + m.group(2).strip().title()) if m else None
    if not title:
        problems.append("no GRADSKA ČETVRT / MJESNI ODBOR title")
    heads = {w["text"]: w for w in words if w["text"] in MONTHS}
    if len(heads) != 12:
        return None, problems + [f"month headings found: {sorted(heads)}"]
    lines = sorted({round(r["top"]) for r in page.rects if r["height"] < 2 and r["width"] > 200})
    table_left = min(w["x0"] for w in heads.values()) - 5
    fills = [r for r in page.rects if r["fill"] and r["width"] > 3 and r["height"] > 3]

    # legend: the coloured square left of each legend line
    legend = {}
    for w in words:
        if w["text"] in ("individualnog", "samo") and w["top"] > lines[-1]:
            cy = (w["top"] + w["bottom"]) / 2
            sq = [r for r in fills if r["x1"] < w["x0"] and r["top"] <= cy <= r["bottom"]]
            if sq:
                legend[col(sq[0])] = "both" if w["text"] == "individualnog" else "zgrade"
    if "samo kolektivnog" in text and len(legend) != 2:
        problems.append(f"legend colours not found: {legend}")

    def band(cy):
        for a, b in zip(lines, lines[1:]):
            if a <= cy < b:
                return a, b
        return None

    def label(bd):
        t = " ".join(w["text"] for w in words if w["x1"] < table_left and bd[0] <= (w["top"] + w["bottom"]) / 2 < bd[1])
        return next((code for key, code in ROWS if key in t.upper()), None), t

    rows = []
    for w in words:
        if not re.fullmatch(r"\d\d\.\d\d\.", w["text"]):
            continue
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        day, month = int(w["text"][:2]), int(w["text"][3:5])
        try:
            d = date(year, month, day)
        except ValueError:
            problems.append(f"impossible date {w['text']}")
            continue
        head = min(heads, key=lambda h: abs((heads[h]["x0"] + heads[h]["x1"]) / 2 - cx))
        if MONTHS.index(head) + 1 != month:
            problems.append(f"{w['text']} sits in the {head} column")
        bd = band(cy)
        code, lab = label(bd) if bd else (None, "")
        if code is None:
            problems.append(f"{w['text']}: not in a known row ({lab!r})")
            continue
        colour = col(w)
        if colour not in (RED, BLACK):
            problems.append(f"{w['text']}: text colour {colour}")
        under = [r for r in fills if r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"]]
        fill = col(min(under, key=lambda r: r["width"] * r["height"])) if under else WHITE
        who = legend.get(fill, "both" if fill == WHITE or not legend else None)
        if who is None:
            problems.append(f"{w['text']}: cell colour {fill} is not in the legend")
            continue
        rows.append((d, code, colour == RED, who))

    footer = page.within_bbox((0, lines[-1] + 1, page.width, page.height)).extract_text() or ""
    paras = []
    for line in footer.splitlines():
        if re.match(r"(Kalendar|Miješani|Lokacija|Raspored|Datumi)", line):
            paras.append(line.strip())
        elif paras:
            paras[-1] += " " + line.strip()
    yard_times = sorted(set(re.findall(r"\d\d-\d\d h", text)))
    return {"title": title, "rows": rows, "footer": paras, "legend": legend, "yard_times": yard_times}, problems


def streets(paras):
    """Streets/settlements from 'Kalendar se odnosi na ... uključuje ...: a, b, c i d'."""
    para = next((p for p in paras if p.startswith("Kalendar se odnosi")), "")
    if ":" not in para:
        return []
    items = [s.strip(" .") for s in para.split(":", 1)[1].split(",")]
    out = []
    for it in items:
        if not it:
            continue
        if out and (it[0].isdigit() or len(out[-1]) == 1):
            out[-1] += (". " if len(out[-1]) == 1 else ", ") + it  # "R, Strohala"; "..., 1 do 79B - neparni"
            continue
        out.append(it)
    if out and " i " in out[-1] and not re.search(r"\d", out[-1]):
        out[-1:] = out[-1].split(" i ")  # "Borlin Gaj i Tičarnica"
    return out


def check(name, dates, problems, hol):
    """One weekday per weekly round (two for monthly rows), red dates next to a public holiday.

    A single black date off the weekday in a weekly round is printed, not refused: the PDFs have
    such cases (Novi Centar 09.12.2026 instead of 08.12.) and the provider's date is what people get."""
    by = {}
    for d, code, moved, series in dates:
        by.setdefault((code, series), []).append((d, moved))
    for (code, series), ds in by.items():
        days = Counter(d.weekday() for d, moved in ds if not moved)
        if len(ds) >= 40:
            main_day = days.most_common(1)[0][0]
            odd = [d for d, moved in ds if not moved and d.weekday() != main_day]
            if len(odd) > 1:
                problems.append(f"{name} {code}/{series}: regular dates on weekdays {dict(days)}")
            for d in odd:
                print(f"   UPOZORENJE {name} {code}: {d:%d.%m.} nije {podaci.DAYS[main_day]} kao ostali odvozi")
        elif len(days) > 2:
            problems.append(f"{name} {code}/{series}: regular dates on weekdays {dict(days)}")
        for d, moved in ds:
            if moved and not any(abs((d - h).days) <= 7 for h in hol):
                problems.append(f"{name} {code} {d}: marked as moved, but no holiday that week")


def zone_rows(rows, who):
    """[(date, codes, moved)] for houses ("kuće": dates for both) or buildings ("zgrade": all)."""
    out = {}
    for d, code, moved, w in rows:
        if code == "R" or (who == "kuće" and w == "zgrade"):
            continue
        codes, mv = out.get(d, ("", False))
        out[d] = (codes + code, mv or moved)
    return sorted((d, c, mv) for d, (c, mv) in out.items())


def count_problems(name, rows, buildings):
    problems = []
    n = Counter(c for _, codes, _ in rows for c in codes)
    for code in "MPKB":
        lo, hi = COUNTS["M2" if code == "M" and buildings else code]
        if code == "B" and not n[code]:
            continue
        if not lo <= n[code] <= hi:
            problems.append(f"{name}: {n[code]}x {code}, expected {lo}-{hi}")
    dup = Counter(c for _, codes, _ in rows for c in codes if codes.count(c) > 1)
    if dup:
        problems.append(f"{name}: same type twice on one day {dict(dup)}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    urls = year_pdfs(year)
    if len(urls) < 30:
        sys.exit(f"Samo {len(urls)} PDF-ova za {year} na {PAGE}, očekivano ~38. Ništa nije upisano.")
    hol = blagdani(year)
    zones, ok, n = {}, True, 0
    with tempfile.TemporaryDirectory() as tmp:
        for url in urls:
            name = url.rsplit("/", 1)[1]
            pdf = Path(tmp) / name
            get(url, pdf)
            if not pdf.read_bytes().startswith(b"%PDF"):
                print(f"{name}: nije PDF")
                ok = False
                continue
            info, problems = read_pdf(pdf, year)
            if info:
                check(name, [(d, c, m, w) for d, c, m, w in info["rows"] if c != "R"], problems, hol)
            if problems:
                print(f"{name}: PROBLEM")
                for p in problems[:15]:
                    print(f"   {p}")
                ok = False
                continue
            rows, title, paras = info["rows"], info["title"], info["footer"]
            bio_buildings = any("BIOOTPADA odnosi se na kolektivno" in p for p in paras)
            if bio_buildings:
                rows = [(d, c, m, "zgrade" if c == "B" else w) for d, c, m, w in rows]
            notes = [p for p in paras if p.startswith(("Miješani", "Lokacija"))]
            yard = sorted(d for d, c, _, _ in rows if c == "R")
            if yard:
                notes.append("Mobilno reciklažno dvorište: " + ", ".join(f"{d:%d.%m.}" for d in yard)
                             + (f" ({', '.join(info['yard_times'])})" if info["yard_times"] else "") + ".")
            split = bool(info["legend"]) or bio_buildings
            base = {"jls": "Karlovac", "ulice": streets(paras)}
            for who in (("kuće", "zgrade") if split else (None,)):
                zrows = zone_rows(rows, who)
                problems = count_problems(f"{name} {who or ''}".strip(), zrows, who == "zgrade" and bool(info["legend"]))
                if problems:
                    print("\n".join(f"   PROBLEM {p}" for p in problems))
                    ok = False
                n += 1
                zone = {**base, "podrucje": title + (f" ({'obiteljske kuće' if who == 'kuće' else 'zgrade'})" if who else "")}
                znotes = list(notes)
                if who == "kuće":
                    znotes.insert(0, "Raspored za obiteljske kuće (individualno stanovanje).")
                elif who == "zgrade":
                    znotes.insert(0, "Raspored za zgrade (kolektivno stanovanje), s dodatnim odvozima samo za zgrade.")
                if znotes:
                    zone["napomena"] = " ".join(znotes)
                old = data["zone"].get(str(n), {})
                zone["raw"] = {**(old.get("raw", {}) if old.get("podrucje") == zone["podrucje"] else {}),
                               str(year): podaci.month_lines(zrows)}
                zones[str(n)] = zone
                cnt = Counter(c for _, codes, _ in zrows for c in codes)
                moved = sum(1 for *_, mv in zrows if mv)
                print(f"Zona {n}: {zone['podrucje']}: " + ", ".join(f"{c} {cnt[c]}" for c in "MBPK" if cnt[c])
                      + f", pomaknuto {moved}, ulica {len(zone['ulice'])}")
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(urls)} PDF-ova, {len(zones)} zona)")


if __name__ == "__main__":
    main()
