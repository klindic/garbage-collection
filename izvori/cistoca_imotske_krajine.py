"""Čistoća Imotske krajine d.o.o. (Imotski): recyclables (bags) in Podbablje, Cista Provo and Runovići.

    python3 -m izvori.cistoca_imotske_krajine [--year 2026]

The company publishes one Word file per municipality and period ("OPCINA-PODBABLJE-RASPORED-ODVOZA-OD-05-2026-
DO-04-2027.docx", spring to spring), found through the WordPress media list (/cms/wp-json/wp/v2/media). Each
file is one table (word/document.xml, <w:tr>/<w:t>): a date, "PAPIR I TEKSTIL" (blue and orange bag) or
"PLASTIKA I STAKLO" (yellow and brown bag), once a month each, a week apart. Every file whose period reaches
into the year is read (the previous period gives January to spring), a newer file replaces older dates in
its own period, and the years it covers are written (e.g. 2026 and 2027). Holidays: the tables already
carry the shifts (e.g. Thursday 19.11. after the 18.11. holiday); a date off the usual weekday within six
days of a public holiday is marked as moved. No mixed waste schedule is published.
"""
import argparse
import calendar
import html
import json
import re
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cistoca-imotske-krajine"
SITE = "https://cistoca-imotske-krajine.hr/cms"
MEDIA = SITE + "/wp-json/wp/v2/media?per_page=100&page={page}&_fields=id,date,source_url"
NAME = re.compile(r"OPCINA-(.+?)-RASPORED-ODVOZA-(?:OPORABLJIVOG-OTPADA-)?OD-(\d\d)-(\d{4})-DO-(\d\d)-(\d{4})"
                  r"(?:-\d+)?\.docx$", re.I)
JLS = {"PODBABLJE": "Podbablje", "CISTA-PROVO": "Cista Provo", "RUNOVIC": "Runovići", "IMOTSKI": "Imotski",
       "LOKVICICI": "Lokvičići", "LOVREC": "Lovreć", "PROLOZAC": "Proložac", "ZAGVOZD": "Zagvozd",
       "ZMIJAVCI": "Zmijavci"}
TYPES = {"PAPIR I TEKSTIL": "KT", "PLASTIKA I STAKLO": "PS"}
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
PROVIDER = {
    "davatelj": "Čistoća Imotske krajine d.o.o.",
    "web": "https://cistoca-imotske-krajine.hr",
    "izvor": SITE + "/",
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Podbablje", "Cista Provo", "Runovići"],
    "nazivi": {"K": "Papir (plava vreća)", "T": "Tekstil (narančasta vreća)", "P": "Plastika (žuta vreća)",
               "S": "Staklo (smeđa vreća)"},
}


def read_docx(path, problems, label):
    """(municipality text, (first month, last month), [(date, codes)]) from one schedule file."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    texts = lambda part: html.unescape("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", part)))
    plist = [" ".join(texts(p).split()) for p in
             re.findall(r"<w:p[ >].*?</w:p>", re.sub(r"<w:tbl>.*?</w:tbl>", "", xml, flags=re.S), re.S)]
    paras = " ".join(plist)
    who = next((m for m in (re.fullmatch(r"za općinu ([A-ZČĆŠŽĐ ]+)", p) for p in plist) if m), None)
    per = re.search(r"za period (\w+) (\d{4})\.\s*[–-]\s*(\w+) (\d{4})\.", paras)
    if not who or not per or per.group(1).upper() not in MONTHS or per.group(3).upper() not in MONTHS:
        problems.append(f"{label}: nema općine ili razdoblja: {paras[:200]!r}")
        return None, None, []
    span = (date(int(per.group(2)), MONTHS.index(per.group(1).upper()) + 1, 1),
            date(int(per.group(4)), MONTHS.index(per.group(3).upper()) + 1, 28))
    rows = []
    tables = re.findall(r"<w:tbl>.*?</w:tbl>", xml, re.S)
    if len(tables) != 1:
        problems.append(f"{label}: {len(tables)} tablica")
    for tr in re.findall(r"<w:tr[ >].*?</w:tr>", tables[0] if tables else "", re.S):
        cells = [" ".join(texts(tc).split()) for tc in re.findall(r"<w:tc>.*?</w:tc>", tr, re.S)]
        if cells[:3] == ["DATUM", "VRSTA OTPADA", "BOJA VREĆA"]:
            continue
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.?", cells[0] if cells else "")
        kinds = [c for c in cells[1:3] if c]
        if not m or len(kinds) != 1 or kinds[0] not in TYPES:
            problems.append(f"{label}: redak {cells}")
            continue
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if not span[0] <= d <= span[1].replace(day=calendar.monthrange(span[1].year, span[1].month)[1]):
            problems.append(f"{label}: {d} izvan razdoblja {per.group(0)}")
        rows.append((d, TYPES[kinds[0]]))
    return who.group(1).strip(), span, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []

    media, page = [], 1
    while True:
        batch = json.loads(fetch(MEDIA.format(page=page)))
        media += batch
        if len(batch) < 100:
            break
        page += 1
    files = defaultdict(list)  # municipality key -> [(upload date, url, end)]
    for m in media:
        name = m["source_url"].rsplit("/", 1)[1]
        hit = NAME.search(name)
        if hit:
            files[re.sub(r"(-\d{2,4})+$", "", hit.group(1).upper())].append(
                (m["date"], m["source_url"], date(int(hit.group(5)), int(hit.group(4)), 1), hit.group(2, 3, 4, 5)))
    for key, v in files.items():  # the same period uploaded twice: keep the newest upload
        newest = {}
        for item in sorted(v):
            newest[item[3]] = item
        files[key] = [item[:3] for item in newest.values()]
    # municipalities with a schedule reaching at least into the second half of the year
    current = {k: v for k, v in files.items() if any(end >= date(year, 6, 1) for _, _, end in v)}
    expired = {JLS.get(k, k): max(end for _, _, end in v) for k, v in files.items() if k not in current}
    unknown = [k for k in files if k not in JLS]
    if unknown:
        problems.append(f"nepoznate općine u nazivima datoteka: {unknown}")

    zones, totals, sources = {}, Counter(), []
    hol = {h for y in (year - 1, year, year + 1, year + 2) for h in pravila.blagdani(y)}
    with tempfile.TemporaryDirectory() as tmp:
        for key in sorted(current, key=lambda k: list(JLS).index(k) if k in JLS else 99):
            jls = JLS.get(key, key)
            dates = {}
            for uploaded, url, end in sorted(current[key]):  # older first, newer replaces its own period
                if end.year < year:
                    continue
                path = Path(tmp) / url.rsplit("/", 1)[1]
                fetch(url, path)
                who, span, rows = read_docx(path, problems, path.name)
                if who and who.replace(" ", "-").replace("Ć", "C") not in (key, key + "I"):
                    problems.append(f"{path.name}: općina u tekstu je {who}")
                if not rows:
                    continue
                first, last = min(d for d, _ in rows), max(d for d, _ in rows)
                dates = {d: c for d, c in dates.items() if not first <= d <= last}
                for d, codes in rows:
                    if d in dates:
                        problems.append(f"{jls}: {d} dvaput")
                    dates[d] = codes
                sources.append(url)
                print(f"{jls}: {url.rsplit('/', 1)[1]} ({len(rows)} odvoza, {first} – {last})")
            dates = {d: c for d, c in dates.items() if d.year >= year}
            if not dates:
                continue
            usual = Counter(d.weekday() for d in dates).most_common(1)[0][0]
            rows, moved = [], []
            for d, codes in sorted(dates.items()):
                mv = d.weekday() != usual
                if mv and not any(abs((d - h).days) <= 6 for h in hol):
                    problems.append(f"{jls}: {d} ({DAN[d.weekday()]}) nije {DAN[usual]}, a nema blagdana blizu")
                if mv:
                    moved.append(d)
                rows.append((d, codes, mv))
            per_month = Counter((d.year, d.month, c) for d, c, _ in rows)
            if any(n > 2 for n in per_month.values()):
                problems.append(f"{jls}: više od dva odvoza iste vrste u mjesecu")
            got = Counter(c for _, c, _ in rows)
            if got["KT"] != got["PS"] or got["KT"] < 6:
                problems.append(f"{jls}: papir/tekstil {got['KT']}, plastika/staklo {got['PS']}")
            totals.update({"KT": got["KT"], "PS": got["PS"]})
            by_year = defaultdict(list)
            for r in rows:
                by_year[r[0].year].append(r)
            span = f"{rows[0][0]:%d.%m.%Y.} – {rows[-1][0]:%d.%m.%Y}"
            zones[str(len(zones) + 1)] = {
                "jls": jls,
                "podrucje": f"Općina {jls} – papir i tekstil te plastika i staklo u vrećama, jednom mjesečno "
                            f"({DAN[usual]})",
                "ulice": [f"{jls} (cijela općina)"],
                "napomena": f"Raspored oporabljivog otpada za razdoblje {span}. Pomaknuti datumi: "
                            + (", ".join(f"{d:%d.%m.%Y.}" for d in moved) or "nema."),
                "raw": {str(y): podaci.month_lines(r) for y, r in sorted(by_year.items())},
            }

    missing = [j for j in PROVIDER["jls"] if j not in [z["jls"] for z in zones.values()]]
    if missing:
        problems.append(f"nema rasporeda za {missing}")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit("Ništa nije upisano.")
    others = ["Imotski", "Lokvičići", "Lovreć", "Proložac", "Zagvozd", "Zmijavci"]
    last_seen = ", ".join(f"{j} do {e:%m/%Y}" for j, e in sorted(expired.items()))
    data = {**PROVIDER, "napomene": [
        "Raspored miješanog komunalnog otpada nije objavljen; ovdje su samo datumi odvoza oporabljivog otpada "
        "u vrećama (papir i tekstil, plastika i staklo).",
        "Vreće: plava za papir, narančasta za tekstil, žuta za plastiku, smeđa za staklo.",
        "Pomaci zbog blagdana već su ugrađeni u objavljene datume; datumi izvan uobičajenog dana označeni su kao "
        "pomaknuti.",
        "Za ostale općine koje opslužuje Čistoća Imotske krajine (" + ", ".join(others) + ") važeći raspored "
        "nije objavljen" + (" (posljednji objavljeni: " + last_seen + ")" if expired else "") + ".",
        "Čistoća Imotske krajine d.o.o., Šetalište Stjepana Radića 22, Imotski; 021 540 037.",
        "Izvori: " + ", ".join(sources) + ".",
    ], "zone": zones}
    out = podaci.PODACI / f"{SLUG}.json"
    if out.exists():  # keep other years of zones that did not change
        old = podaci.load(out)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("jls") == zone["jls"]:
                zone["raw"] = {**{y: v for y, v in prev["raw"].items() if int(y) < year}, **zone["raw"]}
    print(f"Zona: {len(zones)}; odvoza (zbroj): " + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {out.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
