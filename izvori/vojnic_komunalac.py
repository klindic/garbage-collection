"""Vojnić, Tounj, Cetingrad, Lasinja: Vojnić komunalac d.o.o. (vojnickomunalac.hr), one zone per calendar.

    python3 -m izvori.vojnic_komunalac [--year 2026]

The site's menu links one calendar per group of settlements ("Kalendar odvoza – Vojnić, centar", ...): ten
EPSON scans (a JPEG in a PDF, 200 dpi) plus one for Općina Lasinja (300 dpi, turned 90°). Each is a table of
months x waste types with explicit dates (mixed waste weekly or every second week on the group's weekday,
paper and plastic the same day once a month, bulky waste once a year). There is no text layer and no OCR
here, so the dates were transcribed by hand below and every PDF's sha256 is pinned: a changed or new
calendar stops the script until it is transcribed again. Dates printed red are moves "zbog blagdana ili
praznika" (marked as moved); a black date off the group's weekday is accepted only in a week with a public
holiday and is marked as moved too. Dates that cannot be right (a Sunday, 31.04.) are left out and noted.
"""
import argparse
import hashlib
import html
import re
import sys
from collections import Counter
from datetime import date, timedelta

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "vojnic-komunalac"
SITE = "https://vojnickomunalac.hr"
YEAR = 2026
# Transcribed calendars. M: mixed waste ("!" = printed red), PK: paper and plastic (same dates in both rows),
# G: bulky waste; day: the group's weekday (0 = Monday); n: every n-th week; skip: printed dates left out.
CALENDARS = [
    {"file": "VOJNIC-centar.pdf", "sha256": "719669b3748cc273411a39d37464b5090c17228928dcebdce72a2ce2cb0f816e",
     "jls": "Vojnić", "naziv": "Vojnić – centar", "day": 2, "n": 1,
     "ulice": ["Vojnić – centar"],
     "M": "02.01.! 08.01. 14.01. 21.01. 28.01. 04.02. 11.02. 18.02. 25.02. 04.03. 11.03. 18.03. 25.03. 01.04. "
          "08.04. 15.04. 22.04. 06.05. 13.05. 20.05. 27.05. 03.06. 10.06. 17.06. 24.06. 01.07. 08.07. 15.07. "
          "22.07. 29.07. 06.08.! 12.08. 19.08. 26.08. 02.09. 09.09. 16.09. 23.09. 30.09. 07.10. 14.10. 21.10. "
          "28.10. 04.11. 11.11. 19.11.! 25.11. 02.12. 09.12. 16.12. 23.12. 30.12.",
     "PK": "21.01. 18.02. 18.03. 15.04. 27.05. 24.06. 22.07. 19.08. 30.09. 28.10. 25.11. 23.12.", "G": "18.03.",
     "missing": {"M": 1}, "skip": "U travnju je upisan i datum 28.04 (utorak, dan nakon tjednog ritma srijedom bez blagdana); vjerojatno "
             "je riječ o 29.04., ali datum nije upisan – provjerite kod davatelja."},
    {"file": "VOJNIC-KupljenskoJohovoMiholjskoKrstinjaBrusovacaS.Rijeka.pdf",
     "sha256": "454946a187c6ed5dbb2809ef60da18a9af96e6202997f5e93d33f42fb264ff51",
     "jls": "Vojnić", "naziv": "Kupljensko, Johovo, Miholjsko, Krstinja, …", "day": 3, "n": 2,
     "ulice": ["Kupljensko", "Johovo", "Miholjsko", "Krstinja", "Brusovača Gornja", "Brusovača Donja", "Široka Rijeka",
               "Svinica Krstinjska", "Gejkovac", "Džaparovac", "Štakorovica", "Klokoč", "Kestenovac"],
     "M": "02.01.! 15.01. 29.01. 12.02. 26.02. 12.03. 26.03. 09.04. 23.04. 07.05. 21.05. 05.06.! 18.06. 02.07. "
          "16.07. 30.07. 13.08. 27.08. 10.09. 24.09. 08.10. 22.10. 05.11. 20.11.! 03.12. 17.12. 31.12.",
     "PK": "29.01. 26.02. 26.03. 23.04. 21.05. 18.06. 30.07. 27.08. 24.09. 22.10. 20.11. 17.12.", "G": "30.07."},
    {"file": "VOJNIC-Utinja-VreloKljucarBrdo-UtinjskoMalesevic-Selo-Podsed.pdf",
     "sha256": "8de90e7aa543cbc192047f733b2056dee643741f1c533b3e8df944b075b017f5",
     "jls": "Vojnić", "naziv": "Utinja Vrelo, Ključar, Brdo Utinjsko, Malešević Selo, …", "day": 2, "n": 2,
     "ulice": ["Utinja Vrelo", "Ključar", "Brdo Utinjsko", "Malešević Selo", "Podsedlo", "Međeđak", "Hajdini",
               "Krivaja Vojnićka", "Vujići", "prema Crkvištu"],
     "M": "02.01.! 14.01. 28.01. 11.02. 25.02. 11.03. 25.03. 08.04. 22.04. 06.05. 20.05. 03.06. 17.06. 01.07. "
          "15.07. 29.07. 12.08. 26.08. 09.09. 23.09. 07.10. 21.10. 04.11. 19.11.! 02.12. 16.12. 30.12.",
     "PK": "14.01. 11.02. 11.03. 08.04. 06.05. 03.06. 01.07. 12.08. 09.09. 07.10. 04.11. 02.12.", "G": "",
     "missing": {"G": 1}, "skip": "Glomazni otpad: u kalendaru piše 15.03. (nedjelja), pa datum nije upisan – provjerite kod davatelja."},
    {"file": "VOJNIC-Vojisnica-Knezevic-Kosa-Zivkovic-Kosa-Kolaric-Bukovic.pdf",
     "sha256": "e33d705fce93610eae8947a2182f3f832fd6a8e227ee8f5702b9cc2012e0e99c",
     "jls": "Vojnić", "naziv": "Vojišnica, Knežević Kosa, Živković Kosa, Kolarić, …", "day": 2, "n": 2,
     "ulice": ["Vojišnica", "Knežević Kosa", "Živković Kosa", "Kolarić", "Bukovica", "Mandić Selo", "Gornji Vojnić",
               "Donji Vojnić", "Jurga", "Radonja"],
     "M": "08.01. 21.01. 04.02. 18.02. 04.03. 18.03. 01.04. 15.04. 29.04. 13.05. 27.05. 10.06. 24.06. 08.07. "
          "22.07. 06.08.! 19.08. 02.09. 16.09. 30.09. 14.10. 28.10. 11.11. 25.11. 09.12. 23.12.",
     "PK": "21.01. 18.02. 18.03. 15.04. 27.05. 24.06. 22.07. 19.08. 30.09. 28.10. 25.11. 23.12.", "G": "18.03."},
    {"file": "TOUNJ-A-zona.pdf", "sha256": "92a0539a1e074795fab15444bcb6b086418f42d46f54d6db517b2abe8ea4ea43",
     "jls": "Tounj", "naziv": "Zona A – Kamenica, Košare, Kukača (Petrova Draga – Ključ)", "day": 0, "n": 2,
     "ulice": ["Kamenica", "Košare", "Kukača", "Petrova Draga", "Ključ"],
     "M": "12.01. 26.01. 09.02. 23.02. 09.03. 23.03. 07.04.! 20.04. 04.05. 18.05. 01.06. 15.06. 29.06. 13.07. "
          "27.07. 10.08. 24.08. 07.09. 21.09. 05.10. 19.10. 02.11. 16.11. 30.11. 14.12. 28.12.",
     "PK": "26.01. 23.02. 23.03. 20.04. 18.05. 15.06. 27.07. 24.08. 21.09. 19.10. 16.11. 14.12.", "G": "29.06."},
    {"file": "TOUNJ-B-zona.pdf", "sha256": "97bc87cc2e019b88954a471d94b1f626b9657a4d81bea7e54a2b7f5d77b730b0",
     "jls": "Tounj", "naziv": "Zona B – Potok Tounjski, Gerovo, Bistrac, Rebrovići, …", "day": 0, "n": 2,
     "ulice": ["Potok Tounjski", "Gerovo", "Bistrac", "Rebrovići", "Filipovići", "Tržić Tounjski", "Brletići",
               "Stanišići", "Orljak", "Matešići", "Capani", "Ćeti", "Borovci"],
     "M": "05.01. 19.01. 02.02. 16.02. 02.03. 16.03. 30.03. 13.04. 27.04. 11.05. 25.05. 08.06. 23.06.! 06.07. "
          "20.07. 03.08. 17.08. 31.08. 14.09. 28.09. 12.10. 26.10. 09.11. 23.11. 07.12. 21.12.",
     "PK": "19.01. 16.02. 16.03. 13.04. 11.05. 23.06. 20.07. 17.08. 14.09. 12.10. 09.11. 07.12.", "G": "20.07."},
    {"file": "TOUNJ-linije-Matesici-Zdenac.pdf",
     "sha256": "2b07aece8c116cc513f7d23df967ee377f8e847de8956816af18558ec0481ef2",
     "jls": "Tounj", "naziv": "Tounj – linije – Matešići – Zdenac", "day": 0, "n": 1,
     "ulice": ["Tounj", "Matešići", "Zdenac"],
     "M": "05.01. 12.01. 19.01. 26.01. 02.02. 09.02. 16.02. 23.02. 02.03. 09.03. 16.03. 23.03. 30.03. 07.04.! "
          "13.04. 20.04. 27.04. 04.05. 11.05. 18.05. 25.05. 01.06. 08.06. 15.06. 23.06.! 29.06. 06.07. 13.07. "
          "20.07. 27.07. 03.08. 10.08. 17.08. 24.08. 31.08. 07.09. 14.09. 21.09. 28.09. 05.10. 12.10. 19.10. "
          "26.10. 02.11. 09.11. 16.11. 23.11. 30.11. 07.12. 14.12. 21.12. 28.12.",
     "PK": "26.01. 23.02. 23.03. 20.04. 18.05. 15.06. 27.07. 24.08. 21.09. 16.11. 14.12.", "G": "29.06.",
     "missing": {"PK": 1}, "skip": "Papir i plastika u listopadu: u kalendaru piše 18.10. (nedjelja), pa datum nije upisan – provjerite "
             "kod davatelja."},
    {"file": "CETINGRAD-A-zona.pdf", "sha256": "ae0b8eb24bcb2cf5bf4f788c18ba7547161956027139bd2f3b3342038ac46619",
     "jls": "Cetingrad", "naziv": "Zona A – Gojkovac, Glinice, Donja i Gornja Žrvnica, …", "day": 1, "n": 2,
     "ulice": ["Gojkovac", "Glinice", "Donja Žrvnica", "Gornja Žrvnica", "Begovo Brdo", "Polojska Varoš", "Batnoga",
               "Ponor", "Kuk", "Delić Poljana", "T. Varoš", "Gnojnice", "G. Gnojnice", "D. Gnojnice", "Podcetin",
               "Bilo Cetinsko", "Cet. Varoš", "Kapljuh", "Strmčka", "Sadikovac", "Kestenje"],
     "M": "07.01.! 20.01. 03.02. 17.02. 03.03. 17.03. 14.04. 28.04. 12.05. 26.05. 09.06. 24.06.! 07.07. 21.07. "
          "04.08. 18.08. 01.09. 15.09. 29.09. 13.10. 27.10. 10.11. 24.11. 08.12. 22.12.",
     "PK": "20.01. 17.02. 17.03. 14.04. 12.05. 24.06. 21.07. 18.08. 15.09. 13.10. 10.11. 08.12.", "G": "07.07.",
     "missing": {"M": 1}, "skip": "U stupcu za ožujak upisan je i datum 31.04. (ne postoji; vjerojatno 31.03.), pa nije upisan – "
             "provjerite kod davatelja."},
    {"file": "CETINGRAD-B-zona.pdf", "sha256": "a03d5fc1343670192415c03d7d168fa7855aa531b828cc302ce29ac1f477d9d9",
     "jls": "Cetingrad", "naziv": "Zona B – Luke, Trnovi, Kruškovača, Srednje Selo, …", "day": 1, "n": 2,
     "ulice": ["Luke", "Trnovi", "Kruškovača", "Srednje Selo", "Komesarac", "Đurin Potok", "Šiljkovača", "Grabarska",
               "Pašin Potok", "Buhač", "Maljevac", "Maljevačko Selište", "Ruševica", "Bogovolja"],
     "M": "13.01. 27.01. 10.02. 24.02. 10.03. 24.03. 08.04. 21.04. 05.05. 19.05. 02.06. 16.06. 30.06. 14.07. "
          "28.07. 11.08. 25.08. 08.09. 22.09. 06.10. 20.10. 03.11. 17.11. 01.12. 15.12. 29.12.",
     "PK": "13.01. 10.02. 10.03. 08.04. 05.05. 02.06. 14.07. 11.08. 08.09. 06.10. 03.11. 01.12.", "G": "18.08."},
    {"file": "CETINGRAD-uzi-centar.pdf", "sha256": "688dd926b0474aa708491fe533356583d0037bba627b735c746712263aeb5a3c",
     "jls": "Cetingrad", "naziv": "Cetingrad – uži centar", "day": 1, "n": 1,
     "ulice": ["Cetingrad – uži centar"],
     "M": "07.01.! 13.01. 20.01. 27.01. 03.02. 10.02. 17.02. 24.02. 03.03. 10.03. 17.03. 24.03. 08.04. 14.04. "
          "21.04. 28.04. 05.05. 12.05. 19.05. 26.05. 02.06. 09.06. 16.06. 24.06.! 30.06. 07.07. 14.07. 21.07. "
          "28.07. 04.08. 11.08. 18.08. 25.08. 01.09. 08.09. 15.09. 22.09. 29.09. 06.10. 13.10. 20.10. 27.10. "
          "03.11. 10.11. 17.11. 24.11. 01.12. 08.12. 15.12. 22.12. 29.12.",
     "PK": "20.01. 17.02. 17.03. 14.04. 12.05. 24.06. 21.07. 18.08. 15.09. 13.10. 10.11. 08.12.", "G": "21.07.",
     "missing": {"M": 1}, "skip": "U stupcu za ožujak upisan je i datum 31.04. (ne postoji; vjerojatno 31.03.), pa nije upisan – "
             "provjerite kod davatelja."},
    {"file": "OPCINA-LASINJA.pdf", "sha256": "8a5adbf2d79989e604d3d44a2bf4d09d83cfcf693105eb4a6306fac69ae31647",
     "jls": "Lasinja", "naziv": "Općina Lasinja – cijelo područje", "day": 3, "n": 2, "pkday": 4,
     "ulice": ["Lasinja"],
     "M": "09.01.! 22.01. 05.02. 19.02. 05.03. 19.03. 02.04. 16.04. 30.04. 14.05. 28.05. 11.06. 25.06. 09.07. "
          "23.07. 07.08.! 20.08. 03.09. 17.09. 01.10. 15.10. 29.10. 12.11. 26.11. 10.12. 24.12.",
     "PK": "23.01. 20.02. 20.03. 17.04. 15.05. 12.06. 10.07. 21.08. 18.09. 16.10. 13.11. 11.12.", "G": "11.06.",
     "napomena": "U Registru (IRDJU 2024) za Općinu Lasinja još je naveden EKO-FLOR PLUS d.o.o.; Vojnić komunalac "
                 "objavio je kalendar za 2026. te Odluku o načinu pružanja javne usluge i Ugovor za Lasinju "
                 "(ožujak 2026.). Papir i plastika odvoze se petkom."},
]
DAN = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom"]
PROVIDER = {
    "davatelj": "Vojnić komunalac d.o.o.",
    "web": SITE,
    "izvor": SITE + "/",
    "zupanija": "Karlovačka",
    "jls": ["Vojnić", "Tounj", "Cetingrad", "Lasinja"],
    "nazivi": {"P": "Plastika (vreća)", "K": "Papir (vreća)"},
    "napomene": [
        "Kalendar odvoza 2026. objavljen je po skupinama naselja (skenirani PDF-ovi). Datumi ispisani crvenom bojom "
        "su promijenjeni dani odvoza zbog blagdana ili praznika i označeni su kao pomaknuti.",
        "Papir i plastika predaju se u plastičnim vrećama koje moraju biti vezane i stavljene na prilaz kući.",
        "Glomazni otpad: jednom godišnje prema kalendaru, besplatno do 5 m³ (u Lasinji do 3 m³); veće količine "
        "naplaćuju se prema cjeniku.",
        "Općina Lasinja: Registar (IRDJU 2024) još navodi EKO-FLOR PLUS d.o.o., ali Vojnić komunalac objavljuje "
        "kalendar za 2026. i Ugovor o obavljanju javne usluge za Lasinju (ožujak 2026.).",
        "Neki datumi u skenovima nisu mogući (nedjelja, 31.04., utorak umjesto srijede) i nisu upisani; vidi "
        "napomenu zone.",
        "Vojnić komunalac d.o.o., Andrije Hebranga 9, 47220 Vojnić.",
    ],
}


def dates(text, year):
    """'02.01.! 08.01.' -> [(date, red)]."""
    return [(date(year, int(m), int(d)), red == "!") for d, m, red in re.findall(r"(\d\d)\.(\d\d)\.(!?)", text)]


def holiday_week(d, hol):
    monday = d - timedelta(days=d.weekday())
    return any(monday + timedelta(days=i) in hol for i in range(6))


def check(cal, year, problems):
    """[(date, codes, moved)] for one calendar, with every check of the transcription."""
    name, n, hol = cal["naziv"], cal["n"], set(pravila.blagdani(year))
    rows = {}
    for code in ("M", "PK", "G"):
        day = cal.get("pkday", cal["day"]) if code == "PK" else cal["day"]
        found = dates(cal[code], year)
        if [d for d, _ in found] != sorted(d for d, _ in found) or len({d for d, _ in found}) != len(found):
            problems.append(f"{name} {code}: datumi nisu redom ili se ponavljaju")
        for d, red in found:
            off = d.weekday() != day
            if d in hol or d.weekday() == 6:
                problems.append(f"{name} {code} {d:%d.%m.}: blagdan ili nedjelja")
            if off and not holiday_week(d, hol):
                problems.append(f"{name} {code} {d:%d.%m.}: nije {DAN[day]}, a u tjednu nema blagdana")
            if red and not off:
                problems.append(f"{name} {code} {d:%d.%m.}: crveni datum na redovni dan")
            codes, moved = rows.get(d, ("", False))
            rows[d] = (codes + code, moved or off)
    # mixed waste: the regular weekday of each date's week must follow every n weeks; each printed date that
    # was left out ("missing") leaves exactly one double gap
    nominal = [d - timedelta(days=d.weekday() - cal["day"]) for d, _ in dates(cal["M"], year)]
    gaps = [(a, b) for a, b in zip(nominal, nominal[1:]) if (b - a).days != 7 * n]
    if any((b - a).days != 14 * n for a, b in gaps) or len(gaps) != cal.get("missing", {}).get("M", 0):
        problems.append(f"{name}: miješani nije svaki {n}. tjedan: {[f'{a:%d.%m.}-{b:%d.%m.}' for a, b in gaps]}")
    cnt = Counter(d.month for d, _ in dates(cal["M"], year))
    lo, hi = (4, 6) if n == 1 else (2, 3)
    bad = {m: cnt[m] for m in range(1, 13) if not lo - (len(gaps) > 0) <= cnt[m] <= hi}
    if bad:
        problems.append(f"{name}: neobičan broj odvoza miješanog po mjesecu {bad}")
    pk = Counter(d.month for d, _ in dates(cal["PK"], year))
    if any(v > 1 for v in pk.values()) or len(pk) != 12 - cal.get("missing", {}).get("PK", 0):
        problems.append(f"{name}: papir i plastika nisu jednom mjesečno {dict(pk)}")
    if "pkday" not in cal and any("M" not in rows[d][0] for d, _ in dates(cal["PK"], year)):
        problems.append(f"{name}: papir i plastika nisu na dan odvoza miješanog")
    return [(d, c, mv) for d, (c, mv) in rows.items()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=YEAR)
    year = ap.parse_args(argv).year
    if year != YEAR:
        sys.exit(f"Kalendari za {year} nisu prepisani. Ništa nije upisano.")
    problems = []
    page = fetch(SITE + "/").decode("utf-8", "replace")
    links = {html.unescape(u): " ".join(html.unescape(t).split())
             for u, t in re.findall(r'href="([^"]+\.pdf)"[^>]*>\s*(Kalendar odvoza[^<]*)<', page)}
    known = {c["file"]: c for c in CALENDARS}
    for url, label in links.items():
        up = re.search(r"/uploads/(\d{4})/", url)
        if up and int(up.group(1)) < year:
            print(f"Stari kalendar ({up.group(1)}.) preskočen: {label}")
        elif url.rsplit("/", 1)[1] not in known:
            problems.append(f"novi kalendar, prepisati: {label} {url}")
    for c in CALENDARS:
        url = next((u for u in links if u.endswith("/" + c["file"])), None)
        if not url:
            problems.append(f"kalendar {c['file']} više nije na stranici")
            continue
        sha = hashlib.sha256(fetch(url)).hexdigest()
        if sha != c["sha256"]:
            problems.append(f"slika se promijenila, prepisati ponovno: {url} (sha256 {sha})")

    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path)["zone"] if path.exists() else {}
    data = {**PROVIDER, "zone": {}}
    for z, c in enumerate(CALENDARS, 1):
        rows = check(c, year, problems)
        rule = f"miješani {DAN[c['day']]}" + (", svaki drugi tjedan" if c["n"] == 2 else ", svaki tjedan")
        note = " ".join(x for x in (c.get("napomena"), c.get("skip")) if x)
        zone = {"jls": c["jls"], "podrucje": f"{c['naziv']} ({rule})", "ulice": c["ulice"]}
        if note:
            zone["napomena"] = note
        zone["raw"] = {**old.get(str(z), {}).get("raw", {}), str(year): podaci.month_lines(rows)}
        data["zone"][str(z)] = zone
        total = Counter(x for _, codes, _ in rows for x in codes)
        print(f"Zona {z} ({c['jls']}, {c['naziv'][:40]}): M {total['M']}, PK {total['P']}, G {total['G']}, "
              f"pomaknuto {sum(1 for *_, mv in rows if mv)}" + ("; izostavljen datum" if "skip" in c else ""))
    if problems:
        print("\n".join(f"PROBLEM {p}" for p in problems))
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: podaci/{SLUG}.json ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
