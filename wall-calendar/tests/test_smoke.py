"""Smoke tests: run with `.venv/bin/python -m pytest` from wall-calendar/."""

from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendars import CalendarFeeds  # noqa: E402
from app.config import Config  # noqa: E402
from app.state import DisplayController  # noqa: E402
from app.presence import PresenceManager  # noqa: E402
from app.store import Store  # noqa: E402
from app import photos  # noqa: E402
from app import occasions  # noqa: E402

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


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    from app import auth
    auth.reset_failures()
    yield
    auth.reset_failures()


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


@pytest.mark.parametrize(
    "header",
    ["x-forwarded-for", "x-real-ip", "cf-connecting-ip", "forwarded", "x-forwarded-host", "cf-ray"],
)
def test_a_proxy_on_localhost_does_not_inherit_loopback_trust(secured_client, header):
    """A tunnel connector or reverse proxy on the same host connects to
    127.0.0.1, so without this every request from the internet would arrive
    looking local and skip the token entirely."""
    from app import auth

    auth.reset_failures()
    proxied = TestClient(secured_client.app, client=("127.0.0.1", 40000))
    with proxied:
        assert proxied.get("/api/status", headers={header: "203.0.113.9"}).status_code == 401
        assert proxied.post(
            "/api/events",
            headers={header: "203.0.113.9"},
            json={"title": "X", "start": "2026-08-11T08:00:00", "end": "2026-08-11T09:00:00"},
        ).status_code == 401
        # ...and the token still works through the proxy.
        assert proxied.get(
            "/api/status",
            headers={header: "203.0.113.9", "X-Wall-Token": "s3cret-token-value-long"},
        ).status_code == 200


def test_genuine_loopback_still_skips_the_token(secured_client):
    from app import auth

    auth.reset_failures()
    local = TestClient(secured_client.app, client=("127.0.0.1", 40000))
    with local:
        assert local.get("/api/status").status_code == 200


def test_trust_loopback_false_demands_the_token_even_locally(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "timezone: America/New_York\ncalendars: []\ndata_dir: data\n"
        "display:\n  photo_dir: photos\n"
        "remote:\n  token: s3cret-token-value-long\n  trust_loopback: false\n"
    )
    monkeypatch.setenv("WALLDISPLAY_CONFIG", str(config_file))
    for module in [m for m in list(sys.modules) if m.startswith("app.")]:
        del sys.modules[module]
    from app import auth
    from app.main import app

    auth.reset_failures()
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get(
            "/api/status", headers={"X-Wall-Token": "s3cret-token-value-long"}
        ).status_code == 200


def test_repeated_bad_codes_are_rate_limited(secured_client):
    from app import auth

    auth.reset_failures()
    remote = TestClient(secured_client.app, client=("198.51.100.7", 40000))
    with remote:
        codes = [
            remote.get("/api/status", headers={"X-Wall-Token": "wrong"}).status_code
            for _ in range(auth.MAX_FAILURES + 2)
        ]
    assert codes[0] == 401
    assert codes[-1] == 429, "brute-forcing the code should eventually be throttled"
    auth.reset_failures()


def test_security_headers_are_present(client):
    headers = client.get("/remote").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    # A token in the query string must not leak to third parties via Referer.
    assert headers["Referrer-Policy"] == "no-referrer"


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


# --- work schedules (repeating series) ----------------------------------


def test_work_schedule_creates_a_series(client):
    created = client.post(
        "/api/events",
        json={
            "title": "Shift — front desk",
            "start": "2026-08-10T09:00:00",  # a Monday
            "end": "2026-08-10T17:00:00",
            "calendar": "Work",
            "repeat": {"days": ["mon", "wed", "fri"], "weeks": 2},
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "series" and body["created"] == 6

    events = client.get("/api/events?days=30&back=30").json()["events"]
    shifts = [e for e in events if e["title"] == "Shift — front desk"]
    assert len(shifts) == 6
    assert all(s["seriesId"] == body["seriesId"] for s in shifts)
    starts = sorted(s["start"][:10] for s in shifts)
    assert starts == ["2026-08-10", "2026-08-12", "2026-08-14",
                      "2026-08-17", "2026-08-19", "2026-08-21"]
    # Times survive onto every occurrence, in the display's timezone.
    assert all(s["start"].endswith("09:00:00-04:00") for s in shifts)


def test_one_shift_or_the_whole_series_can_be_deleted(client):
    body = client.post(
        "/api/events",
        json={
            "title": "Evening shift",
            "start": "2026-08-10T17:00:00",
            "end": "2026-08-10T22:00:00",
            "repeat": {"days": ["mon", "tue"], "weeks": 3},
        },
    ).json()
    assert body["created"] == 6

    events = client.get("/api/events?days=30&back=30").json()["events"]
    shifts = [e for e in events if e["title"] == "Evening shift"]

    # A swapped shift: delete just that one.
    assert client.delete(f"/api/events/{shifts[0]['id']}").status_code == 200
    remaining = [e for e in client.get("/api/events?days=30&back=30").json()["events"]
                 if e["title"] == "Evening shift"]
    assert len(remaining) == 5

    # The job ended: delete the rest in one go.
    deleted = client.delete(f"/api/events/{remaining[0]['id']}?series=true")
    assert deleted.status_code == 200 and deleted.json()["count"] == 5
    assert [e for e in client.get("/api/events?days=30&back=30").json()["events"]
            if e["title"] == "Evening shift"] == []


def test_bad_weekday_is_rejected(client):
    response = client.post(
        "/api/events",
        json={
            "title": "X", "start": "2026-08-10T09:00:00", "end": "2026-08-10T10:00:00",
            "repeat": {"days": ["funday"], "weeks": 1},
        },
    )
    assert response.status_code == 400


# --- phase 2: the importer ----------------------------------------------


class FakeExtractor:
    available = (True, "")

    def __init__(self, result):
        self.result = result
        self.calls = []

    def extract(self, *, sender, subject, body, today):
        self.calls.append({"sender": sender, "body": body})
        return dict(self.result)


APPOINTMENT = {
    "is_appointment": True,
    "title": "MRI — knee",
    "start": "2026-08-20T10:30:00",
    "duration_minutes": 45,
    "location": "Radiology, 2nd floor",
    "confidence": 0.93,
    "notes": "Arrive 15 minutes early.",
}


@pytest.fixture()
def importer_client(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "timezone: America/New_York\ncalendars: []\ndata_dir: data\n"
        "display:\n  photo_dir: photos\n"
        "importer:\n  enabled: true\n  dry_run: false\n"
    )
    monkeypatch.setenv("WALLDISPLAY_CONFIG", str(config_file))
    for module in [m for m in list(sys.modules) if m.startswith("app.")]:
        del sys.modules[module]
    from app.main import app

    with TestClient(app, client=("127.0.0.1", 50000)) as test_client:
        yield test_client, app


def test_importer_writes_unconfirmed_events(importer_client):
    client, app = importer_client
    app.state.importer.extractor = FakeExtractor(APPOINTMENT)

    result = client.post(
        "/api/import/message",
        json={"text": "Your MRI is confirmed...", "sender": "radiology@clinic.com", "ref": "msg-1"},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["action"] == "imported"
    event = body["event"]
    assert event["confirmed"] is False, "imported events must never be auto-trusted"
    assert event["title"] == "MRI — knee"
    assert event["start"] == "2026-08-20T10:30:00-04:00"  # stamped with display tz
    assert event["end"] == "2026-08-20T11:15:00-04:00"    # 45 minutes

    # Same ref again: dedupe, not a second event.
    again = client.post(
        "/api/import/message",
        json={"text": "Your MRI is confirmed...", "sender": "radiology@clinic.com", "ref": "msg-1"},
    ).json()
    assert again["action"] == "duplicate"
    events = client.get("/api/events?days=60").json()["events"]
    assert len([e for e in events if e["title"] == "MRI — knee"]) == 1


def test_importer_skips_non_appointments_and_low_confidence(importer_client):
    client, app = importer_client

    app.state.importer.extractor = FakeExtractor({**APPOINTMENT, "is_appointment": False})
    assert client.post("/api/import/message", json={"text": "SALE! 20% off"}).json()["action"] == "skipped"

    app.state.importer.extractor = FakeExtractor({**APPOINTMENT, "confidence": 0.3})
    assert client.post("/api/import/message", json={"text": "maybe thursday?"}).json()["action"] == "skipped"

    app.state.importer.extractor = FakeExtractor({**APPOINTMENT, "start": "sometime"})
    assert client.post("/api/import/message", json={"text": "no date"}).json()["action"] == "skipped"

    assert client.get("/api/events?days=60").json()["events"] == []


def test_importer_dry_run_logs_but_writes_nothing(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "timezone: America/New_York\ncalendars: []\ndata_dir: data\n"
        "display:\n  photo_dir: photos\n"
        "importer:\n  enabled: true\n  dry_run: true\n"
    )
    monkeypatch.setenv("WALLDISPLAY_CONFIG", str(config_file))
    for module in [m for m in list(sys.modules) if m.startswith("app.")]:
        del sys.modules[module]
    from app.main import app

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        app.state.importer.extractor = FakeExtractor(APPOINTMENT)
        result = client.post("/api/import/message", json={"text": "Your MRI is confirmed"}).json()
        assert result["action"] == "dry_run"
        assert client.get("/api/events?days=60").json()["events"] == []

        log_file = tmp_path / "data" / "import-log.jsonl"
        assert log_file.exists()
        import json as jsonlib
        entry = jsonlib.loads(log_file.read_text().splitlines()[-1])
        assert entry["action"] == "dry_run"
        assert entry["extraction"]["title"] == "MRI — knee"


def test_importer_disabled_and_unavailable_report_clearly(client, importer_client):
    # Default config: disabled entirely.
    assert client.post("/api/import/message", json={"text": "hi"}).status_code == 503

    # Enabled but no API key / package: says why instead of failing silently.
    icli, app = importer_client
    app.state.importer.extractor.api_key = ""  # the real ClaudeExtractor
    response = icli.post("/api/import/message", json={"text": "hi"})
    assert response.status_code == 503
    assert "API key" in response.json()["detail"]


# --- birthdays -----------------------------------------------------------


def test_birthdays_recur_every_year_and_show_the_age():
    events = occasions.birthday_events(
        [{"name": "Margaret", "date": "1954-03-12"}],
        datetime(2026, 3, 1, tzinfo=TZ), datetime(2028, 4, 1, tzinfo=TZ), TZ,
    )
    assert [e["title"] for e in events] == ["Margaret turns 72", "Margaret turns 73"]
    assert all(e["allDay"] and e["kind"] == "birthday" and not e["editable"] for e in events)
    assert events[0]["start"].startswith("2026-03-12T00:00:00")


def test_birthday_without_a_year_omits_the_age():
    events = occasions.birthday_events(
        [{"name": "Tom", "date": "06-04"}],
        datetime(2026, 6, 1, tzinfo=TZ), datetime(2026, 6, 30, tzinfo=TZ), TZ,
    )
    assert [e["title"] for e in events] == ["Tom's birthday"]


def test_leap_day_birthday_falls_back_to_the_28th():
    span = (datetime(2027, 2, 1, tzinfo=TZ), datetime(2027, 3, 1, tzinfo=TZ))  # not a leap year
    events = occasions.birthday_events([{"name": "Ada", "date": "1996-02-29"}], *span, TZ)
    assert len(events) == 1 and events[0]["start"].startswith("2027-02-28")

    leap = (datetime(2028, 2, 1, tzinfo=TZ), datetime(2028, 3, 1, tzinfo=TZ))
    events = occasions.birthday_events([{"name": "Ada", "date": "1996-02-29"}], *leap, TZ)
    assert len(events) == 1 and events[0]["start"].startswith("2028-02-29")


def test_malformed_birthday_entries_are_skipped_not_fatal():
    events = occasions.birthday_events(
        [{"name": "No date"}, {"date": "1950-01-01"}, {"name": "Ok", "date": "1950-01-01"}],
        datetime(2026, 1, 1, tzinfo=TZ), datetime(2026, 1, 5, tzinfo=TZ), TZ,
    )
    assert [e["title"] for e in events] == ["Ok turns 76"]


def test_birthdays_appear_in_the_calendar(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    # Anchored a week out so it lands inside the queried window whenever the
    # suite happens to run.
    soon = (datetime.now(TZ) + timedelta(days=7)).date()
    config_file.write_text(
        "timezone: America/New_York\ncalendars: []\ndata_dir: data\n"
        "display:\n  photo_dir: photos\n"
        f"birthdays:\n  - {{name: Margaret, date: '1954-{soon.month:02d}-{soon.day:02d}'}}\n"
    )
    monkeypatch.setenv("WALLDISPLAY_CONFIG", str(config_file))
    for module in [m for m in list(sys.modules) if m.startswith("app.")]:
        del sys.modules[module]
    from app.main import app

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        events = client.get("/api/events?days=180&back=30").json()["events"]
        assert any(e["kind"] == "birthday" and "Margaret" in e["title"] for e in events)


# --- medication -----------------------------------------------------------


MEDS = [{"name": "Morning pills", "times": ["08:00", "19:00"], "notes": "With food"}]


def test_medication_doses_are_generated_per_day():
    doses = occasions.medication_events(
        MEDS, datetime(2026, 8, 10, tzinfo=TZ), datetime(2026, 8, 13, tzinfo=TZ), TZ
    )
    assert len(doses) == 6  # two a day for three days
    assert doses[0]["doseId"] == "morning-pills|2026-08-10|08:00"
    assert all(d["kind"] == "medication" and d["takenAt"] is None for d in doses)
    assert doses[0]["notes"] == "With food"


def test_marking_a_dose_is_idempotent_and_reversible(tmp_path: Path):
    store = Store(tmp_path / "test.db")
    dose_id = "morning-pills|2026-08-10|08:00"

    first = store.mark_dose(dose_id, True)
    second = store.mark_dose(dose_id, True)   # double tap on a touch screen
    assert first is not None and second is not None
    assert list(store.doses_taken()) == [dose_id], "must not record two doses"

    assert store.mark_dose(dose_id, False) is None
    assert store.doses_taken() == {}
    store.close()


def test_taken_state_reaches_the_generated_dose(tmp_path: Path):
    store = Store(tmp_path / "test.db")
    store.mark_dose("morning-pills|2026-08-10|08:00", True)
    doses = occasions.medication_events(
        MEDS, datetime(2026, 8, 10, tzinfo=TZ), datetime(2026, 8, 11, tzinfo=TZ), TZ,
        store.doses_taken(["2026-08-10"]),
    )
    by_time = {d["start"][11:16]: d for d in doses}
    assert by_time["08:00"]["takenAt"] is not None
    assert by_time["19:00"]["takenAt"] is None
    store.close()


def test_adherence_counts_only_doses_already_due(tmp_path: Path):
    store = Store(tmp_path / "test.db")
    # Two doses: one at the start of the day (due), one at the very end (not).
    meds = [{"name": "Pills", "times": ["00:01", "23:59"]}]
    summary = occasions.adherence_today(meds, TZ, store.doses_taken())
    assert summary["total"] == 2
    assert summary["taken"] == 0
    assert summary["missed"] == 1, "the 23:59 dose is not late yet"
    store.close()


def test_dose_can_be_ticked_over_the_api(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "timezone: America/New_York\ncalendars: []\ndata_dir: data\n"
        "display:\n  photo_dir: photos\n"
        "medications:\n  - {name: Morning pills, times: ['08:00']}\n"
    )
    monkeypatch.setenv("WALLDISPLAY_CONFIG", str(config_file))
    for module in [m for m in list(sys.modules) if m.startswith("app.")]:
        del sys.modules[module]
    from app.main import app

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        doses = [e for e in client.get("/api/events?days=2").json()["events"]
                 if e["kind"] == "medication"]
        assert doses, "medication doses should appear in the calendar"
        dose_id = doses[0]["doseId"]

        marked = client.post(f"/api/medications/{dose_id}", json={"taken": True})
        assert marked.status_code == 200 and marked.json()["takenAt"]

        again = [e for e in client.get("/api/events?days=2").json()["events"]
                 if e.get("doseId") == dose_id][0]
        assert again["takenAt"] is not None

        assert client.get("/api/status").json()["medications"]["total"] >= 1
        assert client.post("/api/medications/nonsense", json={"taken": True}).status_code == 400


# --- photo captions -------------------------------------------------------


def test_captions_round_trip_and_survive_deletion(tmp_path: Path):
    photo_dir = tmp_path / "photos"
    saved = photos.save_upload(photo_dir, "beach.png", _png_bytes())
    name = saved["name"]

    assert photos.set_caption(photo_dir, name, "  Margaret's graduation, June  ")
    assert photos.list_photos(photo_dir)[0]["caption"] == "Margaret's graduation, June"

    # Clearing removes the entry rather than storing an empty string.
    photos.set_caption(photo_dir, name, "")
    assert photos.read_captions(photo_dir) == {}

    photos.set_caption(photo_dir, name, "Back again")
    photos.delete_photo(photo_dir, name)
    assert photos.read_captions(photo_dir) == {}, "caption should not outlive its photo"


def test_caption_sidecar_is_not_listed_as_a_photo(tmp_path: Path):
    photo_dir = tmp_path / "photos"
    photos.save_upload(photo_dir, "a.png", _png_bytes())
    photos.set_caption(photo_dir, "a.jpg", "hello")
    assert [p["name"] for p in photos.list_photos(photo_dir)] == ["a.jpg"]


def test_unreadable_caption_file_does_not_break_listing(tmp_path: Path):
    photo_dir = tmp_path / "photos"
    photos.save_upload(photo_dir, "a.png", _png_bytes())
    (photo_dir / photos.CAPTIONS_FILE).write_text("{ this is not json")
    listed = photos.list_photos(photo_dir)
    assert len(listed) == 1 and listed[0]["caption"] == ""


def test_caption_endpoint(secured_client):
    headers = {"X-Wall-Token": "s3cret-token-value-long"}
    name = secured_client.post(
        "/api/photos", headers=headers,
        files=[("files", ("trip.png", _png_bytes(), "image/png"))],
    ).json()["added"][0]

    assert secured_client.patch(
        f"/api/photos/{name}", headers=headers, json={"caption": "Sunday at the lake"}
    ).status_code == 200
    assert secured_client.get("/api/photos").json()["items"][0]["caption"] == "Sunday at the lake"

    assert secured_client.patch(
        "/api/photos/ghost.jpg", headers=headers, json={"caption": "x"}
    ).status_code == 404
    # Unauthenticated writes are still refused.
    assert secured_client.patch(f"/api/photos/{name}", json={"caption": "x"}).status_code == 401


def test_leaving_soon_settings_reach_the_browser(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "display:\n  leaving_soon: {enabled: true, minutes_before: 45, chime: true}\n"
    )
    settings = Config.load(config_file).client_settings()
    assert settings["leavingSoon"] == {"enabled": True, "minutesBefore": 45, "chime": True}
    # Chime stays off unless asked for.
    assert Config.load(tmp_path / "missing.yaml").client_settings()["leavingSoon"]["chime"] is False


def test_manifest_and_icons_are_served(client):
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["start_url"] == "/remote"
    assert client.get("/icons/icon-192.png").status_code == 200


def test_remote_page_is_served(client):
    page = client.get("/remote")
    assert page.status_code == 200 and "Access code" in page.text
    assert client.get("/remote.js").status_code == 200
    assert client.get("/remote.css").status_code == 200


def test_config_edits_hit_the_right_block(tmp_path: Path):
    """harden-for-public.sh rewrites a secret in place, so scoping matters."""
    from setup.edit_config import set_in_remote

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "importer:\n"
        "  # a decoy: this token must survive untouched\n"
        "  token: \"DO-NOT-TOUCH\"\n"
        "\n"
        "remote:\n"
        "  # the access code for /remote\n"
        "  token: \"old-code\"\n"
        "  trust_loopback: true\n"
    )
    set_in_remote(config_file, "token", '"new-code"')
    set_in_remote(config_file, "trust_loopback", "false")

    text = config_file.read_text()
    assert 'token: "DO-NOT-TOUCH"' in text, "rewrote a token in the wrong block"
    assert 'token: "new-code"' in text
    assert "trust_loopback: false" in text
    # The comments are most of this file's value; they must survive.
    assert "# the access code for /remote" in text

    loaded = yaml.safe_load(text)
    assert loaded["remote"] == {"token": "new-code", "trust_loopback": False}
    assert loaded["importer"]["token"] == "DO-NOT-TOUCH"


def test_config_edits_add_missing_keys_and_blocks(tmp_path: Path):
    from setup.edit_config import set_in_remote

    # Key absent from an existing block.
    partial = tmp_path / "partial.yaml"
    partial.write_text("remote:\n  enabled: true\n\ndisplay:\n  mode: photos\n")
    set_in_remote(partial, "trust_loopback", "false")
    loaded = yaml.safe_load(partial.read_text())
    assert loaded["remote"] == {"enabled": True, "trust_loopback": False}
    assert loaded["display"] == {"mode": "photos"}, "leaked into the next block"

    # No `remote:` block at all.
    bare = tmp_path / "bare.yaml"
    bare.write_text("display:\n  mode: photos\n")
    set_in_remote(bare, "token", '"generated"')
    assert yaml.safe_load(bare.read_text())["remote"] == {"token": "generated"}

    # The shipped example must survive a round trip.
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    if example.exists():
        copy = tmp_path / "example.yaml"
        copy.write_text(example.read_text())
        set_in_remote(copy, "trust_loopback", "false")
        assert yaml.safe_load(copy.read_text())["remote"]["trust_loopback"] is False
