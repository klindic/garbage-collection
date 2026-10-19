"""Josipdol: Komunalno Josipdol d.o.o. (komunalnojosipdol.hr), one zone per weekday route (Mon, Tue, Thu).

    python3 -m izvori.komunalno_josipdol [--year 2026]

Mixed waste: the page "Raspored odvoza otpada" (WordPress REST via ?rest_route=, page 33885) lists the
settlements collected every Monday, Tuesday and Thursday (no collection on Wednesday and Friday).
Plastic/metal packaging and paper/cardboard go on the same day for all settlements, the last Friday of the
month: the featured image of the post "Raspored odvoza otpada za recikliranje za <year>" lists the twelve
dates; they are transcribed below, checked against the rule and the image's sha256 is pinned, so a new image
stops the script until it is read again. December's date is a week early because the last Friday is
Christmas, so it is marked as moved.
Holidays and other changes: the company posts notices ("Obavijest o promjeni termina odvoza ...",
"Obavijest o neodvozu otpada"); the script reads the notices of the year (and the December before) and
applies the moves ("predviđen za 25.12. ... izvršit će se u petak, 02.01.2026.") and cancellations; a notice
it cannot read stops the script. Holidays without a notice keep their dates (printed as an assumption).
"""
import argparse
import hashlib
import html
import json
import re
import sys
from collections import Counter
from datetime import date

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalno-josipdol"
SITE = "https://komunalnojosipdol.hr"
PAGE = SITE + "/?rest_route=/wp/v2/pages/33885&_fields=id,link,content"
POSTS = SITE + "/?rest_route=/wp/v2/posts&per_page=50&after={after}&_fields=id,date,link,title,content,featured_media"
MEDIA = SITE + "/?rest_route=/wp/v2/media/{id}&_fields=id,source_url"
# recycling image (featured image of the year post), transcribed; (day, month) of each collection
IMAGE = {"year": 2026, "sha256": "e92942be6f2e1ba1b76110fa92566cda901d4cc02bcb9731dfc4ec473ad4b7be",
         "dates": "30.1. 27.2. 27.3. 24.4. 29.5. 26.6. 31.7. 28.8. 25.9. 30.10. 27.11. 18.12."}
DAYS = {"Ponedjeljak": 0, "Utorak": 1, "Srijeda": 2, "Četvrtak": 3, "Petak": 4}
KEY = ["pon", "uto", "sri", "čet", "pet"]
MONTHS = ["siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja", "kolovoza", "rujna",
          "listopada", "studenoga", "prosinca"]
PROVIDER = {
    "davatelj": "Komunalno Josipdol d.o.o.",
    "web": SITE,
    "izvor": SITE + "/?page_id=33885",
    "zupanija": "Karlovačka",
    "jls": ["Josipdol"],
    "nazivi": {"P": "Plastika i metalna ambalaža (žuta)", "K": "Papir i karton (plava)"},
    "napomene": [
        "Miješani komunalni otpad odvozi se jednom tjedno prema danu naselja (ponedjeljak, utorak ili četvrtak); "
        "srijedom i petkom nema odvoza.",
        "Plastika i metalna ambalaža te papir i karton odvoze se istim danom, zadnji petak u mjesecu za sva naselja "
        "(u prosincu 18.12. zbog Božića).",
        "Promjene termina (blagdani, kvarovi vozila) objavljuju se na komunalnojosipdol.hr i Facebook profilu; "
        "objavljene promjene su upisane. Za blagdane bez obavijesti datumi nisu pomaknuti.",
        "Komunalno Josipdol d.o.o., Ogulinska 12, Josipdol, tel. 047 581 298, komunalno@josipdol.hr.",
    ],
}


def plain(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def routes(content, problems):
    """[(weekday, settlements)] from the page: a bold weekday followed by 'Naselja: ...'."""
    text = plain(re.sub(r"</p>", "\n", content))
    out = []
    for m in re.finditer(r"(" + "|".join(DAYS) + r")\s+(Naselj[ae]: ([^\n]+?)|Odvoz se ne vrši)(?=\s+(?:"
                         + "|".join(DAYS) + r"|Odvoz plastike)|$)", text):
        if m.group(3):
            out.append((DAYS[m.group(1)], [n.strip() for n in m.group(3).split(",") if n.strip()]))
    if [d for d, _ in out] != [0, 1, 3]:
        problems.append(f"stranica s rasporedom: našao dane {[d for d, _ in out]}, očekivano pon, uto, čet")
    if "zadnji petak u mjesecu" not in text:
        problems.append("stranica više ne navodi 'zadnji petak u mjesecu' za plastiku i papir")
    return out


def notice_changes(posts, year, problems):
    """({old: new} moves for mixed (M) and recyclables (PK), {cancelled mixed dates}) from the notices."""
    moves, cancelled = {"M": {}, "PK": {}}, set()
    for p in posts:
        title, text = plain(p["title"]["rendered"]), plain(p["content"]["rendered"])
        if not re.search(r"promjen\w* termina|neodvoz", title, re.I):
            continue
        posted = date.fromisoformat(p["date"][:10])

        def day(s):
            d, m, y = (re.findall(r"\d+", s) + [None])[:3]
            y = int(y) if y else posted.year + (1 if posted.month == 12 and int(m) == 1 else 0)
            return date(y, int(m), int(d))

        found = 0
        for sent in re.split(r"(?<=\.)\s+(?=[A-ZČĆŽŠĐ])", text):  # sentence by sentence
            m = re.search(r"Odvoz (.*?), koji je bio predviđen za (\d{1,2}\.\d{1,2}\.(?:\d{4}\.)?)", sent)
            dates = re.findall(r"\d{1,2}\.\s?\d{1,2}\.(?:\d{4}\.)?", sent)
            kind = m and ("M" if "miješan" in m.group(1) else "PK" if re.search("plastik|papir", m.group(1)) else None)
            if kind and len(dates) == 2 and re.search(r"izvršit|obavit", sent):
                moves[kind][day(dates[0])] = day(dates[1])
                found += 1
        if re.search("neodvoz", title, re.I):
            for d, mon in re.findall(r"(\d{1,2})\. (" + "|".join(MONTHS) + ")", text):
                cancelled.add(date(posted.year, MONTHS.index(mon) + 1, int(d)))
                found += 1
        if not found:
            problems.append(f"obavijest koju ne znam pročitati: {p['link']} ({title})")
        else:
            print(f"Obavijest {p['date'][:10]} {title[:70]}")
    return moves, cancelled


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    if year != IMAGE["year"]:
        sys.exit(f"Raspored za {year} nije prepisan. Ništa nije upisano.")
    problems = []
    page = json.loads(fetch(PAGE))
    plan = routes(page["content"]["rendered"], problems)
    posts = json.loads(fetch(POSTS.format(after=f"{year - 1}-11-01T00:00:00")))
    post = next((p for p in posts if re.search(rf"recikl\w* za {year}", plain(p["title"]["rendered"]))), None)
    if not post or not post["featured_media"]:
        sys.exit(f"Objava 'Raspored odvoza otpada za recikliranje za {year}' nije pronađena. Ništa nije upisano.")
    url = json.loads(fetch(MEDIA.format(id=post["featured_media"])))["source_url"]
    sha = hashlib.sha256(fetch(url)).hexdigest()
    if sha != IMAGE["sha256"]:
        sys.exit(f"slika se promijenila, prepisati ponovno: {url} (sha256 {sha}). Ništa nije upisano.")
    moves, cancelled = notice_changes(posts, year, problems)

    hol = set(pravila.blagdani(year))
    recyc = []  # (date, moved)
    for d, m in re.findall(r"(\d{1,2})\.(\d{1,2})\.", IMAGE["dates"]):
        x = date(year, int(m), int(d))
        rule = pravila.mjesecno(year, "pet", -1)[x.month - 1]
        if x != rule and not (rule in hol and x.weekday() == 4):
            problems.append(f"plastika/papir {x:%d.%m.}: nije zadnji petak u mjesecu ({rule:%d.%m.})")
        recyc.append((moves["PK"].get(x, x), x != rule or x in moves["PK"]))
    if Counter(d.month for d, _ in recyc) != Counter(range(1, 13)):
        problems.append("plastika/papir: nije točno jednom svaki mjesec")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, (wd, places) in enumerate(plan, 1):
        rows = {}
        for d in pravila.tjedno(year - 1, KEY[wd])[-1:] + pravila.tjedno(year, KEY[wd]):
            new = moves["M"].get(d, d)
            if new.year != year or d in cancelled:
                continue
            rows[new] = ["M", new != d]
            if d in hol and d not in moves["M"]:
                print(f"PRETPOSTAVKA: {d:%d.%m.} ({podaci.DAYS[d.weekday()]}) je blagdan bez obavijesti; odvoz ostaje.")
        for d, mv in recyc:
            rows.setdefault(d, ["", False])
            rows[d] = [rows[d][0] + "PK", rows[d][1] or mv]
        for d, (codes, mv) in rows.items():
            if "M" in codes and not mv and d.weekday() != wd:
                problems.append(f"zona {z}: {d} nije {podaci.DAYS[wd]}")
        cnt = Counter(d.month for d, (c, _) in rows.items() if "M" in c)
        bad = {m: cnt[m] for m in range(1, 13) if not 3 <= cnt[m] <= 6}
        if bad:
            problems.append(f"zona {z}: neobičan broj odvoza miješanog {bad}")
        data["zone"][str(z)] = {
            "jls": "Josipdol",
            "podrucje": f"{podaci.DAYS[wd].capitalize()} – " + ", ".join(places),
            "ulice": places,
            "raw": {**old.get(str(z), {}).get("raw", {}),
                    str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])},
        }
        total = Counter(c for codes, _ in rows.values() for c in codes)
        print(f"Zona {z} ({podaci.DAYS[wd]}): M {total['M']}, P {total['P']}, K {total['K']}, "
              f"pomaknuto {sum(1 for _, mv in rows.values() if mv)}, naselja {len(places)}")
    if cancelled:
        print("Otkazano (obavijest o neodvozu): " + ", ".join(f"{d:%d.%m.}" for d in sorted(cancelled)))
        data["napomene"] = data["napomene"] + [
            "Zbog kvara vozila odvoz miješanog otpada nije obavljen " + ", ".join(f"{d:%d.%m.}" for d in sorted(cancelled))
            + " (ti datumi su izostavljeni)."]
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(data['zone'])} zone)")


if __name__ == "__main__":
    main()
