"""Collection dates from rules ("every Monday", "every 2nd Wednesday from 7.1.", "1st Friday of the month"),
with Croatian public holidays, for providers that publish rules instead of a list of dates.

    from pravila import tjedno, svaki_n_tjedan, mjesecno, blagdani, primijeni_blagdane
"""
from datetime import date, timedelta

DANI = {"pon": 0, "uto": 1, "sri": 2, "čet": 3, "pet": 4, "sub": 5, "ned": 6}


def uskrs(year):
    """Easter Sunday (Gregorian, anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def blagdani(year):
    """Croatian public holidays (Zakon o blagdanima, spomendanima i neradnim danima)."""
    e = uskrs(year)
    fixed = [(1, 1), (1, 6), (5, 1), (5, 30), (6, 22), (8, 5), (8, 15), (11, 1), (11, 18), (12, 25), (12, 26)]
    return sorted({date(year, m, d) for m, d in fixed} | {e, e + timedelta(days=1), e + timedelta(days=60)})


def _days(year):
    d = date(year, 1, 1)
    while d.year == year:
        yield d
        d += timedelta(days=1)


def tjedno(year, dan):
    """Every week on a weekday ('pon'...'ned')."""
    return [d for d in _days(year) if d.weekday() == DANI[dan]]


def svaki_n_tjedan(year, dan, n, prvi):
    """Every n-th week on a weekday, counting from the first date `prvi` (a date in that year)."""
    return [d for d in tjedno(year, dan) if d >= prvi and (d - prvi).days % (7 * n) == 0]


def neparni_tjedni(year, dan, neparni=True):
    """Weekday in odd (or even) ISO weeks."""
    return [d for d in tjedno(year, dan) if (d.isocalendar()[1] % 2 == 1) == neparni]


def mjesecno(year, dan, koji):
    """`koji`-th (1..5, or -1 for last) weekday of every month."""
    out = []
    for month in range(1, 13):
        days = [date(year, month, x) for x in range(1, 32) if _valid(year, month, x)]
        days = [d for d in days if d.weekday() == DANI[dan]]
        if koji == -1 or len(days) >= koji:
            out.append(days[-1] if koji == -1 else days[koji - 1])
    return out


def _valid(y, m, d):
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def primijeni_blagdane(dates, pravilo="sljedeci", year=None):
    """Move dates that fall on a public holiday. Returns [(date, moved)].

    pravilo: "sljedeci" next working day (Mon-Sat, not a holiday), "subota" the Saturday of that week,
    "prethodni" previous working day, "isti" keep (collection runs on holidays), "izostavi" drop it,
    "tjedni_pomak" one day later for every weekday holiday from Monday up to that day in the same week
    (Gospodarenje otpadom Sisak: a Tuesday holiday also moves the Wednesday and Thursday rounds).
    """
    years = {d.year for d in dates} | ({year} if year else set())
    hol = {h for y in years for h in blagdani(y)} | {h for y in years for h in blagdani(y + 1)}
    out = []
    for d in dates:
        if pravilo == "tjedni_pomak":
            monday = d - timedelta(days=d.weekday())
            shift = sum(1 for h in hol if monday <= h <= d and h.weekday() < 5)
            out.append((d + timedelta(days=shift), shift > 0))
            continue
        if d not in hol or pravilo == "isti":
            out.append((d, False))
            continue
        if pravilo == "izostavi":
            continue
        step = -1 if pravilo == "prethodni" else 1
        n = d + timedelta(days=step) if pravilo != "subota" else d + timedelta(days=5 - d.weekday())
        while n in hol or n.weekday() == 6:
            n += timedelta(days=step)
        out.append((n, True))
    return out
