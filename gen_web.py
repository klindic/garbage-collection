#!/usr/bin/env python3
"""Data for the national page (hrvatska.html) from podaci/*.json: an index, a search list, one file per zone,
calendar feeds and the Excel files.

    python3 gen_web.py <out dir>

Writes into <out dir>:
  data/hr.json            providers, counties, cities/municipalities and zones (what the page loads first)
  data/ulice.json         [[street or settlement, zone], ...] for the address search (loaded on first use)
  data/z/<slug>/<key>.json  one zone's dates, streets and notes (loaded when the zone is chosen)
  ics/<slug>/<key>-<alarm>.ics  calendar feeds, one per zone and reminder time
  excel/<slug>/*.xlsx     copied from excel/

A city or municipality whose provider changed during the year (zones of one provider end before the other's
start, and the earlier one has a single zone) gets one schedule: the earlier dates are put in front of each
later zone. Standard library only, so it runs in GitHub Actions without installing anything.
"""
import csv
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import podaci
from gen_ics import escape, fold

ROOT = Path(__file__).parent
SITE_URL = os.environ.get("SITE_URL", "https://klindic.github.io/garbage-collection/")
# Reminder times offered on the page; all-day events start at 00:00, so -PT6H is 18:00 the day before.
FEEDS = {"1800": ("-PT6H", "Sutra odvoz"), "jutro-0600": ("PT6H", "Danas odvoz"), "bez": (None, None)}
FEED_FROM_DAYS = 31  # feeds hold events from a month before the build on (calendars only need what is ahead)
# Providers shown on the site under another slug and without their name, links or overview workbook.
HIDDEN = {"sisak-gos": "sisak"}
ICONS = {"M": "⚫", "B": "\U0001f7e4", "P": "\U0001f7e1", "K": "\U0001f535", "S": "\U0001f7e2",
         "L": "⚪", "Z": "\U0001f33f", "G": "\U0001f6cb", "T": "\U0001f455"}


def ascii_slug(text):  # same as gen_xlsx.ascii_slug (which needs openpyxl to import)
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^A-Za-z0-9]+", "-", text.encode("ascii", "ignore").decode()).strip("-")


def registry():
    """[(county, name, row)] of every city and municipality, one row each."""
    rows = {}
    for r in csv.DictReader(open(ROOT / "istrazivanje" / "jls_davatelj.csv", encoding="utf-8")):
        rows.setdefault((r["zupanija"], r["jls"]), r)
    missing = {(r["zupanija"], r["jls"]): r["razlog"]
               for r in csv.DictReader(open(ROOT / "istrazivanje" / "bez_rasporeda.csv", encoding="utf-8"))}
    return rows, missing


def reason(text):
    """Short reason code for a JLS without a schedule (from bez_rasporeda.csv): b = it is published but cannot be
    downloaded from here, n = nothing published (or not found), p = only part of it or unclear, not included yet."""
    if re.search(r"cloudflare|blokiran|prekida|nedostupn|nije dostupn|trenutno pada", text, re.I):
        return "b"
    return "n" if re.match(r"ne\b", text) else "p"


def resolve(name, county, by_name):
    """Registry key of a zone's JLS; two pairs of JLS share a name and are told apart by the provider's county."""
    keys = by_name.get(name, [])
    if len(keys) > 1:
        keys = [k for k in keys if k[0] == county] or keys
    return keys[0] if len(keys) == 1 else None


def dates_of(zone):
    return list(podaci.iter_dates(zone))


def type_name(t, nazivi):
    return nazivi.get(t, podaci.TYPES[t][0])


def ics(zone_id, title, rows, nazivi, trigger, prefix, stamp):
    url = f"{SITE_URL}?z={zone_id}"
    uid = re.sub(r"[^A-Za-z0-9-]", "-", zone_id)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//klindic//Odvoz otpada//HR", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:" + escape(title), "X-WR-CALDESC:" + escape(f"{title}. {url}"),
             "X-WR-TIMEZONE:Europe/Zagreb", "REFRESH-INTERVAL;VALUE=DURATION:P1D", "X-PUBLISHED-TTL:P1D"]
    for day, codes, moved in rows:
        names = ", ".join(type_name(t, nazivi) for t in codes)
        summary = "".join(ICONS[t] for t in codes) + " " + names[0] + names[1:].lower()
        lines += ["BEGIN:VEVENT", f"UID:{day.isoformat()}@{uid}.odvoz.klindic.github.io", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
                  "SUMMARY:" + escape(summary), "URL:" + url, "TRANSP:TRANSPARENT"]
        if moved:
            lines.append("DESCRIPTION:Pomaknuto zbog blagdana.")
        if trigger:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{prefix}", f"TRIGGER:{trigger}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold(line) for line in lines) + "\r\n").encode("utf-8")


def main(out):
    out = Path(out)
    rows, missing = registry()
    by_name = defaultdict(list)
    for k in rows:
        by_name[k[1]].append(k)
    counties = sorted({k[0] for k in rows}, key=lambda c: (c != "Grad Zagreb", c))
    jls_keys = sorted(rows, key=lambda k: (counties.index(k[0]), k[1]))
    jls_index = {k: i for i, k in enumerate(jls_keys)}

    providers, zones, problems = [], [], []
    per_jls = defaultdict(list)  # jls index -> [(provider index, key, zone)]
    for slug, data in podaci.providers():
        pi = len(providers)
        hidden = slug in HIDDEN
        providers.append({"s": HIDDEN.get(slug, slug), "src": slug, "d": "" if hidden else data["davatelj"],
                          "w": "" if hidden else data.get("web", ""), "i": "" if hidden else data.get("izvor", ""),
                          "n": data.get("napomene", []), "nz": data.get("nazivi", {}),
                          "bio": data.get("bioNapomena", "")})
        for key, z in data["zone"].items():
            jk = resolve(z["jls"], data.get("zupanija"), by_name)
            if jk is None:
                problems.append(f"{slug} zona {key}: nepoznat JLS {z['jls']!r}")
                continue
            per_jls[jls_index[jk]].append((pi, key, z))

    # Handovers: the earlier provider's single zone ends before every zone of the later one starts.
    merged = {}
    for ji, items in per_jls.items():
        provs = sorted({pi for pi, _, _ in items})
        if len(provs) != 2:
            continue
        spans = {pi: [d for p, _, z in items if p == pi for d, _, _ in dates_of(z)] for pi in provs}
        a, b = sorted(provs, key=lambda pi: min(spans[pi]))
        early = [it for it in items if it[0] == a]
        if len(early) == 1 and max(spans[a]) < min(spans[b]):
            merged[ji] = (early[0], b)
            per_jls[ji] = [it for it in items if it[0] == b]
            print(f"Primopredaja: {jls_keys[ji][1]} – {providers[a]['d']} do {max(spans[a])}, "
                  f"{providers[b]['d']} od {min(spans[b])}")
        else:
            print(f"Dva davatelja (zasebne zone): {jls_keys[ji][1]} – {providers[a]['s']}, {providers[b]['s']}")

    zdir, idir, xdir = out / "data" / "z", out / "ics", out / "excel"
    for d in (zdir, idir, xdir):
        if d.exists():
            shutil.rmtree(d)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    search, used_ids, feeds = [], set(), 0
    jls_zones = defaultdict(list)
    for ji in sorted(per_jls):
        many = len(per_jls[ji]) > 1
        for pi, key, z in per_jls[ji]:
            prov, slug, src = providers[pi], providers[pi]["s"], providers[pi]["src"]
            safe = ascii_slug(key) or "zona"
            zone_id = f"{slug}/{safe}"
            if zone_id in used_ids:
                problems.append(f"dvostruki id zone {zone_id}")
                continue
            used_ids.add(zone_id)
            rows_ = dates_of(z)
            note = z.get("napomena", "")
            if ji in merged:
                (epi, ekey, ez), _ = merged[ji]
                before = dates_of(ez)
                rows_ = before + rows_
                note = (f"Do {before[-1][0]:%d.%m.%Y.} odvoz je obavljao {providers[epi]['d']} (raspored ovdje je spojen). "
                        + note).strip()
            raw = defaultdict(list)
            for d, codes, moved in rows_:
                raw[d.year].append((d, codes, moved))
            zfile = {"raw": {str(y): podaci.month_lines(r) for y, r in sorted(raw.items())},
                     "u": z.get("ulice", []), "o": z.get("opis", ""), "n": note,
                     "bb": z.get("bezBioU", ""), "po": z.get("pilotOd", "")}
            path = zdir / slug / f"{safe}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(zfile, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            jls_name = jls_keys[ji][1]
            years = sorted({str(d.year) for d, _, _ in rows_})
            xls = [y for y in years if (ROOT / "excel" / src / f"Raspored_{ascii_slug(z['jls'])}_Zona{key}_{y}.xlsx").exists()]
            zi = len(zones)
            zones.append({"id": zone_id, "p": pi, "j": ji, "a": z.get("podrucje", ""),
                          "f": rows_[0][0].isoformat() if rows_ else "", "l": rows_[-1][0].isoformat() if rows_ else "",
                          "x": f"excel/{slug}/Raspored_{ascii_slug(z['jls'])}_Zona{key}_" if xls else "", "xy": xls})
            jls_zones[ji].append(zi)
            for u in dict.fromkeys(z.get("ulice", [])):
                search.append([u, zi])
            area = z.get("podrucje", "")
            if many and not z.get("ulice") and area and "nisu objavljena" not in area:
                search.append([area, zi])  # e.g. Rijeka's "MO Turnić, ruta 7A"
            title = f"Odvoz otpada {jls_name}" + (f" – {z.get('podrucje', '')[:40].rstrip(' ,;–-')}" if many else "")
            fdir = idir / slug
            fdir.mkdir(parents=True, exist_ok=True)
            ahead = [r for r in rows_ if r[0] >= date.today() - timedelta(days=FEED_FROM_DAYS)]
            for alarm, (trigger, prefix) in FEEDS.items():
                feed = ics(zone_id, title, ahead, prov["nz"], trigger, prefix, stamp)
                (fdir / f"{safe}-{alarm}.ics").write_bytes(feed)
                feeds += 1
    for p in providers:
        if (ROOT / "excel" / p["src"]).is_dir():
            skip = shutil.ignore_patterns("Pregled_zona.xlsx") if p["src"] in HIDDEN else None
            shutil.copytree(ROOT / "excel" / p["src"], xdir / p["s"], ignore=skip)
        del p["src"]

    jls = []
    for i, k in enumerate(jls_keys):
        r = rows[k]
        entry = {"n": k[1], "t": r["tip"], "c": counties.index(k[0]), "s": int(r["stanovnika"] or 0),
                 "z": jls_zones.get(i, [])}
        if not entry["z"]:
            entry["x"] = reason(missing.get(k, ""))
            entry["d"] = r["davatelj"]
        jls.append(entry)
    index = {"v": date.today().isoformat(), "p": providers, "c": counties, "j": jls, "z": zones}
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "data" / "hr.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "data" / "ulice.json").write_text(json.dumps(search, ensure_ascii=False, separators=(",", ":")),
                                             encoding="utf-8")
    covered = [j for j in jls if j["z"]]
    print(f"JLS s rasporedom {len(covered)}/{len(jls)}, zona {len(zones)}, pojmova za pretragu {len(search)}, "
          f"kalendara {feeds}")
    for p in problems:
        print("PROBLEM", p)
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "_site")
