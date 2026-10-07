"""Gospić: Komunalac Gospić d.o.o. (komunalac-gospic.hr), mixed waste by weekday and street, paper and plastic.

    python3 -m izvori.komunalac_gospic [--year 2026]

Mixed waste: the page "Raspored odvoza miješanog komunalnog otpada" (WordPress REST) lists the streets and
settlements of each weekday (Monday-Friday), collected every week; the dates are computed. Paper (blue
bin) and plastic (yellow bin): the pages "Raspored odvoza papira" and "Raspored odvoza plastike" link
the year PDFs (WeasyPrint tables: weekday group, the exact dates, the streets), read with pdfplumber word
positions. The mixed-waste and recyclables groups are not the same, so every street is matched by name
(abbreviated first names allowed, e.g. "Popa N. Mašića" = "Popa Nikole Mašića"; a few streets split by
section are matched by hand in SECTIONS) and each combination of mixed-waste day and recyclables group
is one zone. Streets only on the recyclables lists get a zone without mixed waste.
No holiday rule is published: the weekly mixed-waste dates are kept as computed (napomena says so); in
the recyclables PDFs a date off the group's weekday within a week of a holiday is marked as moved.
"""
import argparse
import html
import json
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalac-gospic"
SITE = "https://komunalac-gospic.hr"
PAGE = SITE + "/wp-json/wp/v2/pages?slug={slug}&_fields=id,link,modified,content"
SLUGS = {"M": "raspored-odvoza-mijesanog-komunalnog-otpada", "P": "raspored-odvoza-plastike",
         "K": "raspored_odvoza_papira"}
DAYS = ["PONEDJELJAK", "UTORAK", "SRIJEDA", "ČETVRTAK", "PETAK"]
DAN_INS = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom"]
# mixed-waste street sections -> recyclables group (None: not on the recyclables lists)
SECTIONS = {"Pazariška (do POS zgrada)": 0, "Pazariška (od POS zgrada)": 2, "Budačka (do semafora)": 2,
            "Budačka (od semafora do Stop Shop-a)": 3, "Budačka (od Stop Shop-a)": None}
# spellings on the recyclables lists: "Mile Stračevića", "Osiječka", "Pazariška od posjeda zgrada"
TYPOS = {"stracevica": "starcevica", "osijecka": "osjecka", "posjeda": "pos"}
PROVIDER = {
    "davatelj": "Komunalac Gospić d.o.o.",
    "web": SITE,
    "zupanija": "Ličko-senjska",
    "jls": ["Gospić"],
    "nazivi": {"P": "Plastika (žuti spremnik)", "K": "Papir (plavi spremnik)"},
}
NAPOMENE = [
    "Miješani komunalni otpad odvozi se jednom tjedno prema danu za ulicu; posude iznijeti do 6:00 sati.",
    "Raspored ne navodi pomake zbog blagdana: tjedni datumi miješanog otpada izračunati su prema danu u tjednu i "
    "prikazani bez pomaka. Za odvoz na blagdan provjerite kod davatelja (053 658 234, info@komunalac-gospic.hr).",
    "Papir i plastika: posude iznijeti 1-2 m od ruba kolnika najkasnije do 7:00 sati na dan odvoza.",
    "Ispravak od 1.4.2026.: plastika se u travnju odvozila 1. i 2. travnja (ne 8. i 9. kako je ranije pisalo); "
    "raspored koji se sada objavljuje već ima 1. i 2. travnja.",
]


def fold(s):
    s = s.lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def base(name):
    """'Budačka (od kućnog broja 1 do semafora)' -> 'Budačka'; 'Pazariška od POS zgrada' stays."""
    return re.sub(r"\s*\(.*?\)\s*", " ", name).strip()


def keys(name):
    """(full key, short key): 'Popa N. Mašića' -> ('popa n masica', 'p masica'); 'T.Novoselo' -> (.., 't novoselo')."""
    toks = [TYPOS.get(t, t) for t in re.findall(r"\d+|[^\W\d_]+", fold(base(name)))]
    return " ".join(toks), (toks[0][0] + " " + toks[-1]) if len(toks) > 1 else " ".join(toks)


def mixed_lists(content):
    """{weekday 0-4: [streets]} from the day cards of the mixed-waste page."""
    out = {}
    for title, body in re.findall(r'class="day-title">\s*(.*?)\s*</div>\s*<ul[^>]*>(.*?)</ul>', content, re.S):
        day = next((i for i, d in enumerate(DAYS) if fold(d) == fold(html.unescape(title).strip())), None)
        if day is not None:
            out[day] = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", li)).split())
                        for li in re.findall(r"<li[^>]*>(.*?)</li>", body, re.S)]
    return out


def read_table(pdf, year, problems, what):
    """Recyclables PDF -> {weekday 0-4: ([dates], [streets])}."""
    out = {}
    with pdfplumber.open(pdf) as doc:
        text = "\n".join(p.extract_text() or "" for p in doc.pages)
        if f"{year}" not in text:
            problems.append(f"{what}: PDF nije za {year}")
        for page in doc.pages:
            words = page.extract_words()
            dates_x = next((w["x0"] for w in words if w["text"] == "Točni"), None)
            streets_x = next((w["x0"] for w in words if w["text"] == "Pripadajuće"), None)
            if dates_x is None or streets_x is None:
                continue
            end = min([w["top"] for w in words if w["text"] in ("*", "Komunalac") and w["top"] > 100] + [page.height])
            anchors = sorted((w["top"], DAYS.index(w["text"])) for w in words if w["text"] in DAYS and w["x1"] < dates_x)
            for i, (top, day) in enumerate(anchors):
                bottom = anchors[i + 1][0] if i + 1 < len(anchors) else end
                row = [w for w in words if top - 2 <= w["top"] < bottom - 2]
                dtext = " ".join(w["text"] for w in row if dates_x - 5 <= w["x0"] < streets_x - 5)
                stext = " ".join(w["text"] for w in sorted(row, key=lambda w: (round(w["top"]), w["x0"]))
                                 if w["x0"] >= streets_x - 5)
                dates = []
                for d, m in re.findall(r"(\d{1,2})\.(\d{1,2})\.", dtext):
                    try:
                        dates.append(date(year, int(m), int(d)))
                    except ValueError:
                        problems.append(f"{what} {DAYS[day]}: nemoguć datum {d}.{m}.")
                streets = list(dict.fromkeys(s.strip(" .") for s in stext.split(",") if s.strip(" .")))
                out[day] = (dates, streets)
    return out


def check_dates(what, day, dates, problems):
    """[(date, moved)]; 12 dates a year, in order, on the weekday unless a holiday is within a week."""
    hol = pravila.blagdani(dates[0].year) if dates else []
    out = []
    if len(dates) != 12 or dates != sorted(set(dates)):
        problems.append(f"{what} {DAYS[day]}: {len(dates)} datuma ili nisu poredani")
    if any(n > 2 for n in Counter(d.month for d in dates).values()):
        problems.append(f"{what} {DAYS[day]}: više od dva datuma u mjesecu")
    for d in dates:
        moved = d.weekday() != day
        if moved:
            near = [h for h in hol if abs((d - h).days) <= 7]
            if not near:
                problems.append(f"{what} {DAYS[day]}: {d:%d.%m.} ({podaci.DAYS[d.weekday()]}) nije {podaci.DAYS[day]}")
            else:
                print(f"UPOZORENJE {what} {DAYS[day]}: {d:%d.%m.} je {podaci.DAYS[d.weekday()]} (blagdan "
                      f"{near[0]:%d.%m.}), označeno kao pomaknuto")
        if d in hol or d.weekday() == 6:
            problems.append(f"{what} {DAYS[day]}: {d:%d.%m.} je blagdan ili nedjelja")
        out.append((d, moved))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    pages = {}
    for code, slug in SLUGS.items():
        found = json.loads(fetch(PAGE.format(slug=slug)))
        if not found:
            sys.exit(f"Stranica {slug} nije pronađena. Ništa nije upisano.")
        pages[code] = found[0]
    mixed = mixed_lists(pages["M"]["content"]["rendered"])
    if sorted(mixed) != [0, 1, 2, 3, 4] or any(len(v) < 5 for v in mixed.values()):
        problems.append(f"miješani: dani {sorted(mixed)}")
    rec = {}
    with tempfile.TemporaryDirectory() as tmp:
        for code in "KP":
            urls = [html.unescape(u) for u in re.findall(r'href="([^"]+\.pdf)"', pages[code]["content"]["rendered"])]
            urls = [u for u in urls if str(year) in u.rsplit("/", 1)[1]]
            if not urls:
                problems.append(f"{SLUGS[code]}: nema PDF-a za {year}")
                continue
            pdf = Path(tmp) / f"{code}.pdf"
            fetch(urls[0], pdf)
            rec[code] = read_table(pdf, year, problems, {"K": "papir", "P": "plastika"}[code])
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")

    # recyclables groups: the same weekdays and streets for paper and plastic
    groups = {}
    for day in sorted(set(rec["K"]) | set(rec["P"])):
        k, p = rec["K"].get(day), rec["P"].get(day)
        if not k or not p:
            problems.append(f"{DAYS[day]}: samo papir ili samo plastika")
            continue
        kk, pk = {keys(s)[0] for s in k[1]}, {keys(s)[0] for s in p[1]}
        if kk != pk:
            print(f"UPOZORENJE {DAYS[day]}: ulice papira i plastike razlikuju se: {sorted(kk ^ pk)}")
        streets = list(dict.fromkeys(k[1] + [s for s in p[1] if keys(s)[0] not in kk]))
        rows = [(d, "K", mv) for d, mv in check_dates("papir", day, k[0], problems)]
        rows += [(d, "P", mv) for d, mv in check_dates("plastika", day, p[0], problems)]
        groups[day] = (streets, rows)
    full, short = {}, {}
    for day, (streets, _) in groups.items():
        for s in streets:
            f, a = keys(s)
            full.setdefault(f, set()).add(day)
            short.setdefault(a, set()).add(day)

    def group_of(street):
        if street in SECTIONS:
            return SECTIONS[street], True
        f, a = keys(street)
        for index, key in ((full, f), (short, a)):
            if len(index.get(key, ())) == 1:
                return next(iter(index[key])), True
        return None, False

    zones = {}
    for day, streets in sorted(mixed.items()):
        for s in streets:
            g, found = group_of(s)
            if not found:
                print(f"UPOZORENJE: '{s}' ({DAYS[day]}) nije na popisu papira i plastike")
            zones.setdefault((day, g), []).append(s)
    all_mixed = [m for ms in mixed.values() for m in ms]

    def in_mixed(r, g):
        return any(keys(m)[0] == keys(r)[0] or keys(m)[1] == keys(r)[1]
                   or SECTIONS.get(m, -1) == g and fold(m).split()[0] == fold(r).split()[0] for m in all_mixed)

    for g, (streets, _) in groups.items():
        rest = [s for s in streets if not in_mixed(s, g)]
        if rest:
            print(f"UPOZORENJE: samo na popisu papira i plastike ({DAYS[g]}): {rest}")
            zones[(None, g)] = rest

    out = []
    for (day, g), streets in sorted(zones.items(), key=lambda kv: (kv[0][0] is None, kv[0][0] or 0, kv[0][1] is None, kv[0][1] or 0)):
        rows = [(d, "M", False) for d in pravila.tjedno(year, list(pravila.DANI)[day])] if day is not None else []
        rows += groups[g][1] if g is not None else []
        merged = {}
        for d, c, mv in rows:
            cc, mm = merged.get(d, ("", False))
            merged[d] = (cc + c, mm or mv)
        rows = sorted((d, c, mv) for d, (c, mv) in merged.items())
        head = (f"Miješani {DAN_INS[day]}" if day is not None else "Miješani: dan nije naveden") + (
            f", papir i plastika {DAN_INS[g]}" if g is not None else ", papir i plastika: nisu navedeni")
        note = []
        if g is None:
            note.append("Ove ulice nisu na popisima papira i plastike; raspored provjerite kod davatelja.")
        if day is None:
            note.append("Ove ulice su samo na popisima papira i plastike; dan odvoza miješanog otpada nije naveden.")
        zone = {"jls": "Gospić", "podrucje": f"{head}: " + ", ".join(streets[:4]) + (", …" if len(streets) > 4 else ""),
                "ulice": streets, "rows": rows}
        if note:
            zone["napomena"] = " ".join(note)
        out.append(zone)
        cnt = Counter((d.month, c) for d, codes, _ in rows for c in codes)
        if day is not None and any(not 4 <= cnt.get((m, "M"), 0) <= 5 for m in range(1, 13)):
            problems.append(f"{head}: broj odvoza miješanog otpada po mjesecima")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": pages["M"]["link"], "napomene": NAPOMENE, "zone": {}}
    for i, z in enumerate(out, 1):
        rows = z.pop("rows")
        prev = old.get(str(i), {})
        raw = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("podrucje") == z["podrucje"] else {}
        z["raw"] = {**raw, str(year): podaci.month_lines(rows)}
        data["zone"][str(i)] = z
        cnt = Counter(c for _, codes, _ in rows for c in codes)
        print(f"Zona {i}: {z['podrucje'][:75]}: " + ", ".join(f"{c} {cnt[c]}" for c in "MPK" if cnt[c])
              + f", pomaknuto {sum(1 for *_, m in rows if m)}, ulica {len(z['ulice'])}")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(out)} zona)")


if __name__ == "__main__":
    main()
