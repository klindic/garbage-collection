#!/usr/bin/env python3
"""Coverage of podaci/*.json against all 556 cities and municipalities (istrazivanje/jls_davatelj.csv).

    python3 pokrivenost.py [--popis]     summary; --popis also lists every JLS without a schedule

A JLS counts as covered when at least one zone of some provider file names it. Two pairs of JLS share a
name (Privlaka, Sveta Nedelja); the provider's county tells them apart.
"""
import csv
import sys
from collections import defaultdict
from datetime import date

import podaci


def main(argv):
    jls = list({(r["zupanija"], r["jls"]): r for r in  # a JLS with two providers has two rows
                csv.DictReader(open(podaci.ROOT / "istrazivanje" / "jls_davatelj.csv", encoding="utf-8"))}.values())
    by_name = defaultdict(list)
    for r in jls:
        by_name[r["jls"]].append(r)
    covered, providers, zones, unknown = {}, 0, 0, set()
    for slug, data in podaci.providers():
        providers += 1
        for z in data["zone"].values():
            zones += 1
            rows = by_name.get(z["jls"], [])
            if len(rows) > 1:  # same name in two counties
                rows = [r for r in rows if r["zupanija"] == data.get("zupanija")] or rows[:1]
            if not rows:
                unknown.add(f"{z['jls']} ({slug})")
                continue
            last = max((d for d, _, _ in podaci.iter_dates(z)), default=None)
            key = (rows[0]["zupanija"], rows[0]["jls"])
            covered.setdefault(key, set()).add((slug, last))
    pop = lambda r: int(r["stanovnika"] or 0)  # noqa: E731
    total = sum(pop(r) for r in jls)
    have = [r for r in jls if (r["zupanija"], r["jls"]) in covered]
    print(f"Davatelja s podacima: {providers}, zona: {zones}")
    print(f"JLS s rasporedom: {len(have)} od {len(jls)}; stanovnika {sum(map(pop, have)):,} od {total:,} "
          f"({sum(map(pop, have)) / total:.1%})".replace(",", "."))
    ending = sorted((max(d for _, d in v if d), k[1]) for k, v in covered.items() if any(d for _, d in v))
    soon = [f"{n} ({d:%d.%m.})" for d, n in ending if d < date(date.today().year, 12, 1)]
    if soon:
        print(f"Raspored završava prije prosinca ({len(soon)}): {', '.join(soon)}")
    if unknown:
        print(f"Nepoznata imena JLS u podacima: {', '.join(sorted(unknown))}")
    if "--popis" in argv:
        print("\nBez rasporeda (po broju stanovnika):")
        for r in sorted((r for r in jls if (r["zupanija"], r["jls"]) not in covered), key=pop, reverse=True):
            print(f"  {pop(r):>7}  {r['jls']} ({r['zupanija']}) – {r['davatelj'][:60]}")


if __name__ == "__main__":
    main(sys.argv[1:])
