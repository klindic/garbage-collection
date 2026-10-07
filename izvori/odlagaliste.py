"""Odlagalište d.o.o. (Nova Gradiška): Nova Gradiška, Cernik, Dragalić, Gornji Bogićevci, Rešetari, Stara
Gradiška, Staro Petrovo Selo; dates computed from the company's weekday rules.

    python3 -m izvori.odlagaliste [--year 2026]

The page "Raspored odvoza otpada" links four PDFs from 2022, read with pdfplumber: the town streets per
mixed waste weekday (MKO-Grad), the villages per weekday with their municipality (a table whose day and
municipality cells span several rows, read from the cell rectangles), and two notices with the recycling
rules: in town paper on the n-th Tuesday and plastic on the n-th Thursday of the month by the street's
mixed waste day, in the villages paper on the n-th Monday and plastic on the n-th Wednesday by
municipality. The year's dates are computed with pravila.py. Holiday shifts are published only as news
posts ("Nadoknada odvoza otpada", WordPress REST API); every shift found for the year is applied and marked
as moved, other holidays keep the regular day.
"""
import argparse
import html
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "odlagaliste"
SITE = "https://odlagaliste.hr"
PAGE = SITE + "/raspored-odvoza-otpada/"
POSTS = SITE + "/wp-json/wp/v2/posts?search={q}&per_page=50&after={after}&_fields=id,date,link,title,content"
PDFS = {"grad": r"MKO-Grad", "sela": r"Raspored-odvoza-po-danima", "rec_grad": r"RECIKLAZE-GRAD",
        "rec_sela": r"RECIKLAZE-SELA"}
KEY = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DAY_WORD = {"PONEDJELJAK": 0, "UTORAK": 1, "SRIJEDA": 2, "ČETVRTAK": 3, "PETAK": 4}
ON_DAY = {"ponedjeljkom": 0, "utorkom": 1, "srijedom": 2, "četvrtkom": 3, "petkom": 4}
NTH = {"prvi": 1, "drugi": 2, "treći": 3, "četvrti": 4, "prvog": 1, "drugog": 2, "trećeg": 3, "četvrtog": 4,
       "prvu": 1, "drugu": 2, "treću": 3, "četvrtu": 4}
JLS_LABEL = {"Grad NG": "Nova Gradiška", "Općina Staro Petrovo Selo": "Staro Petrovo Selo",
             "Općina Dragalić": "Dragalić", "Općina Cernik": "Cernik", "Općina Rešetari": "Rešetari",
             "Općina Stara Gradiška": "Stara Gradiška", "Općina Gornji Bogićevci": "Gornji Bogićevci",
             "Općina Vrbova": "Staro Petrovo Selo"}  # Vrbova is a village of Staro Petrovo Selo
EXPAND = {"G. D. Crnogovci": ["Gornji Crnogovci", "Donji Crnogovci"], "D. Bogićevci": ["Donji Bogićevci"],
          "D.G.N. Varoš": ["Donji Varoš", "Gornji Varoš", "Novi Varoš"]}
JLS = ["Nova Gradiška", "Cernik", "Dragalić", "Gornji Bogićevci", "Rešetari", "Stara Gradiška",
       "Staro Petrovo Selo"]
PROVIDER = {
    "davatelj": "Odlagalište d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Brodsko-posavska",
    "jls": JLS,
}
NAPOMENE = [
    "Datumi su izračunati iz pravila objavljenih 2022. (raspored miješanog otpada po danima i obavijesti o "
    "odvozu reciklabilnog otpada); pretpostavlja se da i dalje vrijede.",
    "Spremnike treba iznijeti večer prije dana odvoza ili najkasnije do 5 sati na dan odvoza.",
    "Tržnica u Novoj Gradiškoj: miješani otpad ponedjeljkom, srijedom i petkom (navedena je u tri zone).",
]


def plain(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def pdf_text(path):
    with pdfplumber.open(path) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def split_commas(text):
    """Split on commas outside parentheses: 'K.Dijeneša (br.9,9/1,9/2), M.Bauera' -> 2 items."""
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [s for s in (" ".join(x.split()).rstrip(".").strip() for x in out + [cur]) if s]


def street(name):
    """'A.K.Miočića' -> 'A. K. Miočića', 'Psunjska ( zapadno od potoka)' -> 'Psunjska (zapadno od potoka)'."""
    name = re.sub(r"\.(?=[A-ZČĆŠŽĐ])", ". ", name)
    return re.sub(r"\(\s+", "(", name)


def town_streets(text, problems):
    """{weekday: [streets]} from the MKO-Grad PDF."""
    parts = re.split(r"(Ponedjeljkom|Utorkom|Srijedom|Četvrtkom|Petkom) se miješani komunalni otpad \(MKO\) "
                     r"sakuplja u(?: sljedećim ulicama)?:", " ".join(text.split()))
    out = {ON_DAY[d.lower()]: [street(s) for s in split_commas(body)] for d, body in zip(parts[1::2], parts[2::2])}
    if sorted(out) != [0, 1, 2, 3, 4] or any(len(v) < 3 for v in out.values()):
        problems.append(f"MKO-Grad: dani {sorted(out)}")
    return out


def village_table(path, problems):
    """[(weekday, jls label, settlement)] from the village table (day and municipality cells span rows)."""
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        rects = page.rects
    head = next((w for w in words if w["text"] == "NASELJE"), None)
    jls_head = next((w for w in words if w["text"] == "JLS"), None)
    if not head or not jls_head:
        problems.append("tablica sela: nema zaglavlja DAN / NASELJE / JLS")
        return []
    x_name = head["x0"] - 3
    x_jls = min((r["x0"] for r in rects  # vertical line between NASELJE and JLS
                 if r["height"] > 300 and r["width"] < 3 and x_name + 50 < r["x0"] < jls_head["x0"]),
                default=jls_head["x0"] - 60)
    seps = sorted({round(r["top"]) for r in rects  # full-width lines between the weekdays
                   if r["width"] > 300 and r["height"] < 4 and r["top"] > head["bottom"]})
    groups = [r for r in rects if r["fill"] and abs(r["x0"] - x_name) < 5 and r["width"] > 200 and r["height"] > 10]
    days = [(w["top"], DAY_WORD[w["text"]]) for w in words if w["text"] in DAY_WORD and w["x1"] < x_name]
    lines = defaultdict(list)
    for w in words:
        if x_name <= w["x0"] < x_jls and w["top"] > head["bottom"]:
            lines[round(w["top"])].append(w)
    out = []
    for top, ws in sorted(lines.items()):
        name = " ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"]))
        band = [(a, b) for a, b in zip(seps, seps[1:]) if a <= top < b]
        day = [d for t, d in days if band and band[0][0] <= t < band[0][1]]
        cell = [g for g in groups if g["top"] - 1 <= top < g["bottom"]]
        label = " ".join(w["text"] for w in words if cell and w["x0"] >= x_jls and
                         cell[0]["top"] <= w["top"] < cell[0]["bottom"])
        if len(day) != 1 or len(cell) != 1 or label not in JLS_LABEL:
            problems.append(f"tablica sela: {name!r} dan {day}, JLS {label!r}")
            continue
        out.append((day[0], label, name))
    return out


def town_rules(text, problems):
    """{(code, weekday group): (n, weekday)} from the town recycling notice; group = MKO weekday or 'Ljupina'."""
    t = " ".join(text.split())
    i = t.find("PLASTIKA")
    rules = {}
    for code, part, dname in (("K", t[:i], "utorak"), ("P", t[i:], "četvrtak")):
        pat = (rf"(Prvi|Drugi|Treći|Četvrti) {dname} u mjesecu\s*[–-]\s*ulice kojima se otpad "
               rf"(?:sakuplja|odvozi) (\w+(?: i \w+)?)\s*(\+ Ljupina|\(bez Ljupine\))?")
        for nth, who, extra in re.findall(pat, part):
            for w in who.split(" i "):
                rules[(code, ON_DAY[w])] = (NTH[nth.lower()], pravila.DANI[dname[:3]])
            if extra == "+ Ljupina":
                rules[(code, "Ljupina")] = (NTH[nth.lower()], pravila.DANI[dname[:3]])
    if {k[1] for k in rules if k[0] == "K"} != {k[1] for k in rules if k[0] == "P"} or len(rules) != 12:
        problems.append(f"obavijest grad: pravila {rules}")
    return rules


def village_rules(text, problems):
    """{jls: {code: (n, weekday)}} from the village recycling notice."""
    t = " ".join(text.split())
    out = {}
    for names, body in re.findall(r"Općina ([^.]+?) (Papir se sakuplja.+?)(?= Općina |$)", t):
        k = re.search(r"(prvog|drugog|trećeg|četvrtog) ponedjeljka u mjesecu", body)
        p = re.search(r"plastika će se sakupljati svaki mjesec (prvu|drugu|treću|četvrtu) srijedu", body)
        if not k or not p:
            problems.append(f"obavijest sela: {names}: {body[:120]!r}")
            continue
        for n in names.split(", "):
            out[n] = {"K": (NTH[k.group(1)], 0), "P": (NTH[p.group(1)], 2)}
    if sorted(out) != sorted(j for j in JLS if j != "Nova Gradiška"):
        problems.append(f"obavijest sela: općine {sorted(out)}")
    return out


def holiday_moves(year, problems):
    """{(code, old date): new date} from 'Nadoknada odvoza otpada' posts about the year."""
    posts = {}
    for q in ("nadoknada", "odvoz"):
        for p in json.loads(fetch(POSTS.format(q=q, after=f"{year - 1}-10-01T00:00:00"))):
            posts[p["id"]] = p
    hol = set(pravila.blagdani(year))
    moves, used = {}, []
    pat = (r"(OTPAD|PLASTIK\w*|PAPIR\w*)\s+KOJ\w+\s+SE\s+TREBA\s+SAKUPLJATI\s+U\s+(\w+)\s+"
           r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.?\s+SAKUPLJATI\s+ĆE\s+SE\s+U\s+(\w+)\s+(\d{1,2})\.(\d{1,2})\.(\d{4})")
    for post in sorted(posts.values(), key=lambda p: p["date"]):
        title, text = plain(post["title"]["rendered"]), plain(post["content"]["rendered"])
        found = re.findall(pat, text.upper())
        if "nadoknada" in title.lower() and not found and str(year) in text:
            problems.append(f"obavijest {post['link']}: ne razumijem: {text[:200]!r}")
        for what, d1, dd, mm, yy, d2, dd2, mm2, yy2 in found:
            old, new = date(int(yy), int(mm), int(dd)), date(int(yy2), int(mm2), int(dd2))
            if old.year != year:
                continue
            if KEY[old.weekday()] != d1.lower()[:3] or KEY[new.weekday()] != d2.lower()[:3] or old not in hol \
                    or abs((new - old).days) > 6:
                problems.append(f"obavijest {post['link']}: {old} ({d1}) -> {new} ({d2}) nije vjerojatno")
                continue
            code, label = {"O": ("M", "miješani otpad"), "P": ("P", "plastika")}[what[0]] \
                if not what.startswith("PAPIR") else ("K", "papir")
            moves[(code, old)] = new
            used.append(f"{old:%d.%m.} → {new:%d.%m.} ({label})")
            print(f"Obavijest {post['date'][:10]}: {what} {old} -> {new}")
    return moves, used


def zone_rows(year, mko_day, rules, moves, problems, key):
    """[(date, codes, moved)] for weekly MKO on mko_day (or None) and monthly (n, weekday) rules."""
    rows = [(d, "M") for d in pravila.tjedno(year, KEY[mko_day])] if mko_day is not None else []
    for code, (n, wd) in rules.items():
        rows += [(d, code) for d in pravila.mjesecno(year, KEY[wd], n)]
    merged = {}
    for d, code in rows:
        new = moves.get((code, d), d)
        codes, moved = merged.get(new, ("", False))
        if code in codes:
            problems.append(f"zona {key}: {code} dvaput {new}")
        merged[new] = (codes + code, moved or new != d)
    got = Counter(c for codes, _ in merged.values() for c in codes)
    for code, n in got.items():
        if not (50 <= n <= 53 if code == "M" else n == 12):
            problems.append(f"zona {key}: {code} {n} puta")
    return [(d, c, m) for d, (c, m) in merged.items()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []

    page = fetch(PAGE).decode("utf-8", "replace")
    links = {}
    for key, pat in PDFS.items():
        found = sorted(set(re.findall(rf'href="([^"]*{pat}[^"]*\.pdf)"', page, re.I)))
        if not found:
            sys.exit(f"Na {PAGE} nema PDF-a {pat}")
        links[key] = found[-1]
    with tempfile.TemporaryDirectory() as tmp:
        paths = {}
        for key, url in links.items():
            paths[key] = Path(tmp) / f"{key}.pdf"
            fetch(url, paths[key])
        streets = town_streets(pdf_text(paths["grad"]), problems)
        villages = village_table(paths["sela"], problems)
        trules = town_rules(pdf_text(paths["rec_grad"]), problems)
        vrules = village_rules(pdf_text(paths["rec_sela"]), problems)
    print("PDF-ovi: " + ", ".join(links.values()))
    moves, used = holiday_moves(year, problems)

    zones, totals = {}, Counter()

    def add(jls, podrucje, ulice, mko_day, rules, napomena=None):
        key = str(len(zones) + 1)
        rows = zone_rows(year, mko_day, rules, moves, problems, key)
        totals.update(c for _, codes, _ in rows for c in codes)
        zone = {"jls": jls, "podrucje": podrucje, "ulice": ulice}
        if napomena:
            zone["napomena"] = napomena
        zone["raw"] = {str(year): podaci.month_lines(rows)}
        zones[key] = zone

    def rule_text(rules):
        return ", ".join(f"{'papir' if c == 'K' else 'plastika'} {n}. {DAN[wd]} u mjesecu"
                         for c, (n, wd) in sorted(rules.items(), key=lambda kv: "KP".index(kv[0])))

    # town: one zone per MKO weekday, Ljupina apart (its recycling days differ)
    village_ng = {name for _, label, name in villages if label == "Grad NG"}
    for wd in range(5):
        names = [s for s in streets.get(wd, []) if s != "Ljupina"]
        rules = {c: trules[(c, wd)] for c in "KP" if (c, wd) in trules}
        if len(rules) != 2:
            problems.append(f"grad {DAN[wd]}: nema pravila za papir/plastiku")
            continue
        add("Nova Gradiška", f"Nova Gradiška, {DAN[wd]} – miješani svaki {DAN[wd]}; {rule_text(rules)}".replace(
            "svaki srijeda", "svaku srijedu"), names, wd, rules)
        if "Ljupina" in streets.get(wd, []):
            rules = {c: trules[(c, "Ljupina")] for c in "KP"}
            add("Nova Gradiška", f"Ljupina – miješani svaki {DAN[wd]}; {rule_text(rules)}", ["Ljupina"], wd, rules)
    all_town = {s for v in streets.values() for s in v}
    for name in village_ng - all_town:
        problems.append(f"{name} (Grad NG u tablici sela) nije u popisu ulica grada")

    # villages: one zone per weekday and municipality
    groups = defaultdict(list)
    for wd, label, name in villages:
        if label != "Grad NG":
            groups[(JLS_LABEL[label], wd)].extend(EXPAND.get(name, [name]))
    for (jls, wd), names in sorted(groups.items(), key=lambda kv: (JLS.index(kv[0][0]), kv[0][1])):
        if jls not in vrules:
            problems.append(f"{jls}: nema pravila za papir/plastiku")
            continue
        note = "U rasporedu je Vrbova navedena kao \"Općina Vrbova\"; naselje pripada Općini Staro Petrovo Selo." \
            if "Vrbova" in names else None
        add(jls, f"{jls}, {DAN[wd]} – {', '.join(names)}; miješani svaki {DAN[wd]}, {rule_text(vrules[jls])}"
            .replace("svaki srijeda", "svaku srijedu"), names, wd, vrules[jls], note)
    for jls in JLS[1:]:
        if jls in vrules and not any(j == jls for j, _ in groups):
            add(jls, f"Općina {jls} – {rule_text(vrules[jls])}", [f"{jls} (cijela općina)"], None, vrules[jls],
                f"Dan odvoza miješanog otpada za naselja Općine {jls} nije naveden u rasporedu po danima; "
                "navedeni su samo papir i plastika.")

    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    hol = [h for h in pravila.blagdani(year) if h.weekday() < 6]
    done = {old for _, old in moves}
    data = {**PROVIDER, "napomene": NAPOMENE + [
        f"Blagdani {year}.: pomaci se objavljuju samo kao obavijesti na odlagaliste.hr. Primijenjene obavijesti: "
        + ("; ".join(used) if used else "nema") + ". Za ostale blagdane ("
        + ", ".join(f"{h:%d.%m.}" for h in hol if h not in done)
        + ") obavijest nije pronađena, pa je naveden redovni dan odvoza.",
        "Izvori: " + ", ".join(links.values()) + ".",
    ], "zone": zones}
    out = podaci.PODACI / f"{SLUG}.json"
    if out.exists():  # keep other years of zones that did not change
        old = podaci.load(out)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("ulice") == zone["ulice"]:
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    print("Pretpostavka: pravila iz 2022. vrijede i za " + str(year) + ".")
    print(f"Zona: {len(zones)}; odvoza {year} (zbroj po zonama): "
          + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {out.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
