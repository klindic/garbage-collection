"""Sinj, Trilj, Dicmo, Hrvace, Otok: Čistoća Cetinske krajine d.o.o. (cistoca-ck.hr), paper and plastic only.

    python3 -m izvori.cistoca_cetinske_krajine [--year 2026]

The page "Odvoz reciklabilnog otpada" links one PDF per city or municipality (sinj_<year>.pdf, ...; Trilj is
"izmjena_rasporeda_za_papir_i_plastiku.pdf", revised in May 2026). Each is one A4 page headed "GRAD SINJ" /
"OPĆINA OTOK", the year and "Odvoz reciklabilnog komunalnog otpada (PAPIR I PLASTIKA)", then groups of
settlements or streets (the heading may wrap over several lines) each followed by a "Datum:" line with
twelve D.M. dates, one per month (some lack the last dot, e.g. "10.8"). Paper and plastic are collected
together once a month (PK). The dates are not on a fixed weekday, so the check is one date per month, in
month order, never on a Sunday. No mixed-waste schedule is published, and no holiday shifts: dates that
fall on a public holiday are kept and listed in the zone note.
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
from izvori.slavonski_brod_komunalac import fetch
from pravila import blagdani

SLUG = "cistoca-cetinske-krajine"
SITE = "https://www.cistoca-ck.hr"
PAGE = SITE + "/raspored-sakupljanja-komunalnog-otpada/komunalni-otpad/"
JLS = {"GRAD SINJ": "Sinj", "GRAD TRILJ": "Trilj", "OPĆINA DICMO": "Dicmo", "OPĆINA HRVACE": "Hrvace",
       "OPĆINA OTOK": "Otok"}
DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.?")
ROMAN = {"I", "II", "III", "IV", "V"}
PROVIDER = {
    "davatelj": "Čistoća Cetinske krajine d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Splitsko-dalmatinska",
    "jls": ["Sinj", "Trilj", "Dicmo", "Hrvace", "Otok"],
    "nazivi": {"P": "Plastika (reciklabilni otpad)", "K": "Papir i karton (reciklabilni otpad)"},
    "napomene": [
        "Raspored odvoza miješanog komunalnog otpada nije objavljen; ovdje je samo odvoz reciklabilnog otpada "
        "(papir, karton i plastika zajedno, jednom mjesečno). Za miješani otpad: Čistoća Cetinske krajine, "
        "021 668 140, tajnica@cistoca-ck.hr.",
        "Spremnike na dan odvoza iznijeti na prvu dostupnu javnu površinu tako da ne ometaju promet.",
        "Raspored ne navodi pomake zbog blagdana; datumi su upisani kako su objavljeni (oni koji padaju na "
        "blagdan navedeni su u napomeni zone).",
        "Reciklažna dvorišta Sinj (Turjaci 392), Trilj (Čaporice 148) i Dicmo (Kraj 5C): ponedjeljak 12–20, "
        "utorak–petak 7–15 sati; subotom, nedjeljom i praznikom zatvoreno. Informacije 021 668 174.",
    ],
}


def naslov(name):
    """'PUT RUDUŠE' -> 'Put Ruduše', 'RAMSKA ULICA' -> 'Ramska ulica'; mixed-case words ('HV-a', 'donje')
    and Roman numerals stay."""
    words = [w if w in ROMAN or not w.isupper() else w[:1] + w[1:].lower() for w in name.split()]
    return " ".join(w.lower() if i and w in ("Ulica", "Cesta") else w for i, w in enumerate(words))


def lines_of(words, tol=3):
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0] - w["top"]) < tol:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    return [" ".join(x["text"] for x in sorted(ws, key=lambda w: w["x0"])) for _, ws in lines]


def read_pdf(path, year, problems, name):
    """(JLS heading, [(area text, [dates], [notes])])."""
    with pdfplumber.open(path) as pdf:
        lines = [l for p in pdf.pages for l in lines_of(p.extract_words())]
    top = " ".join(lines[:3])  # "2026 GRAD SINJ", "Odvoz reciklabilnog ...", "(PAPIR I PLASTIKA)"
    head = next((k for k in JLS if k in top), None)
    if not head or str(year) not in top.split():
        problems.append(f"{name}: nema naslova općine ili godine {year}: {lines[:3]}")
        return None, []
    start = next((i for i, l in enumerate(lines) if "PAPIR I PLASTIKA" in l.upper()), None)
    if start is None:
        problems.append(f"{name}: nema naslova '(PAPIR I PLASTIKA)'")
        return head, []
    groups, area = [], []
    for line in lines[start + 1:]:
        if line.startswith("Datum:"):
            toks = line.split()[1:]
            dates, early_notes = [], []
            for i, t in enumerate(toks, 1):
                m = DATE.fullmatch(t)
                early = m and int(m.group(2)) == i - 1 and int(m.group(1)) >= 28  # August round on 31.7.
                if not m or int(m.group(2)) != i and not early:
                    problems.append(f"{name} {' '.join(area)[:40]}: {i}. datum {t!r} nije u {i}. mjesecu")
                    continue
                if early:
                    print(f"   UPOZORENJE {name} {' '.join(area)[:40]}: odvoz za {i}. mjesec je {t} (kraj prethodnog "
                          "mjeseca)")
                    early_notes.append(f"Odvoz za {i}. mjesec je {t}")
                if not t.endswith("."):
                    print(f"   NAPOMENA {name}: {t!r} bez točke na kraju, čitam kao {m.group(1)}.{m.group(2)}.")
                try:
                    dates.append(date(year, int(m.group(2)), int(m.group(1))))
                except ValueError:
                    problems.append(f"{name}: nemoguć datum {t}")
            if len(toks) != 12:
                problems.append(f"{name} {' '.join(area)[:40]}: {len(toks)} datuma umjesto 12")
            if not area:
                problems.append(f"{name}: 'Datum:' bez naziva područja")
            groups.append((" ".join(area), dates, early_notes))
            area = []
        elif line.startswith("Korisnici usluge"):
            break
        else:
            area.append(line.strip())
    if area:
        problems.append(f"{name}: područje bez datuma: {' '.join(area)[:60]}")
    return head, groups


def places(text):
    """'SUHAČ – LUČANE – RADOŠIĆ' -> ['Suhač', 'Lučane', 'Radošić'] (' - ' and ' – ' separate places);
    a lower-case part belongs to the place before it ('GLAVICE – donje')."""
    out = []
    for p in re.split(r"\s+[-–]\s+|^[-–]\s+|\s+[-–]$", " ".join(text.split())):
        if p.strip() and p[0].islower() and out:
            out[-1] += " – " + p.strip()
        elif p.strip():
            out.append(naslov(p))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    html = fetch(PAGE).decode("utf-8", "replace")
    urls = list(dict.fromkeys(re.findall(r'href="([^"]*/site/assets/files/[^"]+\.pdf)"', html)))
    urls = [u if u.startswith("http") else SITE + u for u in urls]
    if not urls:
        sys.exit(f"Nema PDF-ova na {PAGE}. Ništa nije upisano.")
    hol = set(blagdani(year))
    problems, zones, seen = [], {}, {}
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    with tempfile.TemporaryDirectory() as tmp:
        for url in urls:
            name = url.rsplit("/", 1)[1]
            pdf = Path(tmp) / name
            fetch(url, pdf)
            if not pdf.read_bytes().startswith(b"%PDF"):
                problems.append(f"{name}: nije PDF")
                continue
            head, groups = read_pdf(pdf, year, problems, name)
            if not head:
                continue
            jls = JLS[head]
            if jls in seen:
                problems.append(f"{jls} u dvije datoteke: {seen[jls]} i {name}")
            seen[jls] = name
            print(f"{jls}: {len(groups)} područja iz {name}")
            for text, dates, early_notes in groups:
                where = f"{jls} {text[:40]}"
                ulice = places(text)
                if len(dates) != 12 or len(set(dates)) != 12:
                    problems.append(f"{where}: {len(set(dates))} različitih datuma")
                for d in dates:
                    if d.weekday() == 6:
                        problems.append(f"{where}: {d:%d.%m.} je nedjelja")
                on_hol = [d for d in dates if d in hol]
                key = str(len(zones) + 1)
                short = ", ".join(ulice[:3]) + (" …" if len(ulice) > 3 else "")
                zone = {"jls": jls, "podrucje": f"{jls} – {short}", "opis": " ".join(text.split()), "ulice": ulice}
                notes = [n + " (prema rasporedu)." for n in early_notes]
                if name.startswith("izmjena"):
                    notes.append("Izmijenjeni raspored za papir i plastiku (svibanj 2026.).")
                if on_hol:
                    ds = ", ".join(f"{d:%d.%m.}" for d in on_hol)
                    notes.append(f"Datum odvoza pada na blagdan ({ds}); raspored ne navodi pomak.")
                    print(f"   UPOZORENJE {where}: na blagdan {ds} (zadržano)")
                if notes:
                    zone["napomena"] = " ".join(notes)
                prev = old.get(key, {})
                zone["raw"] = {**(prev.get("raw", {}) if prev.get("podrucje") == zone["podrucje"] else {}),
                               str(year): podaci.month_lines([(d, "PK", False) for d in dates])}
                zones[key] = zone
                days = Counter(podaci.DAYS[d.weekday()][:3] for d in dates)
                print(f"   Zona {key}: {zone['podrucje']}: PK {len(dates)} ({dict(days)}), mjesta {len(ulice)}")
    missing = sorted(set(JLS.values()) - set(seen))
    if missing:
        problems.append(f"nema rasporeda za: {', '.join(missing)}")
    for p in problems:
        print(f"   PROBLEM {p}")
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, {**PROVIDER, "zone": zones})
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(zones)} zona)")


if __name__ == "__main__":
    main()
