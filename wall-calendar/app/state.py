"""Display mode state machine.

Three modes:
  photos    - digital picture frame, the resting state
  calendar  - someone is standing there
  off       - quiet hours, screen blanked

Presence always wins: walking up during quiet hours wakes it to the calendar.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from .presence import PresenceManager

log = logging.getLogger(__name__)

TICK_SECONDS = 1.0


def _parse_hhmm(value: str, fallback: dtime) -> dtime:
    try:
        hours, minutes = str(value).split(":")
        return dtime(int(hours), int(minutes))
    except (ValueError, AttributeError):
        log.warning("could not parse time %r, using %s", value, fallback)
        return fallback


class DisplayController:
    def __init__(self, display_config: dict, presence: PresenceManager, tz: ZoneInfo) -> None:
        self.tz = tz
        self.presence = presence
        self.default_mode = display_config.get("default_mode", "photos")
        self.idle_timeout = float(display_config.get("idle_timeout_seconds", 90))

        quiet = display_config.get("quiet_hours", {}) or {}
        self.quiet_enabled = bool(quiet.get("enabled", False))
        self.quiet_start = _parse_hhmm(quiet.get("start", "23:00"), dtime(23, 0))
        self.quiet_end = _parse_hhmm(quiet.get("end", "06:30"), dtime(6, 30))

        self.mode = self._resting_mode()
        self._override: str | None = None
        self._override_until = 0.0
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    # --- subscriptions --------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def broadcast(self, message: dict) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A wedged client is not worth stalling the controller over.
                self._subscribers.discard(queue)

    # --- state ----------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "mode": self.mode,
            "present": self.presence.present,
            "presenceBackend": self.presence.backend,
            "presenceSource": self.presence.last_source,
            "quietHours": self.quiet_enabled and self._in_quiet_hours(),
            "overridden": self._override is not None and time.monotonic() < self._override_until,
        }

    def set_override(self, mode: str) -> None:
        """A tap on the screen pins a mode for one idle window."""
        if mode not in ("photos", "calendar"):
            raise ValueError(f"unknown mode {mode!r}")
        self._override = mode
        self._override_until = time.monotonic() + self.idle_timeout
        self._apply(mode)

    def clear_override(self) -> None:
        self._override = None
        self._override_until = 0.0

    def _in_quiet_hours(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(self.tz)).time()
        if self.quiet_start == self.quiet_end:
            return False
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= current < self.quiet_end
        # Window wraps past midnight (the normal case for "23:00" -> "06:30").
        return current >= self.quiet_start or current < self.quiet_end

    def _resting_mode(self) -> str:
        if self.quiet_enabled and self._in_quiet_hours():
            return "off"
        return self.default_mode

    def _target_mode(self) -> str:
        if self._override is not None:
            if time.monotonic() < self._override_until:
                return self._override
            self.clear_override()
        if self.presence.present:
            return "calendar"
        return self._resting_mode()

    def _apply(self, mode: str) -> None:
        if mode == self.mode:
            return
        log.info("display mode %s -> %s", self.mode, mode)
        self.mode = mode
        self.broadcast({"type": "state", "state": self.snapshot()})

    async def run(self) -> None:
        while True:
            try:
                self._apply(self._target_mode())
            except Exception:
                log.exception("display controller tick failed")
            await asyncio.sleep(TICK_SECONDS)

    async def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="display-controller")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
