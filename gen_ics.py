#!/usr/bin/env python3
"""Generate subscribable iCalendar feeds from the RAW schedule in odvoz-zona5.html."""
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SITE_URL = "https://klindic.github.io/garbage-collection/"
UID_DOMAIN = "odvoz-zona5.klindic.github.io"
# One feed per alarm time, because a static host cannot personalise a single feed.
# All-day events start at 00:00, so "-PT6H" fires at 18:00 the day before and "PT6H" at 06:00 the same day.
FEEDS = {f"odvoz-{h}00.ics": (f"-PT{24 - h}H", "Sutra odvoz") for h in range(16, 23)}
FEEDS.update({
    "odvoz-jutro-0530.ics": ("PT5H30M", "Danas odvoz"),
    "odvoz-jutro-0600.ics": ("PT6H", "Danas odvoz"),
    "odvoz-bez.ics": (None, None),
})
DEFAULT_FEED = "odvoz-1800.ics"  # also published as odvoz.ics for existing subscribers

TYPES = {
    "M": ("\u26ab", "Miješani", "Crna kanta: miješani komunalni otpad"),
    "B": ("\U0001f7e4", "Biootpad", "Smeđa kanta: biootpad"),
    "P": ("\U0001f7e1", "Plastika", "Žuta kanta: plastika, staklo i metal"),
    "K": ("\U0001f535", "Papir", "Plava kanta: papir i karton"),
}
ORDER = "MBPK"


def parse_schedule(html):
    year = int(re.search(r"const YEAR = (\d{4});", html).group(1))
    raw = re.search(r"const RAW = `([^`]*)`", html).group(1).split()
    for day, codes in zip(raw[0::2], raw[1::2]):
        month, dom = map(int, day.split("-"))
        yield date(year, month, dom), [t for t in ORDER if t in codes], "!" in codes


def escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    """Fold to 75 octets per RFC 5545 without splitting a UTF-8 character."""
    out, current = [], ""
    for ch in line:
        limit = 75 if not out else 74
        if len((current + ch).encode("utf-8")) > limit:
            out.append(current)
            current = ch
        else:
            current += ch
    out.append(current)
    return "\r\n ".join(out)


def build(html, trigger, alarm_prefix):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//klindic//Odvoz otpada Zona 5//HR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Odvoz otpada",
        "X-WR-CALDESC:" + escape("Odvoz otpada, Zona 5 Sisak. " + SITE_URL),
        "X-WR-TIMEZONE:Europe/Zagreb",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]
    for day, types, moved in parse_schedule(html):
        icons = "".join(TYPES[t][0] for t in types)
        names = ", ".join(TYPES[t][1] for t in types)
        names = names[0] + names[1:].lower()
        summary = f"{icons} {names}"
        details = [TYPES[t][2] for t in types]
        details.append("Kante iznesi do 07:00.")
        if moved:
            details.append("Pomaknuto zbog neradnog dana u tom tjednu.")
        details.append(SITE_URL)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{day.isoformat()}@{UID_DOMAIN}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            "SUMMARY:" + escape(summary),
            "DESCRIPTION:" + escape("\n".join(details)),
            "URL:" + SITE_URL,
            "TRANSP:TRANSPARENT",
        ]
        if trigger:
            lines += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:" + escape(f"{alarm_prefix}: {summary}"),
                f"TRIGGER:{trigger}",
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


if __name__ == "__main__":
    src = Path(__file__).with_name("odvoz-zona5.html").read_text(encoding="utf-8")
    out_dir = Path(sys.argv[1])
    for name, (trigger, prefix) in FEEDS.items():
        data = build(src, trigger, prefix).encode("utf-8")
        (out_dir / name).write_bytes(data)
        if name == DEFAULT_FEED:
            (out_dir / "odvoz.ics").write_bytes(data)
