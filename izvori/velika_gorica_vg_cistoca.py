"""Velika Gorica, Orle, Kravarsko, Pokupsko: VG Čistoća d.o.o. (vgcistoca.hr).

    python3 -m izvori.velika_gorica_vg_cistoca [--year 2026]

Grad Velika Gorica: the year PDF linked from "Raspored odvoza za YYYY. godinu" lists rules per area
in two columns ("Miješani komunalni otpad – svaki utorak", "Plastika – 2. i 4. utorak u mjesecu"):
the town's family houses and apartment buildings, and 18 zones of settlements. The columns are kept
apart by the x position of the words (pdfplumber); footnotes change the meaning of marked rows
(* no biowaste in that settlement, *** a rule that differs by part of the zone). The holiday rule is
the rotated text at the page edge, read with plain pdftotext.
Orle, Kravarsko and Pokupsko: one colour-coded year calendar per municipality, read with
kalendar_boje.read_page (each day number must sit in its weekday column).
"""
import argparse
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch
from kalendar_boje import colour, read_page

SLUG = "velika-gorica-vg-cistoca"
SITE = "https://www.vgcistoca.hr"
PAGE = SITE + "/usluge/raspored-odvoza-za-{year}-godinu/"
MUNICIPALITY = SITE + "/opcine/{slug}/"
MUNICIPALITIES = {"orle": "Orle", "kravarsko": "Kravarsko", "pokupsko": "Pokupsko"}
TYPES = {"miješani komunalni otpad": "M", "biootpad": "B", "papir": "K", "plastika": "P", "staklo": "S"}
TYPE_NAME = {"M": "miješani", "B": "biootpad", "K": "papir", "P": "plastika", "S": "staklo"}
DAYS = {"ponedjeljak": "pon", "utorak": "uto", "srijeda": "sri", "srijedu": "sri", "četvrtak": "čet",
        "petak": "pet", "subota": "sub", "subotu": "sub", "nedjelja": "ned", "nedjelju": "ned"}
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
HEADS = {"PO": "PON", "UT": "UTO", "SR": "SRI", "ČE": "ČET", "PE": "PET", "SU": "SUB", "NE": "NED"}
LEGEND = {("miješani", "komunalni"): "M", ("otpadna", "ambalaža"): "P", ("otpadni", "papir"): "K"}
FOOT_BIO = "biootpad se iz ovih naselja ne odvozi"
FOOT_LEGAL = "vrijedi i za pravne subjekte"
PROVIDER = {
    "davatelj": "VG Čistoća d.o.o.",
    "web": SITE,
    "zupanija": "Zagrebačka",
    "jls": ["Velika Gorica", "Orle", "Kravarsko", "Pokupsko"],
    "nazivi": {"P": "Plastika (otpadna ambalaža)"},
    "bioNapomena": "Biootpad se odvozi samo u naseljima i dijelovima grada navedenima u rasporedu.",
}
NAPOMENE = [
    "Otpad se odvozi od ponedjeljka do petka od 6 do 18 sati.",
    "Glomazni otpad: na poziv 01 6566 749 ili u mobilnom reciklažnom dvorištu prema rasporedu u PDF-u.",
    "Reciklažna dvorišta: Ulica Franje Boška Kirinčića 10, Velika Gorica; Mraclinska Dubrava, Mraclin.",
]


def naslov(name):
    """'LAZI TUROPOLJSKI' -> 'Lazi Turopoljski'."""
    return " ".join("i" if w.lower() == "i" else w.capitalize() for w in name.split())


def settlements(text):
    """'VELIKA I MALA KOSNICA, PETINA' -> [('Velika Kosnica', False), ('Mala Kosnica', False), ('Petina', False)].

    The flag is True for names marked with a single '*'.
    """
    out = []
    for part in text.split(","):
        part = " ".join(part.split())
        if not part:
            continue
        star = part.endswith("*")
        part = part.rstrip("*").strip()
        m = re.fullmatch(r"(\w+) I (\w+) (\w+)", part)
        names = [f"{m.group(1)} {m.group(3)}", f"{m.group(2)} {m.group(3)}"] if m else [part]
        out += [(naslov(n), star) for n in names]
    return out


def rule(text):
    """'2. i 4. utorak u mjesecu' -> [('m', 2, 'uto'), ('m', 4, 'uto')]; 'svaki ponedjeljak i četvrtak' ->
    [('t', 0, 'pon'), ('t', 0, 'čet')]; 'nema odvoza' -> []. None if not understood.
    The last item of '1. utorak i 3. četvrtak i petak' gets the number of the one before it.
    """
    t = " ".join(text.lower().split())
    if t == "nema odvoza":
        return []
    m = re.fullmatch(r"svak[iu] (\w+)(?: i (\w+))?", t)
    if m:
        days = [d for d in m.groups() if d]
        return [("t", 0, DAYS[d]) for d in days] if all(d in DAYS for d in days) else None
    if not t.endswith(" u mjesecu"):
        return None
    out, pending, last = [], [], None
    for part in t[:-len(" u mjesecu")].split(" i "):
        m = re.fullmatch(r"(?:(\d)\.\s*)?(\w+)?", part.strip())
        if not m or not (m.group(1) or m.group(2)) or m.group(2) and m.group(2) not in DAYS:
            return None
        n, day = m.group(1), m.group(2)
        if n and not day:
            pending.append(int(n))
        elif n:
            out += [("m", x, DAYS[day]) for x in pending + [int(n)]]
            pending, last = [], int(n)
        elif last:
            out.append(("m", last, DAYS[day]))
        else:
            return None
    return out if out and not pending else None


def rule_text(items):
    if not items:
        return "nema odvoza"
    if items[0][0] == "t":
        names = [DAN[pravila.DANI[d]] for _, _, d in items]
        acc = {"srijeda": "srijedu", "subota": "subotu", "nedjelja": "nedjelju"}
        return ("svaku " if names[0] in acc else "svaki ") + " i ".join(acc.get(n, n) for n in names)
    by_day = defaultdict(list)
    for _, n, d in items:
        by_day[d].append(n)
    return " i ".join(" i ".join(f"{n}." for n in ns) + f" {DAN[pravila.DANI[d]]}" for d, ns in by_day.items())


def alt_places(text, names):
    """'Pleso i Velika Mlaka zapadno od ...' with names [Velika Mlaka, Pleso] -> ['Pleso', 'Velika Mlaka zapadno od ...']."""
    out = []
    while True:
        n = next((n for n in names if re.match(rf"{re.escape(n)}(, | i )", text, re.I)), None)
        if not n:
            break
        out.append(n)
        text = re.sub(r"^(, | i )", "", text[len(n):]).strip()
    return out + [text]


def dates(year, items):
    out = []
    for kind, n, d in items:
        out += pravila.tjedno(year, d) if kind == "t" else pravila.mjesecno(year, d, n)
    return out


def column_lines(page):
    """Lines of the 9 pt upright text, per column (left/right of the page middle), top to bottom."""
    words = [w for w in page.extract_words(extra_attrs=["upright", "size"])
             if w["upright"] and abs(w["size"] - 9) < 0.15]
    split = page.width * 0.39
    cols = {"L": [], "R": []}
    for side, ws in (("L", [w for w in words if w["x0"] < split]), ("R", [w for w in words if w["x0"] >= split])):
        lines = []
        for w in sorted(ws, key=lambda w: (w["top"], w["x0"])):
            if lines and abs(lines[-1][0] - w["top"]) < 2.5:
                lines[-1][1].append(w)
            else:
                lines.append((w["top"], [w]))
        cols[side] = [" ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"])) for _, ws in lines]
    return cols


def parse_blocks(cols, problems):
    """[(key, heading, {code: (rule text, marker)})] in reading order, plus the footnote texts."""
    blocks, notes = [], []
    for side in ("L", "R"):
        cur, last_heading = None, False
        for line in cols[side]:
            m_zone = re.fullmatch(r"ZONA (\d+) – (.+)", line)
            m_rule = re.fullmatch(r"(Miješani komunalni otpad|Biootpad|Papir|Plastika|Staklo) – (.+?)\s*(\**)", line)
            if line == "STAMBENE ZGRADE – VELIKA GORICA":
                cur = ["Zgrade", "Velika Gorica, stambene zgrade", {}]
                blocks.append(cur)
                last_heading = False
            elif line == "OBITELJSKE KUĆE – VELIKA GORICA":
                cur = ["Grad", "Velika Gorica, obiteljske kuće", {}]
                blocks.append(cur)
                last_heading = False
            elif line == "STAMBENE ZGRADE I OBITELJSKE KUĆE IZVAN VELIKE GORICE":
                cur, last_heading = None, False
            elif m_zone:
                cur = [m_zone.group(1), m_zone.group(2), {}]
                blocks.append(cur)
                last_heading = True
            elif m_rule and cur is not None:
                code = TYPES[m_rule.group(1).lower()]
                if code in cur[2]:
                    problems.append(f"{cur[0]}: {m_rule.group(1)} dvaput")
                cur[2][code] = (m_rule.group(2), m_rule.group(3))
                last_heading = False
            elif last_heading and line.isupper():
                cur[1] += " " + line
            elif line.startswith("*"):
                notes += [n.strip() for n in re.split(r"(?=(?<!\*)\*+ )", line) if n.strip()]
                cur, last_heading = None, False
            elif notes and cur is None:
                notes[-1] += " " + line
            else:
                problems.append(f"nepoznat redak u PDF-u: {line!r}")
    return blocks, notes


def holiday_moves(raw, year, problems):
    """{holiday: Saturday} from 'Otpad se odvozi na sve blagdane ... osim: ... izvršit će se u subotu D. M.'."""
    text = " ".join(raw.split())
    i = text.find("Otpad se odvozi na sve blagdane i državne praznike osim")
    if i < 0:
        problems.append("u PDF-u nema pravila za blagdane")
        return {}, ""
    part = text[i:i + 600]
    end = part.find(" RASPORED ")
    part = part[:end] if end > 0 else part
    moves = {}
    hol = set(pravila.blagdani(year))
    pat = r"(\d{1,2})\.\s?(\d{1,2})\.\)?\s*(?:\([^)]*\)\s*)?izvršit će se u subotu (\d{1,2})\.\s?(\d{1,2})\."
    for d1, m1, d2, m2 in re.findall(pat, part):
        old, new = date(year, int(m1), int(d1)), date(year, int(m2), int(d2))
        if old not in hol or new.weekday() != 5 or abs((new - old).days) > 6:
            problems.append(f"pomak blagdana {old} -> {new} nije vjerojatan")
        moves[old] = new
    if part.count("izvršit će se") != len(moves) or not moves:
        problems.append(f"pravilo za blagdane nije potpuno pročitano: {part!r}")
    return moves, part


def city(year, problems):
    """Zones of Grad Velika Gorica from the year PDF: (zones, collections per type, holiday note, pdf url)."""
    html = fetch(PAGE.format(year=year)).decode("utf-8", "replace")
    links = re.findall(rf'href="([^"]*raspored-odvoza[^"]*{year}[^"]*\.pdf)"', html)
    if not links:
        sys.exit(f"Nema PDF-a s rasporedom za {year} na {PAGE.format(year=year)}")
    url = links[0] if links[0].startswith("http") else SITE + links[0]
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "raspored.pdf"
        fetch(url, pdf)
        raw = subprocess.run(["pdftotext", "-f", "1", "-l", "1", str(pdf), "-"],
                             capture_output=True, text=True, check=True).stdout
        with pdfplumber.open(pdf) as doc:
            cols = column_lines(doc.pages[0])
    if f"GRAD VELIKA GORICA – {year}. GODINA" not in raw:
        problems.append(f"PDF nije raspored za {year}: {url}")
    blocks, notes = parse_blocks(cols, problems)
    moves, holiday_text = holiday_moves(raw, year, problems)
    alt = {}  # *** notes: {weekday: area text}
    for line in raw.splitlines():
        m = re.fullmatch(r"\*\*\* (\w+): (.+?)\.?", line.strip())
        if m and m.group(1).lower() in DAYS:
            alt[DAYS[m.group(1).lower()]] = m.group(2)
    known = {FOOT_BIO: "*", FOOT_LEGAL: "**"}
    for n in notes:
        mark = n.split()[0]
        if not any(k in n and known[k] == mark for k in known):
            problems.append(f"nepoznata fusnota: {n!r}")
    used_marks = {m for b in blocks for _, m in b[2].values() if m} | ({"*"} if any("*" in b[1] for b in blocks) else set())
    for mark in used_marks:
        if mark == "***" and not alt:
            problems.append("oznaka *** bez objašnjenja")
        elif mark in ("*", "**") and not any(n.startswith(mark + " ") for n in notes):
            problems.append(f"oznaka {mark} bez objašnjenja")

    keys = [b[0] for b in blocks]
    numbered = [k for k in keys if k.isdigit()]
    if sorted(keys) != sorted(["Zgrade", "Grad"] + [str(i) for i in range(1, len(numbered) + 1)]) or not numbered:
        problems.append(f"zone u PDF-u: {keys}")

    zones = {}
    totals = Counter()
    for key, heading, rules in sorted(blocks, key=lambda b: (b[0] != "Grad", b[0] != "Zgrade",
                                                             int(b[0]) if b[0].isdigit() else 0)):
        parsed = {}
        for code, (text, mark) in rules.items():
            r = rule(text)
            if r is None:
                problems.append(f"zona {key}: nepoznato pravilo {TYPE_NAME[code]} – {text!r}")
                continue
            parsed[code] = (r, mark)
        if "M" not in parsed:
            problems.append(f"zona {key}: nema miješanog otpada")
            continue
        if key in ("Grad", "Zgrade"):
            places = [(heading, False)]
            parts = [(key, places, None)]
        else:
            places = settlements(heading)
            if not places:
                problems.append(f"zona {key}: nema naselja")
            plain = [p for p in places if not p[1]]
            starred = [p for p in places if p[1]]
            parts = [(key, plain, None)] if plain else []
            if starred:
                parts.append((key + "b" if plain else key, starred, ("bez_bio",)))
        if any(m == "***" for _, m in parsed.values()):
            if len(parts) != 1 or len(alt) < 2:
                problems.append(f"zona {key}: *** se ne može razdvojiti")
                continue
            names = [p[0] for p in parts[0][1]]
            for n in names:
                if not any(n.lower() in a.lower() for a in alt.values()):
                    problems.append(f"zona {key}: {n} nije u objašnjenju oznake ***")
            parts = [(key + "ab"[i], [(a, False) for a in alt_places(text, names)], ("alt", day))
                     for i, (day, text) in enumerate(sorted(alt.items(), key=lambda kv: pravila.DANI[kv[0]]))]
        for zkey, places, special in parts:
            rows, desc = [], []
            for code, (items, mark) in sorted(parsed.items(), key=lambda kv: "MBPKS".index(kv[0])):
                if special == ("bez_bio",) and code == "B":
                    items = []
                if special and special[0] == "alt" and mark == "***":
                    alts = set(alt)
                    items = [it for it in items if it[2] not in alts or it[2] == special[1]]
                if items:
                    rows += [(d, code) for d in dates(year, items)]
                desc.append(f"{TYPE_NAME[code]} {rule_text(items)}")
                if items and any(it[0] == "m" and it[1] > 4 for it in items):
                    problems.append(f"zona {zkey}: {code} peti tjedan")
            expect_n = Counter(code for _, code in rows)
            merged = defaultdict(lambda: ["", False])
            for (d, code) in rows:
                new = moves.get(d, d)
                if code in merged[new][0]:
                    problems.append(f"zona {zkey}: {code} dvaput {new}")
                merged[new][0] += code
                merged[new][1] = merged[new][1] or d in moves
            for d, (codes, moved) in merged.items():
                if d.weekday() == 6:
                    problems.append(f"zona {zkey}: nedjelja {d}")
                if moved and d.weekday() != 5:
                    problems.append(f"zona {zkey}: pomaknuto na {d}, a nije subota")
            got = Counter(c for codes, _ in merged.values() for c in codes)
            if got != expect_n:
                problems.append(f"zona {zkey}: {dict(got)} umjesto {dict(expect_n)}")
            for code, n in got.items():
                if not 12 <= n <= 106:
                    problems.append(f"zona {zkey}: {code} {n} puta")
            totals.update(got)
            ulice = [p[0] for p in places]
            short = [re.sub(r" (istočn|zapadn|sjevern|južn)o od .*", r" (\1i dio)", u) for u in ulice]
            label = ", ".join(short[:3]) + (" …" if len(short) > 3 else "")
            zone = {"jls": "Velika Gorica", "podrucje": f"{label} – " + "; ".join(desc)}
            if key in ("Grad", "Zgrade"):
                zone["ulice"] = [re.sub(r", (.*)", r" (\1)", ulice[0])]
            else:
                zone["opis"] = f"Zona {key}: " + ", ".join(ulice)
                zone["ulice"] = ulice
            notes_z = []
            if special == ("bez_bio",) and "B" in parsed and parsed["B"][0]:
                notes_z.append("Biootpad se iz ovih naselja ne odvozi (oznaka * u rasporedu).")
            if any(m == "**" for _, m in parsed.values()):
                notes_z.append("Raspored miješanog otpada vrijedi i za pravne subjekte, osim ako nije ugovorom "
                               "drukčije određeno.")
            if key == "Zgrade":
                notes_z.append("Raspored za stambene zgrade u Velikoj Gorici.")
            if notes_z:
                zone["napomena"] = " ".join(notes_z)
            zone["raw"] = {str(year): podaci.month_lines([(d, c, m) for d, (c, m) in merged.items()])}
            zones[zkey] = zone
    names = Counter(u for z in zones.values() for u in z.get("ulice", []))
    for n, c in names.items():
        if c > 1:
            problems.append(f"{n} je u {c} zone")
    holiday = ("Blagdani: " + holiday_text + " (izvor: PDF rasporeda; pomaknuti odvozi označeni su kao pomaknuti)."
               if holiday_text else "")
    return zones, totals, holiday, url


class Headings:
    """A pdfplumber page whose two-letter weekday headings (Po Ut Sr Če Pe Su Ne) read as PON ... NED."""

    def __init__(self, page):
        self._page = page

    def crop(self, bbox):
        return Headings(self._page.crop(bbox))

    def extract_words(self):
        return [{**w, "text": HEADS.get(w["text"].upper(), w["text"])} for w in self._page.extract_words()]

    @property
    def rects(self):
        return self._page.rects

    @property
    def curves(self):
        return self._page.curves


def municipality(slug, name, year, problems):
    """One zone from the municipality's colour calendar; (zone, totals, url) or (None, ...)."""
    html = fetch(MUNICIPALITY.format(slug=slug)).decode("utf-8", "replace")
    links = re.findall(rf'href="([^"]*raspored-odvoza-otpada-{slug}-{year}\.pdf)"', html)
    if not links:
        problems.append(f"{name}: nema kalendara za {year} na {MUNICIPALITY.format(slug=slug)}")
        return None, Counter(), None
    url = links[0] if links[0].startswith("http") else SITE + links[0]
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / f"{slug}.pdf"
        fetch(url, pdf)
        with pdfplumber.open(pdf) as doc:
            page = next((p for p in doc.pages if "RASPORED ODVOZA KOMUNALNOG OTPADA" in (p.extract_text() or "")
                         and f"OPĆINA {name.upper()} – {year}. GODINA" in (p.extract_text() or "")), None)
            if page is None:
                problems.append(f"{name}: u PDF-u nema stranice s rasporedom za {year}")
                return None, Counter(), url
            words = page.extract_words()
            palette = {}
            for (label, second), code in LEGEND.items():
                w = next((w for i, w in enumerate(words[:-1]) if w["text"] == label
                          and words[i + 1]["text"] == second and abs(words[i + 1]["top"] - w["top"]) < 2), None)
                boxes = [s for s in page.rects if w and s.get("fill") and colour(s)
                         and 0 <= w["x0"] - s["x1"] < 30 and s["top"] - 3 <= (w["top"] + w["bottom"]) / 2 <= s["bottom"] + 3]
                if not boxes:
                    problems.append(f"{name}: u legendi nema boje za {label}")
                    continue
                palette[colour(boxes[0])] = code
            first = min((w["top"] for w in words if w["text"] == "SIJEČANJ"), default=None)
            legend_top = min((w["top"] for w in words if w["text"] in {k[0] for k in LEGEND}), default=None)
            if first is None or legend_top is None or len(palette) != 3:
                problems.append(f"{name}: ne prepoznajem izgled kalendara")
                return None, Counter(), url
            x0, _, x1, _ = page.bbox
            found, probs = read_page(Headings(page), year, palette, bbox=(x0, first - 5, x1, legend_top - 5))
    problems += [f"{name}: {p}" for p in probs]
    if probs:
        return None, Counter(), url
    hol = pravila.blagdani(year)
    usual = {code: {wd for wd, n in Counter(d.weekday() for d, c in found.items() if c == code).items() if n >= 4}
             for code in "MPK"}
    rows = []
    for d, code in found.items():
        off = d.weekday() not in usual[code]
        if off and not any(abs((d - h).days) <= 6 for h in hol):
            problems.append(f"{name}: {d} {code} izvan uobičajenog dana, a nema blagdana blizu")
        rows.append((d, code, off))
    got = Counter(c for _, c, _ in rows)
    for code in "MPK":
        if got[code] < 12:
            problems.append(f"{name}: {code} samo {got[code]} puta")
    pairs = sum(1 for d, c in found.items() if c == "M" and found.get(d + timedelta(days=1)) == "M")
    zone = {"jls": name, "podrucje": f"Općina {name} – prema godišnjem kalendaru VG Čistoće",
            "ulice": [f"{name} (cijela općina)"]}
    if pairs >= 10:
        zone["napomena"] = ("Kalendar označava odvoz miješanog otpada na dva uzastopna dana; "
                            "pomaknuti odvozi zbog blagdana označeni su prema kalendaru.")
    zone["raw"] = {str(year): podaci.month_lines(rows)}
    return zone, got, url


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    zones, totals, holiday, url = city(year, problems)
    print(f"Grad Velika Gorica: {len(zones)} zona iz {url}")
    sources = [url]
    for slug, name in MUNICIPALITIES.items():
        zone, got, murl = municipality(slug, name, year, problems)
        if zone:
            zones[slug.capitalize()] = zone
            totals.update(got)
            sources.append(murl)
            print(f"Općina {name}: {sum(got.values())} odvoza ({dict(got)}) iz {murl}")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    data = {**PROVIDER, "izvor": PAGE.format(year=year), "napomene": NAPOMENE + [
        holiday,
        "Orle, Kravarsko i Pokupsko: datumi iz godišnjih kalendara općina (" + ", ".join(sources[1:]) + ").",
    ], "zone": zones}
    path = podaci.PODACI / f"{SLUG}.json"
    if path.exists():  # keep other years of zones that did not change
        old = podaci.load(path)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("podrucje") == zone["podrucje"] and prev.get("ulice") == zone.get("ulice"):
                zone["raw"] = {**prev["raw"], **zone["raw"]}
    print(f"Zona: {len(zones)}; odvoza {year} (zbroj po zonama): "
          + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
