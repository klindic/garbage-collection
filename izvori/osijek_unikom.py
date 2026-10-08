"""Osijek and surroundings: Unikom d.o.o., open schedule API behind unikom.hr/raspored-odvoza.

    python3 -m izvori.osijek_unikom [--year 2026]

The API lists every street with the service (settlement + street) and gives one month of collection
dates for an address (street, settlement, house number, family house); a house number that does not
exist gives no dates. Each bin type has its own rounds by house-number range, so long roads change
schedule along the way (Vukovarska cesta five times on each side). The script reads March for the first
odd and even house number of every street and for numbers further along (25, 75, 175, 375), and narrows
down every boundary where two of them disagree. Street parts with the same March form a group per
city/municipality; the whole year is read for one street of each group and, as a check, June and
October for another one (where they differ, the whole year of every street of the group). Identical
years become one zone. House-number runs without mixed waste (gaps between the rounds' ranges) are
left out and listed.
The API does not shift collections for holidays. Unikom announced that 1.1.2026 (Thursday rounds) moves
to Saturday 3.1. and 25.12.2026 (Friday rounds) to Saturday 26.12.; other holidays run as usual.

Checks: dates within the year, plausible counts per type, every street in exactly one zone or split by
house number, the check months of every group, and Unikom's colour calendars (PDF) for Bilje (2. dio),
Bizovac and Petrijevci: mixed waste, paper and plastic must match the API date for date (biowaste and
glass/metal differ there and become a note). Otherwise nothing is written.
HTTP responses are cached in $ODVOZ_CACHE (default: the temp dir) for a week, so a rerun is cheap.
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import urllib.parse
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import pdfplumber

import podaci
from izvori.varazdin_cistoca import Http, describe
from kalendar_boje import read_page

SLUG = "osijek-unikom"
API = "https://api.unikom.hr/api/unikom/planOdvozaOpen"
PAGE = "https://unikom.hr/raspored-odvoza"
CACHE = Path(os.environ.get("ODVOZ_CACHE", Path(tempfile.gettempdir()) / "odvoz-cache")) / SLUG
BINS = ["komunalni", "papir", "plastika", "metal", "biootpad", "staklo"]
TYPES = {"Komunalni": "M", "Biootpad": "B", "Plastika": "P", "Papir": "K", "Staklo": "S", "Metal": "L"}
# up to twice a week: the API puts some streets on two rounds of one bin type
COUNTS = {"M": (20, 110), "B": (15, 110), "K": (6, 60), "P": (6, 60), "S": (2, 14), "L": (2, 14)}
PROBE_MONTH = 3  # March: also has the quarterly glass and metal collection of the municipalities
CHECK_MONTHS = (6, 10)
LADDER = (25, 75, 175, 375)
FALLBACK = (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 30, 31, 40, 41, 50, 51, 100, 101)
MOVED = {2026: {date(2026, 1, 1): date(2026, 1, 3), date(2026, 12, 25): date(2026, 12, 26)}}
# Settlements by city/municipality (DZS, Popis 2021. – prvi rezultati po naseljima); Ovčara is part of Čepin.
NASELJA = {
    "Osijek": ["OSIJEK", "TENJA", "VIŠNJEVAC", "JOSIPOVAC", "SARVAŠ", "KLISA", "BRIJEST", "BRIJEŠĆE", "PODRAVLJE",
               "NEMETIN", "TVRĐAVICA"],
    "Antunovac": ["ANTUNOVAC", "IVANOVAC"],
    "Bilje": ["BILJE", "KOPAČEVO", "KOZJAK", "LUG", "PODUNAVLJE", "TIKVEŠ", "VARDARAC", "ZLATNA GREDA"],
    "Bizovac": ["BIZOVAC", "BROĐANCI", "CEROVAC", "CRET BIZOVAČKI", "HABJANOVCI", "NOVAKI BIZOVAČKI", "SAMATOVCI",
                "SELCI", "ZELČIN"],
    "Čepin": ["ČEPIN", "ČEPIN-OVČARA", "BEKETINCI", "ČEPINSKI MARTINCI", "ČOKADINCI", "LIVANA"],
    "Ernestinovo": ["ERNESTINOVO", "DIVOŠ", "LASLOVO"],
    "Petrijevci": ["PETRIJEVCI", "SATNICA"],
    "Vladislavci": ["VLADISLAVCI", "DOPSIN", "HRASTIN"],
    "Vuka": ["VUKA", "HRASTOVAC", "LIPOVAC HRASTINSKI"],
}
JLS_OF = {n: j for j, names in NASELJA.items() for n in names}
# Colour calendars on unikom.hr checked against the API: (document title word, page heading, settlement, street)
PDF_CHECKS = [("BILJE", "BILJE II", "BILJE", "ULICA ŠPORTOVA"), ("BIZOVAC", "BIZOVAC", "BIZOVAC", None),
              ("BIZOVAC", "PETRIJEVCI", "PETRIJEVCI", None)]
PALETTE = {(0.157, 0.467, 0.337): "M", (0.953, 0.737, 0.0): "P", (0.172, 0.341, 0.635): "K",
           (0.616, 0.62, 0.62): "SL", (0.435, 0.353, 0.294): "B"}
IGNORE = ((1.0, 1.0, 1.0), (0.933, 0.949, 0.969), (0.835, 0.898, 0.875), (0.086, 0.6, 0.478))
LOWER = {"ulica", "cesta", "trg", "put", "obala", "prilaz", "odvojak", "šetalište", "sokak", "vijenac", "naselje",
         "park", "kralja", "kraljice", "bana", "braće", "svetog", "svete", "sv.", "grada", "i", "u", "na", "od",
         "hrvatskih", "hrvatske", "branitelja", "žrtava", "domovinskog", "rata", "vojske", "satnije", "brigade",
         "gardijske", "siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja", "kolovoza", "rujna",
         "listopada", "studenoga", "studenog", "prosinca", "dr.", "kneza", "biskupa", "nova", "stara", "mala", "velika"}
DAYS = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Unikom d.o.o.",
    "web": "https://unikom.hr",
    "izvor": PAGE,
    "zupanija": "Osječko-baranjska",
    "nazivi": {"P": "Plastika"},
    "napomene": [
        "Raspored vrijedi za obiteljske kuće; stambene zgrade imaju vlastiti raspored na unikom.hr/raspored-odvoza.",
        "Odvoz 1.1.2026. (četvrtak) obavlja se u subotu 3.1., a odvoz 25.12.2026. (petak) u subotu 26.12.; "
        "ostali blagdani su radni dani za odvoz.",
        "Glomazni otpad: reciklažno dvorište Jug II, Osijek, ili jednom godišnje besplatno po pozivu (0800 200 025).",
    ],
}


def nice(name):
    """'ULICA KRALJA ZVONIMIRA' -> 'Ulica kralja Zvonimira', 'ČEPIN-OVČARA' -> 'Čepin-Ovčara'."""
    words = []
    for i, w in enumerate(name.split()):
        low = w.lower()
        if re.fullmatch(r"[IVXLC]+\.?", w):
            words.append(w)
        elif i and low in LOWER:
            words.append(low)
        else:
            words.append("-".join(p[:1].upper() + p[1:] for p in low.split("-")))
    return " ".join(words)


def month(api, street, number, m, year):
    """((date, codes), ...) of one month for a family house, or None if there is no such house number."""
    q = [("address", street["ulica"]), ("city", street["mjesto"]), ("building", 0), ("urbVila", 0),
         ("number", number), ("month", m), ("year", year)] + [("spremnik[]", b) for b in BINS]
    weeks = json.loads(api.fetch(API + "?" + urllib.parse.urlencode(q))).get("weeks") or []
    out = []
    for w in weeks:
        day = datetime.strptime(w["date"], "%d.%m.%Y").date()
        if (day.year, day.month) != (year, m):
            raise ValueError(f"datum {day} nije u {m}/{year}")
        kinds = [t.strip() for t in w["vrstaOtpada"].split(",") if t.strip()]
        unknown = [t for t in kinds if t not in TYPES]
        if unknown:
            raise ValueError(f"nepoznata vrsta otpada {unknown}")
        out.append((day, "".join(sorted({TYPES[t] for t in kinds}))))
    return tuple(sorted(out)) or None


class Street:
    """House numbers read for one street: number -> month signature (None: no such house)."""

    def __init__(self, api, street, year):
        self.api, self.street, self.year, self.read = api, street, year, {}

    def __call__(self, n):
        if n not in self.read:
            self.read[n] = month(self.api, self.street, n, PROBE_MONTH, self.year)
        return self.read[n]

    def valid(self, parity):
        return sorted(n for n, s in self.read.items() if s and n % 2 == parity)

    def probe(self):
        """First odd and even house number, numbers along the street, and every boundary narrowed down."""
        for parity in (1, 0):
            next((n for n in range(2 - parity, 14 - parity, 2) if self(n)), None)
        if not self.valid(1) and not self.valid(0):
            next((n for n in FALLBACK if self(n)), None)
        for parity in (1, 0):
            vals = self.valid(parity)
            if not vals:
                continue
            for k in LADDER:
                n = k if k % 2 == parity else k + 1
                if n > vals[0] and not self(n) and not self(n + 2):
                    break
            self.narrow(parity)

    def narrow(self, parity):
        tried = set()
        while True:
            vals = self.valid(parity)
            pairs = [(a, b) for a, b in zip(vals, vals[1:]) if self(a) != self(b) and b - a > 2 and (a, b) not in tried]
            if not pairs:
                return
            a, b = pairs[0]
            tried.add((a, b))
            between = list(range(a + 2, b, 2))
            mid = between[len(between) // 2]
            for n in sorted(between, key=lambda x: abs(x - mid))[:3]:
                if self(n):
                    break

    def segments(self):
        """{signature: [(parity, first, last or None)]}: runs of one schedule per side of the street."""
        out = defaultdict(list)
        for parity in (1, 0):
            vals = self.valid(parity)
            runs = []
            for n in vals:
                if runs and runs[-1][2] == self(n):
                    runs[-1][1] = n
                else:
                    runs.append([n, n, self(n)])
            for i, (lo, hi, sig) in enumerate(runs):
                out[sig].append((parity, lo if i else None, hi if i < len(runs) - 1 else None))
        return out


def spec(parts, sides):
    """'' for a whole street, else 'neparni do 43', 'parni', 'neparni od 49' ... for its parts in one zone."""
    if all(lo is None and hi is None for _, lo, hi in parts) and {p for p, _, _ in parts} == sides:
        return ""
    out = []
    for parity, lo, hi in parts:
        word = "neparni" if parity else "parni"
        if lo is None and hi is None:
            out.append(word)
        elif lo is None:
            out.append(f"{word} do {hi}")
        elif hi is None:
            out.append(f"{word} od {lo}")
        else:
            out.append(f"{word} {lo}" if lo == hi else f"{word} {lo}-{hi}")
    return ", ".join(out)


def year_of(api, street, number, year, have=None):
    """{date: codes} of the whole year for one address (the probe month is reused)."""
    out = {}
    for m in range(1, 13):
        days = have if m == PROBE_MONTH and have else month(api, street, number, m, year)
        if not days:
            raise ValueError(f"{street['ulica']} {number}: nema datuma u {m}/{year}")
        out.update(days)
    return out


def shifted(sched, year):
    """[(date, codes, moved)] with Unikom's announced holiday moves."""
    moves = MOVED.get(year, {})
    rows = [(moves.get(d, d), c, d in moves) for d, c in sched.items()]
    if len({d for d, _, _ in rows}) != len(rows):
        raise ValueError("pomaknuti odvoz pada na dan s drugim odvozom")
    return rows


def pdf_check(api, tmp, year, scheds):
    """Unikom's colour calendars (PDF) against the API for one street each.

    Mixed waste, paper and plastic must agree date for date. Biowaste and glass/metal are only reported:
    in Bizovac and Petrijevci the printed calendar has biowaste in the weeks without mixed waste, while the
    API has it on the mixed-waste days. Returns (problems, {settlement: note}).
    """
    page_html = api.fetch(PAGE).decode("utf-8", "replace")
    links = {}
    for url, text in re.findall(r'<a href="(https://api\.gaussbox\.com/v1/media/download/[^"]+)"[^>]*>(.*?)</a>',
                                page_html, re.S):
        links[" ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split()).upper()] = html.unescape(url)
    problems, notes = [], {}
    for word, heading, settlement, street in PDF_CHECKS:
        url = next((u for t, u in links.items() if word in t and str(year) in t and "RASPORED SAKUPLJANJA" in t), None)
        if url is None:
            problems.append(f"PDF za {word} {year} nije na {PAGE}")
            continue
        pdf_path = Path(tmp) / f"{hashlib.sha1(url.encode()).hexdigest()}.pdf"
        if not pdf_path.exists():
            pdf_path.write_bytes(api.fetch(url))
        pages = pdfplumber.open(pdf_path).pages
        title = re.compile(rf"\b{heading} • {year}")
        page = next((p for p in pages if title.search(" ".join((p.extract_text() or "").split()))), None)
        key = next((k for k in sorted(scheds) if k[0] == settlement and (street is None or k[1] == street)), None)
        if page is None or key is None:
            problems.append(f"PDF {heading}: {'nema stranice' if page is None else 'nema ulice u API-ju'}")
            continue
        found, errs = read_page(page, year, PALETTE, ignore=IGNORE)
        problems += [f"PDF {heading}: {e}" for e in errs]
        ours = {d: {"SL" if c in "SL" else c for c in codes} for d, codes, _ in scheds[key]}
        theirs = {d: set(re.findall(r"SL|[A-Z]", c)) for d, c in found.items() if c}
        days = sorted(set(ours) | set(theirs))
        core = set("MKP")
        # a day with two bins is a split cell, read as one colour: the PDF's bins only have to be among the API's
        strict = [d for d in days if theirs.get(d, set()) & core - ours.get(d, set())
                  or ours.get(d, set()) & core and d not in theirs]
        other = [d for d in days if (theirs.get(d, set()) - ours.get(d, set())) or d not in theirs]
        print(f"PDF {heading} ({nice(key[1])}): {len(theirs)} datuma u PDF-u, {len(ours)} u API-ju; razlika za "
              f"miješani/papir/plastiku {len(strict)}, za biootpad/staklo/metal {len(other)}")
        problems += [f"PDF {heading} {d:%d.%m.}: PDF {''.join(sorted(theirs.get(d, set()))) or '-'}, "
                     f"API {''.join(sorted(ours.get(d, set()))) or '-'}" for d in strict]
        kinds = sorted({"biootpad" if c == "B" else "staklo i metal"
                        for d in other for c in ours.get(d, set()) ^ theirs.get(d, set()) if c not in core})
        if kinds:
            notes[settlement] = (f"Tiskani kalendar za {nice(heading)} (PDF na unikom.hr) za {' i '.join(kinds)} "
                                 f"navodi druge datume; ovdje su datumi iz tražilice na unikom.hr.")
    return problems, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    api = Http(CACHE)
    streets = json.loads(api.fetch(API + "/address?" + urllib.parse.urlencode({"address": "%"})))
    streets = list({(s["mjesto"].strip(), s["ulica"].strip()): s for s in streets}.values())
    print(f"Ulica: {len(streets)} u {len({s['mjesto'].strip() for s in streets})} naselja")
    problems = [f"{m}: naselje bez JLS u NASELJA" for m in sorted({s["mjesto"].strip() for s in streets} - set(JLS_OF))]

    groups = defaultdict(list)  # (jls, month signature) -> [(street, parts, house number, sides)]
    unresolved, split, no_mixed = set(), [], []
    for i, s in enumerate(streets):
        name = f"{nice(s['ulica'])} ({nice(s['mjesto'].strip())})"
        st = Street(api, s, year)
        try:
            st.probe()
        except ValueError as e:
            problems.append(f"{name}: {e}")
            continue
        segs = st.segments()
        # house-number runs without mixed waste are gaps between the API's route ranges, not households
        for sig in [sig for sig in segs if not any("M" in c for _, c in sig)]:
            no_mixed.append(f"{name} {spec(segs.pop(sig), {1, 0})}")
        if not segs:
            unresolved.add((s["mjesto"].strip(), s["ulica"]))
            continue
        if len(segs) > 1:
            split.append(name)
        sides = {p for parts in segs.values() for p, _, _ in parts}
        for sig, parts in segs.items():
            number = next(n for n, v in sorted(st.read.items()) if v == sig)
            groups[(JLS_OF.get(s["mjesto"].strip()), sig)].append((s, parts, number, sides))
        if i % 100 == 0:
            print(f"  {i}/{len(streets)} ulica, HTTP {api.live} novih + {api.cached} iz cachea", flush=True)
    print(f"Grupa (JLS + {PROBE_MONTH}. mjesec): {len(groups)}; ulica podijeljenih po kućnim brojevima: {len(split)}")
    if unresolved:
        print(f"Ulice bez kućnih brojeva s miješanim otpadom u API-ju (preskočene): {len(unresolved)}: "
              + ", ".join(f"{nice(u)} ({nice(m)})" for m, u in sorted(unresolved)))
    if no_mixed:
        print(f"Dijelovi ulica bez miješanog otpada u API-ju (izostavljeni): {len(no_mixed)}: {'; '.join(no_mixed)}")

    by_year = defaultdict(list)  # whole-year schedule -> [(street, parts, sides)]
    regrouped = 0
    for (jls, sig), members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        s, _, number, _ = members[0]
        try:
            whole = year_of(api, s, number, year, have=dict(sig))
        except ValueError as e:
            problems.append(str(e))
            continue
        # check: two more months of the last member must match the year of the first
        s2, _, n2, _ = members[-1]
        same = all(set(month(api, s2, n2, m, year) or ()) == {(d, c) for d, c in whole.items() if d.month == m}
                   for m in CHECK_MONTHS) if len(members) > 1 else True
        if same:
            by_year[tuple(sorted(whole.items()))] += [(s, parts, sides) for s, parts, _, sides in members]
            continue
        regrouped += 1
        for s, parts, number, sides in members:  # the month is not enough: read the year of every member
            try:
                by_year[tuple(sorted(year_of(api, s, number, year, have=dict(sig)).items()))].append((s, parts, sides))
            except ValueError as e:
                problems.append(str(e))
    print(f"Grupa: {len(groups)}, od toga čitanih cijelu godinu po ulici: {regrouped}")

    zones, scheds = [], {}  # scheds: (settlement, street) -> rows, for the PDF check
    for sched, members in by_year.items():
        try:
            rows = shifted(dict(sched), year)
        except ValueError as e:
            problems.append(str(e))
            continue
        per_jls = Counter(JLS_OF.get(s["mjesto"].strip()) for s, _, _ in members)
        mixed_day = podaci.regular_day([(d, c, m) for d, c, m in rows if "M" in c]) or "nedjelja"
        zones.append((per_jls.most_common(1)[0][0], DAYS.index(mixed_day), -len(members), rows, members, per_jls))
        for s, _, _ in members:
            scheds[(s["mjesto"].strip(), s["ulica"])] = rows
    zones.sort(key=lambda z: (z[0] != "Osijek", z[0], z[1], z[2]))
    listed = {(s["mjesto"].strip(), s["ulica"]) for z in zones for s, _, _ in z[4]}
    print(f"Ulica u zonama: {len(listed)}, preskočenih: {len(unresolved)}, ukupno: {len(streets)}")
    if len(listed | unresolved) != len(streets):
        problems.append(f"{len(streets) - len(listed | unresolved)} ulica nije ni u jednoj zoni")
    with tempfile.TemporaryDirectory() as tmp:
        found, notes = pdf_check(api, tmp, year, scheds)
    problems += found

    missing = sorted({m for m, _ in unresolved} - {m for m, _ in listed})
    data = {**PROVIDER, "jls": sorted({JLS_OF[s["mjesto"].strip()] for s in streets if s["mjesto"].strip() in JLS_OF},
                                      key=lambda j: (j != "Osijek", j)), "zone": {}}
    if missing:
        data["napomene"] = data["napomene"] + [
            f"Za naselja {', '.join(nice(m) for m in missing)} tražilica na unikom.hr nema rasporeda po kućnim "
            f"brojevima; raspored je u tiskanom kalendaru općine na unikom.hr."]
    for n, (jls, day, _, rows, members, per_jls) in enumerate(zones, start=1):
        counts = Counter(c for _, codes, _ in rows for c in codes)
        for code, k in counts.items():
            lo, hi = COUNTS[code]
            if not lo <= k <= hi:
                problems.append(f"zona {n}: {k} odvoza {podaci.TYPES[code][0]}")
        problems += [f"zona {n}: odvoz u nedjelju {d}" for d, _, _ in rows if d.weekday() == 6]
        ulice = []
        for s, parts, sides in members:
            label = f"{nice(s['ulica'])} ({nice(s['mjesto'].strip())})"
            extra = spec(parts, sides)
            ulice.append(f"{label} {extra}" if extra else label)
        places = [nice(p) for p, _ in Counter(s["mjesto"].strip() for s, _, _ in members).most_common()]
        zone = {"jls": jls, "podrucje": ", ".join(places[:3]) + (" i dr." if len(places) > 3 else "")
                + f" – {describe(rows)}", "ulice": sorted(ulice)}
        others = sorted(j for j in per_jls if j != jls)
        texts = [f"Zona obuhvaća i ulice u: {', '.join(others)}."] if others else []
        texts += sorted({notes[s["mjesto"].strip()] for s, _, _ in members if s["mjesto"].strip() in notes})
        if texts:
            zone["napomena"] = " ".join(texts)
        zone["raw"] = {str(year): podaci.month_lines(rows)}
        data["zone"][str(n)] = zone
        print(f"Zona {n}: {jls}, {DAYS[day]}, {len(members)} ulica/dijelova, {dict(sorted(counts.items()))}")
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
