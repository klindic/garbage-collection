"""Common schedule format for every provider: podaci/<slug>.json.

{
  "davatelj": "Gospodarenje otpadom Sisak d.o.o.",   provider (company) name
  "web": "https://gos.hr",
  "izvor": "https://...",                             page the schedule was taken from
  "zupanija": "Sisačko-moslavačka",
  "jls": ["Sisak", "Lekenik", ...],                   cities/municipalities it covers
  "napomene": ["...", ...],                           provider-wide notes (recycling yards, rules)
  "nazivi": {"P": "Plastika i metal"},                optional: provider's own names for bin types
  "bioNapomena": "...",                               optional: biowaste note (zones without B get a "no biowaste" line)
  "zone": {
    "1": {
      "jls": "Sisak",                                 which city/municipality the zone is in
      "podrucje": "Zeleni Brijeg, Segestica, Herbos", short description for people
      "opis": "ZELENI BRIJEG (Ante Topića Mimare, ...)", optional: area text as the provider wrote it
      "ulice": ["Ante Topića Mimare", ...],           optional: streets / settlements, for search
      "napomena": "...",                              optional: note for this zone
      "bezBioU": "naseljima ...",                     optional: places in the zone without biowaste collection
      "pilotOd": "YYYY-MM-DD",                        optional: Sisak Zona 5 test project (plastic 2x a month)
      "raw": {"2026": ["01-05 MP 01-12 BK ...", ...]} one string per month, see below
    }
  }
}

"raw": per year, per month "MM-DD codes" pairs; codes are bin type letters from TYPES, "!" marks a
date moved because of a holiday that week. Scripts in izvori/ write these files; gen_xlsx.py turns
them into the Excel template.
"""
import json
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
PODACI = ROOT / "podaci"

# code: (name, bin, Excel fill, Excel font colour, column width)
TYPES = {
    "M": ("Miješani komunalni otpad", "Crna kanta", "262626", "FFFFFF", 16),
    "B": ("Biootpad", "Smeđa kanta", "7B4A2D", "FFFFFF", 12),
    "P": ("Plastika, staklo i metal", "Žuta kanta", "FFE600", "000000", 16),
    "K": ("Papir i karton", "Plava kanta", "3B6EF5", "FFFFFF", 13),
    "S": ("Staklo", "Zelena kanta", "2E8B57", "FFFFFF", 11),
    "L": ("Metal", "Siva kanta", "A6A6A6", "000000", 11),
    "Z": ("Zeleni (vrtni) otpad", "Vreće / zelena kanta", "6B8E23", "FFFFFF", 14),
    "G": ("Glomazni otpad", "Glomazni otpad", "7F7F7F", "FFFFFF", 13),
    "T": ("Tekstil", "Tekstil", "8E44AD", "FFFFFF", 11),
}
ORDER = "MBPKSLZGT"
DAYS = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def providers():
    """All provider files, sorted by name: [(slug, data)]."""
    return [(p.stem, load(p)) for p in sorted(PODACI.glob("*.json"))]


def iter_dates(zone, year=None):
    """(date, "MBP..." codes, moved) for a zone, all years (or one) in date order."""
    for y, months in sorted(zone["raw"].items()):
        if year is not None and int(y) != year:
            continue
        toks = " ".join(months).split()
        for day, codes in zip(toks[0::2], toks[1::2]):
            m, d = map(int, day.split("-"))
            yield date(int(y), m, d), "".join(t for t in ORDER if t in codes), "!" in codes


def month_lines(rows):
    """[(date, codes, moved)] of one year -> ["01-05 MP 01-12 BK ...", ...]."""
    months = {}
    for d, codes, moved in sorted(rows):
        ordered = "".join(t for t in ORDER if t in codes)
        months.setdefault(d.month, []).append(f"{d:%m-%d} {ordered}{'!' if moved else ''}")
    return [" ".join(v) for _, v in sorted(months.items())]


def regular_day(rows):
    """Most common weekday among dates that were not moved."""
    days = Counter(DAYS[d.weekday()] for d, _, moved in rows if not moved)
    return days.most_common(1)[0][0] if days else None


def validate(data):
    """Problems in a provider file (empty list if it is fine)."""
    problems = []
    for key in ("davatelj", "izvor", "zone"):
        if not data.get(key):
            problems.append(f"missing {key}")
    for z, zone in data.get("zone", {}).items():
        if not zone.get("raw"):
            problems.append(f"zona {z}: no dates")
            continue
        seen = set()
        for y, months in zone["raw"].items():
            toks = " ".join(months).split()
            if len(toks) % 2:
                problems.append(f"zona {z} {y}: odd number of tokens")
            for day, codes in zip(toks[0::2], toks[1::2]):
                try:
                    d = date(int(y), *map(int, day.split("-")))
                except ValueError:
                    problems.append(f"zona {z}: bad date {y}-{day}")
                    continue
                bad = set(codes) - set(ORDER) - {"!"}
                if bad or not set(codes) & set(ORDER):
                    problems.append(f"zona {z} {d}: bad codes {codes!r}")
                if d in seen:
                    problems.append(f"zona {z} {d}: listed twice")
                seen.add(d)
    return problems


def dumps(data):
    """JSON text with one zone field per line and one month per line (easy to diff and review)."""
    def val(v):
        return json.dumps(v, ensure_ascii=False)

    out = ["{"]
    top = [k for k in data if k != "zone"]
    for k in top:
        out.append(f'  "{k}": {val(data[k])},')
    out.append('  "zone": {')
    zones = list(data["zone"])
    for i, z in enumerate(zones):
        zone = data["zone"][z]
        out.append(f'    "{z}": {{')
        for k, v in zone.items():
            if k != "raw":
                out.append(f'      "{k}": {val(v)},')
        out.append('      "raw": {')
        years = sorted(zone["raw"])
        for j, y in enumerate(years):
            lines = zone["raw"][y]
            out.append(f'        "{y}": [')
            out += [f"          {val(l)}" + ("," if n < len(lines) - 1 else "") for n, l in enumerate(lines)]
            out.append("        ]" + ("," if j < len(years) - 1 else ""))
        out.append("      }")
        out.append("    }" + ("," if i < len(zones) - 1 else ""))
    out += ["  }", "}"]
    text = "\n".join(out) + "\n"
    assert json.loads(text) == data
    return text


def save(slug, data):
    problems = validate(data)
    if problems:
        raise ValueError(f"{slug}: " + "; ".join(problems[:10]))
    PODACI.mkdir(exist_ok=True)
    (PODACI / f"{slug}.json").write_text(dumps(data), encoding="utf-8")
