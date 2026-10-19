"""Zadar and surroundings: Čistoća d.o.o. Zadar, address calendar of cistoca-zadar.hr ("Kalendar odvoza").

    python3 -m izvori.zadar_cistoca [--year 2026]

The site's address search (autocomplete.php) matches a term anywhere in the street or settlement name,
ignores accents and returns at most 15 hits, so streets are found by searching every one- and
two-character term and extending each term that still has 15 hits by the characters that continue it in
street names found so far (up to four characters; trying every character on every level would take tens
of thousands of requests). calendar.php gives three months of collection days for an address
(settlement, street, house number) and shows only the bins that address has. Each street's schedule is
read for January–March and July–September (winter and summer timetable): in Zadar every street,
elsewhere a sample of streets per settlement and every street where the samples disagree. Within a
settlement, a schedule with the same mixed-waste days as a fuller one that only lacks a bin (no
recycling or bio bin, a street without house numbers) goes with the fuller one; a calendar without
mixed waste is not a household's and is left out (listed). A longer street whose first and last house
number disagree is read for every number (at most 40). Streets with the same half-year form a group;
the whole year is read for one address of each group and, as a check, for a second one; where they
differ, for every address of the group. Identical years become one zone. Holidays are marked on the
calendar but collection is not moved, so dates are kept as given.

Checks: every month of every calendar has each day once with the right weekday, plausible counts per
type, every street in exactly one zone or split by house number, the sampled streets of a settlement
agree over the whole year, and the site's printable year calendar (PDF) of three addresses has the same
days and types. Otherwise nothing is written. Streets the search never returns are missing; the script
prints how many streets it found.
HTTP responses are cached in $ODVOZ_CACHE (default: the temp dir) for a week, so a rerun is cheap.
"""
import argparse
import calendar
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
from izvori.varazdin_cistoca import Http, describe

SLUG = "zadar-cistoca"
SITE = "https://www.cistoca-zadar.hr"
SEARCH = SITE + "/raspored/autocomplete.php"
CALENDAR = SITE + "/raspored/calendar.php"
PDF = SITE + "/kalendar-odvoza/pdf"
FORM = "application/x-www-form-urlencoded"
CACHE = Path(os.environ.get("ODVOZ_CACHE", Path(tempfile.gettempdir()) / "odvoz-cache")) / SLUG
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789 .-đ"
LIMIT, MAX_TERM = 15, 4
SIGN_MONTHS, YEAR_MONTHS = (1, 7), (1, 4, 7, 10)  # calendar.php windows of three months
CODES = {"MKO": "M", "BIO": "B", "PAP": "K", "PL": "P", "REC": "P", "GKO": "G"}
COUNTS = {"M": (40, 366), "B": (15, 160), "K": (5, 60), "P": (5, 110), "G": (1, 12)}  # M: daily in the old town
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
WEEK = ["PON", "UTO", "SRI", "ČET", "PET", "SUB", "NED"]
# Settlements of the served cities/municipalities (DZS, Popis 2021. – prvi rezultati po naseljima).
NASELJA = {
    "Zadar": ["Babindub", "Brgulje", "Crno", "Ist", "Kožino", "Mali Iž", "Molat", "Olib", "Petrčane", "Premuda", "Rava",
              "Silba", "Veli Iž", "Zadar", "Zapuntel"],
    "Nin": ["Grbe", "Nin", "Ninski Stanovi", "Poljica-Brig", "Zaton", "Žerava"],
    "Posedarje": ["Grgurice", "Islam Latinski", "Podgradina", "Posedarje", "Slivnica", "Vinjerac", "Ždrilo"],
    "Novigrad": ["Novigrad", "Paljuv", "Pridraga"],
    "Sukošan": ["Debeljak", "Glavica", "Gorica", "Sukošan"],
    "Vrsi": ["Poljica", "Vrsi"],
    "Poličnik": ["Briševo", "Dračevac Ninski", "Gornji Poličnik", "Lovinac", "Murvica", "Murvica Gornja", "Poličnik",
                 "Rupalj", "Suhovare", "Visočane"],
    "Ražanac": ["Jovići", "Krneza", "Ljubač", "Radovin", "Ražanac", "Rtina"],
    "Starigrad": ["Seline", "Starigrad", "Tribanj"],
    "Privlaka": ["Privlaka"],
    "Galovac": ["Galovac"],
    "Škabrnja": ["Prkos", "Škabrnja"],
    "Preko": ["Lukoran", "Ošljak", "Poljana", "Preko", "Rivanj", "Sestrunj", "Sutomišćica", "Ugljan"],
    "Jasenice": ["Jasenice", "Maslenica", "Rovanjska", "Zaton Obrovački"],
    "Zemunik Donji": ["Smoković", "Zemunik Donji", "Zemunik Gornji"],
    "Kukljica": ["Kukljica"],
    "Pašman": ["Banj", "Barotul", "Dobropoljana", "Kraj", "Mrljane", "Neviđane", "Pašman", "Ždrelac"],
    "Sveti Filip i Jakov": ["Babac", "Donje Raštane", "Gornje Raštane", "Sikovo", "Sveti Filip i Jakov",
                            "Sveti Petar na Moru", "Turanj"],
    "Kali": ["Kali"],
}
# The provider's names for parts of settlements and holiday areas that are not DZS settlements.
ALIAS = {"LJUBAČKI STANOVI": "Ražanac", "PODVRŠJE": "Ražanac", "NINSKE VODICE": "Nin", "GRBLJANSKE VIKENDICE": "Nin"}
PROVIDER = {
    "davatelj": "Čistoća d.o.o. Zadar",
    "web": SITE,
    "izvor": SITE + "/kalendar-odvoza/",
    "zupanija": "Zadarska",
    "napomene": [
        "Odvoz se obavlja i na blagdane, prema kalendaru.",
        "U dijelu općina od lipnja vrijedi ljetni raspored s češćim odvozom.",
        "Glomazni otpad do 4 m³ odvozi se jednom godišnje besplatno, na zahtjev (obrazac na cistoca-zadar.hr).",
    ],
}


def fold(text):
    """Lower case without accents, as the search compares ('đ' stays a letter of its own)."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


JLS_OF = {fold(n).replace("-", " "): j for j, names in NASELJA.items() for n in names}


def search(api, term):
    hits = json.loads(api.fetch(SEARCH + "?" + urllib.parse.urlencode({"term": term})))
    return [h for h in hits if "naselje" in h]  # without hits the term itself comes back as the only item


def crawl(api):
    """{(street id, settlement id): hit} for every street the search returns."""
    found, level, size = {}, list(ALPHABET), 1
    while level and size <= MAX_TERM:
        full = []
        for term in level:
            hits = search(api, term)
            for h in hits:
                key = (h["id"], h["naselje"])
                if key in found:
                    both = found[key]["brojevi"].split(",") + h["brojevi"].split(",")
                    h = {**h, "brojevi": ",".join(dict.fromkeys(both))}
                found[key] = h
            if len(hits) >= LIMIT:
                full.append(term)
        if size == 1:
            level = [t + c for t in full for c in ALPHABET]
        else:
            after = defaultdict(set)
            for h in found.values():
                name = fold(h["ulica_naziv"])
                for i in range(len(name) - size):
                    after[name[i:i + size]].add(name[i + size])
            level = sorted({t + c for t in full for c in after.get(t, ())})
        print(f"  pretraga: {size} znaka, {len(found)} ulica, HTTP {api.live} novih + {api.cached} iz cachea",
              flush=True)
        size += 1
    return found


def jls_of(settlement):
    """City/municipality of a settlement as the provider names it ('VRSI-MULO', 'MURVICA DONJA', ...)."""
    key = fold(settlement).replace("-", " ")
    if settlement in ALIAS:
        return ALIAS[settlement]
    if key in JLS_OF:
        return JLS_OF[key]
    words = key.split()
    if len(words) == 2 and f"{words[1]} {words[0]}" in JLS_OF:
        return JLS_OF[f"{words[1]} {words[0]}"]
    hits = {JLS_OF[p.strip()] for p in fold(settlement).split("-") if p.strip() in JLS_OF}
    hits |= {JLS_OF[words[0]]} if not hits and words[0] in JLS_OF else set()
    return hits.pop() if len(hits) == 1 else None


def house_numbers(hit):
    """House numbers of a street in order ('0' when it has none)."""
    nums = [n.strip() for n in hit["brojevi"].split(",") if re.match(r"\d", n.strip()) and n.strip() != "0"]
    nums.sort(key=lambda n: (int(re.match(r"\d+", n).group()), n))
    return list(dict.fromkeys(nums)) or ["0"]


def read_calendar(api, hit, number, first, year):
    """{date: frozenset of the site's codes} for three months from `first`, or None for an unknown address."""
    body = urllib.parse.urlencode({"naseljeid": hit["naselje_naziv"], "ulicaid": hit["ulica_naziv"].lower(),
                                   "broj": number, "godina": year, "mjesec": first})
    text = api.fetch(CALENDAR, body, FORM).decode("utf-8", "replace")
    blocks = re.findall(r'<div class="month"[^>]*>(.*?)</ul>', text, re.S)
    if not blocks:
        return None
    if len(blocks) != 3:
        raise ValueError(f"{len(blocks)} mjeseca umjesto 3")
    out = {}
    for i, block in enumerate(blocks):
        m = re.search(r"<h3>\s*(\S+)\s*<small>\s*(\d{4})", block)
        month = first + i
        if not m or m.group(1).upper() != MONTHS[month - 1] or int(m.group(2)) != year:
            raise ValueError(f"mjesec {m and m.groups()} umjesto {month}/{year}")
        days = re.findall(r"<li[^>]*>\s*<strong>(\d+)</strong>\s*<em>([^<]+)</em>(.*?)</li>", block, re.S)
        if [int(d) for d, _, _ in days] != list(range(1, calendar.monthrange(year, month)[1] + 1)):
            raise ValueError(f"dani u {month}/{year} nisu potpuni")
        for d, wd, rest in days:
            day = date(year, month, int(d))
            if wd.strip() != WEEK[day.weekday()]:
                raise ValueError(f"{day}: {wd.strip()} umjesto {WEEK[day.weekday()]}")
            codes = frozenset(re.findall(r"<span[^>]*>\s*([A-Z]+)\s*</span>", rest))
            if codes - set(CODES):
                raise ValueError(f"{day}: nepoznate oznake {sorted(codes - set(CODES))}")
            if codes:
                out[day] = codes
    return out


def schedule(api, hit, number, year, months):
    """Calendar of several three-month windows, as a sorted tuple of (date, codes); None if unknown."""
    out = {}
    for first in months:
        part = read_calendar(api, hit, number, first, year)
        if part is None:
            return None
        out.update(part)
    return tuple(sorted((d, " ".join(sorted(c))) for d, c in out.items()))


def fuller(scheds):
    """{schedule: schedule it goes with}. A schedule with the same mixed-waste days as a fuller one that only
    lacks some bins (an address without a recycling or bio bin, a street without house numbers) goes with
    the fullest such schedule; the timetable of the area is the same."""
    def mixed(s):
        return {d for d, c in s if "MKO" in c.split()}

    def within(a, b):
        b = dict(b)
        return all(d in b and set(c.split()) <= set(b[d].split()) for d, c in a)

    order = sorted(set(scheds), key=lambda s: (-sum(len(c.split()) for _, c in s), s))
    return {s: next(t for t in order if mixed(t) == mixed(s) and within(s, t)) for s in set(scheds)}


def runs(nums, mine):
    """'1-15, 21' for the house numbers `mine` among a street's ordered numbers `nums`."""
    parts, cur = [], []
    for n in nums + [None]:
        if n is not None and n in mine:
            cur.append(n)
        elif cur:
            parts.append(cur[0] if len(cur) == 1 else f"{cur[0]}-{cur[-1]}")
            cur = []
    return ", ".join(parts)


def nice(settlement):
    return "-".join(" ".join(w.lower() if w.upper() in ("I", "NA") else w.capitalize() for w in part.split())
                    for part in settlement.split("-"))


def read_pdf(path, year):
    """{date: set of codes} from the printable year calendar (three month columns per page)."""
    out, problems = {}, []
    for page in pdfplumber.open(path).pages:
        words = page.extract_words()
        titles = sorted((w["x0"], MONTHS.index(w["text"].upper()) + 1) for w in words if w["text"].upper() in MONTHS)
        for col, (x, month) in enumerate(titles):
            end = titles[col + 1][0] - 80 if col + 1 < len(titles) else page.width
            cells = [w for w in words if x - 80 <= w["x0"] < end]
            for first in (w for w in cells if w["text"].isdigit()):
                line = sorted((w for w in cells if abs(w["top"] - first["top"]) <= 3), key=lambda w: w["x0"])
                if line[0] is not first or len(line) < 2 or line[1]["text"] not in WEEK:
                    continue
                day = date(year, month, int(first["text"]))
                if line[1]["text"] != WEEK[day.weekday()]:
                    problems.append(f"PDF {day}: {line[1]['text']}")
                codes = {w["text"] for w in line[2:] if w["text"] in CODES}
                if day in out:
                    problems.append(f"PDF {day}: dvaput")
                if codes:
                    out[day] = codes
    return out, problems


def check_pdf(api, tmp, hit, number, year, sched):
    """The site's printable year calendar of an address has the same days and codes as calendar.php."""
    body = urllib.parse.urlencode({"naseljeid": hit["naselje_naziv"], "ulicaid": hit["ulica_naziv"], "broj": number,
                                   "godina": year, "mjesec": 1, "period": 12})
    path = Path(tmp) / f"{hit['id']}-{number}.pdf"
    path.write_bytes(api.fetch(PDF, body, FORM))
    theirs, problems = read_pdf(path, year)
    ours = {d: set(c.split()) for d, c in sched}
    diff = sorted(d for d in set(ours) | set(theirs) if ours.get(d) != theirs.get(d))
    label = f"{hit['ulica_naziv']} {number}, {nice(hit['naselje_naziv'])}"
    print(f"PDF {label}: {len(theirs)} dana s odvozom u PDF-u, {len(ours)} u kalendaru, razlika {len(diff)}")
    return problems + [f"PDF {label} {d}: PDF {sorted(theirs.get(d, []))}, kalendar {sorted(ours.get(d, []))}"
                       for d in diff[:10]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    api = Http(CACHE)
    hits = crawl(api)
    by_place = defaultdict(list)
    for h in sorted(hits.values(), key=lambda h: (h["naselje_naziv"], fold(h["ulica_naziv"]), h["id"])):
        if h["ulica_naziv"].strip(" _"):
            by_place[h["naselje_naziv"]].append(h)
    print(f"Ulica: {sum(map(len, by_place.values()))} u {len(by_place)} naselja")
    problems = [f"{p}: naselje bez JLS" for p in sorted(by_place) if jls_of(p) is None]

    half = {}  # (street key, number) -> half-year schedule (None: unknown address)
    def read(h, n):
        k = ((h["id"], h["naselje"]), n)
        if k not in half:
            try:
                half[k] = schedule(api, h, n, year, SIGN_MONTHS)
            except ValueError as e:
                problems.append(f"{h['ulica_naziv']} {n} ({h['naselje_naziv']}): {e}")
                half[k] = None
        return half[k]

    def read_street(h):
        """{number: schedule} of a street: the first number (and the last on longer streets), every number
        where they differ."""
        nums = house_numbers(h)
        got = {n: read(h, n) for n in dict.fromkeys([nums[0], nums[-1] if len(nums) >= 10 else nums[0]])}
        if len({s for s in got.values() if s}) > 1:
            step = max(1, math.ceil(len(nums) / 40))
            got.update({n: read(h, n) for n in nums[::step] + [nums[-1]]})
        return {n: s for n, s in got.items() if s}

    street_sched = {}  # street key -> {number: half-year schedule as read}
    canon = {}  # (settlement, schedule as read) -> the settlement's schedule it goes with (see fuller())
    uniform = {}  # settlement -> its schedule, when the sampled streets agree
    for i, (place, streets) in enumerate(sorted(by_place.items())):
        if place != "ZADAR" and len(streets) > 4:
            numbered = [h for h in streets if house_numbers(h) != ["0"]] or streets
            k = min(len(numbered), max(3, math.ceil(len(streets) / 8)))
            sample = [numbered[round(j * (len(numbered) - 1) / max(1, k - 1))] for j in range(k)]
            got = {(h["id"], h["naselje"]): read_street(h) for h in sample}
            to = fuller([s for g in got.values() for s in g.values()])
            if len(set(to.values())) == 1:
                uniform[place] = next(iter(to.values()))
                street_sched.update(got)
                canon.update({(place, s): t for s, t in to.items()})
                continue
        for h in streets:
            street_sched[(h["id"], h["naselje"])] = read_street(h)
        to = fuller([s for h in streets for s in street_sched[(h["id"], h["naselje"])].values()])
        canon.update({(place, s): t for s, t in to.items()})
        print(f"  {i + 1}/{len(by_place)} naselja, HTTP {api.live} novih + {api.cached} iz cachea", flush=True)
    print(f"Naselja s jednim rasporedom (prema uzorku ulica): {len(uniform)}; ostala čitana ulica po ulica")

    groups = defaultdict(list)  # half-year schedule -> [(street hit, numbers or None)]
    unknown, no_mixed = [], []
    for place, streets in sorted(by_place.items()):
        for h in streets:
            got = {n: canon[(place, s)] for n, s in (street_sched.get((h["id"], h["naselje"])) or {}).items()}
            if (h["id"], h["naselje"]) not in street_sched and place in uniform:
                got = {None: uniform[place]}
            if not got:
                unknown.append(f"{h['ulica_naziv']} ({place})")
                continue
            # a calendar without mixed waste is not a household's (shared containers, business premises)
            mixed = {n: s for n, s in got.items() if any("MKO" in c.split() for _, c in s)}
            if not mixed:
                no_mixed.append(f"{h['ulica_naziv']} ({place})")
                continue
            if len(set(mixed.values())) == 1 and len(mixed) == len(got):
                groups[next(iter(mixed.values()))].append((h, None))
                continue
            nums = house_numbers(h)
            order = sorted(got, key=nums.index)
            for s in set(mixed.values()):  # unread numbers go with the nearest read number before them
                mine = {n for n in nums if got[max((r for r in order if nums.index(r) <= nums.index(n)),
                                                   key=nums.index, default=order[0])] == s}
                groups[s].append((h, runs(nums, mine)))
    if no_mixed:
        print(f"Ulice čiji kalendar nema miješanog otpada (preskočene): {len(no_mixed)}: {', '.join(no_mixed)}")
    if unknown:
        print(f"Ulice koje kalendar ne poznaje (preskočene): {len(unknown)}: {', '.join(unknown[:40])}")

    years = defaultdict(list)  # whole-year schedule -> members
    reps = {}  # whole-year schedule -> (street key, number) it was read for
    hit_of = {(h["id"], h["naselje"]): h for streets in by_place.values() for h in streets}
    regrouped = 0
    for half_sched, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        addr = {}  # member index -> the address read for this half-year
        for i, (h, _) in enumerate(members):
            got = street_sched.get((h["id"], h["naselje"])) or {}
            n = next((n for n, s in got.items() if s == half_sched), None)
            if n is not None:
                addr[i] = ((h["id"], h["naselje"]), n)
        reads = list(dict.fromkeys(addr.values()))
        if not reads:
            first = members[0][0]
            problems.append(f"grupa bez pročitane adrese: {first['ulica_naziv']} ({first['naselje_naziv']})")
            continue
        full = {a: schedule(api, hit_of[a[0]], a[1], year, YEAR_MONTHS) for a in reads[:1] + reads[-1:]}
        if len(set(full.values())) > 1:  # same half-year, different year: read the year of every address
            regrouped += 1
            full.update({a: schedule(api, hit_of[a[0]], a[1], year, YEAR_MONTHS) for a in reads if a not in full})
        if None in full.values():
            problems.append(f"nema godišnjeg kalendara za {[a for a, y in full.items() if y is None]}")
            continue
        place_years = defaultdict(set)  # settlement -> years of its addresses in this group
        for i, a in addr.items():
            place_years[members[i][0]["naselje_naziv"]].add(full.get(a, full[reads[0]]))
        for i, (h, spec) in enumerate(members):
            if i in addr:
                y = full.get(addr[i], full[reads[0]])
            else:  # an unread street of a settlement with one timetable: the year of its sampled streets
                options = place_years[h["naselje_naziv"]] or {full[reads[0]]}
                if len(options) > 1:
                    problems.append(f"{nice(h['naselje_naziv'])}: ulice iz uzorka imaju različitu godinu")
                y = next(iter(options))
            years[y].append((h, spec))
            reps.setdefault(y, addr.get(i, reads[0]))
    print(f"Grupa po pola godine: {len(groups)}, od toga razdvojenih po cijeloj godini: {regrouped}")

    data = {**PROVIDER, "jls": sorted({jls_of(p) for p in by_place if jls_of(p)}, key=lambda j: (j != "Zadar", j)),
            "zone": {}}
    zones = sorted(years.items(), key=lambda kv: (Counter(jls_of(h["naselje_naziv"]) for h, _ in kv[1])
                                                 .most_common(1)[0][0] != "Zadar", -len(kv[1])))
    raw_codes = Counter()
    any_pl = any("PL" in c.split() for sched, _ in zones for _, c in sched)
    for n, (sched, members) in enumerate(zones, start=1):
        per_jls = Counter(jls_of(h["naselje_naziv"]) for h, _ in members)
        jls = per_jls.most_common(1)[0][0]
        rows = [(d, "".join(CODES[c] for c in codes.split()), False) for d, codes in sched]
        used = Counter(c for _, codes in sched for c in codes.split())
        raw_codes.update(used)
        counts = Counter(c for _, codes, _ in rows for c in set(codes))
        for code, k in counts.items():
            lo, hi = COUNTS[code]
            if not lo <= k <= hi:
                problems.append(f"zona {n}: {k} odvoza {podaci.TYPES[code][0]}")
        if "M" not in counts:
            problems.append(f"zona {n}: nema miješanog otpada")
        recycling = "reciklabilni" if not used["PL"] else "plastika/reciklabilni" if used["REC"] else "plastika"
        places = [nice(p) for p, _ in Counter(h["naselje_naziv"] for h, _ in members).most_common()]
        ulice = {p for p in places if p.upper() in uniform}
        ulice |= {f"{h['ulica_naziv'].strip()} ({nice(h['naselje_naziv'])})" + (f" {spec}" if spec else "")
                  for h, spec in members if fold(h["ulica_naziv"]) != fold(h["naselje_naziv"])}
        ulice |= {nice(h["naselje_naziv"]) for h, _ in members if fold(h["ulica_naziv"]) == fold(h["naselje_naziv"])}
        zone = {"jls": jls, "podrucje": ", ".join(places[:3]) + (" i dr." if len(places) > 3 else "")
                + f" – {describe(rows, {'P': recycling})}", "ulice": sorted(ulice)}
        notes = []
        if len(per_jls) > 1:
            notes.append("Zona obuhvaća i: " + ", ".join(sorted(j for j in per_jls if j != jls)) + ".")
        if used["REC"] and not used["PL"] and any_pl:
            notes.append("Žuti stupac (P) je ovdje reciklabilni otpad: spremnik s narančastim poklopcem.")
        if notes:
            zone["napomena"] = " ".join(notes)
        zone["raw"] = {str(year): podaci.month_lines(rows)}
        data["zone"][str(n)] = zone
        print(f"Zona {n}: {jls}, {len(members)} ulica/dijelova, {dict(sorted(counts.items()))}"
              + (" REC" if used["REC"] else "") + (" PL" if used["PL"] else ""))
    if raw_codes["REC"]:
        data["nazivi"] = {"P": "Plastika / reciklabilni otpad" if raw_codes["PL"]
                          else "Reciklabilni otpad (spremnik s narančastim poklopcem)"}

    placed = Counter((h["id"], h["naselje"]) for z in years.values() for h, spec in z if spec is None)
    problems += [f"ulica {k} u više zona" for k, c in placed.items() if c > 1]
    total = sum(map(len, by_place.values()))
    in_zones = len({(h["id"], h["naselje"]) for z in years.values() for h, _ in z})
    print(f"Ulica u zonama: {in_zones} od {total} (preskočeno {len(unknown) + len(no_mixed)})")
    if in_zones + len(unknown) + len(no_mixed) != total:
        problems.append(f"{total - in_zones - len(unknown) - len(no_mixed)} ulica bez zone")

    with tempfile.TemporaryDirectory() as tmp:
        # the largest zone in Zadar, in Sveti Filip i Jakov and in another municipality
        main_jls = {sched: Counter(jls_of(h["naselje_naziv"]) for h, _ in m).most_common(1)[0][0] for sched, m in zones}
        picks = [next((sched for sched, _ in zones if test(main_jls[sched])), None)
                 for test in (lambda j: j == "Zadar", lambda j: j == "Sveti Filip i Jakov",
                              lambda j: j not in ("Zadar", "Sveti Filip i Jakov"))]
        for sched in filter(None, picks):
            key, number = reps[sched]
            problems += check_pdf(api, tmp, hit_of[key], number, year, sched)
    print(f"Zona: {len(data['zone'])}; HTTP: {api.live} novih zahtjeva, {api.cached} iz cachea "
          f"(rad bez cachea: {len(api.distinct)} zahtjeva)")
    for p in problems[:30]:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json")


if __name__ == "__main__":
    main()
