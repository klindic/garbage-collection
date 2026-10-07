"""Kutina and Velika Ludina: Eko Moslavina d.o.o. (eko-moslavina.hr), one PDF per town or municipality.

    python3 -m izvori.kutina_eko_moslavina [--year 2026]

The schedule page links, per JLS and year, "2_Raspored_<year> MKO BKO PAPIR PLASTIKA.pdf": one block per
weekday group ("PONEDJELJKOM" ...) with mixed waste and biowaste every week on that day, monthly
PLASTIKA and PAPIR dates ("05.01.; 02.02.; ...", wrapped over three lines, so split by x position
under the column headings) and the settlements/streets of the group. On Christmas, New Year's Day
and Labour Day there is no collection; it is done on the first following Saturday (the PDF footer
says so, and the plastic/paper lists already have those Saturdays), so the weekly rounds get the
same rule.
"""
import argparse
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.sisak_gos import fetch
from pravila import tjedno

SLUG = "kutina-eko-moslavina"
SITE = "https://eko-moslavina.hr"
PAGE = SITE + "/raspored"
JLS = {"Kutina": ("Kutina", "GRAD KUTINA"), "V_Ludina": ("Velika Ludina", "OPĆINA VELIKA LUDINA")}
DAYS = {"PONEDJELJKOM": "pon", "UTORKOM": "uto", "SRIJEDOM": "sri", "ČETVRTKOM": "čet", "PETKOM": "pet"}
NO_COLLECTION = [(1, 1), (5, 1), (12, 25)]  # Nova godina, Praznik rada, Božić -> first following Saturday
PROVIDER = {
    "davatelj": "Eko Moslavina d.o.o.",
    "web": SITE,
    "zupanija": "Sisačko-moslavačka",
    "jls": ["Kutina", "Velika Ludina"],
    "napomene": [
        "Posude moraju biti na javnoj površini dostupne djelatnicima od 07 sati na dan odvoza.",
        "Na Božić, Novu godinu i Praznik rada nema odvoza; otpad se odvozi prvu sljedeću subotu.",
        "Reciklažno dvorište Kutina, Radićeva 298B: pon-pet 08-18, sub 08-13.",
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


def schedule_pdfs(year):
    """{folder: url} of the year's '2_Raspored ... MKO BKO PAPIR PLASTIKA' PDFs on the schedule page."""
    page = get(PAGE).decode("utf-8", "replace")
    found = {}
    for href in re.findall(r'href="([^"]+\.pdf)"', page):
        m = re.search(rf"Rasporedi_{year}/([^/]+)/2_Raspored[^/]*MKO BKO PAPIR PLASTIKA[^/]*\.pdf$", href)
        if m:
            found[m.group(1)] = urllib.parse.urljoin(SITE, urllib.parse.quote(href, safe="/:%"))
    return found


def moved_to(year):
    """{holiday: first following Saturday} for the three days without collection."""
    out = {}
    for m, d in NO_COLLECTION:
        h = date(year, m, d)
        if h.weekday() < 5:
            out[h] = h + timedelta(days=5 - h.weekday())
    return out


def streets(text):
    """'Batina, Husain; GRAD KUTINA - ULICE: A.B. Šimića, ...' -> (['Batina', 'Husain'], ['A.B. Šimića', ...])."""
    parts = re.split(r"GRAD KUTINA - ULICE:", " ".join(text.split()), flags=re.I) + [""]
    return [[s.strip(" .") for s in re.split(r"[,;]", part) if s.strip(" .")] for part in parts[:2]]


def read_pdf(path, year):
    """[(weekday, plastika dates, papir dates, streets text)] and problems."""
    page = pdfplumber.open(path).pages[0]
    words = page.extract_words()
    full = " ".join((page.extract_text() or "").split())
    problems = [] if f"{year}. godinu" in full else [f"no '{year}. godinu' in the title"]
    if "prvu narednu subotu" not in full:
        problems.append("the holiday rule (first following Saturday) is no longer in the PDF")

    def find(text):
        return sorted((w for w in words if w["text"] == text), key=lambda w: w["top"])

    plast, papir, nas, posude = find("PLASTIKA"), find("PAPIR"), find("NASELJA/ULICE"), find("Posude")
    day_words = sorted((w for w in words if w["text"] in DAYS), key=lambda w: w["top"])
    if not (len(plast) == len(papir) == len(nas) == len(day_words)) or not posude or not plast:
        return [], problems + [f"blocks: {len(plast)} PLASTIKA, {len(papir)} PAPIR, {len(nas)} NASELJA, "
                               f"{len(day_words)} weekdays"]
    blocks = []
    for i, (pl, pa, na, dw) in enumerate(zip(plast, papir, nas, day_words)):
        end = plast[i + 1]["top"] if i + 1 < len(plast) else posude[0]["top"]
        if not pl["bottom"] < dw["top"] < na["top"]:
            problems.append(f"{dw['text']} is not inside block {i + 1}")
        split = ((pl["x0"] + pl["x1"]) / 2 + (pa["x0"] + pa["x1"]) / 2) / 2
        cols = {"P": [], "K": []}
        for w in words:
            if pl["bottom"] < w["top"] < na["top"] and re.fullmatch(r"\d\d\.\d\d\.;?", w["text"]):
                d, m = int(w["text"][:2]), int(w["text"][3:5])
                try:
                    cols["P" if (w["x0"] + w["x1"]) / 2 < split else "K"].append(date(year, m, d))
                except ValueError:
                    problems.append(f"impossible date {w['text']}")
            elif pl["bottom"] < w["top"] < na["top"] and w["text"] not in DAYS:
                problems.append(f"unexpected {w['text']!r} among the dates of {dw['text']}")
        text = page.crop((0, na["bottom"] + 1, page.width, end - 1)).extract_text() or ""
        blocks.append((DAYS[dw["text"]], cols["P"], cols["K"], text))
    return blocks, problems


def check(day, dates, code, year, sat):
    """Monthly list: 12 dates, one per month, in order, on the group's weekday or a replacement Saturday."""
    problems = []
    if [d.month for d in dates] != list(range(1, 13)):
        problems.append(f"{code}: months {[d.month for d in dates]} (wrong column split?)")
    wd = list(DAYS.values()).index(day)
    for d in dates:
        if d.weekday() != wd and d not in sat.values():
            problems.append(f"{code} {d:%d.%m.} is a {podaci.DAYS[d.weekday()]}, not {podaci.DAYS[wd]}")
        if d in sat:
            problems.append(f"{code} {d:%d.%m.} is a day without collection")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    pdfs = schedule_pdfs(year)
    if set(pdfs) != set(JLS):
        sys.exit(f"Raspored PDF-ovi za {year}: {sorted(pdfs)}, očekivano {sorted(JLS)}. Ništa nije upisano.")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    sat = moved_to(year)
    back = {s: h for h, s in sat.items()}
    zones, ok, n = {}, True, 0
    with tempfile.TemporaryDirectory() as tmp:
        for folder, (jls, title) in JLS.items():
            pdf = Path(tmp) / f"{folder}.pdf"
            get(pdfs[folder], pdf)
            blocks, problems = read_pdf(pdf, year)
            if title not in " ".join((pdfplumber.open(pdf).pages[0].extract_text() or "").split()):
                problems.append(f"no '{title}' in the PDF")
            if jls == "Kutina" and [b[0] for b in blocks] != list(DAYS.values()):
                problems.append(f"Kutina weekday groups {[b[0] for b in blocks]}")
            for day, plastic, paper, text in blocks:
                problems += check(day, plastic, "PLASTIKA", year, sat) + check(day, paper, "PAPIR", year, sat)
            if problems:
                print(f"{jls}: PROBLEM")
                for p in problems[:15]:
                    print(f"   {p}")
                ok = False
                continue
            for day, plastic, paper, text in blocks:
                n += 1
                rows = {}
                for d in tjedno(year, day):
                    rows[sat.get(d, d)] = ["MB", d in sat]
                for code, dates in (("P", plastic), ("K", paper)):
                    for d in dates:
                        rows.setdefault(d, ["", d in back])[0] += code
                rows = sorted((d, c, mv) for d, (c, mv) in rows.items())
                villages, town = streets(text)
                ulice = villages + town
                name = podaci.DAYS[list(DAYS.values()).index(day)]
                if jls == "Kutina":
                    desc = [", ".join(villages[:3]) + (", …" if len(villages) > 3 else "")] if villages else []
                    desc += ["dio ulica grada Kutine"] if town else []
                    podrucje = f"{name.capitalize()}: {' i '.join(desc)}"
                else:
                    podrucje = f"Općina Velika Ludina ({name})"
                cnt = Counter(c for _, codes, _ in rows for c in codes)
                if not 52 <= cnt["M"] <= 53 or cnt["P"] != 12 or cnt["K"] != 12 or not ulice:
                    print(f"   PROBLEM zona {n}: {dict(cnt)}, {len(ulice)} ulica")
                    ok = False
                old = data["zone"].get(str(n), {})
                keep = old.get("raw", {}) if old.get("podrucje") == podrucje else {}
                zones[str(n)] = {"jls": jls, "podrucje": podrucje, "ulice": ulice,
                                 "raw": {**keep, str(year): podaci.month_lines(rows)}}
                print(f"Zona {n} ({jls}, {name}): " + ", ".join(f"{c} {cnt[c]}" for c in "MBPK")
                      + f", pomaknuto {sum(mv for *_, mv in rows)}, ulica/naselja {len(ulice)}")
    if not ok:
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
