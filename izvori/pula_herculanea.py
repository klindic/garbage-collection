"""Pula, Barban, Fažana, Ližnjan, Marčana, Svetvinčenat: Pula Herculanea d.o.o. (herculanea.hr), address search.

    python3 -m izvori.pula_herculanea [--year 2026]

The "Raspored odvoza" page has an address search: get-addresses/?query= returns at most 15 addresses
that contain the text (accents and case ignored), get-address-numbers/ the house numbers of one address,
and the page itself, with ?address=&address_number=, the dates of the current and the next month for
mixed waste, plastic and metal, paper and cardboard and biowaste (or "zadužen zajednički spremnik" for
addresses on shared containers). The schedule really belongs to the local committee (mjesni odbor), so
the script lists every address (breadth-first over substrings: a query that returns 15 is extended by
one character, for queries longer than two characters only if they start a word in a result), reads
three house numbers of each address (first, middle, last) and all of them when they disagree, and puts
addresses (or runs of house numbers) with the same dates into one zone per city/municipality.
The municipality comes from the address's postal town (Krnica is Marčana, Sutivanac (Žminj) Barban, Bankovići
and Brščići (Vodnjan) Svetvinčenat, checked in the provider's PDF, whose header names the local committee). The
postal town Pula also covers villages of Ližnjan (Šišan, Valtura, ...): an address in Pula with the same dates
as one of another municipality is decided by the committee in its PDF ("Mo/ CL Ližnjan").
Only two months are published at a time, so earlier months already in podaci/pula-herculanea.json are
kept on re-runs (a zone keeps the old dates of the zone that held all of its addresses). Holiday moves
are not marked on the site; the dates are taken as published.
HTTP responses are cached in $ODVOZ_CACHE (default: the temp dir): address lists for 45 days, schedules
for 2 days, PDFs for 90 days; at most 2 requests a second. A first run needs about 11 500 requests
(about 6 700 for the address list, which is reused for 45 days), roughly two to three hours.
"""
import argparse
import hashlib
import html
import http.client
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.parse
from collections import Counter, defaultdict, deque
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.varazdin_cistoca import Http

SLUG = "pula-herculanea"
SITE = "https://www.herculanea.hr"
PAGE = SITE + "/hr/usluge/cistoca/kalendar-odvoza/"
ADDRESSES = SITE + "/hr/collection-schedule/get-addresses/?query="
NUMBERS = SITE + "/hr/collection-schedule/get-address-numbers/?"
CACHE = Path(os.environ.get("ODVOZ_CACHE", Path(tempfile.gettempdir()) / "odvoz-cache")) / SLUG
DAY = 86400
AGE = {"list": 45 * DAY, "page": 2 * DAY, "pdf": 90 * DAY}
ALPHA = "abcdefghijklmnopqrstuvwxyz0123456789 .-,"
LIMIT = 15
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
BINS = {"MJEŠANI KOMUNALNI OTPAD": "M", "PLASTIKA I METAL": "P", "PAPIR I KARTON": "K", "BIOOTPAD": "B"}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
JLS = {"PULA": "Pula – Pola", "BARBAN": "Barban", "ŽMINJ": "Barban", "FAŽANA": "Fažana – Fasana",
       "LIŽNJAN": "Ližnjan – Lisignano", "KRNICA": "Marčana", "MARČANA": "Marčana",
       "SVETVINČENAT": "Svetvinčenat", "VODNJAN": "Svetvinčenat"}
# local committee in the PDF header ("Mo/ CL Ližnjan") -> municipality; Pula's 16 committees are listed
MUNICIPAL_MO = {"Ližnjan": "Ližnjan – Lisignano", "Marčana": "Marčana", "Barban": "Barban",
                "Svetvinčenat": "Svetvinčenat", "Fažana": "Fažana – Fasana", "Valbandon": "Fažana – Fasana"}
PULA_MO = {"Arena", "Busoler", "Gregovica", "Kaštanjer", "Monte Zaro", "Monvidal", "Nova Veruda", "Stari grad",
           "Stoja", "Sveti Polikarp - Sisplac", "Sveti Polikarp – Sisplac", "Šijana", "Štinjan", "Valdebek",
           "Veli Vrh", "Veruda", "Vidikovac"}
# addresses whose postal town lies in another municipality, checked in the PDF
CHECK = [("SUTIVANAC,BAŠIĆI", "ŽMINJ"), ("BANKOVIĆI", "VODNJAN"), ("BRŠČIĆI", "VODNJAN")]
ORDER = ["Pula – Pola", "Barban", "Fažana – Fasana", "Ližnjan – Lisignano", "Marčana", "Svetvinčenat"]
PROVIDER = {
    "davatelj": "Pula Herculanea d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Istarska",
    "jls": ORDER,
    "nazivi": {"P": "Plastika i metal", "K": "Papir i karton"},
    "bioNapomena": "Biootpad se odvozi samo u dijelu mjesnih odbora (prema tražilici).",
}
NAPOMENE = [
    "Raspored po adresi (kućnom broju) na " + PAGE + "; objavljuju se samo tekući i sljedeći mjesec, pa se "
    "raspored nadopunjuje svakim pokretanjem skripte.",
    "U središtu Pule i na drugim adresama sa zajedničkim spremnicima nema rasporeda za kućanstva: otpad se "
    "odlaže u zajedničke spremnike (lokacije spremnika na herculanea.hr).",
    "Spremnike treba iznijeti večer prije odvoza.",
    "Pomaci zbog blagdana nisu posebno označeni; datumi su preuzeti kako su objavljeni.",
    "Za neke adrese tražilica daje raspored samo bez kućnog broja (npr. Šišan), a s kućnim brojem javlja da nema "
    "rasporeda; takve adrese su u zoni pod svojim imenom, a brojevi u popisu adresa bez rasporeda.",
]


class Cached(Http):
    """Http (kept-alive connection, retries) with at most 2 requests a second and a cache age per kind."""

    def get(self, url, kind, fresh=False):
        path = self.cache / hashlib.sha1(url.encode()).hexdigest()
        if not fresh and path.exists() and time.time() - path.stat().st_mtime < AGE[kind]:
            self.cached += 1
            return path.read_bytes()
        for attempt in range(6):
            time.sleep(max(0.0, self.last + 0.5 - time.time()))
            try:
                data = self.request(url)
                break
            except (OSError, http.client.HTTPException, RuntimeError, ValueError) as e:
                if attempt == 5 or isinstance(e, ValueError):
                    raise RuntimeError(f"{url}: {e}") from e
                time.sleep(min(2 ** attempt, 30))
            finally:
                self.last = time.time()
        self.live += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".tmp").write_bytes(data)
        path.with_suffix(".tmp").replace(path)
        if self.live % 250 == 0:
            print(f"   ... {self.live} zahtjeva", flush=True)
        return data


def plain(s):
    s = unicodedata.normalize("NFD", s.lower().replace("đ", "d"))
    return "".join(c for c in s if not unicodedata.combining(c))


def starts_word(q, results):
    """Does q start a word in one of the results (accents and case ignored)?"""
    for r in results:
        a = plain(r.rsplit(",", 1)[0])
        if any(m.start() == 0 or not a[m.start() - 1].isalnum() for m in re.finditer(re.escape(plain(q)), a)):
            return True
    return False


def all_addresses(http):
    """Every address of the search: breadth-first over substrings (see the module docstring)."""
    found, queue, n = set(), deque("abcdefghijklmnopqrstuvwxyz0123456789"), 0
    while queue:
        q = queue.popleft()
        res = json.loads(http.get(ADDRESSES + urllib.parse.quote(q), "list"))
        n += 1
        found |= set(res)
        if len(res) >= LIMIT and (len(q) <= 2 or starts_word(q, res)):
            queue.extend(q + c for c in ALPHA if not (c == " " and q.endswith(" ")))
    return sorted(found), n


def split_address(a):
    street, _, town = a.rpartition(",")
    return street.strip(), town.strip()


def house_numbers(http, a):
    """House numbers of an address, asked the way the page's script does (split at ', ', spaces kept);
    'RAKALJ, DALMATINSKA, KRNICA' is also tried as street 'RAKALJ, DALMATINSKA' in Krnica."""
    nums = []
    parts = a.split(", ")
    for street, town in dict.fromkeys([(parts[0], parts[1] if len(parts) > 1 else ""), split_address(a)]):
        url = NUMBERS + urllib.parse.urlencode({"city": town, "address": street})
        nums = json.loads(http.get(url, "list"))
        if nums:
            break
    return sorted(nums, key=lambda n: (int(m.group()) if (m := re.match(r"\d+", n)) else -1, n))


def window(today):
    """[(year, month)] of the current and the next month."""
    nxt = (today.replace(day=1) + timedelta(days=32))
    return [(today.year, today.month), (nxt.year, nxt.month)]


def parse_page(text, months):
    """('ok', {date: codes}, rules, notices) | ('shared'|'none', {}, {}, '') from a result page."""
    if "zadužen zajednički spremnik" in text:
        return "shared", {}, {}, ""
    if "Nema rasporeda" in text or "container-for-bin-schedule" not in text:
        return "none", {}, {}, ""
    m = re.search(r"Raspored odvoza za (\w+) i (\w+) (\d{4})", text)
    names = [MONTHS[mo - 1].lower() for _, mo in months]
    if not m or [m.group(1).lower(), m.group(2).lower()] != names:
        raise ValueError(f"mjeseci na stranici: {m and m.group(0)}, očekivano {names}")
    dates, rules = defaultdict(str), {}
    for block in text.split("container-for-bin-schedule")[1:]:
        head = block.split('class="upper-bin-section-text">')[1].split("—")[0]
        title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", head)).split())
        code = BINS.get(title)
        if not code:
            raise ValueError(f"nepoznata vrsta otpada {title!r}")
        for part, (y, mo) in zip(re.split(r"(?:current|next)-month-dates-section", block)[1:3], months):
            month = re.search(r'days-cro-text">\s*(\w+)\s*/', part.split("month-text-container")[1]).group(1)
            if month != MONTHS[mo - 1]:
                raise ValueError(f"{title}: mjesec {month}, očekivano {MONTHS[mo - 1]}")
            rule = re.search(r'days-cro-text">\s*([^<]*?)\s*/\s*</span>', part.split("days-numbers-container")[0])
            if rule:
                rules[(code, mo)] = " ".join(rule.group(1).split())
            for day in re.findall(r"<div>\s*(\d{1,2})\s*</div>", part.split("date-numbers-container")[1]):
                d = date(y, mo, int(day))
                if code in dates[d]:
                    raise ValueError(f"{title}: {d} dvaput")
                dates[d] += code
    notices = html.unescape(re.sub(r"<[^>]+>", " ", text.split("notifications-container")[1].split("<script")[0])) \
        if "notifications-container" in text else ""
    return "ok", dict(dates), rules, " ".join(notices.split())


def schedule(http, a, n, months):
    url = PAGE + "?" + urllib.parse.urlencode({"address": a, "address_number": n})
    text = http.get(url, "page").decode("utf-8", "replace")
    try:
        state, dates, rules, notices = parse_page(text, months)
    except ValueError:  # a cached page from the previous month's window: fetch it again
        text = http.get(url, "page", fresh=True).decode("utf-8", "replace")
        state, dates, rules, notices = parse_page(text, months)
    pdf = re.search(r"/collection-schedule/pdf/(\d+)/", text)
    return state, dates, rules, notices, pdf and SITE + pdf.group(0)


def signature(state, dates):
    return state if state != "ok" else tuple(sorted(dates.items())) or "none"


def runs(nums, sigs):
    """House numbers -> [(signature, 'first–last' text)] for consecutive numbers with the same schedule."""
    out = []
    for n in nums:
        if out and out[-1][0] == sigs[n]:
            out[-1][1].append(n)
        else:
            out.append((sigs[n], [n]))
    label = lambda n: n or "bez broja"
    return [(s, label(ns[0]) if len(ns) == 1 else f"{label(ns[0])}–{ns[-1]}") for s, ns in out]


def naslov(a):
    """'ŠTINJANSKA CESTA' -> 'Štinjanska cesta', 'ULICA SV. IVANA' -> 'Ulica Sv. Ivana'."""
    words = [w[:1] + w[1:].lower() if w.isupper() else w for w in a.split()]
    small = {"Ulica", "Cesta", "Put", "Prilaz", "Uspon", "Prolaz", "Trg", "I", "Kod", "Za", "Na", "Od", "Do"}
    return " ".join(w.lower() if i and w in small else w for i, w in enumerate(words))


def describe(rows, rules):
    """'miješani svaki petak; plastika i papir svaka druga srijeda' from the site's rules (or weekdays)."""
    names = {"M": "miješani", "P": "plastika", "K": "papir", "B": "biootpad"}
    parts = defaultdict(list)
    for code in "MPKB":
        days = [d for d, c in rows if code in c]
        if not days:
            continue
        rule = Counter(r for (c, _), r in rules.items() if c == code).most_common(1)
        text = rule[0][0].lower() if rule else " i ".join(DAN[w] for w, _ in Counter(d.weekday() for d in days)
                                                             .most_common(2))
        parts[text].append(names[code])
    return "; ".join(f"{' i '.join(v)} {k}" for k, v in parts.items())


def pdf_committee(http, url):
    """('Pula', 'Ližnjan') from the header of the provider's PDF of one address: postal town ('Grad Pula/
    Citta di Pola') and local committee ('Mo/ CL Ližnjan')."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a.pdf"
        path.write_bytes(http.get(url, "pdf"))
        text = " ".join((pdfplumber.open(path).pages[0].extract_text() or "").split())
    town = re.search(r"Grad\s+(.+?)\s*/", text)
    mo = re.search(r"Mo/\s*CL\s+(.+?)\s+(?:Listopad|Studeni|Prosinac|Siječanj|Veljača|Ožujak|Travanj|Svibanj|"
                   r"Lipanj|Srpanj|Kolovoz|Rujan|Calendario|$)", text)
    return town and town.group(1), mo and mo.group(1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    today = date.today()
    months = window(today)
    if year not in {y for y, _ in months}:
        sys.exit(f"Tražilica pokazuje samo {months}; za {year}. nema podataka.")
    problems = []
    http = Cached(CACHE)
    addresses, nq = all_addresses(http)
    print(f"Adresa: {len(addresses)} ({nq} upita tražilice)", flush=True)
    towns = Counter(split_address(a)[1] for a in addresses)
    for t in towns:
        if t not in JLS:
            problems.append(f"nepoznato mjesto {t} ({towns[t]} adresa)")

    # schedules: three house numbers per address, all of them when they disagree
    parts = []  # (address, house numbers text or None, signature, a house number of the part)
    info, pdfs, shared, empty = {}, {}, [], []
    for i, a in enumerate(addresses):
        nums = house_numbers(http, a)
        if not nums:
            empty.append(a)
            continue
        sample = list(dict.fromkeys([nums[0], nums[len(nums) // 2], nums[-1]]))
        sigs = {}
        for n in sample:
            state, dates, rules, notices, pdf = schedule(http, a, n, months)
            sigs[n] = signature(state, dates)
            info[sigs[n]] = (dates, rules, notices)
            pdfs[(a, n)] = pdf
        if len(set(sigs.values())) > 1:
            for n in nums:
                if n not in sigs:
                    state, dates, rules, notices, pdfs[(a, n)] = schedule(http, a, n, months)
                    sigs[n] = signature(state, dates)
                    info[sigs[n]] = (dates, rules, notices)
            for sig, numbers in runs(nums, sigs):
                parts.append((a, numbers, sig, numbers.split("–")[0].replace("bez broja", "")))
        else:
            parts.append((a, None, sigs[sample[0]], sample[0]))
        if (i + 1) % 100 == 0:
            print(f"   {i + 1}/{len(addresses)} adresa; HTTP {http.live} novih, {http.cached} iz cachea", flush=True)

    # municipality of each part: the postal town; a part in Pula with the same dates as a part of another
    # municipality is decided by the local committee in its PDF (villages of Ližnjan have the postal town Pula)
    pula = JLS["PULA"]
    elsewhere = {sig for a, _, sig, _ in parts if JLS.get(split_address(a)[1]) != pula and sig not in ("shared", "none")}
    part_jls = []
    for a, numbers, sig, n in parts:
        jls = JLS.get(split_address(a)[1])
        if jls == pula and sig in elsewhere:
            url = pdfs.get((a, n))
            town, mo = pdf_committee(http, url) if url else (None, None)
            jls = MUNICIPAL_MO.get(mo, pula if mo in PULA_MO else None)
            print(f"Općina prema PDF-u: {a} {numbers or n}: MO {mo} → {jls}")
            if not jls:
                problems.append(f"{a} {numbers or n}: nepoznat mjesni odbor {mo!r} u PDF-u ({url})")
        part_jls.append(jls)
    for street, town in CHECK:
        a = next((x for x in addresses if split_address(x) == (street, town)), None)
        url = next((pdfs[k] for k in pdfs if k[0] == a and pdfs[k]), None)
        mo = pdf_committee(http, url)[1] if url else None
        print(f"Provjera općine: {street}, {town}: MO {mo}, skripta {JLS[town]}")
        if MUNICIPAL_MO.get(mo) != JLS[town]:
            problems.append(f"{street}, {town}: mjesni odbor u PDF-u {mo}, a skripta kaže {JLS[town]}")

    groups = defaultdict(list)
    for (a, numbers, sig, _), jls in zip(parts, part_jls):
        street, town = split_address(a)
        name = naslov(street) + (f" {numbers}" if numbers and numbers != "bez broja" else "")
        if town != "PULA" and naslov(town) not in (naslov(street), jls.split(" –")[0]):
            name += f" ({naslov(town)})"  # e.g. Krnica in Marčana, Sutivanac (Žminj) in Barban
        if sig == "shared":
            shared.append(name)
        elif sig == "none":
            empty.append(name)
        else:
            groups[(jls, sig)].append(name)
    print(f"Dijelova adresa: {len(parts)}; zajednički spremnici: {len(shared)}; bez rasporeda: {len(empty)}")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    zones = {}
    first_day = date(*months[0], 1)
    keyed = sorted(groups.items(), key=lambda g: (ORDER.index(g[0][0]), g[0][1][0][0], -len(g[1])))
    for i, ((jls, sig), names) in enumerate(keyed, 1):
        dates, rules, _ = info[sig]
        rows = sorted(dates.items())
        hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))
        regular = {c: Counter(d.weekday() for d, cc in rows if c in cc).most_common(1)[0][0]
                   for c in "MPKB" if any(c in cc for _, cc in rows)}
        out = []
        for d, codes in rows:
            off = [c for c in codes if d.weekday() != regular[c]]
            week_hol = any(h.isocalendar()[:2] == d.isocalendar()[:2] and h.weekday() < 6 for h in hol)
            out.append((d, codes, bool(off) and week_hol))
        for code in "M":
            for y, mo in months:
                n = sum(1 for d, c, _ in out if code in c and d.month == mo)
                if not 1 <= n <= 31:
                    problems.append(f"zona {i}: {n} odvoza miješanog otpada u {mo}. mjesecu")
        names = sorted(set(names), key=plain)
        zone = {"jls": jls, "podrucje": f"{jls.split(' –')[0]}: {describe(rows, rules)}", "ulice": names}
        by_year = defaultdict(list)
        for d, c, m in out:
            by_year[d.year].append((d, c, m))
        # earlier months of the old zone that held all these addresses
        prev = next((z for z in old.values() if z["jls"] == jls and set(names) <= set(z.get("ulice", []))), None)
        if prev:
            for d, c, m in podaci.iter_dates(prev):
                if d < first_day:
                    by_year[d.year].append((d, c, m))
        zone["raw"] = {str(y): podaci.month_lines(r) for y, r in sorted(by_year.items())}
        zones[str(i)] = zone
    lost = [k for k, z in old.items() if not any(set(z.get("ulice", [])) >= set(n["ulice"]) for n in zones.values())]
    if lost:
        print(f"Stare zone bez nasljednika (njihovi raniji mjeseci se ne prenose): {', '.join(lost)}")
    for i, z in zones.items():
        print(f"Zona {i} {z['jls']}: {len(z['ulice'])} adresa – {z['podrucje']}")

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "napomene": NAPOMENE + [
        f"Adrese sa zajedničkim spremnicima ({len(shared)}): " + ", ".join(sorted(shared, key=plain)) + "."
    ] + ([f"Adrese bez rasporeda u tražilici: {', '.join(sorted(empty, key=plain))}."] if empty else []),
        "zone": zones}
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona); HTTP {http.live} novih, "
          f"{http.cached} iz cachea")


if __name__ == "__main__":
    main()
