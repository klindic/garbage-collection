"""Michieli-Tomić d.o.o. (Gornji Humac): Bol, Milna, Postira, Pučišća, Sutivan on Brač, mixed waste, rules from 2019.

    python3 -m izvori.michieli_tomic [--year 2026]

The home page links "Dinamika prikupljanja otpada" (raspored2019.pdf, January 2019), the only schedule: per
municipality two seasons with the number of collections a week and, for most, the weekdays ("01.10.-31.05. TRI
PUTA TJEDNO ( PON, SRI, PET )", "01.06.-30.09. SVAKI DAN"). The text is read with pdftotext. Seasons with
weekdays become dates; a season without weekdays ("TRI PUTA TJEDNO" in Bol, both seasons of Nerežišća) cannot be
turned into dates, so that period (Bol winter) or municipality (Nerežišća) is left out. The PDF also lists
Stankovci and Seget, which other providers now serve; they are skipped. Selca is not in the PDF. The rules are
applied to the requested year with a note that they date from 2019. No holiday rule is published: the regular
dates are kept and a note says so.
"""
import argparse
import calendar
import html
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "michieli-tomic"
SITE = "https://michieli-tomic.hr"
LINK_TEXT = "Dinamika prikupljanja otpada"
RULES_YEAR = 2019
JLS = {"BOL": "Bol", "MILNA": "Milna", "NEREŽIŠĆA": "Nerežišća", "POSTIRA": "Postira", "PUČIŠĆA": "Pučišća",
       "SUTIVAN": "Sutivan"}
SKIP = {"STANKOVCI": "Zadarska županija, uslugu pruža drugi davatelj",
        "SEGET": "uslugu od 2024. pruža Zeleni Seget d.o.o."}
DAYS = {"PON": "pon", "UTO": "uto", "SRI": "sri", "ČET": "čet", "PET": "pet", "SUB": "sub", "NED": "ned"}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DANI = list(pravila.DANI)
OLD = (f"Raspored prema pravilima iz {RULES_YEAR}.; davatelj nije objavio novi raspored – provjerite prije "
       "odlaganja.")
PROVIDER = {
    "davatelj": "Michieli-Tomić d.o.o.",
    "web": SITE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Bol", "Milna", "Postira", "Pučišća", "Sutivan"],
}
NAPOMENE = [
    OLD,
    f"Pravila iz {RULES_YEAR}.; provjerite kod davatelja (Michieli-Tomić d.o.o., Gornji Humac; 021/647-242).",
    "Upisan je samo miješani komunalni otpad (od vrata do vrata); papir, plastika, staklo i metal odlažu se u "
    "spremnike na zelenim otocima.",
    "Prijelaz na zimski raspored davatelj najavljuje obaviješću na michieli-tomic.hr; pomaci zbog blagdana nisu "
    "objavljeni, pa su upisani redovni dani.",
    "Nerežišća: odvoz 1.7. – 31.8. tri puta, a 1.9. – 30.6. dva puta tjedno, bez navedenih dana – nije upisano.",
    "Selca: davatelj nije objavio raspored.",
]


def rule(text):
    """'TRI PUTA TJEDNO ( PON, SRI, PET )' -> 'pon sri pet'; 'SVAKI DAN' -> 'svaki dan'; no weekdays -> None."""
    t = " ".join(text.split())
    if t == "SVAKI DAN":
        return "svaki dan"
    if t == "SVAKI DAN OSIM NEDELJE":
        return "pon-sub"
    m = re.fullmatch(r"(?:JEDAN PUT|DVA PUTA|TRI PUTA) TJEDNO(?: \( ?([A-ZČ, ]+?) ?\))?", t)
    if not m:
        return False
    if not m.group(1):
        return None
    days = [d.strip() for d in m.group(1).split(",")]
    return " ".join(DAYS[d] for d in days) if all(d in DAYS for d in days) else False


def read_pdf(text, problems):
    """{MUNICIPALITY: [(ranges, rule or None, text)]}."""
    out, cur = {}, None
    for line in text.splitlines():
        line = " ".join(line.split())
        m = re.fullmatch(r"OPĆINA ([A-ZČĆŠŽĐ ]+)", line)
        if m:
            cur = m.group(1)
            out[cur] = []
            continue
        m = re.fullmatch(r"- ((?:\d\d\.\d\d\.-\d\d\.\d\d\.(?: i )?)+) (.+)", line)
        if m and cur:
            ranges = re.findall(r"(\d\d\.\d\d\.)-(\d\d\.\d\d\.)", m.group(1))
            out[cur].append((ranges, rule(m.group(2)), m.group(2)))
    for k in set(JLS) - set(out):
        problems.append(f"u PDF-u nema općine {k}")
    for k in set(out) - set(JLS) - set(SKIP):
        problems.append(f"nova općina u PDF-u: {k}")
    return out


def spans(year, ranges):
    out = []
    for a, b in ranges:
        lo, hi = (date(year, *map(int, reversed(x.strip(".").split(".")))) for x in (a, b))
        out += [(lo, hi)] if lo <= hi else [(date(year, 1, 1), hi), (lo, date(year, 12, 31))]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    home = fetch(SITE + "/").decode("utf-8", "replace")
    links = [u for u, t in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', home, re.S)
             if LINK_TEXT in html.unescape(re.sub(r"<[^>]+>", " ", t))]
    if not links:
        sys.exit(f"Na {SITE} nema poveznice '{LINK_TEXT}'.")
    url = links[0] if links[0].startswith("http") else SITE + links[0]
    problems = []
    if str(RULES_YEAR) not in url:
        problems.append(f"nova datoteka rasporeda {url} – provjeriti godinu pravila i napomene")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "raspored.pdf"
        fetch(url, pdf)
        text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    found = read_pdf(text, problems)
    print(f"PDF {url}: općine {sorted(found)}; pravila iz {RULES_YEAR}. primijenjena na {year}.")
    for k, why in SKIP.items():
        print(f"Preskočeno: {k} ({why})")
    print("Pretpostavka: blagdani bez pomaka (pravilo nije objavljeno).")
    zones = {}
    for key in ["BOL", "MILNA", "NEREŽIŠĆA", "POSTIRA", "PUČIŠĆA", "SUTIVAN"]:
        seasons = found.get(key, [])
        name = JLS[key]
        if any(r is False for _, r, _ in seasons):
            problems.append(f"{name}: nepoznato pravilo {[t for _, r, t in seasons if r is False]}")
            continue
        total = sum((hi - lo).days + 1 for rg, _, _ in seasons for lo, hi in spans(year, rg))
        if total != (366 if calendar.isleap(year) else 365):
            problems.append(f"{name}: razdoblja pokrivaju {total} dana")
        usable = [(rg, r) for rg, r, _ in seasons if r]
        if not usable:
            print(f"{name}: nijedno razdoblje nema dane u tjednu – nije upisano")
            continue
        rows = []
        for rg, r in usable:
            days = DANI if r == "svaki dan" else DANI[:6] if r == "pon-sub" else r.split()
            sp = spans(year, rg)
            rows += [(d, "M", False) for k in days for d in pravila.tjedno(year, k) if any(lo <= d <= hi for lo, hi in sp)]
        rows.sort()
        for d, n in Counter(d for d, _, _ in rows).items():
            if n > 1:
                problems.append(f"{name}: {d} dvaput")
        for rg, r in usable:
            k = 7 if r == "svaki dan" else 6 if r == "pon-sub" else len(r.split())
            for m in range(1, 13):
                first, last = date(year, m, 1), date(year, m, calendar.monthrange(year, m)[1])
                if any(lo <= first and last <= hi for lo, hi in spans(year, rg)):
                    n = sum(1 for d, _, _ in rows if d.month == m)
                    if not min(4 * k, last.day) <= n <= min(5 * k, last.day):
                        problems.append(f"{name}: {m}. mjesec {n} odvoza (pravilo {r})")
        say = lambda r: "svaki dan" if r == "svaki dan" else ", ".join(DAN[DANI.index(k)] for k in r.split())
        desc = "; ".join(" i ".join(f"{a}–{b}" for a, b in rg) + f": {say(r) if r else t.lower() + ' (dani nisu navedeni)'}"
                         for rg, r, t in seasons)
        zone = {"jls": name, "podrucje": f"Općina {name}", "ulice": [f"{name} (cijela općina)"],
                "napomena": f"Pravila iz {RULES_YEAR}.: {desc}."}
        missing = [rg for rg, r, _ in seasons if not r]
        if missing:
            zone["napomena"] += (" Za razdoblje " + ", ".join(f"{a}–{b}" for rg in missing for a, b in rg)
                                 + " dani u tjednu nisu objavljeni, pa za to razdoblje nema upisanih datuma.")
        zone["raw"] = {str(year): podaci.month_lines(rows)}
        zones[str(len(zones) + 1)] = zone
        print(f"Zona {len(zones)} ({name}): {desc}; {len(rows)} odvoza")
    for p in problems:
        print("   PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "izvor": url, "napomene": NAPOMENE, "zone": zones})
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
