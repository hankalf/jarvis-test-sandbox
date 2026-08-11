"""Events the panel generates itself: birthdays and medication doses.

These aren't stored as rows. A birthday recurs forever and a medication dose
happens every day, so materialising them would mean either an unbounded table
or a job that tops it up. Instead they're synthesised on demand for whatever
window is being rendered, which also means editing config.yaml is enough to
change them -- no migration, nothing to clean up.

Only the *state* that can't be derived is stored: whether a dose was marked
taken (see Store.mark_dose / doses_taken).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DEFAULT_BIRTHDAY_COLOR = "#b02a45"
DEFAULT_MEDICATION_COLOR = "#1f7a5a"

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", (text or "").lower()).strip("-") or "item"


def _parse_hhmm(value: str) -> time | None:
    try:
        hours, minutes = str(value).split(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return None


def _parse_birthday(value) -> tuple[int | None, int, int] | None:
    """Accepts YYYY-MM-DD (age known) or MM-DD (age unknown)."""
    if isinstance(value, date):
        return value.year, value.month, value.day
    text = str(value or "").strip()
    parts = text.split("-")
    try:
        if len(parts) == 3:
            return int(parts[0]), int(parts[1]), int(parts[2])
        if len(parts) == 2:
            return None, int(parts[0]), int(parts[1])
    except ValueError:
        pass
    return None


def _days_in_window(start: datetime, end: datetime) -> list[date]:
    day = start.date()
    last = end.date()
    out = []
    while day <= last and len(out) < 400:
        out.append(day)
        day += timedelta(days=1)
    return out


def birthday_events(birthdays: list[dict], start: datetime, end: datetime, tz: ZoneInfo) -> list[dict]:
    events = []
    for entry in birthdays or []:
        name = str(entry.get("name") or "").strip()
        parsed = _parse_birthday(entry.get("date"))
        if not name or parsed is None:
            log.warning("skipping birthday entry %r: needs a name and a date", entry)
            continue
        born_year, month, day = parsed
        colour = entry.get("color") or DEFAULT_BIRTHDAY_COLOR

        for current in _days_in_window(start, end):
            if (current.month, current.day) != (month, day):
                # 29 Feb in a non-leap year: mark it on the 28th rather than
                # silently skipping the birthday for three years out of four.
                if not (month == 2 and day == 29 and current.month == 2
                        and current.day == 28 and not _is_leap(current.year)):
                    continue

            title = f"{name}'s birthday"
            if born_year:
                age = current.year - born_year
                if age > 0:
                    title = f"{name} turns {age}"

            day_start = datetime.combine(current, time.min, tzinfo=tz)
            events.append({
                "id": f"bday_{slug(name)}_{current.isoformat()}",
                "title": title,
                "start": day_start.isoformat(),
                "end": (day_start + timedelta(days=1)).isoformat(),
                "allDay": True,
                "location": None,
                "notes": entry.get("note") or None,
                "calendar": "Birthdays",
                "color": colour,
                "source": "birthday",
                "confirmed": True,
                "editable": False,
                "kind": "birthday",
            })
    return events


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def medication_events(
    medications: list[dict],
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    taken: dict[str, str] | None = None,
) -> list[dict]:
    """One event per scheduled dose. `taken` maps dose id -> ISO timestamp."""
    taken = taken or {}
    events = []
    for entry in medications or []:
        name = str(entry.get("name") or "").strip()
        times = [t for t in (_parse_hhmm(v) for v in entry.get("times") or []) if t]
        if not name or not times:
            log.warning("skipping medication entry %r: needs a name and times", entry)
            continue
        colour = entry.get("color") or DEFAULT_MEDICATION_COLOR
        minutes = int(entry.get("duration_minutes") or 15)

        for current in _days_in_window(start, end):
            for at in times:
                dose_start = datetime.combine(current, at, tzinfo=tz)
                if not (start <= dose_start < end):
                    continue
                dose_id = f"{slug(name)}|{current.isoformat()}|{at.strftime('%H:%M')}"
                events.append({
                    "id": f"med_{dose_id}",
                    "title": name,
                    "start": dose_start.isoformat(),
                    "end": (dose_start + timedelta(minutes=minutes)).isoformat(),
                    "allDay": False,
                    "location": None,
                    "notes": entry.get("notes") or None,
                    "calendar": "Medication",
                    "color": colour,
                    "source": "medication",
                    "confirmed": True,
                    "editable": False,
                    "kind": "medication",
                    "doseId": dose_id,
                    "takenAt": taken.get(dose_id),
                })
    return events


def adherence_today(medications: list[dict], tz: ZoneInfo, taken: dict[str, str]) -> dict:
    """Today's dose count and how many are marked taken, for the remote page."""
    now = datetime.now(tz)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    doses = medication_events(medications, midnight, midnight + timedelta(days=1), tz, taken)
    due = [d for d in doses if datetime.fromisoformat(d["start"]) <= now]
    return {
        "total": len(doses),
        "taken": sum(1 for d in doses if d["takenAt"]),
        # Past their time and still unticked -- the number worth a phone call.
        "missed": sum(1 for d in due if not d["takenAt"]),
        "next": next((d["start"] for d in doses
                      if not d["takenAt"] and datetime.fromisoformat(d["start"]) > now), None),
    }
