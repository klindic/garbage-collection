"""Pakrac, Lipik: Komunalac d.o.o. Pakrac (komunalac-pakrac.hr), mixed waste by street and settlement route.

    python3 -m izvori.komunalac_pakrac [--year 2026]

Mixed waste: the post "Raspored sakupljanja miješanog komunalnog otpada" (WordPress REST) lists the areas.
The towns (Pakrac by street lists, Lipik as a whole) have a weekly rule ("svakoga radnoga ponedjeljka");
the rural settlements have one PDF per route with the explicit dates of every second working weekday,
under the rule "osim u slučaju neradnog dana, tada se odvozi dan kasnije" (shifts already in the dates;
a date off the route's weekday the day after a holiday is marked as moved). The same rule is applied to
the weekly town rounds (an assumption, printed and noted).
Paper and plastic: the leaflet for 1 July - 31 December (a PDF of three scanned images, found in the
WordPress media library) gives five groups of streets and settlements, each with a rule ("zadnja radna
srijeda", "prva radna srijeda") and the dates. The dates are transcribed below, checked against the
rule, and the PDF's sha256 is pinned, so a new leaflet stops the script until it is transcribed again.
Streets and settlements are joined to their paper/plastic group by name; areas the leaflet does not
name get mixed waste only.
"""
import argparse
import hashlib
import html
import json
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalac-pakrac"
SITE = "https://komunalac-pakrac.hr"
POST = SITE + "/wp-json/wp/v2/posts?slug=raspored-sakupljanja-mijesanog-komunalnog-otpada&_fields=id,link,content"
MEDIA = SITE + "/wp-json/wp/v2/media?search=odvojeno&per_page=50&_fields=id,date,source_url"
DAYS = {"PONEDJELJ": 0, "UTOR": 1, "SRIJED": 2, "ČETVRT": 3, "PET": 4}
# Grad Lipik settlements (DZS); every other settlement on the schedule is in Grad Pakrac
LIPIK = {"Antunovac", "Bjelanovac", "Brekinska", "Brezine", "Bujavica", "Bukovčani", "Dobrovac", "Donji Čaglić",
         "Filipovac", "Gaj", "Gornji Čaglić", "Jagma", "Japaga", "Klisa", "Korita", "Kovačevac", "Kukunjevac", "Lipik",
         "Livađani", "Marino Selo", "Poljana", "Ribnjaci", "Skenderovci", "Strižičevac", "Subocka", "Šeovica"}

# Leaflet "Odvojeno sakupljanje otpada - raspored sakupljanja", 1.7.-31.12.2026, transcribed from the images
LEAFLET = {
    "file": "Odvojeno-sakupljanje-otpada-raspored-sakupljanja.pdf",
    "sha256": "36fbcadbb212aab90589a577565c12b2467cdc4d421a233ec206e000b6da02c3",
    "year": 2026, "from_month": 7,
}
GROUPS = [  # (key, jls, streets, settlements, (paper weekday, n), paper dates, (plastic weekday, n), plastic dates)
    ("A", "Pakrac",
     "Križnog puta, Miroslava Krleže, Šeovački put, Veberov sokak, Sedlar, Slavonska, Kragujski put, Kneza Branimira, "
     "Nikole Oršanića, Pepe Polaka, Planinska, 103. brigade, 105. brigade, Osječka, Vukovarska, Vinogradska, Bljesak, "
     "Stanka Grabrića, Andrije Hebranga, Matice hrvatske, Trg bana J.Jelačića, 30. svibnja, Braće Radić, trg pape "
     "I.Pavla II, J.J.Strossmayera, Ljudevita Gaja, Kalvarija, Kralja Tomislava, Vatroslava Lisinskog, Augusta Šenoe, "
     "Obala P.Krešimira IV, Trg 76. bataljuna, Gojka Šuška, Vlade Laučana, Hrvatske velikana, Bolnička, Petra "
     "Preradovića, Prilaz na mali most, Prolaz baruna Trenka, Ivana Gundulića, Svetog Roka, Psunjska, Jana Žiške, "
     "poginulih branitelja", ["Kraguj"],
     ("sri", -1), "29.07. 26.08. 30.09. 28.10. 25.11. 30.12.", ("sri", 1), "01.07. 12.08. 02.09. 07.10. 04.11. 02.12."),
    ("B", "Pakrac",
     "Matije Gupca, Vinka Rehaka, I.G.Kovačića, Kneza Domagoja, Kardinala Alojzija Stepinca, Augusta Cesarca, Aleja "
     "Kestenova, Frankopanska, Šubićeva, Marina Držića, Zvonimirova, Nikole Tesle, Tina Ujevića, Z.N.G., Basarićekova, "
     "Ivana Meštrovića, Mate Lovraka, Grigora Viteza, Marinkovac, Ruđera Boškovića, Krndija, Pilanski put, Radničkih "
     "sindikata, Krančevićeva, Radničko naselje, I.B.Mažuranić, Zona male privrede", ["Prekopakra", "Klisa"],
     ("uto", -1), "28.07. 25.08. 29.09. 27.10. 24.11. 29.12.", ("uto", 1), "07.07. 04.08. 01.09. 06.10. 03.11. 01.12."),
    ("C", "Pakrac", "",
     ["Badljevina", "Omanovac", "Gornja Obrijež", "Veliki Banovac", "Donja Obrijež", "Ploštine", "Kapetanovo polje",
      "Strižičevac", "Toranj", "Mali Banovac", "Batinjani", "Novi Majur", "Stari Majur", "Kusonje", "Dragović",
      "Španovica", "Gornji Grahovljani", "Gornja Šumetlica"],
     ("pon", -1), "27.07. 31.08. 28.09. 26.10. 30.11. 28.12.", ("pon", 1), "06.07. 03.08. 07.09. 05.10. 02.11. 07.12."),
    ("D", "Lipik", "", ["Lipik", "Filipovac", "Dobrovac", "Subocka"],
     ("čet", -1), "30.07. 27.08. 24.09. 29.10. 26.11. 31.12.", ("čet", 1), "02.07. 06.08. 03.09. 01.10. 05.11. 03.12."),
    ("E", "Lipik", "",
     ["Šeovica", "Japaga", "Donji Čaglić", "Kukunjevac", "Kovačevac", "Brezine", "Brekinska", "Gaj", "Poljana",
      "Antunovac", "Marino Selo", "Ribnjaci"],
     ("pet", -1), "31.07. 28.08. 25.09. 30.10. 27.11. 18.12.", ("pet", 1), "03.07. 07.08. 04.09. 02.10. 06.11. 04.12."),
]
DAN_INS = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom"]
RULE_NAME = {("pon", 1): "prvi radni ponedjeljak", ("pon", -1): "zadnji radni ponedjeljak",
             ("uto", 1): "prvi radni utorak", ("uto", -1): "zadnji radni utorak",
             ("sri", 1): "prva radna srijeda", ("sri", -1): "zadnja radna srijeda",
             ("čet", 1): "prvi radni četvrtak", ("čet", -1): "zadnji radni četvrtak",
             ("pet", 1): "prvi radni petak", ("pet", -1): "zadnji radni petak"}
GENERIC = {"put", "sokak", "naselje", "most", "privrede", "brigade", "ii", "iv"}
STREET_ALIAS = {"kranjčevića": "krančevićeva"}  # S.S.Kranjčevića on the page = Krančevićeva on the leaflet
PROVIDER = {
    "davatelj": "Komunalac d.o.o. Pakrac",
    "web": SITE,
    "zupanija": "Požeško-slavonska",
    "jls": ["Pakrac", "Lipik"],
    "nazivi": {"P": "Plastika i metal (žuta kanta)", "K": "Papir i karton (plava kanta)"},
}
NAPOMENE = [
    "Miješani komunalni otpad: ako je dan odvoza neradni dan, odvozi se dan kasnije (pravilo iz rasporeda). "
    "U rasporedima za naselja pomak je već u datumima; za tjedne odvoze u gradovima pravilo je primijenjeno "
    "na izračunate datume.",
    "Papir i plastika: uključen je raspored s letka za 1. srpnja – 31. prosinca 2026. Za siječanj–lipanj datumi nisu "
    "uključeni.",
    "Područja koja letak za papir i plastiku ne navodi (npr. Skenderovci, Bukovčani, Jagma, Korita) imaju samo "
    "miješani otpad; raspored papira i plastike provjerite kod davatelja (034 411 225).",
    "Reciklažno dvorište Pakrac (Ulica križnog puta 18): pon-pet 8-16, subota (prva i treća u mjesecu) 8-13 sati. "
    "Korisnici s područja Grada Lipika mogu koristiti i reciklažno dvorište u Lipiku.",
    "Glomazni otpad: jednom godišnje besplatan odvoz do 4 m³ po korisniku, po pozivu (034 411 225).",
]


def fold(s):
    """Lowercase without diacritics, for matching names written differently."""
    s = s.lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def street_key(name):
    """'I.Gundulića' and 'Ivana Gundulića' -> 'gundulica'; '103. brigade' -> '103 brigade'."""
    toks = re.findall(r"\d+|[^\W\d_]+", name.lower())
    if toks and all(len(t) == 1 for t in toks):  # Z.N.G. / ZNG
        return "".join(toks)
    key = toks[-1] if toks else ""
    key = STREET_ALIAS.get(key, key)
    if key in GENERIC and len(toks) > 1:
        key = toks[-2] + " " + key
    return fold(key)


def settlement_name(raw, known):
    """'G.OBRIJEŽ' -> 'Gornja Obrijež' using the known full names; 'DONJI ČAGLIĆ' -> 'Donji Čaglić'."""
    raw = " ".join(raw.replace(".", ". ").split())
    m = re.fullmatch(r"(\w)\. (.+)", raw)
    if m:
        cands = [k for k in known if fold(k).split()[-1] == fold(m.group(2)) and fold(k)[0] == fold(m.group(1))]
        if len(cands) == 1:
            return cands[0]
    full = next((k for k in known if fold(k) == fold(raw)), None)
    return full or " ".join(w.capitalize() for w in raw.lower().split())


def split_names(text):
    """'NOVI I STARI MAJUR, KUSONJE' -> ['NOVI MAJUR', 'STARI MAJUR', 'KUSONJE']."""
    out = []
    for part in re.split(r",\s*|\.\s+(?=[A-ZČĆŽŠĐ]\.)", text):
        part = part.strip(" .")
        m = re.fullmatch(r"(\w+) I (\w+) (\w+)", part)
        out += [f"{m.group(1)} {m.group(3)}", f"{m.group(2)} {m.group(3)}"] if m else [part] if part else []
    return out


def streets_of(text):
    """Comma list of streets; 'I.G.Kovačića. K.A.Stepinca' (a full stop for a comma) is split too."""
    return [s.strip(" .") for s in re.split(r",\s*|\.\s+(?=[A-ZČĆŽŠĐ]\.[A-ZČĆŽŠĐ])", text) if s.strip(" .")]


def plain(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def page_items(content):
    """<li> items of the post -> [{'kind': 'tjedno'|'ruta', 'weekday', 'grad', 'streets', 'settlements', 'pdfs'}]."""
    known = {n for g in GROUPS for n in g[3]} | LIPIK
    items = []
    for li in re.findall(r"<li>(.*?)</li>", content, re.S):
        text = plain(li)
        pdfs = [html.unescape(u) for u in re.findall(r'href="([^"]+\.pdf)"', li)]
        head, _, rule = text.rpartition("–")
        head, rule = head.strip(), rule.strip()
        day = next((d for k, d in DAYS.items() if k in rule.upper()), None)
        if day is None or not head:
            continue
        item = {"weekday": day, "streets": [], "settlements": [], "pdfs": pdfs, "text": text,
                "kind": "ruta" if "DRUG" in rule.upper() else "tjedno"}
        m = re.match(r"GRAD (PAKRAC|LIPIK)\s*(.*)", head)
        if m and "ULICE" in m.group(2):
            item["grad"] = m.group(1).capitalize()
            body = m.group(2).split(":", 1)[1]
            town, _, prek = body.partition("+ PREKOPAKRA")
            item["streets"] = streets_of(town)
            if prek:
                item["prekopakra"] = streets_of(prek.lstrip(" :"))
        else:
            if m:
                item["settlements"].append(m.group(1).capitalize())
                head = m.group(2).lstrip(", ")
            head = re.sub(r"\bNASELJ[EA]\b\s*", "", head)
            item["settlements"] += [settlement_name(n, known) for n in split_names(head)]
        items.append(item)
    return items


def route_dates(pdf, year, weekday, problems, name):
    """Dates from a route PDF: [(date, moved)]; moved = off the weekday right after a holiday."""
    text = "\n".join(p.extract_text() or "" for p in pdfplumber.open(pdf).pages)
    head = " ".join(text.split("I TO:")[0].split())
    if f"ZA {year}" not in head.replace(" .", "."):
        problems.append(f"{name}: PDF nije za {year}: {head[:80]!r}")
        return []
    if not any(k in head.upper() and v == weekday for k, v in DAYS.items()):
        problems.append(f"{name}: dan u PDF-u ne odgovara stranici ({head[:100]!r})")
    tokens = re.findall(r"\b(\d\d)\.(\d\d)\.", text.split("RASPORED ODVOZA PAPIRA")[0])
    hol = set(pravila.blagdani(year))
    out, odd = [], []
    for d, m in tokens:
        try:
            x = date(year, int(m), int(d))
        except ValueError:
            problems.append(f"{name}: nemoguć datum {d}.{m}.")
            continue
        moved = x.weekday() != weekday
        if moved and not any(0 < (x - h).days <= 2 for h in hol):
            # a single off-day date without a holiday is printed and kept as published (not marked as moved)
            print(f"UPOZORENJE {name[:40]}: {x:%d.%m.} ({podaci.DAYS[x.weekday()]}) nije {podaci.DAYS[weekday]} "
                  "ni dan nakon blagdana; zadržan datum iz PDF-a")
            odd.append(x)
            moved = False
        if x in hol or x.weekday() == 6:
            problems.append(f"{name}: {x:%d.%m.} je blagdan ili nedjelja")
        out.append((x, moved))
    out.sort()
    if len(odd) > 1:
        problems.append(f"{name}: više datuma izvan dana odvoza bez blagdana: {odd}")
    regular = [x for x, mv in out if not mv and x not in odd]
    for a, b in zip(regular, regular[1:]):
        if (b - a).days % 14:
            problems.append(f"{name}: {a:%d.%m.} -> {b:%d.%m.} nije svaki drugi tjedan")
    if len({x for x, _ in out}) != len(out):
        problems.append(f"{name}: datum dvaput")
    return out


def nth_working(year, month, dan, n):
    hol = set(pravila.blagdani(year))
    days = [d for d in pravila.tjedno(year, dan) if d.month == month and d not in hol]
    return days[0] if n == 1 else days[-1]


def leaflet_groups(year, problems):
    """{key: (jls, streets, settlements, rows, note)} from the transcription, checked against each rule."""
    out = {}
    if year != LEAFLET["year"]:
        return out
    for key, jls, streets, places, krule, kdates, prule, pdates in GROUPS:
        rows = []
        for code, rule, text in (("K", krule, kdates), ("P", prule, pdates)):
            dates = [date(year, int(m), int(d)) for d, m in re.findall(r"(\d\d)\.(\d\d)\.", text)]
            want = [nth_working(year, m, *rule) for m in range(LEAFLET["from_month"], 13)]
            if dates != want:
                problems.append(f"letak {key} {code}: {[f'{d:%d.%m.}' for d in dates]} ne odgovara pravilu {RULE_NAME[rule]}")
            rows += [(d, code) for d in dates]
        note = (f"Papir i plastika (od {LEAFLET['from_month']}. mjeseca): papir {RULE_NAME[krule]}, plastika "
                f"{RULE_NAME[prule]} u mjesecu.")
        out[key] = (jls, streets_of(streets), places, rows, note)
    return out


def leaflet_pdf(problems):
    """Download the leaflet named in LEAFLET from the media library and check its sha256."""
    items = json.loads(fetch(MEDIA))
    pinned = [m for m in items if m["source_url"].endswith("/" + LEAFLET["file"])]
    if not pinned:
        problems.append(f"letak {LEAFLET['file']} nije pronađen u medijima")
        return
    newer = [m["source_url"] for m in items if m["date"] > pinned[0]["date"]]
    if newer:
        print(f"UPOZORENJE: u medijima je noviji letak, provjerite ga: {newer}")
    urls = [pinned[0]["source_url"]]
    body = fetch(urls[0])
    digest = hashlib.sha256(body).hexdigest()
    if digest != LEAFLET["sha256"]:
        problems.append(f"slika se promijenila ({urls[0]}, sha256 {digest}): ponovo prepišite datume u GROUPS")
    return urls[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    post = json.loads(fetch(POST))
    if not post:
        sys.exit("Objava s rasporedom nije pronađena. Ništa nije upisano.")
    link, content = post[0]["link"], post[0]["content"]["rendered"]
    items = page_items(content)
    leaflet_url = leaflet_pdf(problems)
    groups = leaflet_groups(year, problems)
    by_street = {street_key(s): k for k, g in groups.items() for s in g[1]}
    by_place = {fold(p): k for k, g in groups.items() for p in g[2]}
    print("PRETPOSTAVKA: tjedni odvozi u gradovima pomiču se dan kasnije kad padnu na neradni dan "
          "(pravilo iz rasporeda za naselja).")

    zones = {}  # (jls, item index, group) -> zone
    with tempfile.TemporaryDirectory() as tmp:
        for i, it in enumerate(items):
            name = ", ".join(it["settlements"]) or f"Grad {it.get('grad')}"
            if it["kind"] == "ruta":
                pdfs = sorted(it["pdfs"], key=lambda u: re.search(r"/uploads/(\d{4}/\d\d)/", u).group(1), reverse=True)
                rows = []
                for url in pdfs[:2]:
                    pdf = Path(tmp) / url.rsplit("/", 1)[1]
                    fetch(url, pdf)
                    p = []
                    rows = route_dates(pdf, year, it["weekday"], p, name)
                    if rows:
                        problems += p
                        break
                if not rows:
                    problems.append(f"{name}: nema PDF-a s datumima za {year} ({pdfs})")
                    continue
                mrule = f"svaki drugi tjedan, {DAN_INS[it['weekday']]}"
            else:
                rows = pravila.primijeni_blagdane(pravila.tjedno(year, list(pravila.DANI)[it["weekday"]]), "sljedeci")
                mrule = f"svaki tjedan, {DAN_INS[it['weekday']]}"
            # split the area by town / settlement and paper-plastic group
            parts = []
            for s in it["streets"]:
                parts.append((it["grad"], by_street.get(street_key(s)), s, None))
            for s in it.get("prekopakra", []):
                parts.append(("Pakrac", by_place.get("prekopakra"), s, "Prekopakra"))
            for s in it["settlements"]:
                parts.append(("Lipik" if s in LIPIK else "Pakrac", by_place.get(fold(s)), None, s))
            for jls, g, street, place in parts:
                if street and g is None:
                    print(f"UPOZORENJE: ulica {street!r} ({name}) nije na letku za papir i plastiku")
                z = zones.setdefault((jls, i, g), {"jls": jls, "item": it, "group": g, "streets": [], "places": [],
                                                   "rows": rows, "mrule": mrule})
                if street:
                    z["streets"].append(street)
                if place and place not in z["places"]:
                    z["places"].append(place)

    out = []
    for jls in PROVIDER["jls"]:
        for (j, i, g), z in sorted(zones.items(), key=lambda kv: (kv[0][1], kv[0][2] or "Z")):
            if j != jls:
                continue
            it = z["item"]
            m_rows = [(d, "M", mv) for d, mv in z["rows"]]
            r_rows = [(d, c, False) for d, c in groups[g][3]] if g else []
            merged = {}
            for d, c, mv in m_rows + r_rows:
                cc, mm = merged.get(d, ("", False))
                merged[d] = (cc + c, mm or mv)
            rows = sorted((d, c, mv) for d, (c, mv) in merged.items())
            day = DAN_INS[it["weekday"]]
            if z["streets"]:
                lead = f"{jls} – miješani {day}" + (f", papir i plastika {DAN_INS[groups[g][3][0][0].weekday()]}" if g else "")
                names = z["places"] + z["streets"]
                podrucje = lead + ": " + ", ".join(names[:4]) + (", …" if len(names) > 4 else "")
            else:
                podrucje = ", ".join(z["places"])
            note = [f"Miješani komunalni otpad: {z['mrule']}; neradni dan → dan kasnije."]
            note.append(groups[g][4] if g else "Papir i plastika: ovo područje nije na letku za srpanj–prosinac; raspored "
                                               "provjerite kod davatelja (034 411 225).")
            if it["kind"] == "tjedno":
                note.append("Pomaci tjednih odvoza zbog blagdana izračunati su prema pravilu (dan kasnije).")
            ulice = list(dict.fromkeys(z["places"] + z["streets"]))
            out.append({"jls": jls, "podrucje": podrucje, "opis": it["text"], "ulice": ulice,
                        "napomena": " ".join(note), "rows": rows, "group": g, "kind": it["kind"]})

    for k, z in enumerate(out, 1):
        cnt = Counter((d.month, c) for d, codes, _ in z["rows"] for c in codes)
        weekly = z["kind"] == "tjedno"
        for month in range(1, 13):
            n = cnt.get((month, "M"), 0)
            if not (4 <= n <= 6 if weekly else 1 <= n <= 3):
                problems.append(f"zona {k} ({z['podrucje'][:40]}): {n}x M u mjesecu {month}")
            for code in "KP":
                if z["group"] and month >= LEAFLET["from_month"] and cnt.get((month, code), 0) != 1:
                    problems.append(f"zona {k}: {cnt.get((month, code), 0)}x {code} u mjesecu {month}")
        if not z["ulice"]:
            problems.append(f"zona {k}: nema ulica ni naselja")
    if len(out) < 10:
        problems.append(f"samo {len(out)} zona")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": link, "napomene": NAPOMENE + [f"Letak za papir i plastiku: {leaflet_url}"], "zone": {}}
    for k, z in enumerate(out, 1):
        rows = z.pop("rows")
        z.pop("group"), z.pop("kind")
        prev = old.get(str(k), {})
        raw = dict(prev.get("raw", {})) if prev.get("podrucje") == z["podrucje"] else {}
        if str(year) in raw:  # keep paper/plastic dates of earlier leaflets for months before this one
            merged = {d: (c, mv) for d, c, mv in rows}
            for d, c, mv in podaci.iter_dates(prev, year):
                kp = "".join(x for x in c if x in "KP")
                if d.month < LEAFLET["from_month"] and kp and kp[0] not in merged.get(d, ("",))[0]:
                    cc, mm = merged.get(d, ("", False))
                    merged[d] = (cc + kp, mm)
            rows = sorted((d, c, mv) for d, (c, mv) in merged.items())
        z["raw"] = {**raw, str(year): podaci.month_lines(rows)}
        data["zone"][str(k)] = z
        cnt = Counter(c for _, codes, _ in rows for c in codes)
        print(f"Zona {k} ({z['jls']}): {z['podrucje'][:70]}: " + ", ".join(f"{c} {cnt[c]}" for c in "MPK" if cnt[c])
              + f", pomaknuto {sum(1 for *_, m in rows if m)}, ulica/naselja {len(z['ulice'])}")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(out)} zona)")


if __name__ == "__main__":
    main()
