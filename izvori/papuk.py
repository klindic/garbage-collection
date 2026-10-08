"""Orahovica, Čačinci, Crnac, Zdenci: Papuk d.o.o. Orahovica, the year's route plan (one route per day).

    python3 -m izvori.papuk [--year 2026]

The plan ("PLAN-ODVOZA-OTPADA-ZA-<year>.-GODINU.pdf") is published in the media library of the Town of
Orahovica (found with the WordPress REST API, newest upload first). Page 1 describes the seven routes
(Orahovica 1-3, Čačinci 1-2, Općina Zdenci, Općina Crnac) with their streets and settlements; the other
pages list every day of the year, two months side by side, with the routes of that day joined by "+"
and the waste type in brackets: none = mixed waste, (PAPIR), (PLASTIKA), (PiP) = paper and plastic.
The text is read with pdftotext -layout; every day's weekday abbreviation must match the calendar.
Holidays are written into the plan (the holiday's name on its day, the round on another day); a route
date off the route's usual weekday is marked as moved. The plan has no mixed waste for Čačinci, Zdenci
and Crnac (only paper and plastic), which the zone notes say.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import podaci
from izvori.slavonski_brod_komunalac import fetch
from pravila import blagdani

SLUG = "papuk"
MEDIA = "https://www.orahovica.hr/wp-json/wp/v2/media?search=plan-odvoza-otpada&per_page=50&_fields=date,source_url"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
DAY_ABBR = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
TYPES = {"": "M", "PAPIR": "K", "PLASTIKA": "P", "PIP": "PK"}
ROUTES = {  # route in the plan: (JLS, name of the route description on page 1)
    "ORAHOVICA 1": "Orahovica", "ORAHOVICA 2": "Orahovica", "ORAHOVICA 3": "Orahovica",
    "ČAČINCI 1": "Čačinci", "ČAČINCI 2": "Čačinci", "ZDENCI": "Zdenci", "CRNAC": "Crnac",
}
TYPOS = {"Slatnski Drenovac": "Slatinski Drenovac"}
HOLIDAY_WORDS = ("GODINA", "KRALJA", "USKRS", "PRAZNIK", "TIJELOVO", "ANTIF", "DRŽAVNOST", "POBJED", "GOSP",
                 "SVI SVETI", "SJEĆANJA", "BOŽIĆ", "STJEPAN")
PROVIDER = {
    "davatelj": "Papuk d.o.o. Orahovica",
    "web": "https://www.papuk-doo.hr",
    "zupanija": "Virovitičko-podravska",
    "jls": ["Orahovica", "Čačinci", "Crnac", "Zdenci"],
}
NAPOMENE = [
    "Plan odvoza objavljen je na stranicama Grada Orahovice; u slučaju nepredvidivih situacija podložan je "
    "promjenama.",
    "Blagdani su upisani u plan: odvoz s blagdana prebačen je na drugi dan (označeno kao pomaknuto).",
    "Oznaka „PiP” u planu ovdje je upisana kao papir i plastika (plan je ne objašnjava); na te dane plan za "
    "rutu ne navodi miješani otpad – provjerite kod davatelja.",
    "Za općine Čačinci, Zdenci i Crnac plan sadrži samo papir i plastiku; raspored miješanog otpada nije "
    "objavljen (pitajte Papuk d.o.o., tel. 033/673-103).",
]
NO_MIXED = "Plan za ovo područje ne navodi miješani komunalni otpad, samo papir i plastiku."


def plan_url(year):
    media = json.loads(fetch(MEDIA))
    urls = [m["source_url"] for m in sorted(media, key=lambda m: m["date"], reverse=True)
            if re.search(rf"PLAN-ODVOZA-OTPADA-ZA-{year}", m["source_url"], re.I)]
    if not urls:
        sys.exit(f"Plan odvoza za {year} nije pronađen ({MEDIA}). Ništa nije upisano.")
    return urls[0]


def descriptions(text):
    """{route: description} from page 1 ('1. ORAHOVICA 1: … 2. ORAHOVICA 2: …')."""
    first = " ".join(text.split("\f")[0].split())
    parts = re.split(r"\b\d\. ((?:OPĆINA )?[A-ZČĆŽŠĐ]+(?: \d)?):?", first)
    return {name.replace("OPĆINA ", ""): body.strip() for name, body in zip(parts[1::2], parts[2::2])}


def places(desc, jls):
    """Streets and settlements of a route description; an empty one (Zdenci, Crnac) -> [JLS]."""
    if not desc:
        return [jls]
    text = re.sub(r"Grad Orahovica:?|Orahovica ulice:|ulice:|te naselja|Čačinci \(", ",", desc)
    out = []
    for seg in re.split(r"[,()]", text):
        m = re.fullmatch(r"\s*(.+?) I i II\s*", seg)  # "Jezero I i II"
        for p in [f"{m.group(1)} I", f"{m.group(1)} II"] if m else re.split(r"\si\s", seg):
            p = TYPOS.get(p.strip(" .:"), p.strip(" .:"))
            if p and p not in out:
                out.append(p)
    return out


def read_plan(text, year, problems):
    """[(date, route, codes)] from the day lines of pages 2+."""
    out, months, split = [], None, None
    day_rx = re.compile(r"(\d{1,2})\.?\s+(" + "|".join(DAY_ABBR) + r")\b")
    for line in text.splitlines():
        heads = [(m.start(), MONTHS.index(m.group(0)) + 1) for m in re.finditer("|".join(MONTHS), line)]
        if len(heads) == 2:
            months, split = [heads[0][1], heads[1][1]], heads[1][0] - 3
            continue
        found = list(day_rx.finditer(line))
        if not months or not found:
            continue
        for i, m in enumerate(found):
            end = found[i + 1].start() if i + 1 < len(found) else len(line)
            month = months[0] if m.start() < split else months[1]
            d = date(year, month, int(m.group(1)))
            if DAY_ABBR[d.weekday()] != m.group(2):
                problems.append(f"{d}: u planu piše '{m.group(2)}'")
            entry = line[m.end():end].strip()
            for item in re.split(r"\+|\s+I\s+", entry):
                item = " ".join(item.split()).upper()
                if not item:
                    continue
                r = re.match(r"(.+?)\s*(?:\((\w+)\)?)?$", item)
                route, kind = r.group(1).strip(), (r.group(2) or "").upper()
                if route in ROUTES and kind in TYPES:
                    out.append((d, route, TYPES[kind]))
                elif any(w in item for w in HOLIDAY_WORDS) and d in blagdani(year):
                    continue
                else:
                    problems.append(f"{d}: nepoznat unos '{item}'")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    url = plan_url(year)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plan.pdf"
        fetch(url, path)
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True,
                              check=True).stdout
    problems = []
    entries = read_plan(text, year, problems)
    desc = descriptions(text)
    days = Counter((d.month, d.day) for d, _, _ in entries)
    per_route = defaultdict(dict)
    for d, route, codes in entries:
        if d in per_route[route]:
            problems.append(f"{route} {d}: dvaput u planu")
        per_route[route][d] = per_route[route].get(d, "") + codes
    if len({(d.month, d.day) for d, _, _ in entries}) < 200:
        problems.append(f"samo {len(days)} dana s odvozom")
    zones = {}
    for n, route in enumerate(ROUTES, 1):
        rows = per_route.get(route, {})
        usual = Counter(d.weekday() for d in rows).most_common(1)[0][0] if rows else None
        out = []
        for d, codes in sorted(rows.items()):
            moved = d.weekday() != usual
            if moved and not any(0 <= (d - h).days <= 7 or 0 <= (h - d).days <= 7 for h in blagdani(year) + blagdani(year - 1)):
                problems.append(f"{route} {d}: izvan uobičajenog dana ({podaci.DAYS[usual]}) bez blagdana u blizini")
            out.append((d, codes, moved))
        cnt = Counter(c for _, codes, _ in out for c in codes)
        monthly = Counter(d.month for d, codes, _ in out if "M" in codes)
        if ROUTES[route] == "Orahovica" and any(not 2 <= monthly[m] <= 5 for m in range(1, 13)):
            problems.append(f"{route}: miješani po mjesecima {dict(monthly)}")
        for c in "PK":  # every four weeks (Orahovica: none in August)
            if not 10 <= cnt[c] <= 14:
                problems.append(f"{route}: {cnt[c]} odvoza {c}")
        jls = ROUTES[route]
        ulice = places(desc.get(route, ""), jls)
        zone = {"jls": jls,
                "podrucje": f"{route.title()} ({'' if 'M' in cnt else 'papir i plastika: '}{podaci.DAYS[usual]})" + (": " + ", ".join(ulice[:4]) + (", …" if len(ulice) > 4 else "") if desc.get(route) else ""),
                "opis": desc.get(route) or f"Općina {jls}",
                "ulice": ulice}
        if "M" not in cnt:
            zone["napomena"] = NO_MIXED
        zone["raw"] = {str(year): podaci.month_lines(out)}
        zones[str(n)] = zone
        print(f"Zona {n} ({route}, {podaci.DAYS[usual]}): " + ", ".join(f"{c} {cnt[c]}" for c in "MPK")
              + f", pomaknuto {sum(m for _, _, m in out)}, ulica/naselja {len(ulice)}")
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "izvor": url, "napomene": NAPOMENE, "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(zones)} zona)")


if __name__ == "__main__":
    main()
