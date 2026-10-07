#!/usr/bin/env python3
"""Excel schedules in the Sisak template: one workbook per zone and year.

    python3 gen_xlsx.py            live page data (odvoz.html) -> excel/Raspored_odvoza_ZonaN_<year>.xlsx
    python3 gen_xlsx.py --podaci   every podaci/<slug>.json -> excel/<slug>/Raspored_<City>_ZonaN_<year>.xlsx
                                   plus excel/<slug>/Pregled_zona.xlsx (zones, areas, streets)

Needs openpyxl. Run it after changing a schedule; build.sh only copies the committed files.
"""
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import podaci
from podaci import DAYS, ORDER, TYPES

MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj",
          "kolovoz", "rujan", "listopad", "studeni", "prosinac"]
DAYS_INS = ["ponedjeljkom", "utorkom", "srijedom", "četvrtkom", "petkom", "subotom", "nedjeljom"]
MOVED = "Pomaknuto zbog neradnog dana u tjednu"
PILOT = "Testni projekt: plastika 2x mjesečno"
SISAK_BIO = "Biootpad samo za korisnike koji su odabrali predaju biootpada u spremnicima."
SISAK_NOTES = ["Spremnike iznijeti na javnu površinu najkasnije do 07:00.",
               'Reciklažna dvorišta: "Sisak Stari" (Kralja Zvonimira 7B) i "Novi Sisak" (Capraška 4), '
               "pon-pet 08-20, sub 08-13."]

HEAD_FILL = "375623"
EDGE = Side(style="thin", color="BFBFBF")
BORDER = Border(left=EDGE, right=EDGE, top=EDGE, bottom=EDGE)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
DATE_FMT = "DD.MM.YYYY."


def name(t, prov=None):
    """Waste type label; a provider can rename one (e.g. yellow bin = "Plastika i metal")."""
    return (prov or {}).get("nazivi", {}).get(t, TYPES[t][0])


def fill(t):
    return TYPES[t][2]


def ink(t):
    return TYPES[t][3]


def put(ws, ref, value, bold=False, size=None, color=None, fill=None, center=False, border=True, fmt=None, **font):
    c = ws[ref]
    c.value = value
    c.font = Font(name="Arial", bold=bold, sz=size, color=color, **font)
    if fill:
        c.fill = PatternFill("solid", fgColor=fill)
    if center:
        c.alignment = CENTER
    if border:
        c.border = BORDER
    if fmt:
        c.number_format = fmt
    return c


def head(ws, ref, value, fill=HEAD_FILL, color="FFFFFF"):
    put(ws, ref, value, bold=True, color=color, fill=fill, center=True)


def notes(prov, z, rows, kinds):
    out = []
    if any(moved for *_, moved in rows):
        regular = DAYS_INS[DAYS.index(podaci.regular_day(rows))]
        out.append('Crveno označeni datumi u listu "Raspored" su pomaknuti zbog neradnog dana u tom tjednu '
                   f"(inače je odvoz {regular}).")
    if z.get("napomena"):
        out.append(z["napomena"])
    if prov.get("bioNapomena"):
        if "B" in kinds:
            out.append(prov["bioNapomena"] + (f" Ne odvozi se u {z['bezBioU']}." if z.get("bezBioU") else ""))
        else:
            out.append("Biootpad se na ovom području ne odvozi; preporučuje se kućno kompostiranje.")
    return out + list(prov.get("napomene", []))


def workbook(prov, zone, year):
    z = prov["zone"][zone]
    rows = list(podaci.iter_dates(z, year))
    kinds = [t for t in ORDER if any(t in types for _, types, _ in rows)]
    pilot_from = z.get("pilotOd")
    last = 4 + len(rows)
    tcol = {t: get_column_letter(4 + i) for i, t in enumerate(kinds)}  # type columns in "Raspored"
    note_col = get_column_letter(4 + len(kinds))

    wb = Workbook()
    ov = wb.active
    ov.title = "Pregled"
    sch = wb.create_sheet("Raspored")
    by = wb.create_sheet("Po vrsti")

    # Raspored: one row per collection day, "x" under each bin type.
    put(sch, "A1", f"Raspored odvoza otpada {year}., Zona {zone} ({z['jls']})", bold=True, size=14, border=False)
    put(sch, "A2", '"x" = odvoz tog dana. Spremnike iznijeti do 07:00. '
                   "Prošli datumi su posivljeni.", size=9, color="595959", italic=True, border=False)
    for col, title in zip("ABC", ("Datum", "Dan", "Mjesec")):
        head(sch, f"{col}4", title)
    for t in kinds:
        head(sch, f"{tcol[t]}4", name(t, prov), fill=fill(t), color=ink(t))
    head(sch, f"{note_col}4", "Napomena")
    sch.row_dimensions[4].height = 32
    for r, (day, types, moved) in enumerate(rows, start=5):
        red = {"bold": True, "color": "C00000"} if moved else {}
        put(sch, f"A{r}", datetime(day.year, day.month, day.day), center=True, fmt=DATE_FMT, **red)
        put(sch, f"B{r}", DAYS[day.weekday()], center=True, **red)
        put(sch, f"C{r}", MONTHS[day.month - 1], center=True)
        for t in kinds:
            put(sch, f"{tcol[t]}{r}", "x" if t in types else None, center=True)
        note = [MOVED] if moved else []
        if pilot_from and "P" in types and day.isoformat() >= pilot_from:
            note.append(PILOT)
        put(sch, f"{note_col}{r}", "; ".join(note) or None).alignment = Alignment(vertical="center")
    for t in kinds:
        rng = f"{tcol[t]}5:{tcol[t]}{last}"
        pattern = PatternFill("solid", fgColor=fill(t), bgColor=fill(t))
        sch.conditional_formatting.add(rng, FormulaRule(formula=[f'{tcol[t]}5="x"'],
                                                        font=Font(bold=True, color=ink(t)), fill=pattern))
    sch.conditional_formatting.add(f"A5:{note_col}{last}",
                                   FormulaRule(formula=["$A5<TODAY()"], font=Font(color="A6A6A6"), stopIfTrue=False))
    sch.freeze_panes = "A5"
    sch.auto_filter.ref = f"A4:{note_col}{last}"
    for col, width in zip("ABC", (13, 12, 11)):
        sch.column_dimensions[col].width = width
    for t in kinds:
        sch.column_dimensions[tcol[t]].width = TYPES[t][4]
    sch.column_dimensions[note_col].width = 44

    # Po vrsti: one row per bin per day, handy for filtering by type.
    for col, title in zip("ABCD", ("Datum", "Dan", "Vrsta otpada", "Napomena")):
        head(by, f"{col}1", title)
    r = 1
    for day, types, moved in rows:
        for t in types:
            r += 1
            put(by, f"A{r}", datetime(day.year, day.month, day.day), fmt=DATE_FMT)
            put(by, f"B{r}", DAYS[day.weekday()])
            put(by, f"C{r}", name(t, prov), bold=True, color=ink(t), fill=fill(t))
            put(by, f"D{r}", MOVED if moved else None)
    by.freeze_panes = "A2"
    by.auto_filter.ref = f"A1:D{r}"
    for col, width in zip("ABCD", (13, 12, 26, 40)):
        by.column_dimensions[col].width = width

    # Pregled: next date per type relative to TODAY(), counts per month.
    rs = lambda col: f"Raspored!${col}$5:${col}${last}"  # noqa: E731
    put(ov, "A1", f"Zona {zone}, {year}: pregled odvoza", bold=True, size=14, border=False)
    put(ov, "A3", "Danas", bold=True, border=False)
    put(ov, "B3", "=TODAY()", border=False, fmt=DATE_FMT)
    for col, title in zip("ABCDE", ("Vrsta otpada", "Sljedeći odvoz", "Dan", "Za dana", f"Ukupno u {year}.")):
        head(ov, f"{col}5", title)
    weekdays = ",".join(f'"{d}"' for d in ["nedjelja"] + DAYS[:6])
    for r, t in enumerate(kinds, start=6):
        put(ov, f"A{r}", name(t, prov), bold=True, color=ink(t), fill=fill(t))
        put(ov, f"B{r}", f'=IFERROR(1/(1/_xlfn.MINIFS({rs("A")},{rs(tcol[t])},"x",{rs("A")},">="&$B$3)),'
                         f'"nema više u {year}.")', center=True, fmt=DATE_FMT)
        put(ov, f"C{r}", f'=IF(ISNUMBER(B{r}),INDEX({{{weekdays}}},WEEKDAY(B{r})),"")', center=True)
        put(ov, f"D{r}", f'=IF(ISNUMBER(B{r}),B{r}-$B$3,"")', center=True, fmt="0")
        put(ov, f"E{r}", f'=COUNTIF({rs(tcol[t])},"x")', center=True)
    top = 6 + len(kinds) + 1
    put(ov, f"A{top}", "Broj odvoza po mjesecu", bold=True, size=12, border=False)
    head(ov, f"A{top + 1}", "Mjesec")
    ov.row_dimensions[top + 1].height = 30
    for i, t in enumerate(kinds):
        head(ov, f"{get_column_letter(2 + i)}{top + 1}", name(t, prov), fill=fill(t), color=ink(t))
    for m, month in enumerate(MONTHS):
        r = top + 2 + m
        put(ov, f"A{r}", month)
        for i, t in enumerate(kinds):
            put(ov, f"{get_column_letter(2 + i)}{r}",
                f'=COUNTIFS({rs("C")},$A{r},{rs(tcol[t])},"x")', center=True)
    total = top + 2 + len(MONTHS)
    put(ov, f"A{total}", "Ukupno", bold=True)
    for i, _ in enumerate(kinds):
        col = get_column_letter(2 + i)
        put(ov, f"{col}{total}", f"=SUM({col}{top + 2}:{col}{total - 1})", bold=True, center=True)
    put(ov, f"A{total + 2}", "Napomene:", bold=True, size=10, border=False)
    for i, text in enumerate(notes(prov, z, rows, kinds)):
        put(ov, f"A{total + 3 + i}", f"• {text}", size=10, border=False)
    for col, width in zip("ABCDE", (26, 24, 24, 24, 20)):
        ov.column_dimensions[col].width = width

    # Ulice i naselja: who this zone is, when the provider lists it.
    if z.get("ulice") or z.get("opis"):
        st = wb.create_sheet("Ulice i naselja")
        put(st, "A1", f"Zona {zone} ({z['jls']}): ulice i naselja", bold=True, size=14, border=False)
        if z.get("opis"):
            put(st, "A2", z["opis"], size=9, color="595959", italic=True, border=False)
        head(st, "A4", "Ulica / naselje")
        for i, street in enumerate(z.get("ulice", []), start=5):
            put(st, f"A{i}", street)
        st.column_dimensions["A"].width = 44
        st.freeze_panes = "A5"
    return wb


def index_workbook(prov):
    """One sheet listing every zone of a provider: city, area, streets, collections per year."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Zone"
    put(ws, "A1", f"{prov['davatelj']}: zone odvoza", bold=True, size=14, border=False)
    put(ws, "A2", f"Izvor: {prov.get('izvor', '')}", size=9, color="595959", italic=True, border=False)
    cols = ("Zona", "Grad / općina", "Područje", "Redovni dan", "Vrste otpada", "Odvoza po godini", "Ulice i naselja")
    for i, title in enumerate(cols):
        head(ws, f"{get_column_letter(i + 1)}4", title)
    for r, (zone, z) in enumerate(prov["zone"].items(), start=5):
        rows = list(podaci.iter_dates(z))
        kinds = "".join(t for t in ORDER if any(t in types for _, types, _ in rows))
        per_year = ", ".join(f"{y}: {len(list(podaci.iter_dates(z, int(y))))}" for y in sorted(z["raw"]))
        values = (zone, z.get("jls"), z.get("podrucje"), podaci.regular_day(rows),
                  ", ".join(name(t, prov) for t in kinds), per_year, ", ".join(z.get("ulice", [])))
        for i, v in enumerate(values):
            put(ws, f"{get_column_letter(i + 1)}{r}", v).alignment = Alignment(vertical="top", wrap_text=True)
    for col, width in zip("ABCDEFG", (8, 18, 34, 14, 30, 16, 90)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A5"
    return wb


def ascii_slug(text):
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^A-Za-z0-9]+", "-", text.encode("ascii", "ignore").decode()).strip("-")


def from_page(html_path):
    """The live Sisak page data (odvoz.html) in the podaci format."""
    from gen_ics import load_data
    data = load_data(Path(html_path).read_text(encoding="utf-8"))
    zones = {}
    for zone, v in data["zones"].items():
        zones[zone] = {"jls": v["place"], "raw": v["raw"]}
        for src, dst in (("note", "napomena"), ("noBioIn", "bezBioU"), ("pilotFrom", "pilotOd")):
            if v.get(src):
                zones[zone][dst] = v[src]
    return {"davatelj": "Gospodarenje otpadom Sisak d.o.o.", "bioNapomena": SISAK_BIO,
            "nazivi": {"P": "Plastika, staklo i metal"},
            "napomene": SISAK_NOTES, "zone": zones}


def main(argv):
    here = Path(__file__).parent
    if "--podaci" in argv:
        for slug, prov in podaci.providers():
            out = here / "excel" / slug
            out.mkdir(parents=True, exist_ok=True)
            for zone, z in prov["zone"].items():
                for year in z["raw"]:
                    workbook(prov, zone, int(year)).save(
                        out / f"Raspored_{ascii_slug(z['jls'])}_Zona{zone}_{year}.xlsx")
            index_workbook(prov).save(out / "Pregled_zona.xlsx")
            print(f"{slug}: {len(prov['zone'])} zona -> {out.relative_to(here)}/")
        return
    prov = from_page(here / "odvoz.html")
    out_dir = Path(argv[0]) if argv else here / "excel"
    out_dir.mkdir(exist_ok=True)
    for zone, z in prov["zone"].items():
        for year in z["raw"]:
            workbook(prov, zone, int(year)).save(out_dir / f"Raspored_odvoza_Zona{zone}_{year}.xlsx")


if __name__ == "__main__":
    main(sys.argv[1:])
