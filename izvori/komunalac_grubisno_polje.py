"""Grubišno Polje: Komunalac d.o.o. Grubišno Polje, standing rules (mixed waste weekday, n-th Monday recyclables).

    python3 -m izvori.komunalac_grubisno_polje [--year 2026]

The article "Raspored odvoza komunalnog otpada" (24.04.2025.) lists the streets and settlements of the
mixed waste days (green bins, weekly: Tuesday to Friday) and of the recyclable rounds (yellow and blue
bins together, the 1st, 2nd, 3rd or 4th Monday of the month); biowaste (brown bins) goes every Friday.
Each mixed waste item is matched to the recyclable list that holds most of its weekday's items (the
lists repeat the same groups; the same street name can stand for parts of different places).
Holidays: the article's rule (mixed waste falling on a public holiday is skipped that week) is applied;
recyclables on a holiday Monday go a week later (the company's notice for 22.6.2026 -> 29.6.2026, used
as the practice for the other Monday holidays). The 4th Monday round "starts when the yellow bins are
handed out"; the June 2026 notice moves a 4th Monday, so it is written with a note.
"""
import argparse
import html
import re
import sys
from collections import Counter
from datetime import timedelta

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import blagdani, mjesecno, tjedno

SLUG = "komunalac-grubisno-polje"
SITE = "https://www.komunalac-gp.hr"
PAGE = SITE + "/komunalac/raspored-odvoza-komunalnog-otpada/48/"
DAYS = {"UTORAK": "uto", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet"}
DAY_NAME = {"uto": "utorak", "sri": "srijeda", "čet": "četvrtak", "pet": "petak"}
HEADINGS = {"Grubišno Polje"}  # a heading line inside a list, not a place of its own
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Grubišno Polje",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Bjelovarsko-bilogorska",
    "jls": ["Grubišno Polje"],
    "nazivi": {"M": "Miješani komunalni otpad (zelena kanta)"},
    "bioNapomena": "Biootpad (smeđe kante) odvozi se svaki petak, za korisnike sa smeđom kantom.",
}
NAPOMENE = [
    "Raspored je trajan (objavljen 24. 4. 2025.); datumi su izračunati iz pravila.",
    "Ako odvoz miješanog otpada padne na državni praznik, taj se tjedan ne odvozi, nego idući tjedan po "
    "rasporedu (pravilo davatelja).",
    "Reciklažni otpad (žute i plave kante) odvozi se zajedno jednom mjesečno, n-ti ponedjeljak u mjesecu; "
    "ponedjeljak koji je praznik odvozi se idući ponedjeljak (obavijest za 22. 6. 2026.).",
    "Iste ulice javljaju se u više skupina (dijelovi ulica ili istoimene ulice u Velikim Zdencima i "
    "Grubišnom Polju); u nejasnim slučajevima provjerite kod davatelja (043 485 006).",
]
LATE = ("Prema rasporedu odvoz žutih i plavih kanata 4. ponedjeljkom počinje kad se kante podijele po "
        "naseljima; obavijest davatelja od 17. 6. 2026. pomiče taj odvoz, pa je upisan – provjerite.")


def lines(page):
    seg = re.sub(r"<(script|style).*?</\1>", "", page, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"</(p|h\d|li|tr|div)>|<br\s*/?>", "\n", seg)))
    return [" ".join(l.split()) for l in text.splitlines() if l.strip()]


def sections(rows):
    """({weekday: [items]}, {n: [items]}) from the mixed waste and the recyclable lists."""
    start, mid = rows.index("ZELENE KANTE"), next(i for i, l in enumerate(rows) if l.startswith("Napomena"))
    rec = next(i for i, l in enumerate(rows) if l.startswith("ŽUTE KANTE"))
    mixed, cur = {}, None
    for l in rows[start + 1:mid]:
        if l in DAYS or l == "PONEDJELJAK":
            cur = DAYS.get(l)
            continue
        if cur:
            mixed.setdefault(cur, []).append(l)
    recyc, n = {}, None
    for l in rows[rec + 1:]:
        m = re.match(r"(\d) ?\. PONEDJELJAK U MJESECU$", l)
        if m:
            n = int(m.group(1))
            continue
        if l.startswith("Podijelite"):
            break
        if n and not l.startswith("odvoz će se"):
            recyc.setdefault(n, []).append(l)
    return mixed, recyc


def key(item):
    return " ".join(re.sub(r"\s*–\s*samo.*$", "", item).replace(".", ". ").split()).lower()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    rows = lines(fetch(PAGE).decode("utf-8", "replace"))
    mixed, recyc = sections(rows)
    problems = []
    if sorted(mixed) != sorted(DAYS.values()) or sorted(recyc) != [1, 2, 3, 4]:
        sys.exit(f"Raspored se promijenio: dani {sorted(mixed)}, ponedjeljci {sorted(recyc)}. Ništa nije upisano.")
    if not any("4. PONEDJELJAK" in l or "kada se podjele" in l for l in rows):
        problems.append("tekst o 4. ponedjeljku se promijenio")
    hol = set(blagdani(year)) | set(blagdani(year + 1))
    groups, town = {}, {}
    for day, items in mixed.items():
        town[day] = next((it for it in items if it in HEADINGS), "")
        counts = Counter(n for it in items for n, lst in recyc.items() if key(it) in map(key, lst))
        main_n = counts.most_common(1)[0][0]
        for it in items:
            if it in HEADINGS:
                continue
            if key(it) in map(key, recyc[main_n]):
                n = main_n
            else:
                found = [n for n, lst in recyc.items() if key(it) in map(key, lst)]
                if len(found) != 1:
                    problems.append(f"{it} ({day}): skupina reciklažnog otpada {found}")
                    continue
                n = found[0]
            groups.setdefault((day, n), []).append(it)
    zones = {}
    for z, ((day, n), items) in enumerate(sorted(groups.items(), key=lambda g: (list(DAYS.values()).index(g[0][0]), g[0][1])), 1):
        out = {d: ["M", False] for d in tjedno(year, day) if d not in hol}
        for d in mjesecno(year, "pon", n):
            moved = d in hol
            d += timedelta(days=7 * moved)
            out.setdefault(d, ["", False])[0] += "PK"
            out[d][1] |= moved
        for d in tjedno(year, "pet"):
            out.setdefault(d, ["", False])[0] += "B"
        rows_ = sorted((d, c, mv) for d, (c, mv) in out.items())
        cnt = Counter(c for _, codes, _ in rows_ for c in codes)
        if not (48 <= cnt["M"] <= 53 and cnt["P"] == 12 and 52 <= cnt["B"] <= 53):
            problems.append(f"zona {z}: broj odvoza {dict(cnt)}")
        places = [it for it in items]
        zone = {"jls": "Grubišno Polje",
                "podrucje": f"{DAY_NAME[day].capitalize()}, reciklažni {n}. ponedjeljak: "
                            + (f"{town[day]} – " if town[day] and n != 4 else "") + ", ".join(places[:4])
                            + (", …" if len(places) > 4 else ""),
                "ulice": places}
        if n == 4:
            zone["napomena"] = LATE
        zone["raw"] = {str(year): podaci.month_lines(rows_)}
        zones[str(z)] = zone
        print(f"Zona {z} ({DAY_NAME[day]}, {n}. ponedjeljak): " + ", ".join(f"{c} {cnt[c]}" for c in "MBPK")
              + f", mjesta {len(places)}")
    moved = [f"{h:%d.%m.}" for h in sorted(hol) if h.year == year and h.weekday() == 0]
    print(f"Ponedjeljci-praznici (reciklažni tjedan kasnije, prema obavijesti za 22.6.): {', '.join(moved)}")
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "napomene": NAPOMENE, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json ({len(zones)} zona)")


if __name__ == "__main__":
    main()
