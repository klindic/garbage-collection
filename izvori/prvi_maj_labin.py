"""Labin, Raša, Sveta Nedelja, Kršan, Pićan: 1. MAJ LABIN d.o.o. (prvimaj.hr), weekly rounds from a table.

    python3 -m izvori.prvi_maj_labin [--year 2026]

The page "Raspored odvoza otpada" links the current "Terminski plan odvoza miješanog komunalnog otpada"
(valid from the date in its title) and the "Raspored prikupljanja odvojenog otpada" for half a year.
The mixed-waste plan is a table: one row per group of settlements or streets, one green cell in the
weekday column (pdfplumber rects), so mixed waste goes every week on that day. Rows with the same
municipality and weekday become one zone. The separate-waste plan lists weeks (from - to) alternating
"plastična i metalna ambalaža" and "papir i karton": the yellow or blue bin is emptied on the zone's
mixed-waste day of that week. Dates start at the mixed-waste plan's date (earlier months already in
podaci/prvi-maj... are kept on re-runs).
Saturday rounds are seasonal (from 30.3.2026.); the separate-waste weeks stop including Saturday from
November, so Saturday zones are written only up to the last such week. Holidays: no rule is published;
a separate-waste week that is stretched to Saturday around a weekday holiday (21.12. - 26.12.) moves
that holiday's rounds to the Saturday, every other holiday keeps the regular day (printed as assumptions).
"""
import argparse
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

SLUG = "1-maj-labin"
SITE = "https://prvimaj.hr"
PAGE = SITE + "/?page_id=277"
DAYS = ["PON", "UTO", "SRI", "ČET", "PET", "SUB", "NED"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
SECTIONS = {"GRAD LABIN": "Labin", "OPĆINA RAŠA": "Raša", "OPĆINA SVETA NEDELJA": "Sveta Nedelja",
            "OPĆINA KRŠAN": "Kršan", "OPĆINA PIĆAN": "Pićan"}
KINDS = {"PLASTIČNA I METALNA AMBALAŽA": "P", "PAPIR I KARTON": "K"}
FIXES = {"PURGARIJAČEPIĆ": "PURGARIJA ČEPIĆ", "SV.BORTUL": "SV. BORTUL", "SVA NASELJA": "PIĆAN (cijela općina)"}
CONTAINERS = r"\s*individualni spremnici od [\d, ]+ litara"
PROVIDER = {
    "davatelj": "1. MAJ LABIN d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Istarska",
    "jls": ["Labin", "Raša", "Sveta Nedelja", "Kršan", "Pićan"],
    "nazivi": {"P": "Plastična i metalna ambalaža (žuti spremnik)", "K": "Papir i karton (plavi spremnik)"},
}
NAPOMENE = [
    "Miješani komunalni otpad odvozi se jednom tjedno; žuti (plastična i metalna ambalaža) i plavi spremnik "
    "(papir i karton) prazne se naizmjence svaki drugi tjedan, isti dan kad i miješani otpad (moguć je vremenski "
    "odmak jer dolaze različita vozila). Vrijedi za korisnike s individualnim spremnicima (od vrata do vrata).",
    "Objavljeni raspored ne sadrži odvoz biootpada ni stakla (staklo: zeleni spremnici na javnim površinama).",
    "Mobilno reciklažno dvorište po naseljima: rasporedi (slike) na " + PAGE + ".",
]


def links(html, year):
    """(mixed-waste plan URL, [separate-waste plan URLs]) from the schedule page; the newest upload wins."""
    urls = sorted(set(re.findall(r'href="(https?://[^"]+\.pdf)"', html)))
    mko = [u for u in urls if re.search(r"terminski-plan-mko", u, re.I)]
    sel = [u for u in urls if re.search(rf"raspored-selektivno-{year}", u, re.I)]
    return (mko[-1] if mko else None), sel


def place(text):
    """'SV.BORTUL' -> 'Sv. Bortul', 'LABIN (ulice ...)' -> 'Labin (ulice ...)': capitals outside brackets."""
    for a, b in FIXES.items():
        text = text.replace(a, b)
    out, depth = [], 0
    for tok in re.split(r"(\s+|[()])", text):
        depth += (tok == "(") - (tok == ")")
        out.append(tok[:1] + tok[1:].lower() if depth == 0 and tok.isupper() else tok)
    return " ".join("".join(out).split())


def split_places(text):
    """Row text -> settlements/streets (top-level commas; commas in brackets stay)."""
    parts, depth, cur = [], 0, ""
    for ch in re.sub(CONTAINERS, "", text):
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return list(dict.fromkeys(place(p) for p in parts + [cur] if p.strip()))


def read_mko(pdf, problems):
    """(start date, [(JLS, weekday, row text)]) from the table: one green cell per row."""
    text = " ".join((pdf.pages[0].extract_text() or "").split())
    m = re.search(r"TERMINSKI PLAN ODVOZA MIJEŠANOG KOMUNALNOG OTPADA\s*[–-]\s*OD (\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if not m:
        problems.append("plan miješanog otpada: nema naslova s datumom početka")
        return None, []
    start = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    rows, jls, cols = [], None, None
    for pn, page in enumerate(pdf.pages):
        words = page.extract_words()
        heads = {w["text"]: (w["x0"] + w["x1"]) / 2 for w in words if w["text"] in DAYS and w["top"] < 60}
        if heads:
            if list(heads) != DAYS:
                problems.append(f"str. {pn + 1}: zaglavlje dana {list(heads)}")
            cols = heads
        if cols is None:
            problems.append("nema zaglavlja s danima")
            return start, []
        lines = page.extract_text_lines()
        green = [r for r in page.rects if r["width"] > 20 and r["height"] > 8 and isinstance(r["non_stroking_color"],
                 (list, tuple)) and len(r["non_stroking_color"]) == 3 and
                 r["non_stroking_color"][0] < 0.05 and 0.6 < r["non_stroking_color"][1] < 0.75 and
                 0.25 < r["non_stroking_color"][2] < 0.37]
        heads_y = sorted((l["top"], SECTIONS[l["text"].strip()]) for l in lines if l["text"].strip() in SECTIONS)
        used = set()
        for g in sorted(green, key=lambda r: r["top"]):
            sec = [j for t, j in heads_y if t < g["top"]]
            jls = sec[-1] if sec else jls
            day = min(cols, key=lambda d: abs(cols[d] - (g["x0"] + g["x1"]) / 2))
            if abs(cols[day] - (g["x0"] + g["x1"]) / 2) > 10:
                problems.append(f"str. {pn + 1}: zelena ćelija na x {g['x0']:.0f} nije ispod dana")
            mine = [l for l in lines if l["x0"] < g["x0"] - 50 and g["top"] - 2 <= l["top"] < g["bottom"] - 3]
            if not mine or used & {id(l) for l in mine}:
                problems.append(f"str. {pn + 1}: zelena ćelija na y {g['top']:.0f} bez vlastitog retka")
                continue
            used |= {id(l) for l in mine}
            rows.append((jls, DAYS.index(day), " ".join(l["text"] for l in mine)))
        # every row of the table (text left of the day columns, under a section) must have a green cell
        table_lines = [l for l in lines if l["x0"] < 60 and l["top"] > 60 and id(l) not in used
                       and not l["text"].startswith(("Napomena", "LOKACIJA")) and l["text"].strip() not in SECTIONS]
        cut = min([l["top"] for l in lines if l["text"].startswith("Napomena")], default=page.height)
        for l in table_lines:
            if l["top"] < cut:
                problems.append(f"str. {pn + 1}: redak bez dana: {l['text']!r}")
    return start, rows


def read_weeks(pdf, problems):
    """[(monday, last day, 'P'|'K')] from the separate-waste plan."""
    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    weeks = []
    for m in re.finditer(r"(\d{1,2})\.(\d{1,2})\.(?:(\d{4})\.)?\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\.\s*("
                         + "|".join(KINDS) + ")", text):
        d1, m1, y1, d2, m2, y2, kind = m.groups()
        end = date(int(y2), int(m2), int(d2))
        a = date(int(y1) if y1 else end.year - (int(m1) > end.month), int(m1), int(d1))
        if a.weekday() != 0 or not 4 <= (end - a).days <= 5:
            problems.append(f"tjedan odvojenog otpada {a} - {end} nije pon - pet/sub")
        weeks.append((a, end, KINDS[kind]))
    weeks.sort()
    for (a, _, k), (b, _, l) in zip(weeks, weeks[1:]):
        if (b - a).days != 7 or k == l:
            problems.append(f"tjedni odvojenog otpada {a} i {b} nisu uzastopni ili se ne izmjenjuju")
    return weeks


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    mko_url, sel_urls = links(fetch(PAGE).decode("utf-8", "replace"), year)
    if not mko_url or not sel_urls:
        sys.exit(f"Na {PAGE} nema plana miješanog ili odvojenog otpada za {year}.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mko.pdf"
        fetch(mko_url, path)
        start, rows = read_mko(pdfplumber.open(path), problems)
        tail = " ".join((pdfplumber.open(path).pages[-1].extract_text() or "").split())
        m = re.search(r"Posljednja izmjena .*?\.(?= |$)(?:.*?Rapcu\.)?", tail)
        plan_note = m.group(0) if m else ""
        weeks = []
        for i, u in enumerate(sel_urls):
            fetch(u, Path(tmp) / f"sel{i}.pdf")
            weeks += read_weeks(pdfplumber.open(Path(tmp) / f"sel{i}.pdf"), problems)
    weeks.sort()
    print(f"Plan miješanog otpada od {start}: {len(rows)} redaka; tjedni odvojenog otpada "
          f"{weeks[0][0] if weeks else '-'} - {weeks[-1][1] if weeks else '-'}")
    if not rows or not weeks or start.year > year:
        for p in problems:
            print(f"   PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    first = max(start, date(year, 1, 1))
    last = min(date(year, 12, 31), weeks[-1][1])
    if weeks[0][0] > first:
        problems.append(f"raspored odvojenog otpada počinje {weeks[0][0]}, a plan miješanog {first}")
    # Saturday rounds end where the separate-waste weeks stop including Saturday
    fri = [a for a, b, _ in weeks if b.weekday() == 4 and a >= first]
    sat_last = fri[0] - timedelta(days=2) if fri else last  # the Saturday before that Monday
    # a week stretched to Saturday around a weekday holiday (previous week ends on Friday): holiday -> Saturday
    moves = {}
    for prev, (a, b, _) in zip(weeks, weeks[1:]):
        for h in pravila.blagdani(year):
            if a <= h <= b and h.weekday() < 5 and b.weekday() == 5 and prev[1].weekday() == 4:
                moves[h] = b
    for h in pravila.blagdani(year):
        if first <= h <= last and h.weekday() < 5:
            print(f"Blagdan {h:%d.%m.}: " + (f"odvoz se pomiče na {moves[h]:%d.%m.} (tjedan odvojenog otpada "
                                               f"produljen do subote)" if h in moves else "odvoz prema rasporedu "
                                               "(pretpostavka, pravilo nije objavljeno)"))
    print(f"Subotnji odvoz (sezonski) do {sat_last:%d.%m.%Y.}")

    groups = {}
    for jls, wd, text in rows:
        groups.setdefault((jls, wd), []).append(text)
    order = list(dict.fromkeys(j for j, _, _ in rows))
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    zones = {}
    for i, ((jls, wd), texts) in enumerate(sorted(groups.items(), key=lambda g: (order.index(g[0][0]), g[0][1])), 1):
        end = sat_last if wd == 5 else last
        rows_z, d = [], first + timedelta(days=(wd - first.weekday()) % 7)
        while d <= end:
            kind = "".join(k for a, b, k in weeks if a <= d <= b)
            rows_z.append((moves.get(d, d), "M" + kind, d in moves))
            d += timedelta(days=7)
        months = Counter(d.month for d, _, _ in rows_z)
        for mo, n in months.items():
            full = date(year, mo, 1) >= first and (mo < end.month or end == date(year, 12, 31))
            if full and not 4 <= n <= 5:
                problems.append(f"zona {i}: {n} odvoza u {mo}. mjesecu")
        if sum("P" in c or "K" in c for _, c, _ in rows_z) < len(rows_z) - 1:
            problems.append(f"zona {i}: odvojeni otpad nije svaki tjedan")
        ulice = [u for t in texts for u in split_places(t)]
        names = [re.sub(r"\(([^,()]*?)(?:,| i )[^()]*\)", r"(\1…)", u) for u in ulice[:4]]
        zone = {"jls": jls, "podrucje": f"{DAN[wd].capitalize()} – " + ", ".join(names) +
                (" …" if len(ulice) > 4 else ""), "opis": " | ".join(texts), "ulice": ulice}
        notes = []
        if wd == 5:
            notes.append(f"Sezonski odvoz subotom (od 30.3.{year}.); raspored odvojenog otpada uključuje subotu "
                         f"do {sat_last:%d.%m.%Y.}, a za razdoblje nakon toga raspored za ovo područje nije objavljen.")
        if any(t.startswith("RABAC") for t in texts) and "Rapcu" in plan_note:
            notes.append(plan_note)
        if any(re.search(CONTAINERS, t) for t in texts):
            notes.append("Ulice Rudarska, K. Kranjca i A. Selana: individualni spremnici od 80, 120 i 240 litara.")
        if notes:
            zone["napomena"] = " ".join(notes)
        raw = podaci.month_lines(rows_z)
        prev = next((z for z in old.values() if z["jls"] == jls and z.get("ulice") == ulice), None)
        if prev:  # keep dates before this plan (earlier plans) and other years
            kept = [(d, c, m) for d, c, m in podaci.iter_dates(prev, year) if d < first]
            raw = podaci.month_lines(kept + rows_z)
            zone["raw"] = {**prev["raw"], str(year): raw}
        else:
            zone["raw"] = {str(year): raw}
        zones[str(i)] = zone
        print(f"Zona {i} {jls} ({DAN[wd]}): {len(rows_z)} odvoza {rows_z[0][0]:%d.%m.} - {rows_z[-1][0]:%d.%m.}, "
              f"{len(ulice)} naselja/ulica")

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    hol = [f"{h:%d.%m.} → {moves[h]:%d.%m.}" for h in sorted(moves)]
    data = {**PROVIDER, "napomene": NAPOMENE + [
        f"Raspored vrijedi od {first:%d.%m.%Y.} (terminski plan miješanog otpada od {start:%d.%m.%Y.}, "
        f"raspored odvojenog otpada do {weeks[-1][1]:%d.%m.%Y.}); raniji mjeseci nisu objavljeni.",
        "Pomaci zbog blagdana nisu objavljeni" + (
            f", osim što je tjedan odvojenog otpada produljen do subote ({', '.join(hol)}); "
            "na ostale blagdane računa se redovan dan odvoza." if hol else "; računa se redovan dan odvoza."),
        f"Plan miješanog otpada: {mko_url}; odvojeni otpad: {', '.join(sel_urls)}.",
    ], "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
