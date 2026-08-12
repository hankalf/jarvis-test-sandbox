"""Configuration loading: YAML on top of defaults, with paths resolved.

A file is the normal case: the calendar runs on a box you can edit. Hosted
platforms have no editable file and no shell worth relying on, so the same
settings can arrive as environment variables, and a whole config can arrive as
one `WALLDISPLAY_CONFIG_YAML` blob. Precedence, weakest first:

    defaults  <  config.yaml  <  WALLDISPLAY_CONFIG_YAML  <  individual vars
"""

from __future__ import annotations

import copy
import os
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
        # Requests from 127.0.0.1 skip the token, so the panel's own browser
        # needs no credential. Set false when something on this same host
        # proxies to the app (tunnel connector, nginx) and you want the token
        # demanded even then. Proxied requests are already refused the loopback
        # shortcut automatically; this is the belt to that's braces.
        "trust_loopback": True,
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


TRUTHY = {"1", "true", "yes", "on"}
FALSEY = {"0", "false", "no", "off"}


def as_bool(value: str) -> bool:
    text = str(value).strip().lower()
    if text in TRUTHY:
        return True
    if text in FALSEY:
        return False
    raise ValueError(f"expected a yes/no value, got {value!r}")


# (environment variable, path into the config, how to read it). Applied in
# order, so a later entry wins -- WALLDISPLAY_PORT beats the platform's PORT.
ENV_OVERRIDES: list[tuple[str, tuple[str, ...], Any]] = [
    ("TZ", ("timezone",), str),
    ("WALLDISPLAY_TIMEZONE", ("timezone",), str),
    ("WALLDISPLAY_HOST", ("server", "host"), str),
    # Railway, Heroku, Fly and friends all name the assigned port this way.
    ("PORT", ("server", "port"), int),
    ("WALLDISPLAY_PORT", ("server", "port"), int),
    ("WALLDISPLAY_DATA_DIR", ("data_dir",), str),
    ("WALLDISPLAY_PHOTO_DIR", ("display", "photo_dir"), str),
    ("WALLDISPLAY_DEFAULT_MODE", ("display", "default_mode"), str),
    # A secret does not belong in a file that gets committed by accident.
    ("WALLDISPLAY_TOKEN", ("remote", "token"), str),
    ("WALLDISPLAY_DEVICE_NAME", ("remote", "device_name"), str),
    ("WALLDISPLAY_TRUST_LOOPBACK", ("remote", "trust_loopback"), as_bool),
    ("WALLDISPLAY_THEME", ("accessibility", "theme"), str),
    ("WALLDISPLAY_TEXT_SCALE", ("accessibility", "text_scale"), float),
    ("ANTHROPIC_API_KEY", ("importer", "api_key"), str),
]

# Set by the platform, not by us -- their presence is how the app knows it is
# being served straight onto the internet rather than onto a hallway wall.
PUBLIC_PLATFORM_VARS = ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID")


def _assign(data: dict, path: tuple[str, ...], value: Any) -> None:
    cursor = data
    for key in path[:-1]:
        target = cursor.get(key)
        if not isinstance(target, dict):
            target = {}
            cursor[key] = target
        cursor = target
    cursor[path[-1]] = value


def env_overlay(env: dict[str, str] | None = None) -> dict:
    """The individual environment variables, as a config-shaped mapping."""
    env = os.environ if env is None else env
    out: dict[str, Any] = {}
    for name, path, coerce in ENV_OVERRIDES:
        raw = env.get(name)
        if raw is None or raw == "":
            continue
        try:
            _assign(out, path, coerce(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name}: {exc}") from exc
    return out


def is_public_platform(env: dict[str, str] | None = None) -> bool:
    """True when this instance answers the open internet directly.

    On a wall panel the app is behind a front door; on a hosting platform the
    URL is the front door, which changes what is safe to allow by default.
    """
    env = os.environ if env is None else env
    explicit = env.get("WALLDISPLAY_PUBLIC")
    if explicit:
        return as_bool(explicit)
    return any(env.get(name) for name in PUBLIC_PLATFORM_VARS)


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
        self.public = False

    @classmethod
    def load(cls, path: str | Path, env: dict[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        p = Path(path).expanduser().resolve()
        raw = yaml.safe_load(p.read_text()) if p.exists() else {}
        if raw is not None and not isinstance(raw, dict):
            raise ValueError(f"{p} must contain a YAML mapping at the top level")
        data = _deep_merge(DEFAULTS, raw)

        # A whole config as one variable, for platforms with no writable file.
        inline = env.get("WALLDISPLAY_CONFIG_YAML")
        if inline:
            parsed = yaml.safe_load(inline)
            if parsed is not None and not isinstance(parsed, dict):
                raise ValueError("WALLDISPLAY_CONFIG_YAML must be a YAML mapping")
            data = _deep_merge(data, parsed)

        data = _deep_merge(data, env_overlay(env))

        # Nothing is "local" when the URL is the front door: the platform's
        # router reaches the app over loopback, so trusting loopback there
        # would hand every visitor the panel's own unauthenticated access.
        if is_public_platform(env):
            data["remote"]["trust_loopback"] = False

        config = cls(data, p.parent)
        config.public = is_public_platform(env)
        return config

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
