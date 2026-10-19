"""Čistoća Metković d.o.o.: Grad Metković, zones = streets with the same collection dates.

    python3 -m izvori.cistoca_metkovic [--year 2026]

The page "Skupljanje i odvoz otpada" renders its wpDataTables table on the server for the whole published
period (about 9,600 rows ULICA | DAN | DATUM | VRSTA OTPADA | MJESEC, one row per street, date and waste
type). Street variants such as "… - KONTEJNERI", "… ( zgrade )" or "… ( SKUPLJANJE MALOM SMEĆAROM)" are
kept apart (they often have other days) but get the plain street name; streets with identical dates form
one zone. Holiday rows: "NOVA GODINA ( nema odvoza - zamjenski termin DD.MM.YYYY.)" drops the date and the
matching "… - zamjenski termin" row is the moved collection; "… - BLAGDAN/HR PRAZNIK - moguća promjena
odvoza" is a normal date with a note. Months the table does not cover are kept from podaci/<slug>.json.
"""
import argparse
import html
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "cistoca-metkovic"
SITE = "https://cistoca-metkovic.hr"
PAGE = SITE + "/skupljanje-i-odvoz-otpada/"
MONTHS = ["SIJEČANJ", "VELJAČA", "OŽUJAK", "TRAVANJ", "SVIBANJ", "LIPANJ", "SRPANJ", "KOLOVOZ", "RUJAN",
          "LISTOPAD", "STUDENI", "PROSINAC"]
DAYS = ["PON", "UTO", "SRI", "ČET", "PET", "SUB", "NED"]
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
TYPES = {"MIJEŠANI OTPAD - zelena posuda": "M", "BIO OPTAD - smeđa posuda": "B", "BIO OTPAD - smeđa posuda": "B",
         "PAPIR I KARTON - plava posuda": "K", "PLASTIKA I AMBALAŽA - žuta posuda": "P"}
VARIANTS = {"kontejneri": "kontejneri", "zgrade": "zgrade", "skupljanje malom smećarom": "mali smećar",
            "spoj na ulicu - skupljanje malom smećarom": "spoj na ulicu, mali smećar",
            "do verajina guvna kontejneri": "kontejneri, do Verajina guvna"}
NAME = {"M": "miješani", "B": "bio", "K": "papir", "P": "plastika"}
PROVIDER = {
    "davatelj": "Čistoća Metković d.o.o.",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Metković"],
    "nazivi": {"M": "Miješani otpad (zelena posuda)", "B": "Biootpad (smeđa posuda)",
               "K": "Papir i karton (plava posuda)", "P": "Plastika i ambalaža (žuta posuda)"},
    "bioNapomena": "Biootpad (smeđa posuda) odvozi se u ulicama s obiteljskim kućama prema rasporedu.",
}


def rows_of(page):
    """[[ULICA, DAN, DATUM, VRSTA, MJESEC]] from the server-rendered table."""
    heads = [" ".join(re.sub(r"<[^>]+>", "", h).split()) for h in re.findall(r"<th[^>]*>(.*?)</th>", page, re.S)]
    if heads[:5] != ["ULICA", "DAN", "DATUM", "VRSTA OTPADA", "MJESEC"]:
        sys.exit(f"Tablica na {PAGE} ima stupce {heads[:6]}")
    body = page[page.find("<tbody"):page.find("</tbody>")]
    cells = lambda tr: re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
    return [[html.unescape(" ".join(re.sub(r"<[^>]+>", "", c).split())) for c in cells(tr)]
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)]


def naslov(name):
    """'SV. ĆIRILA I METODIJA' -> 'Sv. Ćirila i Metodija', 'FRA S.PETROVA' -> 'Fra S. Petrova'."""
    name = re.sub(r"\.(?=\w)", ". ", name)
    words = []
    for w in name.split():
        words.append(w.lower() if w in ("I",) else w if re.fullmatch(r"[IVX]+", w) and len(w) > 1 else w.capitalize())
    return " ".join(words)


def split_street(raw):
    """'DUBROVAČKA ULICA- KONTEJNERI' -> ('Dubrovačka', 'kontejneri'), 'PRUD' -> ('Prud', '')."""
    m = re.match(r"(.*?)\s*(?:\((.*?)\)\s*)?(?:-\s*(KONTEJNERI))?$", raw)
    name, paren, cont = m.group(1), m.group(2) or "", m.group(3) or ""
    variant = " ".join((paren + " " + cont).lower().split())
    name = re.sub(r"^ULICA\s+|\s+ULICA$", "", name.strip())
    if variant and variant not in VARIANTS:
        return None, variant
    name = naslov(name) if not name.startswith("GOSPODARSKI") else name.capitalize()
    return name, VARIANTS.get(variant, "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    problems = []
    page = fetch(PAGE).decode("utf-8", "replace")
    title = re.search(r"KALENDAR SKUPLJANJA PO ULICAMA ZA (\d{4})\. GODINU", page)
    if not title or int(title.group(1)) != year:
        sys.exit(f"Tablica na {PAGE} nije za {year} ({title.group(0) if title else 'bez naslova'})")
    rows = rows_of(page)
    print(f"Redaka u tablici: {len(rows)}")

    entities = defaultdict(dict)  # (name, variant) -> {date: [codes, moved]}
    originals = defaultdict(set)
    dropped, maybe = [], defaultdict(set)
    for r in rows:
        if len(r) != 5:
            problems.append(f"redak {r}")
            continue
        street, day, when, kind, month = r
        try:
            d = datetime.strptime(when, "%d/%m/%Y").date()
        except ValueError:
            problems.append(f"{street}: datum {when!r}")
            continue
        if DAYS[d.weekday()] != day or MONTHS[d.month - 1] != month or d.year != year:
            problems.append(f"{street} {when}: dan {day}, mjesec {month}")
        name, variant = split_street(street)
        if name is None:
            problems.append(f"nepoznata oznaka ulice {street!r} ({variant})")
            continue
        key = (name, variant)
        originals[key].add(street)
        m = re.fullmatch(r"NOVA GODINA \( nema odvoza - zamjenski termin (\d\d)\.(\d\d)\.(\d{4})\.\)", kind)
        if m:
            dropped.append((key, d, date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
            continue
        base, _, rest = kind.partition(" - ")
        base = f"{base} - {rest.split(' - ')[0]}" if rest else base
        code = TYPES.get(base)
        extra = kind[len(base):].strip(" -")
        if code is None or extra not in ("", "zamjenski termin", "BLAGDAN - moguća promjena odvoza",
                                         "HR PRAZNIK- moguća promjena odvoza"):
            problems.append(f"{street} {when}: nepoznata vrsta {kind!r}")
            continue
        if "moguća promjena" in extra:
            maybe[d].add(extra.split("-")[0].strip())
        cell = entities[key].setdefault(d, ["", False])
        if code in cell[0]:
            problems.append(f"{street} {when}: {code} dvaput")
        cell[0] += code
        cell[1] = cell[1] or extra == "zamjenski termin"
    for key, d, new in dropped:  # every "no collection" row needs its replacement row
        if not entities[key].get(new, ["", False])[1] or not 0 < (new - d).days <= 6:
            problems.append(f"{key[0]} {d}: nema zamjenskog termina {new}")
    moved_any = {d for e in entities.values() for d, (_, mv) in e.items() if mv}
    for d in moved_any:
        if not any(0 < (d - h).days <= 6 for h in pravila.blagdani(year)):
            problems.append(f"zamjenski termin {d} bez blagdana prije")
    seen = [d for e in entities.values() for d in e] + [d for _, d, _ in dropped]
    first, last = min(seen), max(seen)

    # zones: identical date signatures
    groups = defaultdict(list)
    for key, dates in entities.items():
        sig = tuple(sorted((d, "".join(sorted(c, key="MBPK".index)), mv) for d, (c, mv) in dates.items()))
        groups[sig].append(key)

    def sort_key(item):
        sig, keys = item
        kinds = sorted({v for _, v in keys})
        mdays = sorted({d.weekday() for d, c, mv in sig if "M" in c and not mv})
        return (kinds[0].startswith("kontejneri"), any(n.startswith("Gospodarski") for n, _ in keys), mdays,
                sorted(keys)[0])

    zones, totals = {}, Counter()
    for i, (sig, keys) in enumerate(sorted(groups.items(), key=sort_key), 1):
        rows_z = [(d, c, mv) for d, c, mv in sig]
        per_month = Counter((d.month, x) for d, c, _ in rows_z for x in c)
        for (m, x), n in per_month.items():
            if n > {"M": 14, "B": 3, "K": 3, "P": 3}[x]:
                problems.append(f"zona {i} ({keys[0][0]}): {x} {n} puta u {m}. mjesecu")
        totals.update(x for _, c, _ in rows_z for x in c)
        days = {}
        for x in "MBKP":
            wd = sorted({d.weekday() for d, c, mv in rows_z if x in c and not mv})
            if wd:
                names = [DAN[w] for w in wd]
                days[x] = " i ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names)
        rule = "; ".join(f"{', '.join(NAME[x] for x in xs)} {txt}" for txt, xs in
                         _group_by_value(days).items())
        variants = sorted({v for _, v in keys})
        label = ", ".join(n for n, _ in sorted(keys)[:3]) + (" …" if len(keys) > 3 else "")
        prefix = "Kontejneri – " if all(v.startswith("kontejneri") for v in variants) else ""
        zone = {"jls": "Metković", "podrucje": f"{prefix}{label} ({rule})",
                "opis": "; ".join(sorted(o for k in keys for o in originals[k])),
                "ulice": sorted({n for n, _ in keys})}
        notes = [f"{n}: {v}" for n, v in sorted(keys) if v and v != "kontejneri"]
        if prefix:
            zone["napomena"] = ("Zajednički kontejneri u navedenim ulicama"
                                + (f" ({'; '.join(notes)})" if notes else "") + ".")
        elif notes or "kontejneri" in variants:
            notes += [f"{n}: kontejneri" for n, v in sorted(keys) if v == "kontejneri"]
            zone["napomena"] = "Dio ulice ili način skupljanja: " + "; ".join(notes) + "."
        zone["raw"] = {str(year): podaci.month_lines(rows_z)}
        zones[str(i)] = zone

    for p in problems[:40]:
        print(f"   PROBLEM {p}")
    if problems or not zones:
        sys.exit(f"Ništa nije upisano ({len(problems)} problema).")
    out = podaci.PODACI / f"{SLUG}.json"
    if out.exists():  # keep dates outside the table's period for zones that did not change
        old = podaci.load(out)
        for z, zone in zones.items():
            prev = old.get("zone", {}).get(z)
            if prev and prev.get("ulice") == zone["ulice"]:
                keep = [(d, c, mv) for d, c, mv in podaci.iter_dates(prev) if not first <= d <= last]
                years = {**prev["raw"], **zone["raw"]}
                for y in {d.year for d, _, _ in keep}:
                    rows_y = [r for r in keep if r[0].year == y]
                    rows_y += [r for r in podaci.iter_dates(zone, y)] if str(y) in zone["raw"] else []
                    years[str(y)] = podaci.month_lines(rows_y)
                zone["raw"] = years
    months = sorted({d.month for e in entities.values() for d in e})
    gap = [MONTHS[m - 1].lower() for m in range(1, 13) if m not in months]
    data = {**PROVIDER, "napomene": [
        f"Raspored iz tablice \"Kalendar skupljanja po ulicama za {year}. godinu\" na {PAGE} (razdoblje "
        f"{first:%d.%m.%Y.} – {last:%d.%m.%Y.})."
        + (f" Za {', '.join(gap)} raspored još nije objavljen." if gap else ""),
        "Blagdani: 1.1. nema odvoza, zamjenski termin označen je kao pomaknut; uz datume "
        + ", ".join(f"{d:%d.%m.}" for d in sorted(maybe)) + " tablica navodi \"moguća promjena odvoza\" "
        "(blagdan ili praznik). Ostalim blagdanima odvoz je prema rasporedu.",
        "Ulice s oznakom \"kontejneri\" odnose se na zajedničke kontejnere u toj ulici, \"mali smećar\" na dijelove "
        "ulice do kojih dolazi manje vozilo.",
        "Općina Kula Norinska (također Čistoća Metković) nije u tablici, pa za nju nema rasporeda.",
    ], "zone": zones}
    print(f"Zona: {len(zones)}; odvoza (zbroj po zonama): " + ", ".join(f"{c} {n}" for c, n in sorted(totals.items())))
    podaci.save(SLUG, data)
    print(f"Upisano: {out.relative_to(podaci.ROOT)}")


def _group_by_value(days):
    """{'M': 'utorak', 'B': 'subota', 'K': 'subota'} -> {'utorak': 'M', 'subota': 'BK'} (insertion order)."""
    out = {}
    for code, txt in days.items():
        out[txt] = out.get(txt, "") + code
    return out


if __name__ == "__main__":
    main()
