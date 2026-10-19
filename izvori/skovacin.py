"""Rogoznica: Škovacin d.o.o. (skovacin.hr), mixed waste by weekday and recyclables by date, per group of settlements.

    python3 -m izvori.skovacin [--year 2026]

The page "Prikupljanje otpada" links the article "Raspored odvoza otpada za <year>. godinu" with one
PDF. Page 1 gives the mixed waste weekdays of three groups of settlements (day names on the left, the
settlements on the right; every text line belongs to the nearest day label). Page 2 is a table of
recyclable collection dates: four bands of settlements on the left (kept apart by the blank space
between them) and the dates "13.1." under the month columns (each date must sit under its own month).
Each band of page 2 is a zone; its mixed waste days are those of its settlements on page 1 (all of them
must agree). Holidays: page 1 says that from 01.10. to 01.05. mixed waste is not collected on public
holidays but the next day (applied and marked as moved; when the next day is a Sunday or another
holiday the first working day after it is assumed); in summer collection runs on holidays. Recyclable
dates already carry their holiday shifts; a date off the band's usual weekday is marked as moved.
"""
import argparse
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "skovacin"
SITE = "https://www.skovacin.hr"
PAGE = SITE + "/prikupljanje-otpada.php"
DAYS = {"ponedjeljak": "pon", "utorak": "uto", "srijeda": "sri", "četvrtak": "čet", "petak": "pet", "subota": "sub"}
MONTHS = ["Siječanj", "Veljača", "Ožujak", "Travanj", "Svibanj", "Lipanj", "Srpanj", "Kolovoz", "Rujan",
          "Listopad", "Studeni", "Prosinac"]
RULE = r"od (\d\d)\.(\d\d)\.\s*-\s*(\d\d)\.(\d\d)\. otpad se neće odvoziti blagdanima i državnim praznicima"
PROVIDER = {
    "davatelj": "Škovacin d.o.o.",
    "web": SITE,
    "zupanija": "Šibensko-kninska",
    "jls": ["Rogoznica"],
    "nazivi": {"P": "Reciklabilni otpad"},
}
NAPOMENE = [
    "Od 01.10. do 01.05. miješani komunalni otpad ne odvozi se blagdanima i državnim praznicima, nego idući "
    "dan (označeno kao pomaknuto); ljeti se odvozi i blagdanom.",
    "Odvoz reciklabilnog otpada prema datumima iz rasporeda (ljeti češće); pomaci zbog blagdana su upisani.",
    "Kontakt: +385 22 558 427, k.usluga@skovacin.hr.",
]


def norm(text):
    return re.sub(r"[\s-]+", "", text).lower()


def settlements(text):
    """'Zatoglav,Stupin, Rogoznica ( Lozica-Podgruda, Put magistrale), ...' -> names; a part in brackets
    stays with its name. Commas inside 'Nova A,B,C' do not split."""
    out, depth, cur = [], 0, ""
    for i, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0 and re.match(r"\s*[A-ZČĆŠŽĐ][a-zčćšžđ]", text[i + 1:]):
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [re.sub(r"\(\s+", "(", " ".join(x.split())) for x in out if x.strip()]


def expand(names):
    """'Rogoznica (Crljina I-XII, Nova A,B,C)' -> ['Rogoznica – Crljina I-XII', 'Rogoznica – Nova A,B,C']."""
    out = []
    for n in names:
        m = re.fullmatch(r"(.+?)\s*\((.+)\)", n)
        out += [f"{m.group(1)} – {x.strip()}" for x in re.split(r",\s+", m.group(2))] if m else [n]
    return out


def lines(words):
    rows = {}
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        key = next((k for k in rows if abs(k - w["top"]) < 3), w["top"])
        rows.setdefault(key, []).append(w)
    return [(k, " ".join(v["text"] for v in sorted(ws, key=lambda v: v["x0"]))) for k, ws in sorted(rows.items())]


def mixed_groups(page):
    """{normalised settlement: weekday keys} from page 1, and problems."""
    words = page.extract_words()
    title = next(w for w in words if w["text"] == "MIJEŠANOG")
    stop = next((w["top"] for w in words if w["text"] == "od" and w["top"] > title["top"]), page.height)
    body = [w for w in words if title["bottom"] < w["top"] < stop - 2]
    labels = [w for w in body if w["text"].rstrip(",").lower() in DAYS]
    if len(labels) != 6:
        return {}, [f"str. 1: {len(labels)} naziva dana, očekivano 6"]
    split = min(w["x0"] for w in body if w not in labels and w["x0"] > max(v["x1"] for v in labels)) - 5
    groups = []  # [(centre y, day keys)] from the label pairs, top to bottom
    for a, b in zip(*[iter(sorted(labels, key=lambda w: w["top"]))] * 2):
        groups.append(((a["top"] + b["bottom"]) / 2, f"{DAYS[a['text'].rstrip(',').lower()]} "
                                                       f"{DAYS[b['text'].rstrip(',').lower()]}"))
    text = {g: "" for _, g in groups}
    for y, line in lines([w for w in body if w["x0"] >= split]):
        g = min(groups, key=lambda c: abs(c[0] - y))[1]
        text[g] += " " + line
    out = {}
    for g, t in text.items():
        for s in settlements(t):
            out[norm(s)] = g
    return out, []


def recycling(page, year):
    """[(settlements, {date})] per band of page 2, and problems."""
    words = page.extract_words()
    heads = {MONTHS.index(w["text"]) + 1: (w["x0"] + w["x1"]) / 2 for w in words if w["text"] in MONTHS}
    if sorted(heads) != list(range(1, 13)):
        return [], [f"str. 2: zaglavlje mjeseci {sorted(heads)}"]
    head_y = max(w["bottom"] for w in words if w["text"] in MONTHS)
    left = min(heads.values()) - 30
    stop = next((w["top"] for w in words if "@" in w["text"] and w["top"] > head_y), page.height)
    body = [w for w in words if head_y < w["top"] < stop - 2]
    label_lines = lines([w for w in body if w["x1"] < left])
    bands = []
    for y, line in label_lines:
        if bands and y - bands[-1][1] < 20:
            bands[-1] = (bands[-1][0], y, bands[-1][2] + " " + line)
        else:
            bands.append((y, y, line))
    # dates: join pieces the PDF split ("8." "7.", "18.1" "2.")
    toks = []
    for w in sorted((w for w in body if w["x0"] >= left), key=lambda w: (round(w["top"]), w["x0"])):
        if toks and abs(toks[-1]["top"] - w["top"]) < 2 and w["x0"] - toks[-1]["x1"] < 6:
            toks[-1] = {**toks[-1], "text": toks[-1]["text"] + w["text"], "x1": w["x1"]}
        else:
            toks.append(dict(w))
    out, problems = [(settlements(text), {}) for _, _, text in bands], []
    for t in toks:
        m = re.fullmatch(r"(\d{1,2})\.\s?(\d{1,2})\.?", t["text"])
        if not m:
            problems.append(f"str. 2: nepoznat zapis {t['text']!r}")
            continue
        day, month = map(int, m.groups())
        col = min(heads, key=lambda k: abs(heads[k] - (t["x0"] + t["x1"]) / 2))
        if col != month:
            problems.append(f"str. 2: {t['text']} u stupcu {MONTHS[col - 1]}")
        band = min(range(len(bands)), key=lambda i: abs((bands[i][0] + bands[i][1]) / 2 - t["top"]))
        try:
            d = date(year, month, day)
        except ValueError:
            problems.append(f"str. 2: nemoguć datum {t['text']}")
            continue
        if d in out[band][1]:
            problems.append(f"str. 2: {d} dvaput u skupini {band + 1}")
        out[band][1][d] = True
    return out, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    listing = fetch(PAGE).decode("utf-8", "replace")
    art = re.search(rf'href="(clanak\.php\?id=\d+)">\s*Raspored odvoza otpada za {year}\. godinu', listing)
    if not art:
        sys.exit(f"Na {PAGE} nema članka s rasporedom za {year}")
    article = SITE + "/" + art.group(1)
    pdfs = re.findall(rf'href="([^"]+/{year}/raspored/[^"]+\.pdf)"', fetch(article).decode("utf-8", "replace"))
    if len(set(pdfs)) != 1:
        sys.exit(f"U članku {article} nije točno jedan PDF rasporeda: {pdfs}")
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raspored.pdf"
        fetch(pdfs[0], path)
        pdf = pdfplumber.open(path)
        text1 = " ".join(pdf.pages[0].extract_text().split())
        groups, probs = mixed_groups(pdf.pages[0])
        problems += probs
        bands, probs = recycling(pdf.pages[1], year)
        problems += probs
    if f"OTPADA ZA {year}. GODINU" not in text1:
        problems.append(f"str. 1: naslov nije za {year}")
    rule = re.search(RULE, text1)
    if not rule:
        problems.append("str. 1: nema pravila za blagdane")
        winter = lambda d: False  # noqa: E731
    else:
        a, b = (int(rule.group(2)), int(rule.group(1))), (int(rule.group(4)), int(rule.group(3)))
        winter = lambda d: (d.month, d.day) >= a or (d.month, d.day) <= b  # noqa: E731
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))
    data = {**PROVIDER, "izvor": article, "napomene": list(NAPOMENE), "zone": {}}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    if len(bands) != 4:
        problems.append(f"str. 2: {len(bands)} skupina naselja, očekivano 4")
    for i, (names, rec) in enumerate(bands, 1):
        z = str(i)
        # page 2 can word a bracketed list slightly differently ("Ražanjska ulica"): compare its start
        days = {next((g for k, g in groups.items() if k[:16] == norm(n)[:16]), None) for n in names}
        if len(days) != 1 or None in days:
            problems.append(f"skupina {z}: naselja nisu u istoj skupini miješanog otpada na str. 1 ({names})")
            continue
        day_keys = days.pop()
        rows = {}
        for key in day_keys.split():
            for d in pravila.tjedno(year, key):
                if d in hol and winter(d):
                    (new, _), = pravila.primijeni_blagdane([d], "sljedeci", year)
                    rows.setdefault(new, ["", True])[0] += "M"
                    print(f"   zona {z}: blagdan {d:%d.%m.} -> miješani {new:%d.%m.}"
                          + (" (pretpostavka: prvi radni dan)" if (new - d).days > 1 else ""))
                else:
                    rows.setdefault(d, ["", False])[0] += "M"
        usual = Counter(d.weekday() for d in rec).most_common(1)[0][0]
        for d in rec:
            off = d.weekday() != usual
            if off and not any(abs((d - h).days) <= 3 for h in hol):
                problems.append(f"zona {z} {d}: reciklabilni nije na uobičajeni dan, a nema blagdana blizu")
            if off:
                print(f"   zona {z}: reciklabilni {d:%d.%m.} (pomak zbog blagdana)")
            rows.setdefault(d, ["", off])[0] += "P"
            rows[d][1] = rows[d][1] or off
        for m in range(1, 13):
            n = Counter(c for d, (codes, _) in rows.items() if d.month == m for c in codes)
            want = 2 if m in (1, 2, 3, 4, 5, 6, 9, 10, 11, 12) else 4
            if not 7 <= n["M"] <= 10 or not want <= n["P"] <= 5:
                problems.append(f"zona {z} {year}-{m:02d}: {dict(n)}")
        names_txt, names = ", ".join(names), expand(names)
        day_txt = " i ".join(podaci.DAYS[pravila.DANI[k]] for k in day_keys.split())
        data["zone"][z] = {
            "jls": "Rogoznica", "podrucje": f"{day_txt.capitalize()} – " + re.sub(r", (\w+) – ", ", ", ", ".join(names[:3])) + ", …",
            "opis": names_txt, "ulice": names,
            "raw": {**old["zone"].get(z, {}).get("raw", {}),
                    str(year): podaci.month_lines([(d, c, mv) for d, (c, mv) in rows.items()])},
        }
        print(f"zona {z} ({day_txt}; {names[0]}, …): {len(rows)} dana, reciklabilni {len(rec)}")
    for p in problems:
        print(f"PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
