"""Funtana, Kaštelir-Labinci, Sveti Lovreč, Tar-Vabriga, Višnjan, Vižinada, Vrsar: Usluga Poreč d.o.o.

    python3 -m izvori.usluga_porec [--year 2026]

usluga.hr (Next.js) lists "PLAN PRIMOPREDAJE OTPADA YYYY - OPĆINE" in the page data (__NEXT_DATA__); the
PDF is served from cms.usluga.hr, which cuts every download off after about 100 KB. The script takes the
full size from the provider's response and, when the provider's copy is incomplete, downloads the copy
Općina Funtana-Fontane published (funtana.hr, WordPress media), which must have exactly that size; a file
of any other size is never read. The plan for Grad Poreč (same page) is only checked: no complete copy
could be found, so the city is left out.
Page 1 of the plan has one column per municipality (headed by the weekday) with explicit dd.mm dates in
a PAPIR and a PLASTIKA row; paper and plastic alternate every week, and the plan's note says mixed waste
is collected on the same day, so mixed waste is every paper and plastic date. Page 2 (STAKLO) has nine
columns, Tar-Vabriga and Višnjan split into two settlement groups, with the settlement lists. Words are
put in columns by x position. Holidays are built into the dates (Friday 1.5. -> Saturday 2.5., no round
on 1.1.); a date off the column's weekday must replace a holiday of that week and is marked as moved.
"""
import argparse
import http.client
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

SLUG = "usluga-porec"
SITE = "https://www.usluga.hr"
PAGE = SITE + "/djelatnosti/cistoca/odvoz"
CMS = "https://www.cms.usluga.hr/uploaded/"
MIRROR = "https://www.funtana.hr/wp-json/wp/v2/media?search=primopredaje&per_page=50&_fields=id,date,source_url"
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
# page 1: municipality heading -> weekday; the extra headings that share a column must be there too
PAGE1 = {"VIŠNJAN": 0, "TAR-VABRIGA": 1, "SV.LOVREČ": 2, "FUNTANA": 3, "VRSAR": 4, "KAŠTELIR-LABINCI": 4}
PAGE1_EXTRA = {"TAR-VABRIGA": "Rogovići", "SV.LOVREČ": "Jakići", "FUNTANA": "VIŽINADA"}
# page 2: the nine glass columns, left to right, and the first settlement of each listed group
GLASS = {"SV.LOVREČ": "SV.LOVREČ", "TAR-VABRIGA 1": "TAR-VABRIGA", "TAR-VABRIGA 2": "TAR-VABRIGA",
         "VIŠNJAN 1": "VIŠNJAN", "VIŠNJAN 2": "VIŠNJAN", "FUNTANA": "FUNTANA", "VRSAR": "VRSAR",
         "KAŠTELIR-LABINCI": "KAŠTELIR-LABINCI", "VIŽINADA": "FUNTANA"}  # glass column -> page-1 weekday column
GLASS_HEADS = ["SV.LOVREČ", "TAR-VABRIGA", "VIŠNJAN", "FUNTANA", "VRSAR", "KAŠTELIR-", "VIŽINADA"]
LISTS = {"TAR-VABRIGA 1": "St. Blek", "TAR-VABRIGA 2": "Barbići", "VIŠNJAN 1": "Anžići", "VIŠNJAN 2": "Baškoti",
         "VRSAR": "Begi"}
ROGOVICI = r"OPĆINA\s+KAŠTELIR-\s*LABINCI"
# zone: (JLS, page-1 column, glass column, settlement list or text, note)
ZONES = [
    ("Funtana – Fontane", "FUNTANA", "FUNTANA", "Funtana (cijela općina)", None),
    ("Kaštelir-Labinci – Castelliere-S. Domenica", "KAŠTELIR-LABINCI", "KAŠTELIR-LABINCI",
     "Kaštelir-Labinci (cijela općina osim Rogovića)", None),
    ("Kaštelir-Labinci – Castelliere-S. Domenica", "TAR-VABRIGA", "TAR-VABRIGA 1", "ROGOVIĆI",
     "Rogovići imaju odvoz s Općinom Tar-Vabriga."),
    ("Sveti Lovreč", "SV.LOVREČ", "SV.LOVREČ", "Sveti Lovreč (cijela općina)",
     "Papir, plastiku i miješani otpad istog dana odvozi i naselje Jakići Gornji (Grad Poreč)."),
    ("Tar-Vabriga – Torre-Abrega", "TAR-VABRIGA", "TAR-VABRIGA 1", "TAR-VABRIGA 1", None),
    ("Tar-Vabriga – Torre-Abrega", "TAR-VABRIGA", "TAR-VABRIGA 2", "TAR-VABRIGA 2", None),
    ("Višnjan – Visignano", "VIŠNJAN", "VIŠNJAN 1", "VIŠNJAN 1", None),
    ("Višnjan – Visignano", "VIŠNJAN", "VIŠNJAN 2", "VIŠNJAN 2", None),
    ("Vižinada – Visinada", "FUNTANA", "VIŽINADA", "Vižinada (cijela općina)",
     "Papir, plastiku i miješani otpad odvozi se istog dana kao u Općini Funtana."),
    ("Vrsar – Orsera", "VRSAR", "VRSAR", "VRSAR", None),
]
PROVIDER = {
    "davatelj": "Usluga Poreč d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Istarska",
    "jls": sorted({z[0] for z in ZONES}),
    "nazivi": {"P": "Plastična ambalaža", "K": "Papir i karton"},
}
NAPOMENE = [
    "Miješani komunalni otpad odvozi se svaki tjedan, istog dana kad i papir odnosno plastika (napomena u planu); "
    "papir i karton te plastična ambalaža odvoze se naizmjence svaki drugi tjedan, staklo svaka četiri tjedna.",
    "Objavljeni plan ne sadrži odvoz biootpada.",
    "Usluga Poreč: 052 431 003, usluga@usluga.hr, Mlinska 1, Poreč.",
]


def documents(html):
    """{document title: file URL} from the page's __NEXT_DATA__ (Apollo cache)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    cache = json.loads(m.group(1))["props"]["pageProps"]["initialData"]
    out = {}
    for key, v in cache.items():
        if key.startswith("Document:"):
            name = cache[v["localization"]["__ref"]]["name"]
            files = [cache[f["__ref"]]["filename"] for f in v["files"]["items"]]
            if files:
                out[name] = CMS + files[0]
    return out


def download(url):
    """(body or None, full size): None when the server sent less than the full file."""
    meta = {}
    try:
        body = fetch(url, meta=meta)
    except http.client.IncompleteRead as e:
        return None, len(e.partial) + e.expected
    size = int({k.lower(): v for k, v in meta.items()}.get("content-length") or len(body))
    whole = len(body) == size and body.startswith(b"%PDF") and b"%%EOF" in body[-2048:]
    return (body if whole else None), size


def plan_pdf(url, year):
    """(PDF bytes, where it came from): the provider's copy, or the identical copy on funtana.hr."""
    body, size = download(url)
    if body:
        return body, url
    print(f"cms.usluga.hr je prekinuo preuzimanje (puna veličina {size} B); tražim kopiju na funtana.hr")
    media = json.loads(fetch(MIRROR))
    cands = [m for m in sorted(media, key=lambda m: m["date"], reverse=True)
             if re.search(rf"plan-primopredaje-otpada.*{year}", m["source_url"], re.I)]
    for m in cands:
        mirror, msize = download(m["source_url"])
        print(f"   {m['source_url']}: {msize} B")
        if mirror and len(mirror) == size:
            return mirror, m["source_url"]
    sys.exit(f"Nema cijele kopije plana ({size} B); ništa nije upisano.")


def dates_in(text, year):
    """'26.03,23.04,' -> [date(year, 3, 26), date(year, 4, 23)]; dates of other years are dropped."""
    out = []
    for d, m, y in re.findall(r"(\d\d)\.(\d\d)(?:\.(\d{4}))?", text):
        if int(y or year) == year:
            out.append(date(year, int(m), int(d)))
    return out


def columns(words, top, bottom, per, ncols, problems, what):
    """Dates of a band of the page, per column: the first row gives the sub-column starts."""
    ws = [w for w in words if top <= w["top"] < bottom and re.match(r"\d\d\.\d\d", w["text"])]
    first = min(w["top"] for w in ws)
    starts = sorted(w["x0"] for w in ws if abs(w["top"] - first) < 3)
    if len(starts) != per * ncols:
        problems.append(f"{what}: {len(starts)} stupaca datuma u prvom retku, očekivano {per * ncols}")
        return None, starts
    cols = [[] for _ in range(ncols)]
    for w in sorted(ws, key=lambda w: (round(w["top"]), w["x0"])):
        i = min(range(len(starts)), key=lambda i: abs(starts[i] - w["x0"]))
        if abs(starts[i] - w["x0"]) > 12:
            problems.append(f"{what}: datum {w['text']} izvan stupaca (x {w['x0']:.0f})")
        cols[i // per].append(w["text"])
    return cols, starts


def check_column(name, dates, weekday, holidays, problems):
    """Dates in order, on the weekday or replacing a holiday of that week -> [(date, moved)]."""
    out = []
    for prev, d in zip([None] + dates, dates):
        if prev and d <= prev:
            problems.append(f"{name}: {d} nije poslije {prev}")
        regular = d - timedelta(days=d.weekday() - weekday)
        moved = d.weekday() != weekday
        if moved and regular not in holidays:
            problems.append(f"{name}: {d} ({DAN[d.weekday()]}) nije {DAN[weekday]}, a nema blagdana tog tjedna")
        out.append((d, moved))
    return out


def read_plan(pdf, year, problems):
    """({page-1 column: {"K": [(date, moved)], "P": ...}}, {glass column: [...]}, {group: settlements})."""
    p1, p2 = pdf.pages[:2]
    text1, text2 = (" ".join((p.extract_text() or "").split()) for p in (p1, p2))
    for t in (text1, text2):
        if f"PLAN PRIMOPREDAJE OTPADA {year}" not in t:
            problems.append(f"naslov plana nije za {year}.")
    if "miješanog komunalnog otpada identičan je danu primopredaje papira" not in text1:
        problems.append("nema napomene da je miješani otpad istog dana kao papir i plastika")
    holidays = set(pravila.blagdani(year))
    w1 = p1.extract_words()
    label = {w["text"]: w["top"] for w in w1 + p2.extract_words() if w["x0"] < 130}
    if not {"PAPIR", "PLASTIKA", "NAPOMENA:", "STAKLO"} <= set(label):
        problems.append(f"nema oznaka redaka PAPIR/PLASTIKA/STAKLO: {sorted(label)}")
        return {}, {}, {}
    heads = sorted((w for w in w1 if w["text"] in PAGE1 and w["top"] < label["PAPIR"]), key=lambda w: w["x0"])
    if [w["text"] for w in heads] != list(PAGE1):
        problems.append(f"str. 1: stupci {[w['text'] for w in heads]}, očekivano {list(PAGE1)}")
        return {}, {}, {}
    first = {}
    for kind, top, bottom in (("K", label["PAPIR"] - 6, label["PLASTIKA"] - 6),
                              ("P", label["PLASTIKA"] - 6, label["NAPOMENA:"] - 6)):
        cols, starts = columns(w1, top, bottom, 3, len(PAGE1), problems, f"str. 1 {kind}")
        if cols is None:
            return {}, {}, {}
        for (name, wd), texts in zip(PAGE1.items(), cols):
            first.setdefault(name, {})[kind] = check_column(
                f"{name} {kind}", dates_in(" ".join(texts), year), wd, holidays, problems)
    for i, h in enumerate(heads):  # every heading must sit over its column
        if not starts[3 * i] - 10 <= (h["x0"] + h["x1"]) / 2 <= starts[3 * i + 2] + 40:
            problems.append(f"str. 1: naslov {h['text']} nije iznad svog stupca")
    for name, extra in PAGE1_EXTRA.items():
        h = next(w for w in heads if w["text"] == name)
        near = [w["text"] for w in w1 if h["top"] < w["top"] < label["PAPIR"] and abs(w["x0"] - h["x0"]) < 40]
        if not any(extra in t for t in near):
            problems.append(f"str. 1: ispod {name} nema '{extra}'")

    w2 = p2.extract_words()
    top2 = min(w["top"] for w in w2 if w["text"] == "OPĆINA") + 30  # the headings, not "OPĆINA KAŠTELIR-" lower
    heads2 = sorted((w for w in w2 if w["text"] in GLASS_HEADS and w["top"] < top2), key=lambda w: w["x0"])
    if [w["text"] for w in heads2] != GLASS_HEADS:
        problems.append(f"str. 2: naslovi {[w['text'] for w in heads2]}, očekivano {GLASS_HEADS}")
        return first, {}, {}
    cols, starts = columns(w2, label["STAKLO"] - 6, p2.height, 2, len(GLASS), problems, "str. 2 staklo")
    if cols is None:
        return first, {}, {}
    glass = {g: check_column(f"{g} S", dates_in(" ".join(t), year), PAGE1[GLASS[g]], holidays, problems)
             for g, t in zip(GLASS, cols)}
    # settlement lists between the headings and the glass dates, by glass column
    lists = {}
    col_x, names = starts[::2], list(GLASS)
    band = [w for w in w2 if max(h["bottom"] for h in heads2) < w["top"] < label["STAKLO"] - 6]
    for w in sorted(band, key=lambda w: (round(w["top"]), w["x0"])):
        g = names[max([i for i, x in enumerate(col_x) if x <= w["x0"] + 6], default=0)]
        lists[g] = (lists.get(g, "") + " " + w["text"]).strip()
    if set(lists) != set(LISTS):
        problems.append(f"str. 2: popisi naselja u stupcima {sorted(lists)}, očekivano {sorted(LISTS)}")
    out = {}
    for g, text in lists.items():
        head, *rest = re.split(ROGOVICI, text)  # Rogovići (Kaštelir-Labinci) are listed under Tar-Vabriga
        out[g] = settlements(head)
        if rest:
            out["ROGOVIĆI"] = settlements(" ".join(rest))
        if not out[g] or out[g][0] != LISTS.get(g):
            problems.append(f"str. 2: popis {g} počinje s {out[g][:1]}, očekivano {LISTS.get(g)}")
    if out.get("ROGOVIĆI") != ["Rogovići"]:
        problems.append(f"str. 2: Rogovići nisu uz Tar-Vabrigu: {out.get('ROGOVIĆI')}")
    return first, glass, out


def settlements(text):
    """'St. Blek, St. Vergotini, kod Tara, Tar' -> ['St. Blek', 'St. Vergotini kod Tara', 'Tar']."""
    out = []
    for part in text.split(","):
        part = " ".join(part.split())
        if part.startswith("kod ") and out:
            out[-1] += " " + part
        elif part:
            out.append(part)
    return out


def weekly(name, rows, weekday, holidays, year, problems):
    """Paper and plastic together: exactly one collection every week (none in a week with a holiday).

    The plan lists the first dates of the next January, so the first days of January may be in last year's plan."""
    weeks, missing = Counter(d.isocalendar()[:2] for d, _ in rows), set()
    d = date(year, 1, 1) + timedelta(days=(weekday - date(year, 1, 1).weekday()) % 7)
    while d.year == year:
        n = weeks.get(d.isocalendar()[:2], 0)
        if n == 0 and d.timetuple().tm_yday <= 7 and d not in holidays:
            print(f"{name}: {d} nije u planu za {year}. (prvi tjedan godine, možda u planu za prošlu godinu)")
            missing.add(d)
        elif n != 1 and not (n == 0 and d in holidays):
            problems.append(f"{name}: tjedan {d}: {n} odvoza papira/plastike")
        d += timedelta(days=7)
    return missing


def short(names, n=6):
    return ", ".join(names[:n]) + ("…" if len(names) > n else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    docs = documents(fetch(PAGE).decode("utf-8", "replace"))
    url = docs.get(f"PLAN PRIMOPREDAJE OTPADA {year} - OPĆINE")
    if not url:
        sys.exit(f"Na {PAGE} nema plana za općine za {year}: {sorted(docs)}")
    city = docs.get(f"PLAN PRIMOPREDAJE OTPADA {year} - POREČ")
    city_note = "Grad Poreč – Parenzo nije uključen: gradski plan nije objavljen."
    if city:
        body, size = download(city)
        city_note = ("Grad Poreč – Parenzo nije uključen: gradski plan (" + city + ") " +
                     ("još se ne čita ovom skriptom." if body else
                      "ne može se preuzeti u cijelosti (poslužitelj prekida prijenos), a cijela kopija nije pronađena."))
        print(f"Plan za Grad Poreč ({size} B): {'cijel' if body else 'preuzimanje nepotpuno'} – grad nije uključen")
    body, source = plan_pdf(url, year)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plan.pdf"
        path.write_bytes(body)
        first, glass, lists = read_plan(pdfplumber.open(path), year, problems)
    if not first or not glass:
        for p in problems:
            print(f"   PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    holidays, missing = set(pravila.blagdani(year)), set()
    for name, kinds in first.items():
        missing |= weekly(name, kinds["K"] + kinds["P"], PAGE1[name], holidays, year, problems)
        if {d for d, _ in kinds["K"]} & {d for d, _ in kinds["P"]}:
            problems.append(f"{name}: papir i plastika istog dana")
    for g, rows in glass.items():
        gaps = {(b - a).days for (a, _), (b, _) in zip(rows, rows[1:])}
        if not 12 <= len(rows) <= 14 or not gaps <= {27, 28, 29}:
            problems.append(f"{g} staklo: {len(rows)} odvoza, razmaci {sorted(gaps)}")

    zones, moved = {}, set()
    for i, (jls, col, gcol, where, note) in enumerate(ZONES, 1):
        wd = PAGE1[col]
        rows = [(d, "MK", m) for d, m in first[col]["K"]] + [(d, "MP", m) for d, m in first[col]["P"]]
        rows += [(d, "S", m) for d, m in glass[gcol]]
        merged = {}
        for d, codes, m in rows:
            c, mm = merged.get(d, ("", False))
            if set(c) & set(codes):
                problems.append(f"zona {i}: {d} dvaput {codes}")
            merged[d] = (c + codes, mm or m)
        moved |= {d for d, (_, m) in merged.items() if m}
        for month in range(1, 13):
            n = sum(1 for d, (c, _) in merged.items() if d.month == month and "M" in c)
            if not 3 <= n <= 5:
                problems.append(f"zona {i}: {n} odvoza miješanog otpada u {month}. mjesecu")
        ulice = lists.get(where, [where])
        place = short(ulice) if where in lists else where.replace(" (cijela općina", ", cijela općina").rstrip(")")
        zone = {"jls": jls, "podrucje": f"{DAN[wd].capitalize()} – {place}", "ulice": ulice}
        if note:
            zone["napomena"] = note
        zone["raw"] = {str(year): podaci.month_lines([(d, c, m) for d, (c, m) in merged.items()])}
        zones[str(i)] = zone
        print(f"Zona {i} {jls} ({DAN[wd]}): " + ", ".join(
            f"{k} {sum(k in c for c, _ in merged.values())}" for k in "MPKS"))

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    shifts = ", ".join(sorted({f"{d:%d.%m.}" for d in moved}, key=lambda s: s[3:] + s[:2]))
    data = {**PROVIDER, "napomene": NAPOMENE + [
        f"Blagdani su uračunati u objavljene datume (pomaknuti odvozi: {shifts}); na 1.1. nema odvoza, "
        "a na ostale blagdane odvozi se prema rasporedu." + (
            f" Odvoz {', '.join(f'{d:%d.%m.}' for d in sorted(missing))} nije u planu za {year}." if missing else ""),
        f"Izvor: {source}" + (" (identična kopija plana; poslužitelj cms.usluga.hr prekida preuzimanje)."
                              if source != url else "."),
        city_note,
    ], "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years of zones that did not change
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("ulice") == zone["ulice"] and prev.get("jls") == zone["jls"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
