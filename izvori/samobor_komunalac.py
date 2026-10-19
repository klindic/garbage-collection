"""Samobor: Komunalac d.o.o. Samobor (komunalac-samobor.hr), town zones Samobor 1-8 and ~70 settlements.

    python3 -m izvori.samobor_komunalac [--year 2026]

The schedule page gives mixed waste as weekday rules ("SVAKI PONEDJELJAK - Samobor 1, ..."), a list
of replacement dates for holidays ("3.1. (umjesto 1.1.)"), and explicit "d.m." lists of biowaste and
paper + plastic/metal dates per group of zones and settlements. Every place gets its weekday, bio list
and paper/plastic lists; Samobor 1-8 are zones 1-8 (streets from the zone page), settlements with the
same schedule share one zone. Each list must be a regular 14- or 28-day series once the replacements
are undone, and the date lists must match the PDF notice linked from the same page.
"""
import argparse
import html
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
from izvori.sisak_gos import fetch
from pravila import blagdani, tjedno

SLUG = "samobor-komunalac"
SITE = "https://www.komunalac-samobor.hr"
PAGE = SITE + "/komunalac/raspored-odvoza-komunalnog-otpada-c599"
ZONES = SITE + "/komunalac/grad-zone-odvoza-c639"
WEEKDAYS = {"PONEDJELJAK": "pon", "UTORAK": "uto", "SRIJEDA": "sri", "ČETVRTAK": "čet", "PETAK": "pet"}
DATES = re.compile(r"^(\d{1,2}\.\d{1,2}\.,?\s*)+$")
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Samobor",
    "web": SITE,
    "zupanija": "Zagrebačka",
    "jls": ["Samobor"],
    "nazivi": {"K": "Papir (PAP)", "P": "Plastika i metal (PET/MET)"},
    "bioNapomena": "Biootpad se odvozi samo u zonama i naseljima navedenima u rasporedu odvoza biootpada.",
    "napomene": [
        "Odvoz se obavlja od 6 do 21 sat; spremnike iznijeti do 6 sati na dan odvoza.",
        "Na blagdane se odvozi u zamjenskim terminima (uglavnom subotom), označeni su kao pomaknuti.",
        "Glomazni otpad: šest termina godišnje po naselju, samo uz zahtjev (glomazni@komunalac-samobor.hr) "
        "najkasnije tjedan dana prije; termini su u PDF obavijesti na stranici rasporeda.",
    ],
}

_last = [0.0]


def get(url, dest=None, tries=4):
    """fetch() with at most 2 requests per second and retries with backoff."""
    for i in range(tries):
        time.sleep(max(0.0, _last[0] + 0.5 - time.monotonic()))
        _last[0] = time.monotonic()
        try:
            return fetch(url, dest)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if i == tries - 1 or getattr(e, "code", 500) < 500:
                raise
            time.sleep(2 * 2 ** i)


def lines(page):
    """Visible text of a page, one block (div, p, li, heading) per line."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    body = re.sub(r"<(br|/p|/div|/li|/h\d|/tr)[^>]*>", "\n", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return [" ".join(l.split()) for l in text.splitlines() if l.strip()]


def key(name):
    """Comparison key: no diacritics or case ('Savršćak' = 'Savrščak'), without a leading 'Ulica'."""
    s = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().strip(" *.")
    return re.sub(r"^ulica ", "", s)


def places(label):
    """'Smerovišće, Mali i Veliki Lipovec, Gradišće' -> ['Smerovišće', 'Mali Lipovec', 'Veliki Lipovec', 'Gradišće']."""
    out = []
    for p in label.strip("* ").split(","):
        p = p.strip()
        m = re.fullmatch(r"(\w+) i (\w+) (\w+)", p)
        out += [f"{m.group(1)} {m.group(3)}", f"{m.group(2)} {m.group(3)}"] if m else [p] if p else []
    return out


def dmy(text, year):
    return [date(year, int(m), int(d)) for d, m in re.findall(r"(\d{1,2})\.(\d{1,2})\.", text)]


def parse(page, year):
    """Weekday rules, replacements, bio lists, paper/plastic lists from the schedule page."""
    ls = lines(page)
    start = next(i for i, l in enumerate(ls) if l.startswith("ZAMJENSKI TERMINI"))
    end = next(i for i, l in enumerate(ls) if l.startswith("Dokumenti za preuzimanje"))
    repl = {date(year, int(om), int(od)): date(year, int(nm), int(nd)) for nd, nm, od, om in
            re.findall(r"(\d{1,2})\.(\d{1,2})\.\s*\(umjesto (\d{1,2})\.(\d{1,2})\.\)", ls[start])}
    weekday, lists, section, label, kind = {}, [], None, None, None
    for line in ls[start + 1:end]:
        m = re.match(r"SVAK[IA] (\w+) - (.*)", line)
        if m:
            for p in places(m.group(2)):
                weekday[p] = WEEKDAYS[m.group(1)]
        elif line.startswith("RASPORED ODVOZA"):
            step = 28 if "ČETVRTI" in line else 14 if "DRUGI" in line else 7
            section = ("B" if "BIOOTPADA" in line else "KP" if "PAPIRA" in line else "M", step)
        elif line in ("PAP", "PET/MET"):
            kind = "K" if line == "PAP" else "P"
        elif DATES.match(line):
            lists.append({"codes": kind or section[0], "step": section[1], "label": label,
                          "places": places(label), "dates": dmy(line, year), "text": line})
        elif not line.startswith("*U naseljima"):
            label, kind = line, None
    return weekday, repl, lists


def series_problems(lst, repl, year):
    """A list is every 14 or 28 days on one weekday for the whole year, once replacements are undone."""
    back = {new: old for old, new in repl.items()}
    orig = [back.get(d, d) for d in lst["dates"]]
    steps = {(b - a).days for a, b in zip(orig, orig[1:])}
    problems = []
    if steps != {lst["step"]}:
        problems.append(f"steps {sorted(steps)} days, expected {lst['step']}")
    if orig and (orig[0] > date(year, 1, 1) + timedelta(days=lst["step"] + 6)
                 or orig[-1] < date(year, 12, 31) - timedelta(days=lst["step"] + 6)):
        problems.append(f"does not cover the year ({orig[0]} - {orig[-1]})")
    return [f"{lst['label']} {lst['codes']}: {p}" for p in problems]


def pdf_dates(page, year):
    """Date lists of the PDF notice linked from the page (page 1, in reading order)."""
    m = re.search(r'href="([^"]+\.pdf)"', page)
    if not m:
        return None
    url = m.group(1) if m.group(1).startswith("http") else SITE + m.group(1)
    with tempfile.TemporaryDirectory() as tmp:
        get(url, Path(tmp) / "obavijest.pdf")
        pdf = pdfplumber.open(Path(tmp) / "obavijest.pdf")
        text = pdf.pages[0].extract_text() or ""
        text = text.split("ZAMJENSKI")[0]
        return url, dmy(text, year) if f"{year}. GODINI" in text else []


def zone_streets(page):
    """{'1': [streets], ...} from the town zone page ('SAMOBOR 1:' then one street per line)."""
    out, cur = {}, None
    for line in lines(page):
        m = re.fullmatch(r"SAMOBOR (\d):", line)
        if m:
            cur = out.setdefault(m.group(1), [])
        elif cur is not None and line.startswith(("Grad je", "Dokumenti", "Naslovnica")):
            cur = None
        elif cur is not None:
            cur.append(line.rstrip("."))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page = get(PAGE).decode("utf-8", "replace")
    streets = zone_streets(get(ZONES).decode("utf-8", "replace"))
    if f"1.1.{year}" not in page:
        sys.exit(f"Stranica {PAGE} nije raspored za {year}. Ništa nije upisano.")
    weekday, repl, lists = parse(page, year)
    problems = []

    hol = set(blagdani(year))
    for old, new in repl.items():
        if old not in hol or abs((new - old).days) > 7:
            problems.append(f"replacement {new:%d.%m.} for {old:%d.%m.}, which is not a public holiday nearby")
    for h in sorted(hol):
        if h.weekday() < 5 and h not in repl:
            problems.append(f"no replacement date for the holiday {h:%d.%m.} (a working day)")
    for lst in lists:
        problems += series_problems(lst, repl, year)
    if sorted(streets) != [str(n) for n in range(1, 9)] or min(map(len, streets.values()), default=0) < 5:
        problems.append(f"zone page: zones {sorted(streets)}")

    # every place gets its weekday, one bio list and paper/plastic lists
    names = {key(p): p for p in weekday}
    sched = {p: {"day": d, "B": None, "K": None, "P": None} for p, d in weekday.items()}
    for i, lst in enumerate(lists):
        for p in lst["places"]:
            if key(p) not in names:
                problems.append(f"{p!r} ({lst['label']}) has no mixed waste weekday")
                continue
            s = sched[names[key(p)]]
            for code in lst["codes"]:
                if s[code] is not None:
                    problems.append(f"{p}: two {code} lists")
                s[code] = i
    for p, s in sched.items():
        if s["K"] is None or s["P"] is None:
            print(f"   UPOZORENJE {p}: nema ga u rasporedu papira i plastike")

    cross = pdf_dates(page, year)
    html_dates = [d for lst in lists for d in lst["dates"]]
    if cross is None:
        problems.append("no PDF notice on the page")
    elif cross[1] != html_dates:
        diff = next((i for i, (a, b) in enumerate(zip(cross[1], html_dates)) if a != b), min(len(cross[1]), len(html_dates)))
        problems.append(f"PDF and page date lists differ ({len(cross[1])} / {len(html_dates)} dates, first at #{diff})")
    else:
        print(f"PDF ({cross[0].rsplit('/', 1)[1]}) i stranica: istih {len(html_dates)} datuma u {len(lists)} popisa")

    # zones: Samobor 1-8 as published, settlements with an identical schedule grouped
    groups = {}
    for p, s in sched.items():
        town = re.fullmatch(r"Samobor (\d)", p)
        gk = ("town", town.group(1)) if town else ("x", s["day"], s["B"], s["K"], s["P"])
        groups.setdefault(gk, []).append(p)
    order = sorted(groups, key=lambda g: (g[0] != "town", g[1] if g[0] == "town" else list(sched).index(groups[g][0])))
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    zones = {}
    for n, gk in enumerate(order, start=1):
        members = groups[gk]
        s = sched[members[0]]
        moved = {new for new in repl.values()}
        rows = {}
        for old in tjedno(year, s["day"]):
            d = repl.get(old, old)
            rows[d] = ["M", d != old]
        for code in "BKP":
            if s[code] is not None:
                for d in lists[s[code]]["dates"]:
                    rows.setdefault(d, ["", d in moved])[0] += code
        rows = sorted((d, c, mv) for d, (c, mv) in rows.items())
        days = {podaci.DAYS[d.weekday()] for d, c, mv in rows if "M" in c and not mv}
        if days != {podaci.DAYS[list(WEEKDAYS.values()).index(s["day"])]}:
            problems.append(f"{members[0]}: mixed waste on {days}")
        if gk[0] == "town":
            zone = {"jls": "Samobor", "podrucje": f"Samobor {gk[1]} (grad)", "ulice": streets.get(gk[1], [])}
        else:
            zone = {"jls": "Samobor", "podrucje": ", ".join(members[:4]) + (", …" if len(members) > 4 else ""),
                    "ulice": members}
        if s["K"] is None:
            zone["napomena"] = "Naselje nije navedeno u objavljenom rasporedu odvoza papira i plastike."
        old = data["zone"].get(str(n), {})
        keep = old.get("raw", {}) if old.get("podrucje") == zone["podrucje"] else {}
        zone["raw"] = {**keep, str(year): podaci.month_lines(rows)}
        zones[str(n)] = zone
        cnt = {c: sum(c in codes for _, codes, _ in rows) for c in "MBKP"}
        print(f"Zona {n}: {zone['podrucje']}: " + ", ".join(f"{c} {v}" for c, v in cnt.items() if v)
              + f", pomaknuto {sum(mv for *_, mv in rows)}, mjesta/ulica {len(zone['ulice'])}")
        if not 52 <= cnt["M"] <= 53 or cnt["B"] not in (0, 26, 27) or cnt["K"] not in (0, 13, 14, 26, 27):
            problems.append(f"zone {n}: unexpected counts {cnt}")
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        sys.exit("Ništa nije upisano.")
    data["zone"] = zones
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona, {len(sched)} mjesta)")


if __name__ == "__main__":
    main()
