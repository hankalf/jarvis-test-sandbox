"""Fetching and expanding read-only ICS calendar feeds."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 30.0


def _as_https(url: str) -> str:
    """iCloud hands out webcal:// links; they're plain HTTPS underneath."""
    if url.startswith("webcal://"):
        return "https://" + url[len("webcal://") :]
    return url


def _stable_id(feed_name: str, uid: str, start: datetime | date) -> str:
    raw = f"{feed_name}|{uid}|{start.isoformat()}"
    return "f_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


class CalendarFeeds:
    """Holds the last successfully fetched copy of each feed.

    A feed that fails to refresh keeps serving its previous contents rather
    than blanking the wall display -- a flaky network shouldn't look like an
    empty schedule.
    """

    def __init__(self, feeds: list[dict], tz: ZoneInfo) -> None:
        self.feeds = feeds
        self.tz = tz
        self._parsed: dict[str, icalendar.Calendar] = {}
        self._status: dict[str, dict] = {
            f["name"]: {"ok": False, "lastSuccess": None, "error": "not fetched yet"}
            for f in feeds
        }
        self._lock = asyncio.Lock()

    @property
    def status(self) -> list[dict]:
        return [{"name": name, **info} for name, info in self._status.items()]

    async def refresh(self) -> None:
        if not self.feeds:
            return
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            results = await asyncio.gather(
                *(self._fetch_one(client, f) for f in self.feeds),
                return_exceptions=True,
            )
        async with self._lock:
            for feed, result in zip(self.feeds, results):
                name = feed["name"]
                if isinstance(result, BaseException):
                    self._status[name] = {
                        "ok": False,
                        "lastSuccess": self._status[name].get("lastSuccess"),
                        "error": f"{type(result).__name__}: {result}",
                    }
                    log.warning("calendar %r refresh failed: %s", name, result)
                    continue
                self._parsed[name] = result
                self._status[name] = {
                    "ok": True,
                    "lastSuccess": datetime.now(self.tz).isoformat(),
                    "error": None,
                }

    async def _fetch_one(
        self, client: httpx.AsyncClient, feed: dict
    ) -> icalendar.Calendar:
        resp = await client.get(_as_https(feed["url"]))
        resp.raise_for_status()
        # Parsing is CPU-bound and a year of a busy calendar is not tiny, so
        # keep it off the event loop.
        return await asyncio.to_thread(icalendar.Calendar.from_ical, resp.content)

    def events_between(self, start: datetime, end: datetime) -> list[dict]:
        out: list[dict] = []
        for feed in self.feeds:
            cal = self._parsed.get(feed["name"])
            if cal is None:
                continue
            try:
                occurrences = recurring_ical_events.of(cal).between(start, end)
            except Exception as exc:  # a single malformed VEVENT shouldn't blank the wall
                log.warning("expanding %r failed: %s", feed["name"], exc)
                continue
            for occurrence in occurrences:
                event = self._normalize(occurrence, feed)
                if event is not None:
                    out.append(event)
        out.sort(key=lambda e: (e["start"], e["title"]))
        return out

    def _normalize(self, component, feed: dict) -> dict | None:
        dtstart = component.get("DTSTART")
        if dtstart is None:
            return None
        raw_start = dtstart.dt
        dtend = component.get("DTEND")
        raw_end = dtend.dt if dtend is not None else None

        all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)
        if all_day:
            if raw_end is None:
                raw_end = raw_start + timedelta(days=1)
            start_dt = datetime.combine(raw_start, time.min, tzinfo=self.tz)
            end_dt = datetime.combine(raw_end, time.min, tzinfo=self.tz)
        else:
            start_dt = self._localize(raw_start)
            end_dt = (
                self._localize(raw_end)
                if raw_end is not None
                else start_dt + timedelta(hours=1)
            )

        # Zero-length events render as invisible slivers; give them a minute.
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=1)

        uid = str(component.get("UID", ""))
        return {
            "id": _stable_id(feed["name"], uid, start_dt),
            "title": str(component.get("SUMMARY", "(no title)")),
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "allDay": all_day,
            "location": str(component.get("LOCATION")) if component.get("LOCATION") else None,
            "notes": str(component.get("DESCRIPTION")) if component.get("DESCRIPTION") else None,
            "calendar": feed["name"],
            "color": feed["color"],
            "source": "feed",
            "confirmed": True,
            "editable": False,
        }

    def _localize(self, value: datetime) -> datetime:
        """Floating times (no tzinfo) are meant as local wall-clock time."""
        if value.tzinfo is None:
            return value.replace(tzinfo=self.tz)
        return value.astimezone(self.tz)
