"""Greben Brela d.o.o.: Općina Brela, two street groups, schedule published per period (now from 1.10.2026).

    python3 -m izvori.greben_brela [--year 2026]

The page "Raspored odvoza otpada" is rewritten when the season changes ("RASPORED ODVOZA OTPADA OD 01.10.2026"):
two paragraphs give the mixed-waste weekdays and their streets ("PONEDJELJAK, SRIJEDA, PETAK <streets>"), then
paper ("PAPIR I KARTON – ČETVRTKOM") and PET/MET packaging ("PET/MET – UTORAK I SUBOTA") for everybody. The
script reads these lines, writes the dates from the start date to the end of that year (no end date is
published) and keeps the earlier dates already in podaci/<slug>.json, so re-runs in later periods add up.
No holiday rule is published: the regular dates are kept and a note says so.
"""
import argparse
import html
import re
import sys
from collections import Counter
from datetime import date

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "greben-brela"
SITE = "https://greben-brela.hr"
PAGE = SITE + "/raspored-odvoza-otpada/"
DAYS = {"PONEDJELJAK": "pon", "UTORAK": "uto", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet",
        "SUBOTA": "sub", "NEDJELJA": "ned", "PONEDJELJKOM": "pon", "UTORKOM": "uto", "SRIJEDOM": "sri",
        "ČETVRTKOM": "čet", "PETKOM": "pet", "SUBOTOM": "sub"}
DAY_RE = "|".join(DAYS)
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Greben Brela d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Brela"],
    "nazivi": {"K": "Papir i karton (plava kanta)", "P": "PET i metalna ambalaža (PET/MET)"},
}
NAPOMENE = [
    "Greben Brela objavljuje raspored za tekuće razdoblje i mijenja ga promjenom sezone; raniji (ljetni) raspored "
    "nije sačuvan na stranici.",
    "Pomaci zbog blagdana nisu objavljeni; upisani su redovni dani odvoza.",
    "Od 1.5. do 1.11. zabranjeno je odlaganje krupnog otpada.",
]

DANI = list(pravila.DANI)


def day_list(t):
    """'PONEDJELJAK, SRIJEDA, PETAK' / 'UTORAK I SUBOTA' / 'ČETVRTKOM' -> 'pon sri pet'."""
    words = [w for w in re.split(r"\s*,\s*|\s+I\s+", t.strip()) if w]
    return " ".join(DAYS[w] for w in words) if words and all(w in DAYS for w in words) else None


def read_page(problems):
    """(start date, [(weekdays, streets)], {code: weekdays}) from the page."""
    page = fetch(PAGE).decode("utf-8", "replace")
    paras = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)).replace("\xa0", " ").split())
             for p in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S)]
    paras = [p for p in paras if p]
    start, groups, other = None, [], {}
    for p in paras:
        m = re.fullmatch(r"OD (\d{1,2})\.(\d{1,2})\.(\d{4})\.?", p)
        if m:
            start = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            continue
        m = re.fullmatch(rf"((?:{DAY_RE})(?:\s*,\s*(?:{DAY_RE}))*)\s+([A-ZČĆŠŽĐa-zčćšžđ].*)", p)
        if m and day_list(m.group(1)) and not m.group(2).isupper():
            groups.append((day_list(m.group(1)), [s.strip() for s in m.group(2).split(",") if s.strip()]))
            continue
        m = re.fullmatch(r"(PAPIR I KARTON|PET/MET)\s*[–-]\s*(.+)", p)
        if m:
            days = day_list(m.group(2))
            if not days:
                problems.append(f"nepoznati dani: {p!r}")
            other["K" if m.group(1).startswith("PAPIR") else "P"] = days
    if not start:
        problems.append("na stranici nema datuma početka ('OD DD.MM.YYYY')")
    if len(groups) != 2 or set(other) != {"K", "P"}:
        problems.append(f"neočekivan sadržaj: {len(groups)} skupina ulica, ostalo {other}")
    return start, groups, other


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    start, groups, other = read_page(problems)
    if start and start.year != year:
        problems.append(f"objavljen je raspored od {start:%d.%m.%Y}; za {year}. nije objavljen")
    if problems:
        for p in problems:
            print("   PROBLEM", p)
        sys.exit("Ništa nije upisano.")
    end = date(year, 12, 31)
    print(f"Raspored od {start:%d.%m.%Y}; upisujem {start:%d.%m.%Y} – {end:%d.%m.%Y} (kraj nije objavljen)")
    print("Pretpostavka: blagdani bez pomaka (pravilo nije objavljeno).")
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    zones = {}
    for z, (mko, streets) in enumerate(groups, 1):
        dates = {}
        for code, rule in [("M", mko), ("K", other["K"]), ("P", other["P"])]:
            for k in rule.split():
                for d in pravila.tjedno(year, k):
                    if start <= d <= end:
                        if code in dates.get(d, ""):
                            problems.append(f"zona {z}: {code} dvaput {d}")
                        dates[d] = dates.get(d, "") + code
        rows = sorted((d, c, False) for d, c in dates.items())
        # checks: weekday of every date, counts per month
        for d, codes, _ in rows:
            for c in codes:
                if DANI[d.weekday()] not in {"M": mko, "K": other["K"], "P": other["P"]}[c].split():
                    problems.append(f"zona {z}: {c} {d} nije po pravilu")
        for m in range(start.month + (start.day > 1), 13):
            for c, rule in [("M", mko), ("K", other["K"]), ("P", other["P"])]:
                n = sum(1 for d, cs, _ in rows if d.month == m and c in cs)
                k = len(rule.split())
                if not 4 * k <= n <= 5 * k:
                    problems.append(f"zona {z}: {c} u {m}. mjesecu {n} puta")
        prev = old.get(str(z))
        keep = []
        if prev and prev.get("ulice") == streets:
            keep = [r for r in podaci.iter_dates(prev) if r[0] < start or r[0] > end]
        elif prev:
            print(f"Zona {z}: popis ulica se promijenio, stari datumi se ne prenose")
        by_year = {}
        for r in keep + rows:
            by_year.setdefault(r[0].year, []).append(r)
        days_txt = ", ".join(DAN[DANI.index(k)] for k in mko.split())
        zones[str(z)] = {"jls": "Brela", "podrucje": f"{days_txt.capitalize()} – " + ", ".join(streets[:3]) + " …",
                         "ulice": streets,
                         "napomena": f"Od {start:%d.%m.%Y}.: miješani otpad {days_txt}; papir i karton "
                                     + ", ".join(DAN[DANI.index(k)] for k in other["K"].split()) + "; PET/MET "
                                     + ", ".join(DAN[DANI.index(k)] for k in other["P"].split()) + ".",
                         "raw": {str(y): podaci.month_lines(v) for y, v in sorted(by_year.items())}}
        print(f"Zona {z} ({days_txt}): {len(streets)} ulica; " + ", ".join(
            f"{c} {n}" for c, n in sorted(Counter(c for _, cs, _ in rows for c in cs).items()))
              + (f"; zadržano starih datuma: {len(keep)}" if keep else ""))
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    first = min(d for z in zones.values() for d, _, _ in podaci.iter_dates(z))
    napomene = NAPOMENE + [f"Upisani su datumi od {first:%d.%m.%Y}. do {end:%d.%m.%Y}.; raspored od "
                           f"{start:%d.%m.%Y}. nema objavljen datum završetka."]
    podaci.save(SLUG, {**PROVIDER, "napomene": napomene, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
