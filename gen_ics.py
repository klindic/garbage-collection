#!/usr/bin/env python3
"""Generate subscribable iCalendar feeds for every zone from the schedule data in odvoz.html."""
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SITE_URL = "https://klindic.github.io/garbage-collection/"
# One feed per zone and alarm time, because a static host cannot personalise a single feed.
# All-day events start at 00:00, so "-PT6H" fires at 18:00 the day before and "PT6H" at 06:00 the same day.
FEEDS = {f"{h}00": (f"-PT{24 - h}H", "Sutra odvoz") for h in range(16, 23)}
FEEDS.update({
    "jutro-0530": ("PT5H30M", "Danas odvoz"),
    "jutro-0600": ("PT6H", "Danas odvoz"),
    "bez": (None, None),
})
DEFAULT_ALARM = "1800"  # odvoz-zonaN.ics is the same as odvoz-zonaN-1800.ics
# Before zones were added the site only had Zona 5, published as odvoz-<alarm>.ics and odvoz.ics.
# Those names stay live with the same UIDs so existing subscriptions keep working.
LEGACY_ZONE = "5"

TYPES = {
    "M": ("\u26ab", "Miješani", "Crna kanta: miješani komunalni otpad"),
    "B": ("\U0001f7e4", "Biootpad", "Smeđa kanta: biootpad"),
    "P": ("\U0001f7e1", "Plastika", "Žuta kanta: plastika, staklo i metal"),
    "K": ("\U0001f535", "Papir", "Plava kanta: papir i karton"),
}
ORDER = "MBPK"


def load_data(html):
    return json.loads(re.search(r'<script type="application/json" id="zones">(.*?)</script>', html, re.S).group(1))


def parse_schedule(data, zone):
    """(date, bin types, moved) for every collection day of the zone, all years in order."""
    for year, months in sorted(data["zones"][zone]["raw"].items()):
        raw = " ".join(months).split()
        for day, codes in zip(raw[0::2], raw[1::2]):
            month, dom = map(int, day.split("-"))
            yield date(int(year), month, dom), [t for t in ORDER if t in codes], "!" in codes


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


def build(data, zone, trigger, alarm_prefix):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    url = f"{SITE_URL}?zona={zone}"
    place = data["zones"][zone]["place"]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//klindic//Odvoz otpada Zona {zone}//HR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Odvoz otpada Zona {zone}",
        "X-WR-CALDESC:" + escape(f"Odvoz otpada, Zona {zone} ({place}). {url}"),
        "X-WR-TIMEZONE:Europe/Zagreb",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]
    for day, types, moved in parse_schedule(data, zone):
        icons = "".join(TYPES[t][0] for t in types)
        names = ", ".join(TYPES[t][1] for t in types)
        names = names[0] + names[1:].lower()
        summary = f"{icons} {names}"
        details = [TYPES[t][2] for t in types]
        details.append("Kante iznesi do 07:00.")
        if moved:
            details.append("Pomaknuto zbog neradnog dana u tom tjednu.")
        details.append(url)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{day.isoformat()}@odvoz-zona{zone}.klindic.github.io",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            "SUMMARY:" + escape(summary),
            "DESCRIPTION:" + escape("\n".join(details)),
            "URL:" + url,
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
    data = load_data(Path(__file__).with_name("odvoz.html").read_text(encoding="utf-8"))
    out_dir = Path(sys.argv[1])
    for zone in data["zones"]:
        for alarm, (trigger, prefix) in FEEDS.items():
            feed = build(data, zone, trigger, prefix).encode("utf-8")
            names = [f"odvoz-zona{zone}-{alarm}.ics"]
            if zone == LEGACY_ZONE:
                names.append(f"odvoz-{alarm}.ics")
            if alarm == DEFAULT_ALARM:
                names.append(f"odvoz-zona{zone}.ics")
                if zone == LEGACY_ZONE:
                    names.append("odvoz.ics")
            for name in names:
                (out_dir / name).write_bytes(feed)
