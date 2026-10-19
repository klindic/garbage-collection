"""Belišće: Kombel d.o.o. (kombel.hr), standing rules per weekday and settlement.

    python3 -m izvori.kombel [--year 2026]

The page "Raspored odvoza komunalnog otpada" lists the mixed waste day of the two town zones (streets),
of a few streets with Bistrinci (Monday) and of the suburban settlements, then the rules for plastic
(first Tuesday: Belišće and Bistrinci; first Wednesday: the other settlements), paper (first Friday:
Belišće and Bistrinci; last Friday: the other settlements) and biowaste (Wednesday Belišće, Thursday the
settlements). The weekday lists are parsed; the recyclable and bio rules must appear word for word,
else the script stops. Every zone is one weekday line (Monday splits into the Belišće streets and
Bistrinci, which differ in the biowaste day).
Holidays, from the company's notices (2025-2026): mixed waste is collected on holidays, except on
Christmas and New Year's Day, when it moves to the next day; biowaste is not collected on a holiday;
the plastic round moves a week when its Tuesday or Wednesday is a holiday, and the suburbs' plastic
always follows the town's Tuesday (notices for April and July: the 8th, not the 1st); paper on a holiday
Friday moves a week. Notices that are not out yet are assumed to follow this practice (printed).
"""
import argparse
import html
import re
import sys
from collections import Counter
from datetime import date, timedelta

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import blagdani, mjesecno, tjedno

SLUG = "kombel"
SITE = "https://www.kombel.hr"
PAGE = SITE + "/raspored-odvoza-komunalnog-otpada/"
DAYS = {"PONEDJELJAK": "pon", "UTORAK": "uto", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet"}
RULES = [  # text on the page -> what it means here
    "SVAKI PRVI UTORAK U MJESECU – Belišće i Bistrinci",
    "SVAKA PRVA SRIJEDA U MJESECU – ostala prigradska naselja (osim Bistrinaca)",
    "SVAKI PRVI PETAK U MJESECU – Belišće i Bistrinci",
    "SVAKI ZADNJI PETAK U MJESECU – prigradska naselja osim Bistrinaca",
    "SRIJEDA – Belišće",
    "ČETVRTAK – prigradska naselja",
]
PROVIDER = {
    "davatelj": "Kombel d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Osječko-baranjska",
    "jls": ["Belišće"],
    "nazivi": {"P": "Plastika (žuti spremnik)", "K": "Stari papir (plavi spremnik)"},
    "bioNapomena": "Biootpad se odvozi srijedom u Belišću i četvrtkom u prigradskim naseljima.",
}
NAPOMENE = [
    "Pravila odvoza su trajna (bez godišnjeg kalendara); datumi su izračunati iz njih.",
    "Blagdani prema obavijestima davatelja: miješani otpad odvozi se redovno, osim na Božić i Novu godinu "
    "(dan kasnije); biootpad se na blagdan ne odvozi; plastika i papir na blagdan se odvoze tjedan dana "
    "kasnije. Obavijesti za blagdane objavljuju se na kombel.hr (Novosti).",
    "Od 15. lipnja do 30. kolovoza odvoz počinje u 6 sati; spremnike pripremite večer prije.",
    "Krupni otpad: u prigradskim naseljima jednom godišnje po dogovoru s mjesnim odborom, u Belišću po "
    "dogovoru s korisnicima.",
    "Kombel d.o.o., Radnička 1/B, Belišće, tel. 031/662-243.",
]


def lines(page):
    seg = re.sub(r"<(script|style).*?</\1>", "", page, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"</(p|h\d|li|tr)>|<br\s*/?>", "\n", seg)))
    return [" ".join(l.split()) for l in text.splitlines() if l.strip()]


def groups(rows):
    """[(weekday, area, places, is_town)] from 'SRIJEDA: BELIŠĆE I zona: Zinke Kunc; …' lines."""
    out = []
    for line in rows:
        m = re.match(r"(%s): (.*)$" % "|".join(DAYS), line)
        if not m:
            continue
        day, body = DAYS[m.group(1)], m.group(2)
        if body.startswith("prigradska naselja:"):
            places = [p.strip(" .").title() for p in body.split(":", 1)[1].split(";") if p.strip(" .")]
            out.append((day, "prigradska naselja", places, False))
        elif re.match(r"BELIŠĆE I+ zona:", body):
            zone, streets = body.split(":", 1)
            out.append((day, zone.replace("BELIŠĆE", "Belišće"), [s.strip(" .") for s in re.split(r"[;,]", streets) if s.strip(" .")], True))
        elif body.startswith("BISTRINCI, BELIŠĆE:"):
            streets = [s.strip(" .") for s in body.split(":", 1)[1].split(";") if s.strip(" .")]
            out.append((day, "Bistrinci", ["Bistrinci"], False))
            out.append((day, "Belišće (ulice uz Bistrince)", streets, True))
        else:
            raise ValueError(f"nepoznat redak rasporeda: {line!r}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    rows = lines(fetch(PAGE).decode("utf-8", "replace"))
    missing = [r for r in RULES if r not in rows]
    if missing:
        sys.exit(f"Pravila na stranici su se promijenila (nema: {missing}). Ništa nije upisano.")
    found = groups(rows)
    # the second list on the page: "GAT -svaki utorak", "BELIŠĆE BEL I -srijeda"
    listed = {m.group(1).title(): DAYS[m.group(2).upper()] for l in rows
              if (m := re.match(r"([A-ZČĆŽŠĐ]+) ?[–-] ?svaki (\w+)$", l))}
    problems = [f"{p}: {listed.get(p)} / {d}" for d, _, ps, town in found if not town for p in ps
                if listed.get(p) != d]
    hol = set(blagdani(year)) | set(blagdani(year + 1)) | set(blagdani(year - 1))

    def mixed(day):
        out = []
        for d in tjedno(year, day):
            if (d.month, d.day) in ((12, 25), (1, 1)):  # practice of the notices: one day later
                out.append((d + timedelta(days=1), True))
            else:
                out.append((d, False))
        return out

    def plastic(suburb):
        out = []
        for d in mjesecno(year, "uto", 1):
            moved = d in hol or d + timedelta(days=1) in hol
            d += timedelta(days=7 * moved + suburb)
            out.append((d, moved or (suburb and d.day > 7)))
        return out

    def paper(last):
        out = []
        for d in mjesecno(year, "pet", -1 if last else 1):
            if d in hol and not last:
                out.append((d + timedelta(days=7), True))
            else:
                out.append((d, False))
        return out

    zones = {}
    for n, (day, area, places, town) in enumerate(found, 1):
        rows_ = {}
        for code, dates in [("M", mixed(day)), ("P", plastic(not town and area != "Bistrinci")),
                            ("K", paper(not town and area != "Bistrinci")),
                            ("B", [(d, False) for d in tjedno(year, "sri" if town else "čet") if d not in hol])]:
            for d, moved in dates:
                if d.year != year:
                    continue
                c, mv = rows_.get(d, ("", False))
                rows_[d] = (c + code, mv or moved)
        out = sorted((d, c, mv) for d, (c, mv) in rows_.items())
        cnt = Counter(c for _, codes, _ in out for c in codes)
        if not (51 <= cnt["M"] <= 53 and cnt["P"] == 12 and 12 <= cnt["K"] <= 13 and 45 <= cnt["B"] <= 53):
            problems.append(f"{area}: broj odvoza {dict(cnt)}")
        if any(d.weekday() == 6 for d, _, _ in out):
            problems.append(f"{area}: odvoz nedjeljom")
        title = podaci.DAYS[list(DAYS.values()).index(day)]
        zones[str(n)] = {
            "jls": "Belišće",
            "podrucje": f"{title.capitalize()}: {area}" + (" – " + ", ".join(places[:4]) + (", …" if len(places) > 4 else "")
                                                         if area not in places else ""),
            "ulice": places,
            "raw": {str(year): podaci.month_lines(out)},
        }
        print(f"Zona {n} ({title}, {area}): " + ", ".join(f"{c} {cnt[c]}" for c in "MBPK") + f", mjesta {len(places)}")
    later = [f"{h:%d.%m.}" for h in blagdani(year) if h.weekday() < 5 and h > date.today()]
    if later:
        print(f"Pretpostavka prema praksi (obavijest još nije objavljena): {', '.join(later)}")
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "napomene": NAPOMENE, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json ({len(zones)} zona)")


if __name__ == "__main__":
    main()
