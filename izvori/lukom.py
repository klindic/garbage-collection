"""Ludbreg, Mali Bukovec, Veliki Bukovec, Sveti Đurđ: Lukom d.o.o. Ludbreg (lukom.hr), one PDF per area.

    python3 -m izvori.lukom [--year 2026]

The "Raspored odvoza otpada za YYYY. godinu" page (linked from the "Otpad" page) lists one PDF per group
of settlements, per group of Ludbreg streets and per municipality, plus one for occasional users. Each
PDF (a Word table) has waste types as rows and months as columns; a cell holds one or more dd.mm. dates
stacked over several lines, so every date is placed by its word position (pdfplumber): row band between
the table rules, month from the nearest column header. The PDFs already include holiday shifts: a date
off the row's usual weekday with a public holiday within a week is marked as moved. Plastic and metal
are separate bins (P and L); baby nappies (special bags) go into the zone note. The page also publishes
Općina Martijanec, which the JLS registry lists under PRE-KOM, so those PDFs (and the Martijanec
settlement Poljanec inside a Ludbreg group) are left out. The mobile recycling yard dates go into the
zone note.
"""
import argparse
import html
import re
import sys
import tempfile
import urllib.parse
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "lukom"
SITE = "https://www.lukom.hr"
OTPAD = SITE + "/?u=otpad&id=114"
MONTHS = ["Siječanj", "Veljača", "Ožujak", "Travanj", "Svibanj", "Lipanj", "Srpanj", "Kolovoz",
          "Rujan", "Listopad", "Studeni", "Prosinac"]
ROWS = [("Miješani", "M"), ("Biootpad", "B"), ("Papir", "K"), ("Plastika", "P"), ("Metal", "L"),
        ("Staklo", "S"), ("Tekstil", "T"), ("pelene", "N")]  # N: nappies, kept for the note only
# collections per month (min, max) for each type in a full-year table
PER_MONTH = {"M": (1, 3), "B": (1, 3), "K": (1, 2), "P": (1, 2), "L": (1, 2), "S": (0, 1), "T": (0, 1)}
SETTLEMENTS = {
    "Ludbreg": ["Apatija", "Bolfan", "Čukovec", "Globočec Ludbreški", "Hrastovsko", "Kućan Ludbreški", "Ludbreg",
                "Segovina", "Selnik", "Sigetec Ludbreški", "Slokovec", "Vinogradi Ludbreški"],
    "Sveti Đurđ": ["Hrženica", "Karlovec Ludbreški", "Komarnica Ludbreška", "Luka Ludbreška", "Obrankovec",
                   "Priles", "Sesvete Ludbreške", "Struga", "Sveti Đurđ"],
    "Mali Bukovec": ["Lunjkovec", "Mali Bukovec", "Martinić", "Novo Selo Podravsko", "Sveti Petar", "Županec"],
    "Veliki Bukovec": ["Dubovica", "Kapela Podravska", "Veliki Bukovec"],
    "Martijanec": ["Čičkovina", "Gornji Martijanec", "Hrastovljan", "Križovljan", "Madaraševec", "Martijanec",
                   "Poljanec", "Rivalno", "Slanje", "Sudovčina", "Vrbanovec"],
}
ALIAS = {"Globočec": "Globočec Ludbreški", "Sigetec": "Sigetec Ludbreški", "Kućan": "Kućan Ludbreški"}
JLS_ORDER = ["Ludbreg", "Mali Bukovec", "Veliki Bukovec", "Sveti Đurđ"]
PROVIDER = {
    "davatelj": "Lukom d.o.o. Ludbreg",
    "web": SITE,
    "zupanija": "Varaždinska",
    "jls": JLS_ORDER,
    "nazivi": {"P": "Plastika", "L": "Metal"},
}
NAPOMENE = [
    "Datumi u rasporedu već uključuju pomake zbog blagdana; takvi su datumi označeni kao pomaknuti.",
    "Reciklažno dvorište „Meka”, Ludbreg, Ulica 5. studenog 31: pon 12-16, sri 8-15, čet 10-18, sub 8-12 sati.",
    "Glomazni otpad: jednom godišnje besplatno do 4 m³, na zahtjev (obrazac na lukom.hr, lukom@lukom.hr).",
    "Informacije: 042 819 106, lukom@lukom.hr.",
    "Lukom objavljuje i raspored za Općinu Martijanec; u registru JLS Martijanec je kod PRE-KOM-a, pa ta "
    "područja (i naselje Poljanec iz rasporeda Bolfan, Čukovec, Segovina, Poljanec) nisu uključena.",
]


def page_links(year):
    """(page url, [(link text, pdf url)]) of the year page, in page order."""
    otpad = fetch(OTPAD).decode("utf-8", "replace")
    m = re.search(rf'href="([^"]*za(?:%20| ){year}\.(?:%20| )godinu[^"]*)"', otpad)
    if not m:
        return None, []
    url = urllib.parse.urljoin(SITE + "/", html.unescape(m.group(1)).replace(" ", "%20"))
    body = fetch(url).decode("utf-8", "replace")
    links = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', body, re.S):
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        href = urllib.parse.urljoin(SITE + "/", html.unescape(href))
        if "/userfiles/files/" in href and (href, text) not in [(u, t) for t, u in links]:
            links.append((text, href))
    return url, links


def names_in(text):
    """Settlement names in a link or PDF title: [(name as written, jls)]."""
    out = []
    for jls, names in SETTLEMENTS.items():
        for n in names + [a for a, full in ALIAS.items() if full in names]:
            for m in re.finditer(rf"(?<![\wČĆŽŠĐčćžšđ]){re.escape(n)}(?![\wčćžšđ])", text, re.I):
                out.append((m.start(), m.end(), n, jls))
    out.sort(key=lambda t: (t[0], -t[1]))
    picked, end = [], -1
    for s, e, n, jls in out:
        if s >= end:
            picked.append((n, jls))
            end = e
    return picked


def read_table(pdf, year):
    """First page: title, {code: [dates]}, mobile yard note, problems."""
    page = pdfplumber.open(pdf).pages[0]
    words = page.extract_words()
    problems = []
    heads = {w["text"]: (w["x0"] + w["x1"]) / 2 for w in words if w["text"] in MONTHS}
    if len(heads) != 12:
        return None, {}, "", [f"zaglavlja mjeseci: {sorted(heads)}"]
    rules = []
    for y in sorted(r["top"] for r in page.rects if r["height"] < 2 and r["width"] > 30):
        if not rules or y - rules[-1] > 2:
            rules.append(y)
    title = " ".join(w["text"] for w in words if w["bottom"] < rules[0])
    if f"za {year}. godinu" not in title:
        problems.append(f"naslov nije za {year}: {title!r}")
    left = min(heads.values()) - 30
    rows = {}
    for top, bottom in zip(rules, rules[1:]):
        band = [w for w in words if top < (w["top"] + w["bottom"]) / 2 < bottom]
        label = " ".join(w["text"] for w in sorted(band, key=lambda w: (w["top"], w["x0"])) if w["x1"] < left)
        if label.startswith("Vrsta otpada"):
            continue
        code = next((c for key, c in ROWS if key.lower() in label.lower()), None)
        if code is None:
            problems.append(f"nepoznat red {label!r}")
            continue
        for w in band:
            if w["x1"] < left:
                continue
            m = re.fullmatch(r"(\d\d)\.(\d\d)\.?", w["text"])
            if not m:
                problems.append(f"{label}: nepoznata riječ {w['text']!r}")
                continue
            day, month = map(int, m.groups())
            head = min(heads, key=lambda h: abs(heads[h] - (w["x0"] + w["x1"]) / 2))
            if MONTHS.index(head) + 1 != month:
                problems.append(f"{label}: {w['text']} stoji u stupcu {head}")
                continue
            try:
                rows.setdefault(code, []).append(date(year, month, day))
            except ValueError:
                problems.append(f"{label}: nemoguć datum {w['text']}")
    return title, rows, yard_note(page.extract_text() or ""), problems


def yard_note(text):
    """'Mobilno reciklažno dvorište: Hrženica, kod društvenog doma (08:00-09:00): 20.01., 14.04., ...; ...'."""
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        m = re.match(r"MOBILNO RECIKLAŽNO DVORIŠTE\s*[–-]\s*(.+)", line)
        t = re.match(r"Radno vrijeme\s*:\s*(.+?)\s*sati\s+((?:\d\d\.\d\d\.,?\s*)+)$", lines[i + 1].strip())
        if m and t:
            head, _, rest = m.group(1).strip().partition(",")
            if title_case(head) in SETTLEMENTS["Martijanec"]:
                continue
            place = title_case(head) + ("," + rest if rest else "")
            out.append(f"{place} ({' '.join(t.group(1).split())}): {' '.join(t.group(2).split()).rstrip(',')}")
    return ("Mobilno reciklažno dvorište – " + "; ".join(out)).rstrip(".") + "." if out else ""


def mark_moved(code, dates, problems, name):
    """[(date, moved)]: a date off the usual weekday is moved when a holiday is within a week of it."""
    hol = pravila.blagdani(dates[0].year)
    usual = Counter(d.weekday() for d in dates).most_common(1)[0][0]
    out = []
    for d in sorted(dates):
        moved = d.weekday() != usual
        if moved and not any(abs((d - h).days) <= 7 for h in hol):
            problems.append(f"{name} {code}: {d:%d.%m.} ({podaci.DAYS[d.weekday()]}) nije uobičajeni dan "
                            f"({podaci.DAYS[usual]}), a nema blagdana u blizini")
        if d.weekday() == 6 or d in hol:
            problems.append(f"{name} {code}: {d:%d.%m.} je nedjelja ili blagdan")
        out.append((d, moved))
    if len(set(dates)) != len(dates):
        problems.append(f"{name} {code}: isti datum dvaput")
    return out


def occasional(pdf, year):
    """'Povremeni korisnici' PDF -> [(area title, [streets], [dates])], problems."""
    text = "\n".join(p.extract_text() or "" for p in pdfplumber.open(pdf).pages)
    if f"{year}" not in text:
        return [], [f"povremeni korisnici: nema godine {year}"]
    out, problems = [], []
    for head, body in re.findall(r"^(NASELJ[EA] [^\n]+?)\n(.*?)(?=^NASELJ|^VAŽNO|\Z)", text, re.S | re.M):
        m = re.search(r"DATUM ODVOZA:\s*(.+)", body, re.S)
        if not m:
            problems.append(f"povremeni korisnici {head}: nema datuma")
            continue
        dates = []
        for d, mo in re.findall(r"(\d\d)\.(\d\d)\.", m.group(1)):
            try:
                dates.append(date(year, int(mo), int(d)))
            except ValueError:
                problems.append(f"povremeni korisnici {head}: nemoguć datum {d}.{mo}.")
        streets = " ".join(body[:m.start()].split())
        streets = [s.strip(" .") for s in streets.split(",") if s.strip(" .")]
        out.append((" ".join(head.split()).rstrip(":"), streets, dates))
    return out, problems


def title_case(s):
    return " ".join(w if w.lower() in ("i",) else w.capitalize() for w in s.lower().split())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    page, links = page_links(year)
    if not page or len(links) < 15:
        sys.exit(f"Stranica rasporeda za {year}. nije pronađena ili ima samo {len(links)} PDF-ova. Ništa nije upisano.")
    problems, zones, skipped = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for text, url in links:
            found = names_in(text)
            jls = {j for _, j in found}
            if "povremen" in text.lower():
                pdf = Path(tmp) / url.rsplit("/", 1)[1]
                fetch(url, pdf)
                areas, p = occasional(pdf, year)
                problems += p
                for head, streets, dates in areas:
                    places = [n for n, j in names_in(head)]
                    name = ", ".join(places)
                    rows = mark_moved("M", dates, problems, f"povremeni {name}")
                    zones.append(("Ludbreg", {
                        "podrucje": f"Povremeni korisnici – {name}" + (f" ({', '.join(streets[:3])}, …)" if streets else ""),
                        "opis": head + (": " + ", ".join(streets) if streets else ""),
                        "ulice": streets + places if streets else places,
                        "napomena": "Samo za povremene korisnike: miješani komunalni otpad u vrećama od 60 l (vreće se "
                                    "podižu u Lukomu, Koprivnička 17, ili se šalju na adresu).",
                        "rows": [(d, "M", mv) for d, mv in rows]}))
                continue
            if jls == {"Martijanec"}:
                skipped.append(text)
                continue
            if not text.lower().startswith("ulica") and (not jls or len(jls - {"Martijanec"}) != 1):
                problems.append(f"{text}: općina nije prepoznata ({sorted(jls)})")
                continue
            pdf = Path(tmp) / url.rsplit("/", 1)[1]
            fetch(url, pdf)
            if not pdf.read_bytes().startswith(b"%PDF"):
                problems.append(f"{url}: nije PDF")
                continue
            title, rows, yard, p = read_table(pdf, year)
            name = re.split(r"\s[–-]\s", title or "", maxsplit=1)[-1].strip() if title else text
            problems += [f"{name}: {x}" for x in p]
            if p or not rows:
                continue
            if name.lower().startswith("ulica"):
                jls_name, streets = "Ludbreg", [s.strip() for s in re.sub(r"^ulica\s+", "", name, flags=re.I).split(",") if s.strip()]
                podrucje = "Ludbreg – " + ", ".join(streets[:4]) + ", …"
            elif name.lower().startswith("općina"):
                jls_name = next((j for j in JLS_ORDER if j.lower() in name.lower()), None)
                if not jls_name:
                    problems.append(f"{name}: općina nije prepoznata")
                    continue
                streets, podrucje = SETTLEMENTS[jls_name], "Općina " + jls_name
            else:
                found = [(n, j) for n, j in names_in(name) if j != "Martijanec"]
                if len({j for _, j in found}) != 1:
                    problems.append(f"{name}: općina nije prepoznata ({found})")
                    continue
                jls_name, streets = found[0][1], [n for n, _ in found]
                podrucje = ", ".join(streets)
            notes = []
            diapers = sorted(rows.pop("N", []))
            if diapers:
                same = diapers == sorted(rows.get("M", []))
                notes.append("Dječje pelene (posebne vreće) odvoze se " + ("istim danima kao miješani komunalni otpad."
                                if same else "ovim danima: " + ", ".join(f"{d:%d.%m.}" for d in diapers) + "."))
            if any(j == "Martijanec" for _, j in names_in(name)):
                notes.append("Isti raspored objavljen je i za naselje Poljanec (Općina Martijanec), koje ovdje nije uključeno.")
            if yard:
                notes.append(yard)
            all_rows = {}
            for code, dates in rows.items():
                for d, mv in mark_moved(code, dates, problems, name):
                    c, m = all_rows.get(d, ("", False))
                    all_rows[d] = (c + code, m or mv)
                per = Counter(d.month for d in dates)
                lo, hi = PER_MONTH[code]
                bad = {m: per.get(m, 0) for m in range(1, 13) if not lo <= per.get(m, 0) <= hi}
                if bad:
                    problems.append(f"{name} {code}: broj odvoza po mjesecima {bad}")
            zones.append((jls_name, {"podrucje": podrucje, "opis": title, "ulice": streets,
                                     "napomena": " ".join(notes),
                                     "rows": sorted((d, c, m) for d, (c, m) in all_rows.items())}))
    for t in skipped:
        print(f"Preskočeno (Općina Martijanec, u registru PRE-KOM): {t}")
    for _, z in zones:
        if not z["ulice"]:
            problems.append(f"{z['podrucje']}: nema ulica ni naselja")
    if {j for j, _ in zones} != set(JLS_ORDER):
        problems.append(f"općine: {sorted({j for j, _ in zones})}")
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "izvor": page, "napomene": NAPOMENE, "zone": {}}
    ordered = [z for j in JLS_ORDER for z in [z for jj, z in zones if jj == j and not z["podrucje"].startswith("Povremeni")]
               + [z for jj, z in zones if jj == j and z["podrucje"].startswith("Povremeni")]]
    for i, z in enumerate(ordered, 1):
        jls = next(j for j, zz in zones if zz is z)
        rows = z.pop("rows")
        prev = old.get(str(i), {})
        raw = {y: v for y, v in prev.get("raw", {}).items() if y != str(year)} if prev.get("podrucje") == z["podrucje"] else {}
        zone = {"jls": jls, **{k: v for k, v in z.items() if v}, "raw": {**raw, str(year): podaci.month_lines(rows)}}
        data["zone"][str(i)] = zone
        cnt = Counter(c for _, codes, _ in rows for c in codes)
        print(f"Zona {i} ({jls}): {zone['podrucje'][:60]}: " + ", ".join(f"{c} {cnt[c]}" for c in podaci.ORDER if cnt[c])
              + f", pomaknuto {sum(1 for *_, m in rows if m)}")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(ordered)} zona)")


if __name__ == "__main__":
    main()
