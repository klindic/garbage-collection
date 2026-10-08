"""Komunalno Hvar d.o.o.: Grad Hvar (town bin users and the suburban villages), rules from October 2024.

    python3 -m izvori.komunalno_hvar [--year 2026]

The page "Čistoća" links "Raspored odvoza komunalnog otpada" (raspored-2024.pdf, 9.10.2024), still the only
household schedule. It is a text PDF with two small tables read with pdftotext -layout (columns split at runs
of spaces): GRAD HVAR / KORISNICI KANTI – mixed waste Monday and Thursday, paper on Saturdays and PET/MET/glass
on Wednesdays "(osim praznikom)"; PRIGRADSKA NASELJA – mixed waste Tuesday and Friday, paper on Tuesday
(Brusje and its coves, Velo Grablje, Milna) or Friday (Zaraće, Sveta Nedjelja). The standing weekly rules are
applied to the requested year with a note that they date from 2024. Holidays: paper and PET/MET/glass in town
are not collected on public holidays (no replacement), so those dates are dropped; for mixed waste nothing is
published and the regular dates are kept.
"""
import argparse
import html
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalno-hvar"
SITE = "https://komunalnohvar.com"
PAGE = SITE + "/cistoca/"
LINK_TEXT = "Raspored odvoza komunalnog otpada"
RULES_YEAR = 2024
DAYS = {"Ponedjeljak": "pon", "Utorak": "uto", "Srijeda": "sri", "Četvrtak": "čet", "Petak": "pet", "Subota": "sub",
        "Ponedjeljkom": "pon", "Utorkom": "uto", "Srijedom": "sri", "Četvrtkom": "čet", "Petkom": "pet",
        "Subotom": "sub"}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DANI = list(pravila.DANI)
GROUPS = {  # paper groups of the suburban table -> (podrucje, places)
    "Brusje i bruške vale, V. Grablje i Milna": ("Brusje i bruške uvale, Velo Grablje, Milna",
                                                 ["Brusje", "Bruške uvale", "Velo Grablje", "Milna"]),
    "Zaraće i sv. Nedjelja": ("Zaraće, Sveta Nedjelja", ["Zaraće", "Sveta Nedjelja"]),
}
OLD = (f"Raspored prema pravilima iz {RULES_YEAR}.; davatelj nije objavio novi raspored – provjerite prije "
       "odlaganja.")
PROVIDER = {
    "davatelj": "Komunalno Hvar d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Hvar"],
    "nazivi": {"P": "PET i metal", "S": "Staklo"},
    "napomene": [
        OLD,
        "Raspored za kućanstva iz listopada 2024. (raspored-2024.pdf) i dalje je jedini objavljen; raspored za "
        "pravne subjekte objavljuje se zasebno.",
        "Grad Hvar: papir te PET/MET i staklo ne odvoze se praznikom (zamjenski dan nije naveden), pa ti datumi nisu "
        "upisani. Za miješani otpad pravilo za blagdane nije objavljeno; upisani su redovni dani.",
        "Prigradska naselja: dan odvoza PET/MET ambalaže i stakla nije naveden u rasporedu.",
        "Gradska jezgra koristi spremnike s otpadomjerima; Jagodna nije navedena u rasporedu.",
        "Krupni otpad: jednom godišnje besplatno po pozivu. Reciklažno dvorište, Ulica Antifašizma 33: pon – pet "
        "7–14, sub 7–12 sati.",
    ],
}


def cols(line):
    return [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]


def read_pdf(text, problems):
    """[(podrucje, places, {code: weekdays}, codes skipped on holidays)] from the pdftotext -layout output."""
    lines = [l for l in text.splitlines() if l.strip()]
    i1 = next((i for i, l in enumerate(lines) if "GRAD HVAR / KORISNICI KANTI" in l), None)
    i2 = next((i for i, l in enumerate(lines) if l.strip().startswith("PRIGRADSKA NASELJA")), None)
    if i1 is None or i2 is None:
        problems.append("PDF nema očekivane naslove (GRAD HVAR / PRIGRADSKA NASELJA)")
        return []
    town = [cols(l) for l in lines[i1 + 2:i2]]
    sub = [cols(l) for l in lines[i2 + 2:]]
    want_head = ["MIJEŠANI KOMUNALNI OTPAD", "ODVOZ PAPIRA", "ODVOZ PET/MET/STAKLO", "ODVOZ KRUPNOG OTPADA"]
    if cols(lines[i1 + 1]) != want_head or cols(lines[i2 + 1]) != want_head:
        problems.append(f"zaglavlja tablica: {cols(lines[i1 + 1])} / {cols(lines[i2 + 1])}")
        return []
    out = []
    try:
        m_town = " ".join(DAYS[r[0]] for r in town)
        k_town, ps_town = DAYS[town[0][1]], DAYS[town[0][2]]
        if town[1][1:3] != ["(osim praznikom)"] * 2:
            problems.append(f"Grad Hvar: očekivano '(osim praznikom)': {town[1]}")
        out.append(("Grad Hvar – korisnici kanti", ["Hvar"], {"M": m_town, "K": k_town, "P": ps_town, "S": ps_town},
                    "KPS"))
        m_sub = " ".join(DAYS[r[0]] for r in sub)
        for r in sub:
            g = re.fullmatch(r"(.+?) - (\w+)", r[1])
            if not g or g.group(1) not in GROUPS:
                problems.append(f"prigradska naselja: nepoznat redak {r}")
                continue
            podrucje, places = GROUPS[g.group(1)]
            out.append((podrucje, places, {"M": m_sub, "K": DAYS[g.group(2)]}, ""))
    except (KeyError, IndexError) as e:
        problems.append(f"PDF se ne može pročitati: {e} ({town} / {sub})")
        return []
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = fetch(PAGE).decode("utf-8", "replace")
    links = [u for u, t in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', page, re.S)
             if " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split()) == LINK_TEXT]
    if not links:
        sys.exit(f"Na {PAGE} nema poveznice '{LINK_TEXT}'.")
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "raspored.pdf"
        fetch(links[0], pdf)
        text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    areas = read_pdf(text, problems)
    if str(RULES_YEAR) not in links[0]:
        problems.append(f"novi raspored ({links[0]}) – provjeriti godinu pravila i napomene")
    print(f"PDF {links[0]}: {len(areas)} područja; pravila iz {RULES_YEAR}. primijenjena na {year}.")
    print("Pretpostavka: miješani otpad blagdanom po redovnom rasporedu (pravilo nije objavljeno).")
    hol = set(pravila.blagdani(year))
    zones = {}
    for z, (podrucje, places, rules, no_holiday) in enumerate(areas, 1):
        dates, dropped = {}, []
        for code, rule in rules.items():
            for k in rule.split():
                for d in pravila.tjedno(year, k):
                    if code in no_holiday and d in hol:
                        dropped.append(f"{d:%d.%m.} {code}")
                        continue
                    if code in dates.get(d, ""):
                        problems.append(f"zona {z}: {code} dvaput {d}")
                    dates[d] = dates.get(d, "") + code
        rows = sorted((d, c, False) for d, c in dates.items())
        for d, codes, _ in rows:
            for c in codes:
                if DANI[d.weekday()] not in rules[c].split():
                    problems.append(f"zona {z}: {c} {d} nije po pravilu")
        for m in range(1, 13):
            for c, rule in rules.items():
                n = sum(1 for d, cs, _ in rows if d.month == m and c in cs)
                k = len(rule.split())
                if not 4 * k - sum(h.month == m for h in hol) <= n <= 5 * k:
                    problems.append(f"zona {z}: {c} u {m}. mjesecu {n} puta")
        say = lambda r: ", ".join(DAN[DANI.index(k)] for k in r.split())
        desc = f"miješani otpad {say(rules['M'])}; papir {say(rules['K'])}"
        if "P" in rules:
            desc += f"; PET/MET i staklo {say(rules['P'])}"
        zones[str(z)] = {"jls": "Hvar", "podrucje": podrucje, "ulice": places,
                         "napomena": f"Pravila iz {RULES_YEAR}.: {desc}.",
                         "raw": {str(year): podaci.month_lines(rows)}}
        print(f"Zona {z} ({podrucje}): {desc}; " + ", ".join(f"{c} {n}" for c, n in sorted(
            Counter(c for _, cs, _ in rows for c in cs).items())) + (f"; blagdanom bez odvoza: {', '.join(dropped)}"
                                                                     if dropped else ""))
    if len(zones) != 3:
        problems.append(f"{len(zones)} područja umjesto 3")
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
