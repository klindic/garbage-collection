"""Zagreb: Zagrebački holding, Podružnica Čistoća. Public API behind cistoca.hr and the Razvrstaj MojZG app.

    python3 -m izvori.zagreb_cistoca [--year 2026]

The API (https://razvrstajap.zagreb.hr/docs) gives rules per address: weekday + "EVERY WEEK", or
"BLUE WEEK" (paper) / "YELLOW WEEK" (plastic) on alternating weeks anchored in /configs. Rules belong
to a local board (mjesni odbor) or a street, so the script samples two addresses per street and board,
and reads every address only where the samples differ. Addresses come from broad searches plus one
search per name in the city's open street register (data.zagreb.hr). Identical schedules become one
zone. No holiday shifts are published: collection follows the rules on holidays too. The computed next
dates are checked against cistoca.hr's own "next collection" answer for sample addresses.
HTTP responses are cached in $ODVOZ_CACHE (default: the temp dir), so a rerun is cheap.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import podaci
import pravila

SLUG = "zagreb-cistoca"
API = "https://razvrstajap.zagreb.hr"
REGISTER = ("https://data.zagreb.hr/dataset/8e0bd4c4-4b6f-49b9-8327-f2ef9c213b05/resource/"
            "e232d55f-16f7-4310-bb30-ecdaf4b21066/download/rpj_ulica.csv")
UA = {"User-Agent": "Mozilla/5.0 (odvoz-otpada; +https://klindic.github.io/garbage-collection/)"}
CACHE = Path(os.environ.get("ODVOZ_CACHE", Path(tempfile.gettempdir()) / "odvoz-cache")) / SLUG
BROAD = ["ul", "ce", "ic", "ka", "va", "na", "ra", "ja", "ta", "ov", "ni", "ko"]
TYPES = {"mjesoviti": "M", "bio": "B", "papir": "K", "plastika": "P", "staklo": "S", "tekstil": "T",
         "glomazni": "G"}
DAYS = {"MON": 0, "TUE": 1, "WED": 2, "THR": 3, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
DAY_NAMES = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
PROVIDER = {
    "davatelj": "Zagrebački holding d.o.o. – Podružnica Čistoća",
    "web": "https://www.cistoca.hr",
    "izvor": "https://www.cistoca.hr/usluge/skupljanje-i-odvoz-otpada-22/odvoz-otpada-iz-kucanstva/1307",
    "zupanija": "Grad Zagreb",
    "jls": ["Zagreb"],
    "nazivi": {"P": "Plastika i metal"},
    "napomene": [
        "Papir se odvozi u plavom, a plastika i metal u žutom tjednu; tjedni se izmjenjuju.",
        "Staklo se odlaže na zelenim otocima; glomazni otpad odvozi se na zahtjev.",
        "Čistoća ne objavljuje pomake zbog blagdana; odvoz je i na blagdane prema rasporedu.",
    ],
}
_last = [0.0]
_lock = threading.Lock()


def cache_path(url):
    return CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".json")


def get(url, cache=True, pause=0.2):
    """GET with a disk cache, retries with backoff and request starts at least `pause` s apart (thread-safe)."""
    path = cache_path(url)
    if cache and path.exists():
        return path.read_bytes()
    for attempt in range(6):
        with _lock:
            wait = _last[0] + pause - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                body = r.read()
            break
        except Exception as e:  # noqa: BLE001 (network errors of every kind get the same retry)
            if attempt == 5:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(2 ** attempt)
    if cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_bytes(body)
        tmp.replace(path)  # atomic, so an interrupted run never leaves a half-written cache file
    return body


def search(q, limit):
    url = f"{API}/addresses?" + urllib.parse.urlencode({"q": q, "limit": limit})
    return json.loads(get(url))["data"]


def all_addresses():
    """Every address the API knows: broad searches, then one search per register street name."""
    seen = {}
    for q in BROAD:
        for a in search(q, 300000):
            seen[a["id"]] = a
    register = csv.DictReader(io.StringIO(get(REGISTER).decode("utf-8-sig")), delimiter=";")
    names = {r["UL_IME"] for r in register}
    have = {a["name"].casefold() for a in seen.values()}
    for name in sorted(n for n in names if n.casefold() not in have):
        for a in search(name, 3000):
            if a["name"].casefold() == name.casefold():
                seen[a["id"]] = a
    return list(seen.values()), names


def prefetch(ids, workers=6):
    """Fetch the rules of many addresses in parallel into the cache (get() keeps the request rate).

    A request that still fails after its retries is skipped here; the caller fetches it again one by one."""
    def one(address_id):
        try:
            rules(address_id)
            return True
        except RuntimeError:
            return False

    ids = [i for i in ids if not cache_path(f"{API}/addresses/{i}").exists()]
    failed = 0
    with ThreadPoolExecutor(workers) as pool:
        for n, ok in enumerate(pool.map(one, ids), start=1):
            failed += not ok
            if n % 2000 == 0:
                print(f"  preuzeto {n}/{len(ids)} adresa ({failed} neuspjelih, ponovit će se) ...", flush=True)


def rules(address_id):
    data = json.loads(get(f"{API}/addresses/{address_id}"))["data"]
    return data[0] if data else None


def signature(detail):
    """Schedule content of an address, independent of rule ids: ((type, ((day, repeat, from, to), ...)), ...)."""
    out = []
    for s in detail.get("schedules") or []:
        picks = tuple(sorted((p["dayOfWeek"], p["repeat"], p.get("timeFrom") or "", p.get("timeTo") or "")
                             for p in s["pickups"]))
        if picks:
            out.append((s["type"], picks))
    return tuple(sorted(out))


def house_key(number):
    m = re.match(r"(\d+)(.*)", number or "")
    return (int(m.group(1)), m.group(2)) if m else (10 ** 6, number or "")


def runs(numbers):
    """Compact house-number list: '1-15, 19, 21a'."""
    keys = sorted(numbers, key=house_key)
    parts, start, prev = [], None, None
    for n in keys:
        k = house_key(n)
        if not k[1] and prev is not None and not house_key(prev)[1] and k[0] == house_key(prev)[0] + 1:
            prev = n
            continue
        if start is not None:
            parts.append(start if start == prev else f"{start}-{prev}")
        start = prev = n
    if start is not None:
        parts.append(start if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def week_anchors():
    cfg = {c["key"]: c["value"] for c in json.loads(get(f"{API}/configs", cache=False))["data"]}
    return date.fromisoformat(cfg["BLUE WEEK"]), date.fromisoformat(cfg["YELLOW WEEK"])


def dates(sig, year, blue, yellow):
    """{date: codes} for a schedule signature."""
    out = defaultdict(set)
    for wtype, picks in sig:
        code = TYPES.get(wtype)
        if code is None:
            raise ValueError(f"unknown waste type {wtype!r}")
        for day, repeat, *_ in picks:
            weekly = pravila.tjedno(year, DAY_NAMES[DAYS[day]])
            if repeat == "EVERY WEEK":
                chosen = weekly
            elif repeat in ("BLUE WEEK", "YELLOW WEEK"):
                anchor = blue if repeat == "BLUE WEEK" else yellow
                monday = anchor - timedelta(days=anchor.weekday())
                chosen = [d for d in weekly if ((d - monday).days // 7) % 2 == 0]
            else:
                raise ValueError(f"unknown repeat {repeat!r}")
            for d in chosen:
                out[d].add(code)
    return {d: "".join(sorted(c)) for d, c in out.items()}


def describe(sig):
    parts = []
    for wtype, picks in sig:
        days = []
        for day, repeat, t_from, t_to in picks:
            label = DAY_NAMES[DAYS[day]]
            if repeat == "BLUE WEEK":
                label += " (plavi tjedan)"
            elif repeat == "YELLOW WEEK":
                label += " (žuti tjedan)"
            if t_from and t_from.startswith(("2", "0")) and t_to and t_to < t_from:
                label += " noću"
            days.append(label)
        parts.append(f"{podaci.TYPES[TYPES[wtype]][0].split(' ')[0]}: {', '.join(days)}")
    return "; ".join(parts)


def check_against_site(sample, sig_of, year, blue, yellow):
    """Compare our next date per type with cistoca.hr's answer for a few addresses and test days."""
    names = {"Bio": "B", "Papir": "K", "Plastika": "P", "Mješoviti": "M", "Miješani": "M"}
    problems = []
    for addr in sample:
        for test_day in (date(year, 3, 2), date(year, 9, 14)):
            url = (f"https://www.cistoca.hr/?RazvrstajApiGuid={addr['id']}&test_datum={test_day.isoformat()}")
            try:
                resp = json.loads(get(url))
            except Exception as e:  # noqa: BLE001
                problems.append(f"{addr['name']} {addr['houseNumber']}: cistoca.hr {e}")
                continue
            ours = dates(sig_of[addr["id"]], year, blue, yellow)
            for item in resp.get("WebResponses") or []:
                code = names.get(item.get("Tip"))
                if not code or not item.get("Datum"):
                    continue
                text = item["Datum"].strip().strip(".").lower()
                if text in ("danas", "sutra"):
                    theirs = test_day + timedelta(days=text == "sutra")
                else:
                    theirs = date(*reversed([int(x) for x in text.split(".")]))
                mine = min((d for d, c in ours.items() if code in c and d >= test_day), default=None)
                if mine != theirs:
                    problems.append(f"{addr['name']} {addr['houseNumber']} {item['Tip']} od {test_day}: "
                                    f"mi {mine}, cistoca.hr {theirs}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    addresses, register = all_addresses()
    print(f"Adresa: {len(addresses)}, naziva ulica: {len({a['name'] for a in addresses})} "
          f"(u registru {len(register)})")
    groups = defaultdict(list)
    for a in addresses:
        groups[(a["name"], a["boardId"])].append(a)
    for members in groups.values():
        members.sort(key=lambda a: house_key(a["houseNumber"]))
    prefetch([m[k]["id"] for m in groups.values() for k in (0, len(m) // 2, -1)])
    sig_of, mixed = {}, 0
    for i, ((name, board), members) in enumerate(sorted(groups.items())):
        probe = {members[0]["id"]: members[0], members[len(members) // 2]["id"]: members[len(members) // 2],
                 members[-1]["id"]: members[-1]}
        sigs = {}
        for a in probe.values():
            d = rules(a["id"])
            sigs[a["id"]] = signature(d) if d else ()
        if len(set(sigs.values())) > 1:  # differs along the street: read every house number
            mixed += 1
            prefetch([a["id"] for a in members if a["id"] not in sigs])
            for a in members:
                if a["id"] not in sigs:
                    d = rules(a["id"])
                    sigs[a["id"]] = signature(d) if d else ()
        only = next(iter(sigs.values()))
        for a in members:
            sig_of[a["id"]] = sigs.get(a["id"], only)
        if i % 500 == 0:
            print(f"  {i}/{len(groups)} ulica u mjesnim odborima ...", flush=True)
    no_rules = [a for a in addresses if not sig_of[a["id"]]]
    print(f"Ulica u odborima: {len(groups)}, s različitim rasporedima po kućnim brojevima: {mixed}, "
          f"adresa bez rasporeda (zajednički spremnici ili nepoznato): {len(no_rules)}")

    blue, yellow = week_anchors()
    zones_by_sig = defaultdict(lambda: defaultdict(list))  # sig -> (street, board id) -> house numbers
    board_name = {a["boardId"]: a["boardName"] for a in addresses}
    for a in addresses:
        sig = sig_of[a["id"]]
        if sig:
            zones_by_sig[sig][(a["name"], a["boardId"])].append(a["houseNumber"])
    problems = []
    zone_list = sorted(zones_by_sig.items(), key=lambda kv: -sum(len(v) for v in kv[1].values()))
    data = {**PROVIDER, "zone": {}}
    for n, (sig, streets) in enumerate(zone_list, start=1):
        try:
            sched = dates(sig, year, blue, yellow)
        except ValueError as e:
            problems.append(f"zona {n}: {e}")
            continue
        counts = Counter(c for codes in sched.values() for c in codes)
        if counts.get("M", 0) < 50:
            problems.append(f"zona {n}: samo {counts.get('M', 0)} odvoza miješanog otpada")
        ulice = []
        for (street, board), numbers in sorted(streets.items()):
            label = f"{street} ({board_name[board]})"
            ulice.append(label if len(numbers) == len(groups[(street, board)]) else f"{label} {runs(numbers)}")
        night = any(t_from and t_to and t_to < t_from for _, picks in sig for *_, t_from, t_to in picks)
        zone = {"jls": "Zagreb", "podrucje": describe(sig), "ulice": ulice,
                "raw": {str(year): podaci.month_lines([(d, c, False) for d, c in sched.items()])}}
        if night:
            zone["napomena"] = "Dio odvoza je noću; spremnik iznesi navečer na dan odvoza."
        data["zone"][str(n)] = zone
    sample = random.Random(year).sample([a for a in addresses if sig_of[a["id"]]], 12)
    problems += check_against_site(sample, sig_of, year, blue, yellow)
    print(f"Zona (različitih rasporeda): {len(data['zone'])}")
    for p in problems[:30]:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json")



if __name__ == "__main__":
    main()
