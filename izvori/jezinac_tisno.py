"""Tisno: Ježinac d.o.o. (jezinac.hr), mixed waste by street groups (winter and summer), recyclables by settlement.

    python3 -m izvori.jezinac_tisno [--year 2026]

The year PDF linked from the home page ("RASPORED ODVOZA OTPADA", Raspored<year>.pdf) lists, per
settlement (TISNO, JEZERA, BETINA), groups of streets with their winter days ("Zimski termin
(01.01.-31.05.2026. - 01.10.-31.12.2026.) PONEDJELJAK i PETAK") and summer days ("Ljetni termin
(01.06.2026. - 30.09.2026.) PONEDJELJAK – SRIJEDA – PETAK", read as Monday, Wednesday and Friday). The
columns are kept apart by the x position of the words (pdfplumber); only the households' part (FIZIČKE
OSOBE) is used. Page 3 is a colour calendar of recyclables: yellow = paper and cardboard (yellow bin),
orange = plastic, glass and metal (green bin with orange lid), read with kalendar_boje.read_page; every
settlement takes the coloured days on its weekday from the table under it (Tisno Tuesday, Betina, Dubrava,
Dazlina, Ivinj and Prosika Wednesday, Jezera Thursday).
No holiday rule is published; changes come as notices ("Promjena rasporeda odvoza otpada ...", WordPress
REST). The notices of the year are listed in NOTICES with the sentence they were read from; a new one stops
the script until it is added there. Moved dates are marked as moved, cancelled ones are dropped.
"""
import argparse
import html
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import colour, read_page

SLUG = "jezinac-tisno"
SITE = "https://jezinac.hr"
POSTS = SITE + "/wp-json/wp/v2/posts?search=rasporeda&per_page=50&after={after}&_fields=id,date,link,title,content"
DAYS = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK", "SUBOTA", "NEDJELJA"]
INSTR = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
HEADINGS = {"TISNO": "Tisno", "JEZERA": "Jezera", "BETINA": "Betina"}
LEGEND = {"Žuti": "K", "Zeleni": "P"}
# Notices: post id -> (sentence that must be in the post, [(date, new date or None, codes)]); a change applies
# to every zone that has one of the codes on that date. Codes: M mixed, K paper (yellow), P plastic/glass/metal.
NOTICES = {
    2210: ("reciklažni otpad koji se prema rasporedu odvozi četvrtkom (JEZERA) na dan 01.01.2026. neće se odvoziti , "
           "već će se otpad odvoziti u petak 02.01.2026. Reciklažni otpad koji se prema rasporedu odvozi utorkom "
           "(TISNO), 06.01.2026., neće se odvoziti , već će se odvoziti u srijedu 07.01.2026.",
           [(date(2026, 1, 1), date(2026, 1, 2), "KP"), (date(2026, 1, 6), date(2026, 1, 7), "KP")]),
    2251: ("Odvoz miješanog komunalnog otpada koji se prema rasporedu odvozi ponedjeljkom, 06.04.2026. neće se "
           "odvoziti . Otpad će se prikupiti u utorak 07.04.2026.", [(date(2026, 4, 6), date(2026, 4, 7), "M")]),
    2300: ("Odvoz miješanog komunalnog otpada koji se prema rasporedu odvozi petkom, 01.05.2026. neće se odvoziti.",
           [(date(2026, 5, 1), None, "M")]),
    2319: ("Odvoz otpada koji se prema rasporedu odvozi čertvrtkom (narančasti spremnici za metal, staklo i "
           "plastiku) na dan 04.06.2026. (Tijelovo) biti će pomaknut na petak 05.06.2026.",
           [(date(2026, 6, 4), date(2026, 6, 5), "P")]),
    2337: ("od 1. listopada 2026. godine započinje primjena rasporeda odvoza komunalnog otpada", []),
}
PROVIDER = {
    "davatelj": "Ježinac d.o.o.",
    "web": SITE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Tisno"],
    "nazivi": {"K": "Papir i karton (žuti spremnik)", "P": "Plastika, staklo i metal (zeleni spremnik s "
                                                             "narančastim poklopcem)"},
    "bioNapomena": "Biootpad: kućno kompostiranje (upute na jezinac.hr).",
}
NAPOMENE = [
    "Raspored za kućanstva (fizičke osobe); pravne osobe u Tisnom, Jezerima i Betini imaju odvoz zimi od "
    "ponedjeljka do petka, ljeti svaki dan.",
    "Radno vrijeme odvoza: zimski termin 6–13 h, ljetni termin 5–12 h.",
    "Mobilno reciklažno dvorište i glomazni otpad (spremnici na javnim površinama u svibnju) prema rasporedu u PDF-u; "
    "preuzimanje glomaznog otpada na adresi jednom godišnje do 3 m³ na zahtjev (022/439-257, kontakt@jezinac.hr).",
    "Pomaci zbog blagdana nisu objavljeni kao pravilo; uključeni su pomaci iz obavijesti Ježinca (pomaknuti odvozi "
    "su označeni, otkazani izostavljeni). Odvoz miješanog otpada 1.5.2026. nije obavljen; na trgovima, glavnim "
    "ulicama i kod poslovnih prostora obavljen je u subotu 2.5.2026.",
]


def lines_of(page):
    """[(top, [words])] of a page, words of one line sorted by x."""
    out = []
    for w in sorted(page.extract_words(x_tolerance=2), key=lambda w: (w["top"], w["x0"])):
        if out and abs(out[-1][0] - w["top"]) < 2.5:
            out[-1][1].append(w)
        else:
            out.append((w["top"], [w]))
    return [(t, sorted(ws, key=lambda w: w["x0"])) for t, ws in out]


def groups_of(pdf, year, problems):
    """[(settlement, streets text, winter text, summer text)] of the households' part of the PDF."""
    groups, place, done = [], None, False
    for page in pdf.pages[:2]:
        for top, ws in lines_of(page):
            line = " ".join(w["text"] for w in ws)
            if done:
                break
            if line.startswith(("ZIMSKI TERMIN", "LJETNI TERMIN", "RASPORED ODVOZA")) and groups:
                done = line.startswith("ZIMSKI TERMIN") or done
                continue
            if line in HEADINGS and 240 < ws[0]["x0"] < 320:
                place = HEADINGS[line]
                continue
            if any(w["text"] == "Zimski" and 250 < w["x0"] < 320 for w in ws):
                if place is None:
                    problems.append(f"skupina ulica prije naziva naselja: {line!r}")
                groups.append([place, [], [], []])
            if not groups:
                continue
            for w in ws:
                col = 1 if w["x0"] < 210 else 2 if w["x0"] < 420 else 3
                groups[-1][col].append(w["text"])
    if not done:
        problems.append("nije nađen kraj rasporeda za fizičke osobe")
    return [(p, " ".join(a), " ".join(b), " ".join(c)) for p, a, b, c in groups]


def split_streets(t):
    """Split on commas outside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in t:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [" ".join(s.split()) for s in out + [cur] if s.strip()]


def season(t, year, name, problems):
    """'Zimski termin (01.01.-31.05.2026. - 01.10.-31.12.2026.) PONEDJELJAK i PETAK' -> ([(start, end)], weekdays)."""
    ds = re.findall(r"(\d\d)\.(\d\d)\.(?:(\d{4})\.)?", t)
    spans = []
    for (d0, m0, y0), (d1, m1, y1) in zip(ds[0::2], ds[1::2]):
        if int(y1 or year) != year or int(y0 or y1) != year:
            problems.append(f"{name}: razdoblje nije za {year}: {t!r}")
        spans.append((date(year, int(m0), int(d0)), date(year, int(m1), int(d1))))
    days = [DAYS.index(w) for w in re.findall(r"\b(%s)\b" % "|".join(DAYS), t)]
    if len(ds) % 2 or not spans or not days:
        problems.append(f"{name}: ne razumijem {t!r}")
    return spans, days


def recycling_days(page, problems):
    """{settlement name (upper): weekday} from the table 'DAN ODVOZA | NASELJE' on the calendar page."""
    words = page.extract_words()
    head = next((w for w in words if w["text"] == "DAN"), None)
    if not head:
        problems.append("nema tablice DAN ODVOZA / NASELJE")
        return {}
    right = min((w["x0"] for w in words if w["text"] == "RASPORED" and abs(w["top"] - head["top"]) < 10),
                default=page.width / 2)  # the mobile recycling yard text starts here
    cells = [w for w in words if w["top"] > head["bottom"] + 5 and w["x1"] < right - 5]
    days = [w for w in cells if w["text"] in DAYS]
    out = {}
    for w in cells:
        if w in days or w["text"] == "NASELJE":
            continue
        mid = (w["top"] + w["bottom"]) / 2
        d = min(days, key=lambda x: abs((x["top"] + x["bottom"]) / 2 - mid))
        name = w["text"].rstrip(",")
        if name in out:
            problems.append(f"naselje {name} dvaput u tablici reciklabilnog otpada")
        out[name] = DAYS.index(d["text"])
    return out


def calendar(page, year, problems):
    """{date: 'K' | 'P'} from the colour calendar (legend: yellow bin, green bin with orange lid)."""
    words = page.extract_words()
    palette = {}
    for label, code in LEGEND.items():
        w = next((w for w in words if w["text"] == label), None)
        boxes = [r for r in page.rects if w and r.get("fill") and colour(r) and r["x0"] > w["x1"]
                 and r["top"] - 4 <= (w["top"] + w["bottom"]) / 2 <= r["bottom"] + 4]
        if not boxes:
            problems.append(f"u legendi nema boje za {label}")
            continue
        palette[colour(min(boxes, key=lambda r: r["x0"]))] = code
    legend_top = min((w["top"] for w in words if w["text"] in LEGEND), default=page.height)
    ignore = {colour(r) for r in page.rects if r.get("fill") and colour(r)} - set(palette)
    found, probs = read_page(page, year, palette, ignore=tuple(ignore), bbox=(0, 0, page.width, legend_top - 3))
    problems += [f"kalendar: {p}" for p in probs]
    weeks = defaultdict(set)
    for d, c in found.items():
        if d.weekday() not in (1, 2, 3):
            problems.append(f"kalendar: {d} obojen, a nije utorak–četvrtak")
        weeks[d.isocalendar()[:2]].add(c)
    for wk, cs in weeks.items():
        if len(cs) != 1:
            problems.append(f"kalendar: tjedan {wk} ima dvije boje")
    return found, palette


def notices(year, problems):
    """[(date, new date or None, codes)] of the year from the company's notices."""
    after = f"{year - 1}-12-01T00:00:00"
    posts = json.loads(fetch(POSTS.format(after=after)))
    out = []
    for p in posts:
        title = html.unescape(p["title"]["rendered"])
        if not title.lower().startswith("promjena rasporeda"):
            continue
        body = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", p["content"]["rendered"])).split())
        if p["id"] not in NOTICES:
            problems.append(f"nova obavijest {p['link']} ({title!r}) – pregledati i upisati u NOTICES")
            continue
        sentence, changes = NOTICES[p["id"]]
        if re.sub(r"\s", "", sentence) not in re.sub(r"\s", "", body):
            problems.append(f"obavijest {p['link']} se promijenila")
        out += [c for c in changes if c[0].year == year]
    seen = {p["id"] for p in posts}
    for pid, (_, changes) in NOTICES.items():
        if pid not in seen and any(c[0].year == year for c in changes):
            problems.append(f"obavijest {pid} više nije objavljena")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    home = fetch(SITE + "/").decode("utf-8", "replace")
    links = sorted(set(re.findall(rf'href="([^"]*/wp-content/uploads/[^"]*Raspored{year}[^"]*\.pdf)"', home)))
    if len(links) != 1:
        sys.exit(f"Na {SITE} nije nađen jedan PDF rasporeda za {year}: {links}")
    url = links[0]
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "raspored.pdf"
        fetch(url, pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            first = pdf.pages[0].extract_text() or ""
            if f"RASPORED ODVOZA MIJEŠANOG KOMUNALNOG OTPADA ZA {year}. GODINU" not in first:
                problems.append(f"PDF nije raspored za {year}")
            groups = groups_of(pdf, year, problems)
            cal_page = pdf.pages[2]
            if f"RECIKLABILNOGOTPADA{year}" not in re.sub(r"\s", "", cal_page.extract_text() or ""):
                problems.append(f"3. stranica nije kalendar reciklabilnog otpada za {year}")
            rec_days = recycling_days(cal_page, problems)
            found, palette = calendar(cal_page, year, problems)
    moves = notices(year, problems)
    print(f"PDF: {url}; {len(groups)} skupina ulica; reciklabilni po naseljima: {rec_days}; "
          f"kalendar: {dict(Counter(found.values()))}")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "izvor": url, "napomene": NAPOMENE, "zone": {}}
    used = Counter()
    first_day, last_day = date(year, 1, 1), date(year, 12, 31)
    all_streets = Counter()
    for place, st, winter, summer in groups:
        name = f"{place}: {st[:40]}"
        w_spans, w_days = season(winter, year, name, problems)
        s_spans, s_days = season(summer, year, name, problems)
        streets = split_streets(st)
        all_streets.update(streets)
        hits = {n for n in rec_days if re.search(rf"\b{n}\b", st.upper())} - {place.upper()}
        settlements = sorted(hits) or [place.upper()]
        rdays = {rec_days.get(s) for s in settlements}
        if len(rdays) != 1 or None in rdays:
            problems.append(f"{name}: dan reciklabilnog otpada nije jednoznačan ({settlements})")
            continue
        rday = rdays.pop()
        covered = Counter()
        out = {}
        for spans, days in ((w_spans, w_days), (s_spans, s_days)):
            for a, b in spans:
                d = a
                while d <= b:
                    covered[d] += 1
                    if d.weekday() in days:
                        out[d] = "M"
                    d += timedelta(days=1)
        if len(covered) != (last_day - first_day).days + 1 or max(covered.values()) != 1:
            problems.append(f"{name}: zimski i ljetni termin ne pokrivaju godinu točno jednom")
        for d, c in found.items():
            if d.weekday() == rday:
                out[d] = out.get(d, "") + c
        rows = {d: (c, False) for d, c in out.items()}
        for old_d, new_d, codes in moves:
            have, was = rows.get(old_d, ("", False))
            moved = "".join(c for c in have if c in codes)
            if not moved:
                continue
            used[old_d] += 1
            rest = "".join(c for c in have if c not in codes)
            if rest:
                rows[old_d] = (rest, was)
            else:
                rows.pop(old_d)
            if new_d:
                have2 = rows.get(new_d, ("", False))[0]
                if set(have2) & set(moved):
                    problems.append(f"{name}: {moved} već postoji {new_d}")
                rows[new_d] = (have2 + moved, True)
        # checks
        per = Counter()
        for d, (codes, moved) in rows.items():
            for c in codes:
                per[c, d.month] += 1
                ok = d.weekday() == rday if c in "KP" else \
                    d.weekday() in (s_days if any(a <= d <= b for a, b in s_spans) else w_days)
                if not ok and not moved:
                    problems.append(f"{name}: {c} {d} nije na pravi dan")
        for (c, m), n in per.items():
            if not (6 <= n <= 14 if c == "M" else 1 <= n <= 3):
                problems.append(f"{name}: {c} {n} puta u mjesecu {m}")
        k = str(len(data["zone"]) + 1)
        label = ", ".join(s.title() for s in settlements) if hits else place
        w_txt, s_txt = (", ".join(INSTR[i] for i in ds[:-1]) + " i " + INSTR[ds[-1]] if len(ds) > 1 else INSTR[ds[0]]
                        for ds in (w_days, s_days))
        zone = {
            "jls": "Tisno",
            "podrucje": f"{label} – " + ", ".join(streets[:3]) + (" …" if len(streets) > 3 else ""),
            "ulice": streets,
            "napomena": f"Miješani otpad zimi ({', '.join(f'{a:%d.%m.}–{b:%d.%m.}' for a, b in w_spans)}) {w_txt}, "
                        f"ljeti ({', '.join(f'{a:%d.%m.}–{b:%d.%m.}' for a, b in s_spans)}) {s_txt}; papir i karton "
                        f"te plastika, staklo i metal {INSTR[rday]}, naizmjence svaki drugi tjedan prema kalendaru.",
        }
        prev = old["zone"].get(k, {})
        keep = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("ulice") == streets else {}
        zone["raw"] = {**keep, str(year): podaci.month_lines([(d, c, m) for d, (c, m) in rows.items()])}
        data["zone"][k] = zone
        print(f"Zona {k}: {zone['podrucje'][:70]} – {len(streets)} ulica, "
              f"{dict(Counter(c for codes, _ in rows.values() for c in codes))}")
    for d, _, _ in moves:
        if not used[d]:
            problems.append(f"obavijest za {d}: tog dana nema odvoza ni u jednoj zoni")
    print("Obavijesti: " + ", ".join(f"{a:%d.%m.}->{f'{b:%d.%m.}' if b else 'nema odvoza'} ({c})" for a, b, c in moves))
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
