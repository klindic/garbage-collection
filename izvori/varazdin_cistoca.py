"""Varaždin and surroundings: Čistoća d.o.o. Varaždin, public schedule API of moja.cistoca-vz.hr.

    python3 -m izvori.varazdin_cistoca [--year 2026]

The public "Raspored odvoza" page (no login, only a session cookie) lists the settlements with the
service, their streets and the house numbers of every street. For an address it returns the whole year
of collection dates, each labelled with the provider's zone and group ("ZONA 9 - Skupina A - papir"; in
the eleven municipalities the municipality instead of a zone: "VINICA - Skupina A - plastika").
Households have individual bins (group A), so every address is read with group A. The script reads the
first and last house number of every street (longer streets also the first and last odd and even number
and the middle one) and every house number where these disagree. One zone per provider zone or
municipality (numbered when a municipality has several schedules). Streets without house numbers
(settlements that no longer have the service) are skipped. Diapers (purple bin, on request) are not a
bin type here; they become a note.

Checks: every date in the year and not on a Sunday, plausible counts per type, every street with house
numbers in exactly one zone or split by house numbers, the same dates for all addresses of a Varaždin
zone, and the provider's printable schedule (PDF) of three sample addresses has the same dates and lists
the address's street. Otherwise nothing is written.
HTTP responses are cached in $ODVOZ_CACHE (default: the temp dir) for a week, so a rerun is cheap.
"""
import argparse
import hashlib
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import podaci

SLUG = "varazdin-cistoca"
SITE = "https://moja.cistoca-vz.hr"
API = SITE + "/Public/"
UA = {"User-Agent": "Mozilla/5.0 (odvoz-otpada; +https://klindic.github.io/garbage-collection/)"}
CACHE = Path(os.environ.get("ODVOZ_CACHE", Path(tempfile.gettempdir()) / "odvoz-cache")) / SLUG
MAX_AGE = 7 * 86400
GROUP = 1  # "A pojedinačni spremnici": households
TYPES = {"miješani komunalni otpad": "M", "biootpad": "B", "papir": "K", "plastika": "P", "staklo": "S",
         "metal": "L", "tekstil": "T"}
DIAPERS = "pelene"
COUNTS = {"M": (20, 60), "B": (15, 60), "K": (10, 60), "P": (10, 60), "S": (2, 14), "L": (2, 14), "T": (1, 14)}
# Settlements of the service by city/municipality (DZS, Popis 2021. – prvi rezultati po naseljima).
NASELJA = {
    "Beretinec": ["Beretinec", "Ledinec", "Ledinec Gornji", "Črešnjevo"],
    "Breznički Hum": ["Breznički Hum", "Butkovec", "Krščenovec", "Radešić", "Šćepanje"],
    "Cestica": ["Babinec", "Brezje Dravsko", "Cestica", "Dubrava Križovljanska", "Falinić Breg", "Gornje Vratno",
                "Jarki", "Kolarovec", "Križanče", "Križovljan Radovečki", "Mali Lovrečan", "Malo Gradišće",
                "Natkrižovljan", "Otok Virje", "Radovec", "Radovec Polje", "Selci Križovljanski", "Veliki Lovrečan",
                "Virje Križovljansko", "Vratno Otok"],
    "Gornji Kneginec": ["Donji Kneginec", "Gornji Kneginec", "Lužan Biškupečki", "Turčin", "Varaždin Breg"],
    "Jalžabet": ["Imbriovec Jalžabetski", "Jakopovec", "Jalžabet", "Kaštelanec", "Kelemen", "Leštakovec",
                 "Novakovec", "Pihovec", "Poduzetnička Zona Jalžabet"],
    "Mali Bukovec": ["Lunjkovec", "Mali Bukovec", "Martinić", "Novo Selo Podravsko", "Sveti Petar", "Županec"],
    "Petrijanec": ["Donje Vratno-dio", "Družbinec", "Majerje", "Nova Ves Petrijanečka", "Petrijanec",
                   "Strmec Podravski", "Zelendvor"],
    "Sračinec": ["Sračinec", "Svibovec Podravski"],
    "Sveti Ilija": ["Beletinec", "Doljan", "Križanec", "Krušljevec", "Seketin", "Sveti Ilija", "Tomaševec Biškupečki",
                    "Žigrovec"],
    "Sveti Đurđ": ["Hrženica", "Karlovec Ludbreški", "Komarnica Ludbreška", "Luka Ludbreška", "Obrankovec", "Priles",
                   "Sesvete Ludbreške", "Struga", "Sveti Đurđ"],
    "Trnovec Bartolovečki": ["Bartolovec", "Trnovec", "Zamlaka", "Šemovec", "Štefanec", "Žabnik"],
    "Varaždin": ["Donji Kućan", "Gojanec", "Gornji Kućan", "Hrašćica", "Jalkovec", "Kućan Marof", "Poljana Biškupečka",
                 "Varaždin", "Zbelava", "Črnec Biškupečki"],
    "Varaždinske Toplice": ["Boričevec Toplički", "Donja Poljana", "Drenovec", "Gornja Poljana", "Grešćevina",
                            "Hrastovec Toplički", "Jalševec Svibovečki", "Jarki Horvatićevi", "Leskovec Toplički",
                            "Lovrentovec", "Lukačevec Toplički", "Martinkovec", "Petkovec Toplički", "Pišćanovec",
                            "Retkovec Svibovečki", "Rukljevina", "Svibovec", "Tuhovec", "Varaždinske Toplice",
                            "Vrtlinovec", "Črnile", "Čurilovec", "Škarnik"],
    "Veliki Bukovec": ["Dubovica", "Kapela Podravska", "Veliki Bukovec"],
    "Vidovec": ["Budislavec", "Cargovec", "Domitrovec", "Krkanec", "Nedeljanec", "Papinec", "Prekno", "Tužno",
                "Vidovec", "Zamlača", "Šijanec"],
    "Vinica": ["Donje Vratno", "Gornje Ladanje", "Goruševnjak", "Marčan", "Pešćenica Vinička", "Vinica",
               "Vinica Breg"],
}
JLS_OF = {n: j for j, names in NASELJA.items() for n in names}
PROVIDER = {
    "davatelj": "Čistoća d.o.o. Varaždin",
    "web": "https://cistoca-vz.hr",
    "izvor": SITE + "/Public/PublicSchedule",
    "zupanija": "Varaždinska",
    "nazivi": {"P": "Plastika"},
    "napomene": [
        "Raspored vrijedi za kućanstva s pojedinačnim spremnicima (skupina A); zgrade sa zajedničkim spremnicima "
        "i poslovni korisnici mogu imati drugačiji raspored na moja.cistoca-vz.hr.",
        "Reciklažna dvorišta Varaždin 1 i 2, Motičnjak 9 i 9/A, Varaždin.",
    ],
}


class Http:
    """Sequential requests over kept-alive connections (one per host: new TLS handshakes are slow and to
    some servers often fail), at most 3 a second, retries with backoff, cookies and a disk cache.
    Also used by the Osijek and Zadar scripts."""

    def __init__(self, cache, headers=None):
        self.cache, self.headers = cache, {**UA, **(headers or {})}
        self.conns, self.cookies = {}, {}
        self.last, self.live, self.cached = 0.0, 0, 0
        self.distinct = set()  # requests a run without a cache sends

    @staticmethod
    def connect(host):
        proxy = urllib.request.getproxies().get("https")
        context = ssl.create_default_context()
        if not proxy:
            return http.client.HTTPSConnection(host, 443, context=context, timeout=120)
        p = urllib.parse.urlsplit(proxy)
        conn = http.client.HTTPSConnection(p.hostname, p.port or 80, context=context, timeout=120)
        conn.set_tunnel(host, 443)
        return conn

    def request(self, url, body=None, ctype=None, hops=3):
        target = urllib.parse.urlsplit(url)
        host = target.hostname
        headers = {**self.headers, **({"Content-Type": ctype} if ctype else {})}
        if self.cookies.get(host):
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies[host].items())
        conn = self.conns.get(host) or self.conns.setdefault(host, self.connect(host))
        try:
            where = target.path + (f"?{target.query}" if target.query else "")
            conn.request("GET" if body is None else "POST", where, body=body and body.encode(), headers=headers)
            r = conn.getresponse()
            data = r.read()
        except (OSError, http.client.HTTPException):
            self.conns.pop(host).close()
            raise
        if r.getheader("Connection", "").lower() == "close":
            self.conns.pop(host).close()
        for c in r.headers.get_all("Set-Cookie") or []:
            k, _, v = c.split(";", 1)[0].partition("=")
            self.cookies.setdefault(host, {})[k.strip()] = v.strip()
        if r.status in (301, 302, 303, 307, 308) and hops:
            return self.request(urllib.parse.urljoin(url, r.getheader("Location")), hops=hops - 1)
        if r.status >= 500:
            raise RuntimeError(f"HTTP {r.status}")
        if r.status != 200:
            raise ValueError(f"{url}: HTTP {r.status}")
        return data

    def fetch(self, url, body=None, ctype=None):
        path = self.cache / hashlib.sha1((url if body is None else f"{url}\n{body}").encode()).hexdigest()
        self.distinct.add(path.name)
        if path.exists() and time.time() - path.stat().st_mtime < MAX_AGE:
            self.cached += 1
            return path.read_bytes()
        for attempt in range(8):
            time.sleep(max(0.0, self.last + 0.34 - time.time()))
            try:
                data = self.request(url, body, ctype)
                break
            except (OSError, http.client.HTTPException, RuntimeError) as e:
                if attempt == 7:
                    raise RuntimeError(f"{url}: {e}") from e
                time.sleep(min(2 ** attempt, 30))
            finally:
                self.last = time.time()
        self.live += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".tmp").write_bytes(data)
        path.with_suffix(".tmp").replace(path)
        return data


WEEKDAYS = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
SHORT_NAMES = {"M": "miješani", "B": "biootpad", "K": "papir", "P": "plastika", "S": "staklo", "L": "metal",
               "G": "glomazni", "Z": "zeleni", "T": "tekstil"}


def describe(rows, names=None):
    """Short text of a year's collection days for a zone's description, e.g.
    'miješani i biootpad: utorak; papir i plastika: 1. utorak u mjesecu'. Also used by the Osijek and Zadar scripts."""
    names = {**SHORT_NAMES, **(names or {})}
    parts = {}
    for code in podaci.ORDER:
        days = sorted(d for d, c, _ in rows if code in c)
        if not days:
            continue
        per_day = Counter(d.weekday() for d in days)
        main = sorted(w for w, n in per_day.items() if n >= 0.6 * max(per_day.values()))
        summer = sorted(w for w, n in per_day.items() if w not in main and n >= 4
                        and all(6 <= d.month <= 9 for d in days if d.weekday() == w))
        when = "svaki dan" if len(main) == 7 else " i ".join(WEEKDAYS[w] for w in main)
        when += f" (ljeti i {' i '.join(WEEKDAYS[w] for w in summer)})" if summer else ""
        if len(days) >= 40:
            text = when
        elif len(days) >= 20:
            text = f"{when}, svaki drugi tjedan"
        elif len(days) >= 9:
            nth, n = Counter((d.day - 1) // 7 + 1 for d in days if d.weekday() == main[0]).most_common(1)[0]
            text = f"{nth}. {WEEKDAYS[main[0]]} u mjesecu" if n >= 0.75 * len(days) else f"{when}, jednom u 4 tjedna"
        else:
            text = f"{len(days)} puta godišnje"
        parts.setdefault(text, []).append(names[code])
    return "; ".join(f"{', '.join(v[:-1])} i {v[-1]}: {k}" if len(v) > 1 else f"{v[0]}: {k}" for k, v in parts.items())


def post_json(api, endpoint, payload):
    return json.loads(api.fetch(API + endpoint, json.dumps(payload, sort_keys=True), "application/json"))


def post_form(api, endpoint, payload):
    body = urllib.parse.urlencode(payload)
    return json.loads(api.fetch(API + endpoint, body, "application/x-www-form-urlencoded; charset=UTF-8"))


def streets(api):
    """[(settlement, street id, street name, [house numbers sorted])] of every settlement with the service."""
    out = []
    for s in post_json(api, "jsonGetNaseljaSaUslugom", {})["resultCombo"]:
        for u in post_json(api, "jsonGetUliceNaselja", {"idNaselja": s["idNaselja"]})["resultCombo"]:
            nums = post_json(api, "jsonGetKBRUlice", {"idUlice": u["idUlice"]})["resultCombo"]
            nums.sort(key=lambda n: (n["kbr"], n["kbrCijeli"]))
            out.append((s["nazivNaselja"].strip(), u["idUlice"], u["nazivUlice"].strip(), nums))
    return out


def area_key(area):
    """(zone key, JLS of the label or None) for a schedule label: "ZONA 9" -> ("9", None), the municipality
    "VINICA" -> ("Vinica", "Vinica"), the settlement "SVIBOVEC PODRAVSKI" -> ("Svibovec Podravski", "Sračinec")."""
    number = re.fullmatch(r"ZONA (\S+)", area)
    if number:
        return number.group(1), None
    jls = next((j for j in NASELJA if j.upper() == area.upper()), None)
    if jls:
        return jls, jls
    place = next((n for n in JLS_OF if n.upper() == area.upper()), None)
    if place:
        return place, JLS_OF[place]
    raise ValueError(f"nepoznato područje {area!r}")


def schedule(api, street_id, num, year, settlement):
    """Group A schedule of an address, or None:
    {"zona" (label), "key", "jls" (of the label), "dates": {date: codes}, "diapers", "times", "labels"}.

    The label is "ZONA 9" in Varaždin and the municipality ("VINICA") or settlement in the municipalities.
    An address listed under two labels keeps the one of its own city/municipality."""
    d = post_form(api, "JSONgetRasporedPoKoordinatamaAdresiDrugiPut",
                  {"idUlice": street_id, "kbrCijeli": num["kbrCijeli"], "idSkupine": GROUP,
                   "idSKBR": num["idSKBR"], "odabraniDatum": ""})
    if not d.get("Success"):
        return None
    rows = defaultdict(list)  # label -> [(day, kind, time)]
    for r in json.loads(d["dtRaspored"]):
        m = re.fullmatch(r"(.+?) - Skupina (\S+) - (.+)", r["description"].strip())
        if not m or m.group(2) != "A":
            raise ValueError(f"neočekivan opis {r['description']!r}")
        day = date.fromisoformat(r["datum"][:10])
        if day.year != year:
            raise ValueError(f"datum {day} nije u {year}.")
        rows[m.group(1).strip()].append((day, m.group(3).strip().lower(), r["subject"].rsplit(" - ", 1)[-1]))
    labels = sorted(rows)
    if len(labels) > 1:
        own = [a for a in labels if (area_key(a)[1] or "Varaždin") == JLS_OF.get(settlement)]
        if len(own) != 1:
            raise ValueError(f"više zona za jednu adresu: {labels}")
        labels = own
    area = labels[0]
    dates, diapers, times = defaultdict(set), set(), set()
    for day, kind, at in rows[area]:
        times.add(at)
        if kind == DIAPERS:
            diapers.add(day)
        elif kind in TYPES:
            dates[day].add(TYPES[kind])
        else:
            raise ValueError(f"nepoznata vrsta otpada {kind!r}")
    key, jls = area_key(area)
    return {"zona": area, "key": key, "jls": jls, "dates": {k: "".join(sorted(v)) for k, v in dates.items()},
            "diapers": diapers, "times": times, "labels": sorted(rows)}


def probes(nums):
    """House numbers read first: both ends, and on longer streets the ends of each parity and the middle."""
    pick = [nums[0], nums[-1]]
    if len(nums) >= 10:
        for parity in (1, 0):
            same = [n for n in nums if n["kbr"] % 2 == parity]
            pick += [same[0], same[-1]] if same else []
        pick.append(nums[len(nums) // 2])
    return list({n["idSKBR"]: n for n in pick}.values())


def runs(nums, mine):
    """Consecutive runs of `mine` within the sorted house numbers `nums`: ["1-41", "45"]."""
    parts, cur = [], []
    for n in nums + [None]:
        if n is not None and n["idSKBR"] in mine:
            cur.append(n)
        elif cur:
            parts.append(cur[0]["kbrCijeli"] if len(cur) == 1 else f"{cur[0]['kbrCijeli']}-{cur[-1]['kbrCijeli']}")
            cur = []
    return parts


def number_spec(nums, mine):
    """Short text for the house numbers `mine` of a street: "1-41", "neparni 43-99, parni", ..."""
    plain = runs(nums, mine)
    by_parity = []
    for parity, word in ((1, "neparni"), (0, "parni")):
        same = [n for n in nums if n["kbr"] % 2 == parity]
        r = runs(same, mine)
        if r == [f"{same[0]['kbrCijeli']}-{same[-1]['kbrCijeli']}"] or (len(same) == 1 and r):
            by_parity.append(word)
        elif r:
            by_parity.append(f"{word} {', '.join(r)}")
    return ", ".join(plain) if len(plain) <= len(by_parity) else ", ".join(by_parity)


def check_report(api, tmp, street, num, sched):
    """The provider's printable schedule (PDF) of an address: same dates and the street in its zone list."""
    query = urllib.parse.urlencode({"idSkbr": num["idSKBR"], "idSkupine": GROUP})
    url = f"{SITE}/Public/PublicScheduleReport?{query}"
    pdf = Path(tmp) / f"{num['idSKBR']}.pdf"
    pdf.write_bytes(api.fetch(url))
    text = subprocess.run(["pdftotext", "-l", "1", str(pdf), "-"], capture_output=True, text=True).stdout
    zones_text = subprocess.run(["pdftotext", "-f", "2", str(pdf), "-"], capture_output=True, text=True).stdout
    theirs = Counter(re.findall(r"(?<![\d.])(\d\d\.\d\d)\.(?!\d)", text))
    ours = Counter(f"{d:%d.%m}" for d, codes in sched["dates"].items() for _ in codes)
    ours.update(f"{d:%d.%m}" for d in sched["diapers"])
    problems = []
    if theirs != ours:
        diff = (theirs - ours) + (ours - theirs)
        problems.append(f"{street} {num['kbrCijeli']}: PDF i API se razlikuju ({', '.join(sorted(diff))})")
    names = {" ".join(x.split()) for x in " ".join(zones_text.split("\n")).split(",")}
    if f"{sched['zona']} - Skupina A" not in zones_text or not any(street in n for n in names):
        problems.append(f"{street} {num['kbrCijeli']}: PDF ne navodi ulicu u zoni {sched['zona']}")
    return problems


def label(settlement, street):
    return settlement if street == settlement else f"{street} ({settlement})"


def zone_text(zid, label_jls, members):
    """jls, podrucje and an optional note for a zone from its [(settlement, street)]."""
    per_jls = Counter(JLS_OF[s] for s, _ in members)
    jls = label_jls or per_jls.most_common(1)[0][0]
    places = [s for s, _ in Counter(s for s, _ in members).most_common()]
    podrucje = (f"{zid}: " if label_jls else f"Zona {zid}: ") + ", ".join(places[:4])
    podrucje += " i dr." if len(places) > 4 else ""
    others = [f"{', '.join(sorted({s for s, _ in members if JLS_OF[s] == j}))} ({j})" for j in per_jls if j != jls]
    return jls, podrucje, ("Zona obuhvaća i: " + "; ".join(others) + ".") if others else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    api = Http(CACHE, {"X-Requested-With": "XMLHttpRequest"})
    all_streets = streets(api)
    served = [s for s in all_streets if s[3]]
    print(f"Naselja: {len({s[0] for s in all_streets})}, ulica: {len(all_streets)}, "
          f"s kućnim brojevima: {len(served)}")
    problems = [f"{s}: naselje bez JLS u NASELJA" for s in sorted({s[0] for s in served} - set(JLS_OF))]

    zone_of = {}  # (street id, idSKBR) -> (zone key, dates signature)
    scheds = defaultdict(dict)  # zone key -> {dates signature: (settlement, street, house number, schedule)}
    no_group_a, no_mixed, two_labels = [], [], []

    def read(settlement, sid, name, n):
        try:
            s = schedule(api, sid, n, year, settlement)
        except ValueError as e:
            problems.append(f"{name} {n['kbrCijeli']} ({settlement}): {e}")
            return
        if s is None:
            no_group_a.append(f"{name} {n['kbrCijeli']} ({settlement})")
            return
        if len(s["labels"]) > 1:
            two_labels.append(f"{name} {n['kbrCijeli']} ({settlement}): {' i '.join(s['labels'])}, uzeto {s['zona']}")
        if not any("M" in c for c in s["dates"].values()):
            no_mixed.append(f"{name} {n['kbrCijeli']} ({settlement}, {s['zona']})")
            return
        sig = (tuple(sorted(s["dates"].items())), tuple(sorted(s["diapers"])))
        zone_of[(sid, n["idSKBR"])] = (s["key"], sig)
        scheds[s["key"]].setdefault(sig, (settlement, name, n, s))

    split = []
    for i, (settlement, sid, name, nums) in enumerate(served):
        for n in probes(nums):
            read(settlement, sid, name, n)
        if len({zone_of.get((sid, n["idSKBR"])) for n in probes(nums)} - {None}) > 1:
            split.append(f"{name} ({settlement})")
            for n in nums:
                if (sid, n["idSKBR"]) not in zone_of:
                    read(settlement, sid, name, n)
        if i % 100 == 0:
            print(f"  {i}/{len(served)} ulica, HTTP {api.live} novih + {api.cached} iz cachea", flush=True)
    print(f"Ulica podijeljenih po kućnim brojevima: {len(split)}" + (f" ({', '.join(split)})" if split else ""))
    if no_group_a:
        print(f"Adrese bez rasporeda za kućanstva (skupina A): {len(no_group_a)}: {', '.join(no_group_a[:20])}")
    if no_mixed:
        print(f"Adrese čiji raspored nema miješanog otpada (izostavljene): {len(no_mixed)}: {', '.join(no_mixed)}")
    if two_labels:
        print(f"Adrese pod dva rasporeda: {len(two_labels)}: {'; '.join(two_labels)}")

    # zone id: the provider's zone number, or the municipality (numbered when it has several schedules)
    zid = {}
    for key in sorted(scheds, key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else 0, k)):
        variants = list(scheds[key])
        if len(variants) > 1 and key.isdigit():
            (a, b) = [scheds[key][v] for v in variants[:2]]
            problems.append(f"zona {key}: različiti datumi za {a[1]} {a[2]['kbrCijeli']} i "
                            f"{b[1]} {b[2]['kbrCijeli']}")
        for i, sig in enumerate(variants, start=1):
            zid[(key, sig)] = key if len(variants) == 1 else f"{key} {i}"

    members = defaultdict(list)  # zone id -> [(settlement, street label)]
    for settlement, sid, name, nums in served:
        known = {n["idSKBR"]: zid[zone_of[(sid, n["idSKBR"])]] for n in nums if (sid, n["idSKBR"]) in zone_of}
        if not known:
            continue
        if len(set(known.values())) == 1:  # one zone: the unread house numbers go with it
            members[next(iter(known.values()))].append((settlement, label(settlement, name)))
            continue
        for z in sorted(set(known.values())):
            mine = {k for k, v in known.items() if v == z}
            members[z].append((settlement, f"{label(settlement, name)} {number_spec(nums, mine)}"))

    data = {**PROVIDER, "jls": sorted({JLS_OF[s] for s, _, _, _ in served if s in JLS_OF},
                                      key=lambda j: (j != "Varaždin", j)), "zone": {}}
    times, diaper_same = set(), True
    for (key, sig), z in zid.items():
        settlement, name, num, s = scheds[key][sig]
        counts = Counter(c for codes in s["dates"].values() for c in codes)
        for code, n in counts.items():
            lo, hi = COUNTS[code]
            if not lo <= n <= hi:
                problems.append(f"zona {z}: {n} odvoza {podaci.TYPES[code][0]}")
        if "M" not in counts:
            problems.append(f"zona {z}: nema miješanog otpada")
        problems += [f"zona {z}: odvoz u nedjelju {d}" for d in s["dates"] if d.weekday() == 6]
        times |= s["times"]
        diaper_same &= s["diapers"] <= {d for d, c in s["dates"].items() if "M" in c}
        jls, podrucje, note = zone_text(z, s["jls"], members[z])
        zone = {"jls": jls, "podrucje": podrucje, "ulice": sorted({m for _, m in members[z]})}
        if note:
            zone["napomena"] = note
        zone["raw"] = {str(year): podaci.month_lines([(d, c, False) for d, c in s["dates"].items()])}
        data["zone"][z] = zone
        print(f"Zona {z}: {jls}, {len(members[z])} ulica/naselja, {dict(sorted(counts.items()))}")
    if len(times) == 1:
        data["napomene"] = [f"Spremnike iznijeti do {times.pop()} na dan odvoza."] + data["napomene"]
    data["napomene"].append("Pelene (ljubičasti spremnik) za korisnike koji su ih prijavili odvoze se " + (
        "istim danom kad i miješani otpad." if diaper_same else "prema rasporedu na moja.cistoca-vz.hr."))

    unassigned = [f"{name} ({settlement})" for settlement, sid, name, nums in served
                  if not any((sid, n["idSKBR"]) in zone_of for n in nums)]
    if unassigned:
        print(f"Ulice bez rasporeda za kućanstva: {len(unassigned)}: {', '.join(unassigned)}")

    # PDF check: the two largest zones and the largest one of the other kind (zone number / municipality)
    by_size = sorted(zid.items(), key=lambda kv: -len(members[kv[1]]))
    picked = by_size[:2]
    kinds = {key.isdigit() for (key, _), _ in picked}
    picked += [kv for kv in by_size[2:] if len(kinds) == 2 or kv[0][0].isdigit() not in kinds][:1]
    samples = [scheds[key][sig] for (key, sig), _ in picked]
    with tempfile.TemporaryDirectory() as tmp:
        for settlement, name, num, s in samples:
            found = check_report(api, tmp, name, num, s)
            print(f"PDF {name} {num['kbrCijeli']} ({settlement}), zona {s['zona']}: {'OK' if not found else found}")
            problems += found
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
