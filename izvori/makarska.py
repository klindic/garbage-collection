"""Makarska: Makarski komunalac d.o.o., two districts (Rajon 1 Mon/Thu, Rajon 2 Tue/Sat).

    python3 -m izvori.makarska [--year 2026]

The year PDF on the calendar page gives every date as a coloured cell (green mixed, yellow plastic,
blue paper, cyan mixed + paper, red non-working day). The site's JavaScript holds the same dates as
lists (updated monthly, usually up to the end of the current month) and the street list of each
district. The JavaScript is newer, so it wins for the dates it covers and the PDF fills in the rest
of the year; dates where the two disagree are printed so they can be checked.
"""
import argparse
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

import pdfplumber

import podaci
from izvori.sisak_gos import fetch
from kalendar_boje import read_page

SLUG = "makarska-komunalac"
SITE = "https://www.makarski-komunalac.hr"
PAGE = SITE + "/info/kalendar_odvoza.html"
JS = SITE + "/assets/makarski-komunalac.hr/js/kalendar_odvoza_javascript.js"
PALETTE = {(0.714, 0.843, 0.659): "M", (1.0, 0.898, 0.6): "P", (0.643, 0.761, 0.957): "K",
           (0.0, 1.0, 1.0): "MK", (0.918, 0.6, 0.6): None}
IGNORE = ((1.0, 1.0, 1.0), (0.953, 0.953, 0.953))
JS_TYPES = {"mješani komunalni": "M", "plastika": "P", "papir": "K", "mko/papir": "MK"}
DISTRICTS = {  # zone: (JS schedule, JS street list, description)
    "1": ("MON_THU_SCHEDULE", "MON_THU_ADDRESSES", "Rajon 1 (ponedjeljak i četvrtak)"),
    "2": ("TUE_SAT_SCHEDULE", "TUE_SAT_ADDRESSES", "Rajon 2 (utorak i subota)"),
}
PROVIDER = {
    "davatelj": "Makarski komunalac d.o.o.",
    "web": SITE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Makarska"],
    "nazivi": {"P": "Plastika, metal i tetrapak"},
    "napomene": [
        "Raspored je informativnog karaktera i podložan promjenama.",
        "Od 15.06. do 30.09. vrijedi ljetni raspored s dodatnim odvozima.",
        "Adrese kojih nema na popisu: Makarski komunalac, 021/695-010.",
    ],
}


def js_object(js, name):
    """{key: [dates]} from `const NAME = { "key": ["YYYY-MM-DD", ...], ... };`."""
    body = re.search(rf"const {name} = \{{(.*?)\n\}};", js, re.S).group(1)
    return {k: re.findall(r'"(\d{4}-\d{2}-\d{2})"', v)
            for k, v in re.findall(r'"([^"]+)":\s*\[(.*?)\]', body, re.S)}


def js_list(js, name):
    body = re.search(rf"const {name} = \[(.*?)\];", js, re.S).group(1)
    return re.findall(r'"([^"]+)"', body)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    m = re.search(rf'href="([^"]*raspored_odvoza_{year}\.pdf)"', html)
    if not m:
        sys.exit(f"Nema PDF-a za {year} na {PAGE}")
    pdf_url = m.group(1) if m.group(1).startswith("http") else SITE + "/" + m.group(1).lstrip("/")
    js = fetch(JS).decode("utf-8", "replace")
    path = podaci.PODACI / f"{SLUG}.json"
    data = podaci.load(path) if path.exists() else {**PROVIDER, "zone": {}}
    data.update(PROVIDER)
    data["izvor"] = PAGE
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "raspored.pdf"
        fetch(pdf_url, pdf_path)
        pages = pdfplumber.open(pdf_path).pages
        for zone, (sched_name, streets_name, desc) in DISTRICTS.items():
            page = next((p for p in pages if f"RAJON {zone}" in " ".join((p.extract_text() or "").split())), None)
            if page is None:
                print(f"Rajon {zone}: nema stranice u PDF-u")
                ok = False
                continue
            found, problems = read_page(page, year, PALETTE, ignore=IGNORE)
            # cross-check with the JavaScript lists on every date both sources cover
            js_codes = {}
            for key, dates in js_object(js, sched_name).items():
                for d in dates:
                    if d.startswith(str(year)):
                        js_codes.setdefault(date.fromisoformat(d), set()).update(JS_TYPES[key])
            last = max(js_codes, default=None)
            changed = []
            for d in sorted(set(js_codes) | {d for d in found if last and d <= last}):
                a, b = set(found.get(d, "")), js_codes.get(d, set())
                if a != b:
                    changed.append(f"{d:%d.%m.} PDF {''.join(sorted(a)) or '-'} / JS {''.join(sorted(b)) or '-'}")
            merged = {d: "".join(sorted(c)) for d, c in js_codes.items()}
            merged.update({d: c for d, c in found.items() if last is None or d > last})
            print(f"Rajon {zone}: {len(merged)} odvoza (JS do {last}, PDF poslije), problema {len(problems)}")
            if changed:
                print(f"   JS se razlikuje od PDF-a na {len(changed)} datuma: {', '.join(changed)}")
            for p in problems[:15]:
                print(f"   PROBLEM {p}")
            if problems:
                ok = False
                continue
            old = data["zone"].get(zone, {})
            data["zone"][zone] = {
                "jls": "Makarska", "podrucje": desc, "ulice": js_list(js, streets_name),
                "raw": {**old.get("raw", {}),
                        str(year): podaci.month_lines([(d, c, False) for d, c in merged.items()])},
            }
    if not ok:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)}")


if __name__ == "__main__":
    main()
