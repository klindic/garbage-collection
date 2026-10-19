"""Pakoštane: LOŠI d.o.o. (losi.hr), mixed and recyclable waste by settlement, winter and summer weekday rules.

    python3 -m izvori.losi_pakostane [--year 2026]

The page "Odvoz i prikupljanje komunalnog otpada" links the schedule PDF of the year
(pakostane_raspored-<year>.pdf; the PDF itself carries no year, so the year must be in the file name).
It has two tables, "Zimski period od 1.10. do 31.5" and "Ljetni period od 1.6. do 30.9.", with one column
per weekday and the settlements below (Pakoštane, Drage, Vrana); in the Thursday column "- mko" marks mixed
waste and "- rec." recyclables, and a column headed "reciklabilni" holds recyclables only. The columns are
kept apart by the x position of the words (pdfplumber). The holiday rule is in the PDF's note: no
collection on the listed holidays, the next one follows the normal schedule (those dates are dropped).
Grad Obrovac is not included: its schedule exists only in an older, unlinked PDF (09/2025) without a year.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch

SLUG = "losi-pakostane"
SITE = "https://www.losi.hr"
PAGE = SITE + "/odvoz-i-prikupljanje-komunalnog-otpada"
DAYS = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak", "Subota", "Nedjelja"]
INSTR = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
PLACES = ["Pakoštane", "Drage", "Vrana"]
HOLIDAY_TEXT = "odvoz se ne obavlja. Sljedeći odvoz vrši se prema redovnom rasporedu."
PROVIDER = {
    "davatelj": "LOŠI d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Zadarska",
    "jls": ["Pakoštane"],
    "nazivi": {"P": "Reciklabilni otpad"},
}
NAPOMENE = [
    "Otok Vrgada (Općina Pakoštane) nije naveden u rasporedu.",
    "Grad Obrovac nije uključen: Loši na svojoj stranici navodi samo Općinu Pakoštane, a raspored za Obrovac postoji "
    "samo u starijem PDF-u iz rujna 2025. (raspored_odvoza_pak-obr.pdf) bez godine, koji više nije povezan sa "
    "stranice.",
]


def lines_of(page):
    out = []
    for w in sorted(page.extract_words(x_tolerance=2), key=lambda w: (w["top"], w["x0"])):
        if out and abs(out[-1][0] - w["top"]) < 3:
            out[-1][1].append(w)
        else:
            out.append((w["top"], [w]))
    return [(t, sorted(ws, key=lambda w: w["x0"])) for t, ws in out]


def blocks(page, problems):
    """[(name, (d0, m0, d1, m1), {weekday: [(place, code)]})] for the winter and summer tables."""
    out, cur, heads, pending = [], None, None, []
    for top, ws in lines_of(page):
        line = " ".join(w["text"] for w in ws)
        m = re.match(r"(Zimski|Ljetni) period od (\d{1,2})\.(\d{1,2})\.? do (\d{1,2})\.(\d{1,2})\.?$", line)
        if m:
            cur = [m.group(1), tuple(int(x) for x in m.group(2, 3, 4, 5)), defaultdict(list)]
            out.append(cur)
            heads, pending = None, []
            continue
        if line.startswith("Napomena"):
            cur = None
        if cur is None:
            continue
        if heads is None:
            if "Ponedjeljak" in line:
                # column heads: weekday names (Thursday may be split over two lines above)
                heads = {}
                for w in ws + pending:
                    for i, d in enumerate(DAYS):
                        if w["text"] == d:
                            heads[i] = [w["x0"], ""]
                for w in pending + ws:
                    col = min(heads, key=lambda i: abs(heads[i][0] - w["x0"]))
                    heads[col][1] += " " + w["text"]
                cur.append(heads)
            else:
                pending += ws
            continue
        cells = defaultdict(list)
        for w in ws:  # entries are left-aligned under their weekday heading
            col = max((i for i in heads if heads[i][0] <= w["x0"] + 3), key=lambda i: heads[i][0], default=None)
            if col is None or not cells[col] and w["x0"] - heads[col][0] > 25:
                problems.append(f"{cur[0]}: riječ {w['text']!r} nije ispod stupca")
                continue
            cells[col].append(w["text"])
        for col, words in cells.items():
            entry = " ".join(words)
            head = heads[col][1].lower()
            m = re.fullmatch(r"(\w+)(?: - (mko|rec\.))?", entry)
            if not m or m.group(1) not in PLACES:
                problems.append(f"{cur[0]}: ne razumijem {entry!r} ({DAYS[col]})")
                continue
            if m.group(2):
                code = "M" if m.group(2) == "mko" else "P"
            elif "reciklabilni" in head and "mko" not in head:
                code = "P"
            elif "reciklabilni" in head:
                problems.append(f"{cur[0]}: {entry!r} u stupcu {head!r} bez oznake mko/rec.")
                continue
            else:
                code = "M"
            cur[2][col].append((m.group(1), code))
    return [(b[0], b[1], b[2]) for b in out]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    page = fetch(PAGE).decode("utf-8", "replace")
    links = sorted(set(re.findall(rf'href="([^"]*raspored[^"]*{year}[^"]*\.pdf)"', page, re.I)))
    if len(links) != 1:
        sys.exit(f"Na {PAGE} nije nađen (jedan) raspored s godinom {year} u nazivu: {links}")
    url = links[0] if links[0].startswith("http") else SITE + links[0]
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "raspored.pdf"
        fetch(url, pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            if len(pdf.pages) != 1:
                problems.append(f"PDF ima {len(pdf.pages)} stranica, očekivana je jedna (Pakoštane)")
            pg = pdf.pages[0]
            text = " ".join((pg.extract_text() or "").split())
            tables = blocks(pg, problems)
    if "Raspored odvoza komunalnog otpada - Pakoštane" not in text:
        problems.append("PDF nije raspored za Pakoštane")
    m = re.search(r"Na blagdane / praznike \((.*?)\)\s*" + re.escape(HOLIDAY_TEXT), text)
    if not m:
        problems.append("u PDF-u nema pravila za blagdane")
    holidays = {date(year, int(mo), int(d)) for d, mo in re.findall(r"(\d{1,2})\.(\d{1,2})\.", m.group(1))} if m else set()
    if [b[0] for b in tables] != ["Zimski", "Ljetni"]:
        problems.append(f"tablice u PDF-u: {[b[0] for b in tables]}")
    print(f"PDF {url}: " + "; ".join(f"{n} {s}: " + ", ".join(f"{DAYS[d]}={v}" for d, v in sorted(t.items()))
                                    for n, s, t in tables))
    print(f"Bez odvoza (blagdani iz PDF-a): {', '.join(f'{d:%d.%m.}' for d in sorted(holidays))}")

    # day of year -> table
    which = {}
    for name, (d0, m0, d1, m1), _ in tables:
        start, end = date(year, m0, d0), date(year, m1, d1)
        d = date(year, 1, 1)
        while d.year == year:
            inside = start <= d <= end if start <= end else (d >= start or d <= end)
            if inside:
                if d in which:
                    problems.append(f"{d} je i u {which[d]} i u {name} razdoblju")
                which[d] = name
            d += timedelta(days=1)
    if len(which) != (date(year, 12, 31) - date(year, 1, 1)).days + 1:
        problems.append("zimski i ljetni period ne pokrivaju cijelu godinu")
    rules = {(n, place): defaultdict(set) for n, _, _ in tables for place in PLACES}
    spans = {n: s for n, s, _ in tables}
    for name, _, table in tables:
        for wd, entries in table.items():
            for place, code in entries:
                rules[name, place][code].add(wd)

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "napomene": NAPOMENE + [
        "Na blagdane " + ", ".join(f"{d:%d.%m.}" for d in sorted(holidays)) + " odvoz se ne obavlja; sljedeći "
        "odvoz je prema redovnom rasporedu (pravilo iz PDF-a rasporeda)."], "zone": {}}
    for place in PLACES:
        rows = []
        d = date(year, 1, 1)
        while d.year == year:
            r = rules[which[d], place]
            codes = "".join(c for c in "MP" if d.weekday() in r.get(c, ()))
            if codes and d not in holidays:
                rows.append((d, codes, False))
            d += timedelta(days=1)
        per = Counter((c, d.month) for d, codes, _ in rows for c in codes)
        for (c, mo), n in per.items():
            if not (3 <= n <= 5 if c == "P" else 3 <= n <= 14):
                problems.append(f"{place}: {c} {n} puta u mjesecu {mo}")
        if not rules["Zimski", place].get("M") or not rules["Ljetni", place].get("P"):
            problems.append(f"{place}: nema pravila za oba razdoblja")
        desc = []
        for name, label in (("Zimski", "zimi"), ("Ljetni", "ljeti")):
            d0, m0, d1, m1 = spans.get(name, (0, 0, 0, 0))
            r = rules[name, place]
            mko = [INSTR[i] for i in sorted(r["M"])]
            desc.append(f"{label} ({d0}.{m0}.–{d1}.{m1}.) miješani " + ", ".join(mko[:-1]) + (" i " if len(mko) > 1 else "") + mko[-1]
                        + ", reciklabilni " + " i ".join(INSTR[i] for i in sorted(r["P"])))
        k = str(len(data["zone"]) + 1)
        zone = {"jls": "Pakoštane", "podrucje": f"{place} – " + "; ".join(desc), "ulice": [place],
                "napomena": "Raspored po naselju (cijelo naselje)."}
        prev = old["zone"].get(k, {})
        keep = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("ulice") == [place] else {}
        zone["raw"] = {**keep, str(year): podaci.month_lines(rows)}
        data["zone"][k] = zone
        print(f"Zona {k}: {zone['podrucje']} – {dict(Counter(c for _, codes, _ in rows for c in codes))}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
