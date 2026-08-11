"""Configuration loading: YAML on top of defaults, with paths resolved."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

DEFAULTS: dict[str, Any] = {
    "timezone": "UTC",
    "server": {"host": "0.0.0.0", "port": 8080},
    "calendars": [],
    "calendar_refresh_minutes": 15,
    "display": {
        "default_mode": "photos",
        "idle_timeout_seconds": 90,
        "photo_dir": "photos",
        "photo_interval_seconds": 45,
        "clock_24h": False,
        "week_starts_on": "sunday",
        "agenda_days": 14,
        "quiet_hours": {"enabled": False, "start": "23:00", "end": "06:30"},
        # Full-screen "time to get ready" card before appointments that have a
        # location. The chime is off by default -- an unexpected sound from a
        # wall is startling in a way a changed picture is not.
        "leaving_soon": {"enabled": True, "minutes_before": 30, "chime": False},
    },
    # [{name: Margaret, date: 1954-03-12, note: "Call her!"}]
    # date can be YYYY-MM-DD (shows the age) or MM-DD.
    "birthdays": [],
    # [{name: "Morning pills", times: ["08:00"], notes: "With food"}]
    "medications": [],
    "presence": {
        "backend": "http",
        "hold_seconds": 20,
        "gpio": {"pin": 17},
        "serial": {"port": "/dev/ttyUSB0", "baud": 256000},
    },
    "accessibility": {
        "theme": "light",
        "text_scale": 1.0,
        "simple_view": True,
        "show_week_month": True,
        "reduce_motion": False,
        "show_weekday_banner": True,
    },
    "remote": {
        "enabled": True,
        "token": "",
        "device_name": "Wall Calendar",
        "max_upload_mb": 40,
        "resize_long_edge": 2560,
    },
    "importer": {
        "enabled": False,
        "dry_run": True,
        "min_confidence": 0.5,
        "calendar": "From email",
        "color": "#7a3fa0",
        "model": "claude-opus-5",
        "api_key": "",
        "gmail": {
            "host": "imap.gmail.com",
            "username": "",
            "app_password": "",
            "folder": "Appointments",
            "poll_minutes": 10,
        },
    },
    "data_dir": "data",
}

# Fallback palette for feeds that don't name a colour, so two calendars never
# come out the same shade by accident. Chosen to stay legible as text and as
# fills on both the light and dark themes.
PALETTE = ["#1d5fbf", "#b8541a", "#1f7a5a", "#7a3fa0", "#8a6d13", "#b02a45"]


def _deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    def __init__(self, data: dict, base_dir: Path) -> None:
        self._data = data
        self.base_dir = base_dir

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        p = Path(path).expanduser().resolve()
        raw = yaml.safe_load(p.read_text()) if p.exists() else {}
        if raw is not None and not isinstance(raw, dict):
            raise ValueError(f"{p} must contain a YAML mapping at the top level")
        return cls(_deep_merge(DEFAULTS, raw), p.parent)

    def _resolve(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self._data["timezone"])

    @property
    def server(self) -> dict:
        return self._data["server"]

    @property
    def display(self) -> dict:
        return self._data["display"]

    @property
    def presence(self) -> dict:
        return self._data["presence"]

    @property
    def refresh_seconds(self) -> int:
        return max(60, int(self._data["calendar_refresh_minutes"]) * 60)

    @property
    def calendars(self) -> list[dict]:
        """Feed definitions, with blanks and unreplaced placeholders dropped."""
        feeds = []
        for i, entry in enumerate(self._data.get("calendars") or []):
            url = (entry.get("url") or "").strip()
            if not url or "REPLACE_ME" in url:
                continue
            feeds.append(
                {
                    "name": entry.get("name") or f"Calendar {i + 1}",
                    "url": url,
                    "color": entry.get("color") or PALETTE[i % len(PALETTE)],
                }
            )
        return feeds

    @property
    def accessibility(self) -> dict:
        return self._data["accessibility"]

    @property
    def remote(self) -> dict:
        return self._data["remote"]

    @property
    def birthdays(self) -> list[dict]:
        return self._data.get("birthdays") or []

    @property
    def medications(self) -> list[dict]:
        return self._data.get("medications") or []

    @property
    def importer(self) -> dict:
        out = dict(self._data["importer"])
        out["_log_path"] = str(self.data_dir / "import-log.jsonl")
        return out

    @property
    def photo_dir(self) -> Path:
        return self._resolve(self.display["photo_dir"])

    @property
    def feed_cache_dir(self) -> Path:
        """Last-good copy of each feed, so a reboot without working internet
        still comes up showing the calendar instead of an empty screen."""
        return self.data_dir / "feed-cache"

    @property
    def data_dir(self) -> Path:
        return self._resolve(self._data["data_dir"])

    @property
    def db_path(self) -> Path:
        return self.data_dir / "walldisplay.db"

    def client_settings(self) -> dict:
        """The slice of config the browser needs to render itself."""
        d = self.display
        a = self.accessibility
        return {
            "timezone": self._data["timezone"],
            "clock24h": bool(d["clock_24h"]),
            "weekStartsOn": str(d["week_starts_on"]).lower(),
            "agendaDays": int(d["agenda_days"]),
            "photoIntervalSeconds": int(d["photo_interval_seconds"]),
            "calendars": [{"name": c["name"], "color": c["color"]} for c in self.calendars],
            "theme": str(a["theme"]).lower(),
            "textScale": max(0.6, min(2.5, float(a["text_scale"]))),
            "simpleView": bool(a["simple_view"]),
            "showWeekMonth": bool(a["show_week_month"]),
            "reduceMotion": bool(a["reduce_motion"]),
            "showWeekdayBanner": bool(a["show_weekday_banner"]),
            "deviceName": self.remote["device_name"],
            "leavingSoon": {
                "enabled": bool(d["leaving_soon"]["enabled"]),
                "minutesBefore": int(d["leaving_soon"]["minutes_before"]),
                "chime": bool(d["leaving_soon"]["chime"]),
            },
            "hasMedications": bool(self.medications),
        }
