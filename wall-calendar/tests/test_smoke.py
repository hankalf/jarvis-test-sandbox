"""Smoke tests: run with `.venv/bin/python -m pytest` from wall-calendar/."""

from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendars import CalendarFeeds  # noqa: E402
from app.config import Config  # noqa: E402
from app.state import DisplayController  # noqa: E402
from app.presence import PresenceManager  # noqa: E402
from app.store import Store  # noqa: E402
from app import photos  # noqa: E402

TZ = ZoneInfo("America/New_York")

SAMPLE_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:timed-1
DTSTART;TZID=America/New_York:20260810T090000
DTEND;TZID=America/New_York:20260810T093000
SUMMARY:Dr. Patel follow-up
LOCATION:Suite 210
END:VEVENT
BEGIN:VEVENT
UID:allday-1
DTSTART;VALUE=DATE:20260812
DTEND;VALUE=DATE:20260813
SUMMARY:Day off
END:VEVENT
BEGIN:VEVENT
UID:weekly-1
DTSTART;TZID=America/New_York:20260803T170000
DTEND;TZID=America/New_York:20260803T230000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=8
SUMMARY:Evening shift
END:VEVENT
END:VCALENDAR
"""


def _feeds_with_sample() -> CalendarFeeds:
    feed = {"name": "Work", "url": "https://example.invalid/x.ics", "color": "#5b9cf8"}
    feeds = CalendarFeeds([feed], TZ)
    feeds._parsed["Work"] = icalendar.Calendar.from_ical(SAMPLE_ICS)
    return feeds


def test_timed_and_allday_events_normalize():
    feeds = _feeds_with_sample()
    events = feeds.events_between(
        datetime(2026, 8, 10, tzinfo=TZ), datetime(2026, 8, 14, tzinfo=TZ)
    )
    by_title = {e["title"]: e for e in events}

    appointment = by_title["Dr. Patel follow-up"]
    assert appointment["allDay"] is False
    assert appointment["start"].startswith("2026-08-10T09:00:00")
    assert appointment["location"] == "Suite 210"
    assert appointment["calendar"] == "Work"
    assert appointment["editable"] is False

    day_off = by_title["Day off"]
    assert day_off["allDay"] is True
    assert day_off["start"].startswith("2026-08-12T00:00:00")
    assert day_off["end"].startswith("2026-08-13T00:00:00")


def test_recurring_events_expand_per_occurrence():
    feeds = _feeds_with_sample()
    events = feeds.events_between(
        datetime(2026, 8, 3, tzinfo=TZ), datetime(2026, 8, 17, tzinfo=TZ)
    )
    shifts = [e for e in events if e["title"] == "Evening shift"]
    # Mon + Wed across two weeks.
    assert len(shifts) == 4
    assert len({s["id"] for s in shifts}) == 4, "each occurrence needs a distinct id"


def test_events_outside_the_window_are_excluded():
    feeds = _feeds_with_sample()
    events = feeds.events_between(
        datetime(2026, 9, 1, tzinfo=TZ), datetime(2026, 9, 5, tzinfo=TZ)
    )
    assert events == []


def test_store_roundtrip_and_dedupe(tmp_path: Path):
    store = Store(tmp_path / "test.db")
    start = datetime(2026, 8, 10, 14, 0, tzinfo=TZ)

    created = store.add_event(title="Dentist", start=start, end=start + timedelta(hours=1))
    assert created is not None and created["editable"] is True

    found = store.events_between(start - timedelta(hours=1), start + timedelta(hours=2))
    assert [e["title"] for e in found] == ["Dentist"]

    # An imported event carries a source_ref; importing it twice must no-op.
    assert store.add_event(
        title="Imported", start=start, end=start + timedelta(hours=1),
        source="email", source_ref="msg-42", confirmed=False,
    ) is not None
    assert store.add_event(
        title="Imported again", start=start, end=start + timedelta(hours=1),
        source="email", source_ref="msg-42",
    ) is None

    imported = [e for e in store.events_between(start, start + timedelta(hours=2)) if e["source"] == "email"]
    assert len(imported) == 1 and imported[0]["confirmed"] is False

    assert store.confirm_event(imported[0]["id"]) is True
    assert store.delete_event(imported[0]["id"]) is True
    assert store.delete_event(imported[0]["id"]) is False
    store.close()


def test_store_excludes_non_overlapping_events(tmp_path: Path):
    store = Store(tmp_path / "test.db")
    start = datetime(2026, 8, 10, 14, 0, tzinfo=TZ)
    store.add_event(title="Dentist", start=start, end=start + timedelta(hours=1))

    assert store.events_between(start + timedelta(days=1), start + timedelta(days=2)) == []
    # Touching-but-not-overlapping: an event ending exactly at the window start.
    assert store.events_between(start + timedelta(hours=1), start + timedelta(hours=3)) == []
    store.close()


@pytest.mark.parametrize(
    "start,end,now,expected",
    [
        ("23:00", "06:30", datetime(2026, 8, 10, 2, 0, tzinfo=TZ), True),   # wraps midnight
        ("23:00", "06:30", datetime(2026, 8, 10, 23, 30, tzinfo=TZ), True),
        ("23:00", "06:30", datetime(2026, 8, 10, 12, 0, tzinfo=TZ), False),
        ("01:00", "05:00", datetime(2026, 8, 10, 3, 0, tzinfo=TZ), True),   # same-day window
        ("01:00", "05:00", datetime(2026, 8, 10, 6, 0, tzinfo=TZ), False),
    ],
)
def test_quiet_hours_window(start, end, now, expected):
    config = {"quiet_hours": {"enabled": True, "start": start, "end": end}}
    controller = DisplayController(config, PresenceManager({"backend": "http"}), TZ)
    assert controller._in_quiet_hours(now) is expected


def test_presence_drives_mode():
    presence = PresenceManager({"backend": "http", "hold_seconds": 30})
    controller = DisplayController(
        {"default_mode": "photos", "idle_timeout_seconds": 90}, presence, TZ
    )
    assert controller._target_mode() == "photos"
    presence.trigger("test")
    assert controller._target_mode() == "calendar"


def test_presence_wakes_the_screen_during_quiet_hours():
    presence = PresenceManager({"backend": "http", "hold_seconds": 30})
    controller = DisplayController(
        {
            "default_mode": "photos",
            "idle_timeout_seconds": 90,
            # A window covering every moment, so the test never depends on
            # what time it happens to run.
            "quiet_hours": {"enabled": True, "start": "00:00", "end": "23:59"},
        },
        presence,
        TZ,
    )
    assert controller._target_mode() == "off"
    presence.trigger("mmwave")
    assert controller._target_mode() == "calendar"


def test_config_skips_placeholder_feeds(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "timezone: America/New_York\n"
        "calendars:\n"
        "  - name: Work\n"
        "    url: https://example.invalid/real.ics\n"
        "  - name: Medical\n"
        "    url: https://example.invalid/REPLACE_ME/basic.ics\n"
    )
    config = Config.load(config_file)
    assert [c["name"] for c in config.calendars] == ["Work"]
    # Unspecified colours come from the palette, never blank.
    assert config.calendars[0]["color"].startswith("#")
    # Defaults survive a partial config file.
    assert config.display["idle_timeout_seconds"] == 90


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "timezone: America/New_York\ncalendars: []\ndata_dir: data\n"
        "display:\n  photo_dir: photos\n"
        "presence:\n  backend: http\n"
    )
    monkeypatch.setenv("WALLDISPLAY_CONFIG", str(config_file))
    for module in [m for m in list(sys.modules) if m.startswith("app.")]:
        del sys.modules[module]
    from app.main import app  # re-imported so it picks up the temp config

    # The panel's own browser talks to 127.0.0.1; model that.
    with TestClient(app, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def test_api_end_to_end(client):
    assert client.get("/api/health").json()["status"] == "ok"

    settings = client.get("/api/settings").json()
    assert settings["timezone"] == "America/New_York"

    created = client.post(
        "/api/events",
        json={"title": "Bloodwork", "start": "2026-08-11T08:15:00", "end": "2026-08-11T08:45:00"},
    )
    assert created.status_code == 201
    # A naive timestamp is stamped with the configured zone, not UTC.
    assert "-04:00" in created.json()["start"]

    events = client.get("/api/events?days=30&back=30").json()["events"]
    assert any(e["title"] == "Bloodwork" for e in events)

    assert client.post("/api/events", json={"title": "Bad", "start": "2026-08-11T09:00:00", "end": "2026-08-11T08:00:00"}).status_code == 400

    assert client.get("/api/state").json()["mode"] in ("photos", "calendar", "off")
    assert client.post("/api/state/mode", json={"mode": "calendar"}).json()["mode"] == "calendar"
    assert client.post("/api/state/mode", json={"mode": "nonsense"}).status_code == 400
    assert client.post("/api/presence", json={"source": "test"}).json()["present"] is True

    assert client.get("/api/photos").json() == {"photos": [], "items": []}
    assert client.delete(f"/api/events/{created.json()['id']}").status_code == 200
    assert client.delete("/api/events/does-not-exist").status_code == 404


def test_kiosk_page_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "Wall Calendar" in page.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_websocket_pushes_state(client):
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "state" and "mode" in first["state"]


# --- accessibility, photos, and the remote surface ----------------------


def test_accessibility_settings_reach_the_browser(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "timezone: America/New_York\n"
        "accessibility:\n  theme: dark\n  text_scale: 1.5\n  show_week_month: false\n"
    )
    settings = Config.load(config_file).client_settings()
    assert settings["theme"] == "dark"
    assert settings["textScale"] == 1.5
    assert settings["showWeekMonth"] is False
    # Untouched keys keep their (elderly-friendly) defaults.
    assert settings["simpleView"] is True
    assert settings["showWeekdayBanner"] is True


def test_text_scale_is_clamped_to_something_renderable(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("accessibility:\n  text_scale: 99\n")
    assert Config.load(config_file).client_settings()["textScale"] == 2.5


def test_feed_cache_survives_a_restart_without_network(tmp_path: Path):
    feed = {"name": "Work", "url": "https://example.invalid/x.ics", "color": "#1d5fbf"}
    cache = tmp_path / "feed-cache"

    warm = CalendarFeeds([feed], TZ, cache_dir=cache)
    warm._write_cache("Work", SAMPLE_ICS)

    # A fresh process with no network at all still has the calendar.
    cold = CalendarFeeds([feed], TZ, cache_dir=cache)
    cold.load_cache()
    events = cold.events_between(
        datetime(2026, 8, 10, tzinfo=TZ), datetime(2026, 8, 14, tzinfo=TZ)
    )
    assert any(e["title"] == "Dr. Patel follow-up" for e in events)
    # ...and says so, rather than claiming to be in sync.
    status = cold.status[0]
    assert status["ok"] is False and status["cached"] is True


def test_unreadable_cache_does_not_crash_startup(tmp_path: Path):
    feed = {"name": "Work", "url": "https://example.invalid/x.ics", "color": "#1d5fbf"}
    cache = tmp_path / "feed-cache"
    feeds = CalendarFeeds([feed], TZ, cache_dir=cache)
    path = feeds._cache_path("Work")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a calendar")

    feeds.load_cache()
    assert feeds.events_between(
        datetime(2026, 8, 10, tzinfo=TZ), datetime(2026, 8, 14, tzinfo=TZ)
    ) == []


def _png_bytes(size=(40, 30), colour=(90, 120, 200)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def test_photo_upload_normalises_and_lists(tmp_path: Path):
    photo_dir = tmp_path / "photos"
    # Larger than the resize target on the long edge, so it must come back down.
    result = photos.save_upload(
        photo_dir, "IMG_0042.PNG", _png_bytes((900, 400)), resize_long_edge=200
    )
    assert result["name"].endswith(".jpg") and result["converted"] is True

    from PIL import Image

    with Image.open(photo_dir / result["name"]) as image:
        assert max(image.size) == 200

    listed = photos.list_photos(photo_dir)
    assert [p["name"] for p in listed] == [result["name"]]
    assert listed[0]["sizeBytes"] > 0


def test_photo_upload_rejects_non_images_and_odd_names(tmp_path: Path):
    photo_dir = tmp_path / "photos"
    with pytest.raises(ValueError):
        photos.save_upload(photo_dir, "invoice.pdf", b"%PDF-1.4")
    with pytest.raises(ValueError):
        photos.save_upload(photo_dir, "broken.png", b"not actually a png")


def test_photo_names_cannot_escape_the_photo_directory(tmp_path: Path):
    photo_dir = tmp_path / "photos"
    photo_dir.mkdir()
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"x")

    # The directory component is stripped, so "../secret.jpg" can only ever
    # name a file inside photo_dir -- traversal is impossible by construction.
    resolved = photos.resolve_in(photo_dir, "../secret.jpg")
    assert resolved is not None and resolved.parent == photo_dir.resolve()
    assert photos.delete_photo(photo_dir, "../secret.jpg") is False
    assert secret.exists()
    assert photos.safe_name("../../etc/passwd") == "passwd"

    saved = photos.save_upload(photo_dir, "../../../evil.png", _png_bytes())
    assert (photo_dir / saved["name"]).exists()


def test_uploading_the_same_name_twice_keeps_both(tmp_path: Path):
    photo_dir = tmp_path / "photos"
    first = photos.save_upload(photo_dir, "beach.png", _png_bytes())
    second = photos.save_upload(photo_dir, "beach.png", _png_bytes(colour=(10, 200, 10)))
    assert first["name"] != second["name"]
    assert len(photos.list_photos(photo_dir)) == 2


@pytest.fixture()
def secured_client(tmp_path: Path, monkeypatch):
    """A server with a token set, talking to a non-loopback client."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "timezone: America/New_York\ncalendars: []\ndata_dir: data\n"
        "display:\n  photo_dir: photos\n"
        "remote:\n  enabled: true\n  token: s3cret-token-value-long\n"
    )
    monkeypatch.setenv("WALLDISPLAY_CONFIG", str(config_file))
    for module in [m for m in list(sys.modules) if m.startswith("app.")]:
        del sys.modules[module]
    from app.main import app

    # TestClient defaults to a loopback client address, which the guard trusts;
    # override it so these tests exercise the remote path.
    with TestClient(app, client=("10.0.0.9", 50000)) as test_client:
        yield test_client


def test_remote_endpoints_require_the_token(secured_client):
    assert secured_client.get("/api/status").status_code == 401
    assert secured_client.get("/api/photos").status_code == 200  # reads stay open
    assert secured_client.post(
        "/api/events",
        json={"title": "Sneaky", "start": "2026-08-11T08:00:00", "end": "2026-08-11T09:00:00"},
    ).status_code == 401
    assert secured_client.delete("/api/photos/anything.jpg").status_code == 401


def test_remote_endpoints_accept_the_token(secured_client):
    headers = {"X-Wall-Token": "s3cret-token-value-long"}

    status = secured_client.get("/api/status", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["photoCount"] == 0 and "upcoming" in body and body["warnings"] == []

    created = secured_client.post(
        "/api/events",
        headers=headers,
        json={"title": "Podiatry", "start": "2026-08-11T08:00:00", "end": "2026-08-11T09:00:00"},
    )
    assert created.status_code == 201

    # Bearer form works too, for anything that speaks standard auth headers.
    assert secured_client.get(
        "/api/status", headers={"Authorization": "Bearer s3cret-token-value-long"}
    ).status_code == 200
    # ...and a wrong token still fails.
    assert secured_client.get("/api/status", headers={"X-Wall-Token": "nope"}).status_code == 401


def test_photo_upload_and_delete_over_the_api(secured_client):
    headers = {"X-Wall-Token": "s3cret-token-value-long"}
    upload = secured_client.post(
        "/api/photos",
        headers=headers,
        files=[
            ("files", ("holiday.png", _png_bytes(), "image/png")),
            ("files", ("notes.txt", b"not an image", "text/plain")),
        ],
    )
    assert upload.status_code == 200
    body = upload.json()
    # One good file must not be lost because another in the batch was bad.
    assert len(body["added"]) == 1 and len(body["rejected"]) == 1
    assert "notes.txt" in body["rejected"][0]["name"]

    name = body["added"][0]
    assert secured_client.get("/api/photos").json()["items"][0]["name"] == name
    assert secured_client.delete(f"/api/photos/{name}", headers=headers).status_code == 200
    assert secured_client.get("/api/photos").json()["items"] == []


def test_status_warns_when_no_token_is_configured(client):
    # `client` has no token; the guard falls open on loopback but still nags.
    warnings = client.get("/api/status").json()["warnings"]
    assert any("No remote token" in w for w in warnings)


@pytest.mark.parametrize(
    "stylesheet,selector",
    [
        ("style.css", ".photo-empty"),   # hidden when photos exist
        ("style.css", ".icon-btn"),      # hidden by show_week_month: false
        ("style.css", ".views"),         # same
        ("remote.css", ".gate"),         # hidden once a token is accepted
    ],
)
def test_hidable_elements_survive_their_own_display_rule(stylesheet, selector):
    """A CSS rule that sets `display` beats the browser's built-in [hidden]
    rule, so an element JavaScript hides stays on screen. Bitten three times;
    now checked."""
    css = (Path(__file__).resolve().parents[1] / "web" / stylesheet).read_text()
    sets_display = f"{selector} {{" in css and "display:" in css.split(f"{selector} {{", 1)[1].split("}", 1)[0]
    if not sets_display:
        return  # nothing to override, [hidden] works on its own
    assert f"{selector}[hidden]" in css, (
        f"{selector} sets `display` in {stylesheet} but has no [hidden] rule, "
        "so hiding it from JavaScript will not work"
    )


def test_remote_page_is_served(client):
    page = client.get("/remote")
    assert page.status_code == 200 and "Access code" in page.text
    assert client.get("/remote.js").status_code == 200
    assert client.get("/remote.css").status_code == 200
