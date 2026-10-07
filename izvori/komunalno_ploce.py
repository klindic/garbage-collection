"""Ploče and Gradac: Komunalno održavanje d.o.o. Ploče (komunalno.ploce.hr), one zone per calendar.

    python3 -m izvori.komunalno_ploce [--year 2026]

The page "Raspored odvoza otpada po naseljima" (WordPress REST) has one year calendar image per group of
settlements, each under a heading with the settlement names. Every collection day is a coloured circle
behind the day number. Grad Ploče villages: dark green = mixed waste (green bin), blue = paper (blue
bin), orange = plastic (yellow bin), and the mixed-waste weekdays are printed under the calendar
("01.10. - 31.05. srijedom MKO, 01.06. - 30.09. srijedom i subotom MKO", kept in IMAGES). Općina Gradac:
brown = mixed waste (brown bin), orange = recyclables (one green bin with an orange lid for paper,
plastic, metal and glass, written as P+K+S). The circles are read from the image: the month boxes and
the weekday letters give the columns, the rows of white day numbers the rows, and every day number
must sit in its weekday column; a circle split in two colours is two collections. Holiday shifts are
drawn in the calendars: a mixed-waste day off the printed (or usual) weekdays next to a holiday is
marked as moved. Each image's sha256 is kept below; a changed image stops the script. The town of
Ploče uses street containers (mixed waste daily except Sunday) and has no calendar, so it is only a
note; the info sheet image (Ploce-SVI) has no dates.
"""
import argparse
import calendar
import hashlib
import html
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

import podaci
import pravila
from izvori.slavonski_brod_komunalac import fetch

SLUG = "komunalno-ploce"
SITE = "https://komunalno.ploce.hr"
PAGE = SITE + "/raspored-odvoza-otpada-po-naseljima/"
REST = SITE + "/wp-json/wp/v2/pages?slug=raspored-odvoza-otpada-po-naseljima&_fields=link,modified,content"
DAN = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
# image (without "-scaled.jpg") -> sha256, JLS, mixed-waste weekdays printed under the calendar
# (1.10.-31.5., 1.6.-30.9.; None where the calendar prints none)
IMAGES = {
    "Blato-Spilice-Stasevica": ("b5c9b71106303f08a137c7c5cbf9844b01c089340ff27b45da4683d68fe2a5f3",
                                "Ploče", ((2,), (2, 5))),
    "Bacina-Birina-Stablina-Ceveljusa": ("7168cb3fd0e9d5dcc192b0118ca14c2510cc4dfa9eb93e258424fc78ca13242f",
                                         "Ploče", ((3,), (0, 3))),
    "Struga-Rogotin": ("9213cc44b16b70f0cf88dd454e4f915ec8b21d2cc00442816b38e46b20ecd45a",
                       "Ploče", ((1,), (1, 4))),
    "Banja-Komin": ("4c5fbec556d6a1111d0b1c1ac05db77a05c506a8f5dcb96763d85b44c4b1c3b3",
                    "Ploče", ((4,), (1, 4))),
    "Gradac-Brist": ("b35bae071bb7fb4020606a235a5a47e8ed1d0f34defed289004456e074d8b534", "Gradac", None),
    "Podaca-Zaostrog-Drvenik": ("75ef176e321ba24780409adf5088548c0922193fb8c46578b28978f9da665ddd",
                                "Gradac", None),
}
INFO_IMAGE = "Ploce-SVI-2026-1"
PALETTE = {"zelena": (10, 125, 72), "plava": (10, 150, 210), "narančasta": (250, 155, 30),
           "smeđa": (170, 95, 30)}
CODES = {"zelena": "M", "smeđa": "M", "plava": "K", "narančasta": "P"}
PROVIDER = {
    "davatelj": "Komunalno održavanje d.o.o. Ploče",
    "web": SITE,
    "izvor": PAGE,
    "zupanija": "Dubrovačko-neretvanska",
    "jls": ["Ploče", "Gradac"],
    "napomene": [
        "Kantu treba iznijeti u večernjim satima prije dana odvoza, na javnu površinu ispred nekretnine.",
        "Ploče (grad): kontejneri za miješani otpad prazne se svaki dan osim nedjelje, kontejneri za reciklažu "
        "jednom tjedno; za grad nema kalendara s datumima.",
        "Ljetni raspored (od lipnja do rujna, češći odvoz) ucrtan je u kalendare; izmjene objavljuje Komunalno "
        "održavanje na komunalno.ploce.hr.",
        "Pomaci zbog blagdana ucrtani su u kalendare (označeni kao pomaknuti).",
        "Glomazni otpad: besplatan kontejner od 5 m³ jednom godišnje, naručuje se tjedan dana ranije na "
        "095 4477 701 (radnim danom 7–13 h); od 15.6. do 15.9. glomazni otpad se ne odvozi.",
        "Reciklažno dvorište Ploče, Dalmatinska 5A: ponedjeljak, srijeda i petak 7–14, utorak i četvrtak 14–19, "
        "subota 7–12 sati.",
        "Informacije: 020 676 601, 095 4477 701 (radnim danom 7–13 h).",
    ],
}


def bands(profile, gap=3):
    """[(start, end)] runs of True in a 1-D profile, joining gaps of up to `gap`."""
    out, start, last = [], None, -10
    for i, v in enumerate(list(profile) + [False]):
        if v:
            start = i if start is None else start
            last = i
        elif start is not None and i - last > gap:
            out.append((start, last))
            start = None
    return out


def read_image(path, year):
    """({date: set of colours}, problems) for the twelve month boxes of one calendar."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    H, W = a.shape[:2]
    white = a.min(axis=2) > 200
    # the four month columns: x range of the long white box edges
    edges = []
    for y in range(int(0.1 * H), int(0.7 * H)):
        d = np.diff(np.concatenate(([0], white[y].view(np.int8), [0])))
        edges += [(int(s), int(e)) for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1)
                  if 0.19 * W < e - s < 0.23 * W]
    groups = []
    for s, e in sorted(edges):
        if groups and s - groups[-1][-1][0] < 20:
            groups[-1].append((s, e))
        else:
            groups.append([(s, e)])
    cols = [(int(np.median([g[0] for g in grp])), int(np.median([g[1] for g in grp]))) for grp in groups
            if len(grp) >= 2]
    if len(cols) != 4:
        return {}, [f"{len(cols)} stupaca s mjesecima umjesto 4"]
    near = {k: np.sqrt(((a - np.array(v)) ** 2).sum(axis=2)) < 60 for k, v in PALETTE.items()}
    found, problems = {}, []
    top = int(0.1 * H)
    for bc, (x0, x1) in enumerate(cols):
        sub = white[top:int(0.75 * H), x0 + 8:x1 - 8]
        rows = bands(sub.any(axis=1))
        heads = []  # weekday letters: the row under a month name, seven letters about 51 px apart
        for i, (r0, r1) in enumerate(rows[1:], 1):
            title = bands(sub[rows[i - 1][0]:rows[i - 1][1] + 1].any(axis=0), gap=12)
            letters = bands(sub[r0:r1 + 1].any(axis=0), gap=8)
            if 10 <= r1 - r0 <= 25 and len(title) == 1 and title[0][1] - title[0][0] > 80 and len(letters) == 7:
                cx = [x0 + 8 + (c0 + c1) / 2 for c0, c1 in letters]
                if all(40 < q - p < 62 for p, q in zip(cx, cx[1:])):
                    heads.append((i, cx))
        if len(heads) != 3:
            problems.append(f"{bc + 1}. stupac mjeseci: {len(heads)} redaka s danima u tjednu umjesto 3")
            continue
        for br, (i, cx) in enumerate(heads):
            m = br * 4 + bc + 1
            weeks = calendar.Calendar().monthdayscalendar(year, m)
            ys = [top + (r0 + r1) / 2 for r0, r1 in rows[i:i + 1 + len(weeks)]]
            if len(ys) != len(weeks) + 1 or not all(30 < q - p < 60 for p, q in zip(ys, ys[1:])):
                problems.append(f"{m}. mjesec: redovi s danima nisu pravilni")
                continue
            pitch = (cx[-1] - cx[0]) / 6
            r = int(0.36 * pitch)
            yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
            disc = yy ** 2 + xx ** 2 <= r * r
            for k, cy in enumerate(ys[1:]):
                for col, x in enumerate(cx):
                    day = weeks[k][col]
                    n = int(white[int(cy - 12):int(cy + 12), int(x - 15):int(x + 15)].sum())
                    if bool(day) != (n > 15):
                        problems.append(f"{m}. mjesec, {k + 1}. red, {DAN[col]}: "
                                        f"{'nema broja dana ' + str(day) if day else 'broj izvan mjeseca'}")
                    if not day:
                        continue
                    win = (slice(int(cy) - r, int(cy) + r + 1), slice(int(x) - r, int(x) + r + 1))
                    hits = {c: int((v[win] & disc).sum()) for c, v in near.items()}
                    colours = {c for c, h in hits.items() if h > 0.12 * disc.sum()}
                    if any(0.03 * disc.sum() < h <= 0.12 * disc.sum() for h in hits.values()):
                        problems.append(f"{date(year, m, day)}: nejasna boja kruga {hits}")
                    if colours:
                        found[date(year, m, day)] = colours
    return found, problems


def page_sections(content):
    """[(names text, image base name)] in page order: the heading or list item before each image."""
    out, label = [], None
    for m in re.finditer(r"<(h\d|li)[^>]*>(.*?)</\1>|<img[^>]+src=\"([^\"]+)\"", content, re.S):
        if m.group(3):
            base = re.sub(r"(-scaled|-\d+x\d+)?\.jpg$", "", m.group(3).rsplit("/", 1)[1])
            if label and base not in (b for _, b in out):
                out.append((label, base))
        else:
            label = " ".join(html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).split())
    return out


def names(text):
    """'Staševica, Spilice-Crpala-Gnječi, Peračko Blato' / 'Komin i Banja' -> list of places."""
    return [p.strip() for p in re.split(r",| i ", text) if p.strip()]


def check_rows(name, found, rule, year, problems):
    """[(date, codes, moved)] with checks; rule = printed mixed-waste weekdays (winter, summer) or None."""
    hol = set(pravila.blagdani(year)) | set(pravila.blagdani(year + 1))
    combined = "smeđa" in {c for cs in found.values() for c in cs}
    mixed = sorted(d for d, cs in found.items() if cs & {"zelena", "smeđa"})
    if rule:
        def regular(d):
            return d.weekday() in (rule[1] if 6 <= d.month <= 9 else rule[0])
        expected = [d for d in (date(year, 1, 1) + timedelta(days=i) for i in range(366)) if d.year == year
                    and regular(d)]
    else:  # the usual weekdays of each month (at least three times in it)
        usual = {m: {w for w, n in Counter(d.weekday() for d in mixed if d.month == m).items() if n >= 3}
                 for m in range(1, 13)}

        def regular(d):
            return d.weekday() in usual[d.month]
        expected = [d for d in (date(year, 1, 1) + timedelta(days=i) for i in range(366)) if d.year == year
                    and regular(d)]
    missing = [d for d in expected if d not in mixed]
    moved, replaced = set(), set()
    for d in mixed:
        if not regular(d):
            src = [h for h in hol if 0 < abs((h - d).days) <= 6 and regular(h) and (h in missing or h.year > year)]
            if src:
                moved.add(d)
                replaced.update(src)
            else:
                problems.append(f"{name}: miješani {d} nije na danu iz rasporeda, a blagdana nema")
    for d in missing:
        if d not in hol:
            problems.append(f"{name}: nema miješanog {d}, a nije blagdan")
    rows = []
    for d, cs in sorted(found.items()):
        codes = "".join(sorted({CODES[c] for c in cs}))
        if combined:
            codes = codes.replace("P", "PKS")
        rows.append((d, codes, d in moved))
    n = Counter(c for _, codes, _ in rows for c in codes)
    if not 60 <= n["M"] <= 130:
        problems.append(f"{name}: miješani {n['M']} puta")
    for code in ("PK" if not combined else "P"):
        ds = sorted(d for d, codes, _ in rows if code in codes)
        lo, hi = (12, 13) if not combined else (26, 52)
        if not lo <= len(ds) <= hi or any((b - a).days > 42 for a, b in zip(ds, ds[1:])):
            problems.append(f"{name}: {code} {len(ds)} puta ili predugi razmak")
    return rows, moved, [d for d in missing if d in hol and d not in replaced]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026)
    year = ap.parse_args(argv).year
    content = json.loads(fetch(REST))[0]["content"]["rendered"]
    sections = [(label, base) for label, base in page_sections(content) if base != INFO_IMAGE]
    if sorted(b for _, b in sections) != sorted(IMAGES):
        sys.exit(f"Kalendari na stranici nisu poznati: {[b for _, b in sections]}; provjerite IMAGES.")
    problems = []
    path = podaci.PODACI / f"{SLUG}.json"
    old = podaci.load(path) if path.exists() else {"zone": {}}
    data = {**PROVIDER, "zone": {}}
    with tempfile.TemporaryDirectory() as tmp:
        for label, base in sorted(sections, key=lambda s: IMAGES[s[1]][1] != "Ploče"):
            sha, jls, rule = IMAGES[base]
            url = re.search(rf'https?://[^"\s]+/{re.escape(base)}(?:-scaled)?\.jpg', content).group(0)
            img = Path(tmp) / f"{base}.jpg"
            fetch(url, img)
            got_sha = hashlib.sha256(img.read_bytes()).hexdigest()
            if got_sha != sha:
                problems.append(f"slika se promijenila: {base} (sha256 {got_sha}); provjerite kalendar i IMAGES")
                continue
            found, img_problems = read_image(img, year)
            problems += [f"{base}: {p}" for p in img_problems]
            if not found:
                continue
            rows, moved, skipped = check_rows(base, found, rule, year, problems)
            places = names(label)
            combined = any("S" in c for _, c, _ in rows)
            if rule:
                when = (f"miješani otpad 1.10.–31.5. {' i '.join(DAN[w] for w in rule[0])}, 1.6.–30.9. "
                        f"{' i '.join(DAN[w] for w in rule[1])}")
            else:
                when = "miješani otpad prema kalendaru (ljeti češće)"
            note = ["Zelena kanta: miješani otpad; plava kanta: papir; žuta kanta: plastika."] if not combined else [
                "Smeđa kanta: miješani komunalni otpad (i biootpad); zelena kanta s narančastim poklopcem: "
                "reciklabilni otpad – papir i karton, plastika, metal i staklo zajedno."]
            if skipped:
                note.append("Bez odvoza miješanog otpada na blagdan: " +
                            ", ".join(f"{d.day}.{d.month}." for d in skipped))
            key = str(len(data["zone"]) + 1)
            prev = old["zone"].get(key, {})
            data["zone"][key] = {
                "jls": jls,
                "podrucje": f"{', '.join(places)} – {when}",
                "opis": label,
                "ulice": places,
                "napomena": " ".join(note),
                "raw": {**({k: v for k, v in prev.get("raw", {}).items() if k != str(year)}
                           if prev.get("jls") == jls else {}),
                        str(year): podaci.month_lines(rows)},
            }
            n = Counter(c for _, codes, _ in rows for c in codes)
            print(f"{jls} – {label}: {dict(n)}, pomaknuto: {', '.join(f'{d:%d.%m.}' for d in sorted(moved)) or '-'}"
                  f", bez odvoza na blagdan: {', '.join(f'{d:%d.%m.}' for d in skipped) or '-'}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit("Ništa nije upisano.")
    podaci.save(SLUG, data)
    print(f"Upisano: {path.relative_to(podaci.ROOT)} ({len(data['zone'])} zona)")


if __name__ == "__main__":
    main()
