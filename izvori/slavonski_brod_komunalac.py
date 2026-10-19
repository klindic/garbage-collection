"""Slavonski Brod: Komunalac d.o.o. Slavonski Brod (komunalac-sb.hr), rules per street.

    python3 -m izvori.slavonski_brod_komunalac [--year 2026]

The street finder on komunalac-sb.hr reads https://komsb.silicabit.com/json/ulice.json: for every
street (or house-number range) the weekday of mixed waste ("komunalni") and biowaste ("bio"), both
every week, and the weekday plus two weeks of the month for useful waste ("korisni": the blue paper
bin and the yellow plastic bin, e.g. 2nd and 4th Monday). Streets with the same three rules form a
zone. Holiday shifts come from the company's "Radno vrijeme ..." notices for the year (WordPress
REST API); a holiday without a notice yet follows the practice of the last notices (Christmas and
New Year's Day move to the Saturday of that week, every other holiday is a normal collection day).
"""
import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import podaci
import pravila

SLUG = "slavonski-brod-komunalac"
SITE = "https://www.komunalac-sb.hr"
STREETS = "https://komsb.silicabit.com/json/ulice.json"
POSTS = SITE + "/wp-json/wp/v2/posts?search={q}&per_page=50&after={after}&_fields=id,date,link,title,content"
GREEN = SITE + "/raspored-odvoza-zelenog-otpada/"
UA = {"User-Agent": "Mozilla/5.0 (odvoz-otpada; +https://klindic.github.io/garbage-collection/)"}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
KEY = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Slavonski Brod",
    "web": SITE,
    "izvor": SITE + "/raspored/komunalni",
    "zupanija": "Brodsko-posavska",
    "jls": ["Slavonski Brod"],
}
NAPOMENE = [
    "Miješani komunalni otpad i biootpad odvoze se svaki tjedan, papir (plava kanta) i plastika "
    "(žuta kanta) zajedno dvaput mjesečno, prema danima iz tražilice ulica na komunalac-sb.hr.",
    "Zeleni otpad odvozi se jednom mjesečno po terenima (" + GREEN + "); nije uključen u ovaj raspored.",
    "Reciklažna dvorišta: Sjeverna vezna cesta 52z, Gospodarska ulica 4 (Kolonija) i Stanka Vraza 2C "
    "(odlagalište Vijuš – jug).",
]

_last = [0.0]


def fetch(url, dest=None, meta=None, tries=4):
    """GET with our User-Agent, at most 2 requests a second and retries with backoff.

    meta: optional dict that receives the response headers.
    """
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@")
    for attempt in range(tries):
        wait = _last[0] + 0.5 - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                body = r.read()
                if meta is not None:
                    meta.update(r.headers.items())
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            permanent = isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429
            if permanent or attempt == tries - 1:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(2 ** (attempt + 1))
    if dest:
        Path(dest).write_bytes(body)
    return body


def plain(fragment):
    """HTML -> one line of text; digits that the editor split with spaces ("0 3 .01.") are joined."""
    t = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    t = " ".join(t.replace("\xa0", " ").split())
    t = re.sub(r"(?<=\d) (?=\d)", "", t)
    return re.sub(r" (?=\.\d)", "", t)


def shift(rows, moves):
    """[(date, codes)] -> [(date, codes, moved)] with the holiday moves {old: new} applied."""
    return [(moves.get(d, d), codes, d in moves) for d, codes in rows]


def saturday(d):
    return d + timedelta(days=5 - d.weekday())


def holiday_moves(year, notices, practice, problems):
    """Moves for one year from the provider's notices.

    notices: {holiday: ("redovno", None) | ("pomak", new date) | None}; practice(holiday) -> new date
    or None, used when there is no notice. Returns ({old: new}, notes for napomene).
    """
    moves, notes = {}, []
    for h in pravila.blagdani(year):
        if h.weekday() >= 5:
            continue
        found = notices.get(h)
        if found is None:
            new = practice(h)
            why = "obavijest još nije objavljena" if h >= date.today() else "obavijest nije pronađena"
            if new:
                moves[h] = new
                notes.append(f"{h:%d.%m.} → {new:%d.%m.} (pretpostavka prema praksi, {why})")
            else:
                notes.append(f"{h:%d.%m.} redovno (pretpostavka prema praksi, {why})")
        elif found[0] == "redovno":
            notes.append(f"{h:%d.%m.} redovno (obavijest)")
        else:
            new = found[1]
            if new.weekday() == 6 or abs((new - h).days) > 6:
                problems.append(f"blagdan {h}: pomak na {new} nije vjerojatan")
            moves[h] = new
            notes.append(f"{h:%d.%m.} → {new:%d.%m.} (obavijest)")
    return moves, notes


def read_notices(year, posts, problems):
    """{holiday: ("redovno", None) | ("pomak", date)} from the company's notices about the year."""
    hol = [h for h in pravila.blagdani(year) if h.weekday() < 5]
    out = {}
    for post in posts:
        text = plain(post["content"]["rendered"])
        if "otpad" not in text:
            continue
        spots = sorted((m.start(), h) for h in hol for m in re.finditer(rf"\b{h:%d\.%m\.%Y}", text))
        for i, (pos, h) in enumerate(spots):
            end = spots[i + 1][0] if i + 1 < len(spots) else len(text)
            part = text[pos:end]
            if "neće odvoziti" in part:
                m = re.search(r"odvozit[i]? će se u (\w+),? (\d{1,2})\.(\d{1,2})\.(\d{4})", part)
                if not m:
                    problems.append(f"obavijest {post['link']}: za {h:%d.%m.%Y} nema novog datuma")
                    continue
                new = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
                if KEY[new.weekday()] != m.group(1).lower()[:3]:  # "subotu", "srijedu"
                    problems.append(f"obavijest {post['link']}: {new} nije {m.group(1)}")
                    continue
                found = ("pomak", new)
            elif re.search(r"odvozi(?: se)? prema redovnom rasporedu", part):
                found = ("redovno", None)
            elif re.search(r"komunaln\w* otpad|biootpad|reciklabiln", part):
                problems.append(f"obavijest {post['link']}: nepoznata formulacija za {h:%d.%m.%Y}: {part[:160]}")
                continue
            else:  # only the recycling yards' opening hours
                continue
            if out.get(h, found) != found:
                problems.append(f"obavijesti se ne slažu za {h:%d.%m.%Y}: {out[h]} / {found}")
            out[h] = found
    return out


def practice(h):
    """Last years' notices: Christmas and New Year's Day -> Saturday of that week, the rest as usual."""
    return saturday(h) if (h.month, h.day) in ((1, 1), (12, 25)) else None


def check_street(s):
    """Problem text for a street whose rules we do not understand, else None."""
    for kind in ("komunalni", "bio", "korisni"):
        r = s.get(kind) or {}
        if r.get("dan") not in (1, 2, 3, 4, 5):
            return f"{kind}: dan {r.get('dan')!r}"
    for kind in ("komunalni", "bio"):
        if s[kind].get("tjedan") != []:
            return f"{kind}: tjedan {s[kind].get('tjedan')!r} (očekivano [] = svaki tjedan)"
    t = s["korisni"].get("tjedan")
    if not (isinstance(t, list) and len(t) == 2 and len(set(t)) == 2 and all(x in (1, 2, 3, 4) for x in t)):
        return f"korisni: tjedan {t!r}"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []

    meta = {}
    streets = json.loads(fetch(STREETS, meta=meta))
    modified = meta.get("Last-Modified")
    after = f"{year - 1}-10-01T00:00:00"
    posts = {}
    for q in ("radno vrijeme", "odvoz"):
        for p in json.loads(fetch(POSTS.format(q=urllib.parse.quote(q), after=after))):
            posts[p["id"]] = p
    notices = read_notices(year, posts.values(), problems)
    moves, notes = holiday_moves(year, notices, practice, problems)

    # streets -> zones (identical rules)
    groups = defaultdict(list)
    skipped = 0
    for s in streets:
        if s is None:
            skipped += 1
            continue
        why = check_street(s)
        if why:
            problems.append(f"ulica {s.get('displayName')!r}: {why}")
            continue
        rule = (s["komunalni"]["dan"], s["bio"]["dan"], s["korisni"]["dan"], tuple(sorted(s["korisni"]["tjedan"])))
        groups[rule].append(" ".join(s["displayName"].split()))
    print(f"Ulica: {sum(map(len, groups.values()))} (+{skipped} prazan zapis), zona: {len(groups)}")

    names = [n for g in groups.values() for n in g]
    for n in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"ulica {n!r} je u više zona")

    zones = {}
    for i, (rule, ulice) in enumerate(sorted(groups.items()), 1):
        k, b, kd, weeks = rule
        rows = [(d, "M") for d in pravila.tjedno(year, KEY[k - 1])]
        rows += [(d, "B") for d in pravila.tjedno(year, KEY[b - 1])]
        rows += [(d, "KP") for n in weeks for d in pravila.mjesecno(year, KEY[kd - 1], n)]
        expect = {"M": len(pravila.tjedno(year, KEY[k - 1])), "B": len(pravila.tjedno(year, KEY[b - 1])),
                  "K": 12 * len(weeks)}
        merged = {}
        for d, codes, moved in shift(rows, moves):
            if d.weekday() == 6:
                problems.append(f"zona {i}: odvoz u nedjelju {d}")
            old = merged.get(d, ("", False))
            if set(old[0]) & set(codes):
                problems.append(f"zona {i}: dva odvoza iste vrste {d}")
            merged[d] = (old[0] + codes, old[1] or moved)
        for d, (codes, moved) in merged.items():
            days = {"M": k, "B": b, "K": kd, "P": kd}
            if not moved and any(d.isoweekday() != days[c] for c in codes):
                problems.append(f"zona {i}: {d} nije na pravilan dan za {codes}")
        for code, n in expect.items():
            got = sum(code in c for c, _ in merged.values())
            if got != n or not 10 <= n <= 53:
                problems.append(f"zona {i}: {code} {got} odvoza, očekivano {n}")
        zones[str(i)] = {
            "jls": "Slavonski Brod",
            "podrucje": f"Miješani {DAN[k - 1]}, biootpad {DAN[b - 1]}; papir i plastika "
                        f"{weeks[0]}. i {weeks[1]}. {DAN[kd - 1]} u mjesecu",
            "ulice": sorted(ulice, key=str.lower),
            "raw": {str(year): podaci.month_lines([(d, c, m) for d, (c, m) in merged.items()])},
        }

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")

    when = f" (datoteka izmijenjena {parsedate_to_datetime(modified):%d.%m.%Y.})" if modified else ""
    data = {**PROVIDER, "napomene": NAPOMENE + [
        f"Dani odvoza po ulicama: {STREETS}{when}.",
        "Blagdani " + str(year) + ": odvoz je i na blagdane prema rasporedu, osim: " +
        "; ".join(n for n in notes if "→" in n) + ". Izvor: obavijesti \"Radno vrijeme ...\" na " + SITE +
        " (redovno prema obavijesti: " + ", ".join(n.split()[0] for n in notes if "redovno (obavijest)" in n) + ").",
    ], "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years of zones that did not change
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("podrucje") == zone["podrucje"] and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    for n in notes:
        print(f"Blagdan {n}")
    total = defaultdict(int)
    for zone in zones.values():
        for _, codes, _ in podaci.iter_dates(zone, year):
            for c in codes:
                total[c] += 1
    print(f"Odvoza {year} (zbroj po zonama): " + ", ".join(f"{c} {n}" for c, n in sorted(total.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
