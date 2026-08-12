"""Wall calendar display server."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import photos as photo_lib
from .auth import TokenGuard, enforce_public_safety, token_warnings
from .calendars import CalendarFeeds
from .config import Config
from . import occasions
from .importer import ImporterManager
from .presence import PresenceManager
from .state import DisplayController
from .store import Store

logging.basicConfig(
    level=os.environ.get("WALLDISPLAY_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("walldisplay")

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
WEB_DIR = PROJECT_DIR / "web"
CONFIG_PATH = Path(
    os.environ.get("WALLDISPLAY_CONFIG", PROJECT_DIR / "config.yaml")
).expanduser()

# Loaded at import rather than in lifespan so the static mounts below can be
# registered in the right order -- the catch-all "/" mount must come last.
CONFIG = Config.load(CONFIG_PATH)

# Before anything is served, not in lifespan: a public instance with no access
# code must never accept a single request.
if CONFIG.public:
    enforce_public_safety(CONFIG)
    log.info("public deployment: loopback trust off, access code required")

# Hosted platforms hand over an empty filesystem, so create both rather than
# assuming the checkout shipped them.
CONFIG.photo_dir.mkdir(parents=True, exist_ok=True)
CONFIG.data_dir.mkdir(parents=True, exist_ok=True)

# `write` covers anything that changes what the panel shows; `remote` covers
# the caregiver surface, which stays shut when remote access is switched off.
require_write = TokenGuard(CONFIG)
require_remote = TokenGuard(CONFIG, remote_only=True)
STARTED_AT = datetime.now(timezone.utc)


WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class Repeat(BaseModel):
    """Weekly repetition, which is what a work schedule is: 'Mondays,
    Wednesdays, and Fridays, 9 to 5, for the next 12 weeks'."""

    days: list[str] = Field(min_length=1, max_length=7)
    weeks: int = Field(default=4, ge=1, le=52)


class NewEvent(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime | None = None
    allDay: bool = False
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)
    calendar: str = Field(default="Added here", max_length=80)
    color: str = Field(default="#8b93a7", max_length=32)
    repeat: Repeat | None = None
    # Reserved for the email/SMS importer: set these and re-imports dedupe.
    source: str = Field(default="manual", max_length=32)
    sourceRef: str | None = Field(default=None, max_length=300)
    confirmed: bool = True


class ModeRequest(BaseModel):
    mode: str


class PhotoCaption(BaseModel):
    caption: str | None = Field(default=None, max_length=200)


class DoseMark(BaseModel):
    taken: bool = True


class ImportMessage(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    sender: str = Field(default="", max_length=200)
    subject: str = Field(default="", max_length=500)
    ref: str | None = Field(default=None, max_length=200)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = CONFIG
    if not CONFIG_PATH.exists():
        log.warning("no config at %s; running on defaults with no calendars", CONFIG_PATH)

    tz = config.timezone
    store = Store(config.db_path)
    feeds = CalendarFeeds(config.calendars, tz, cache_dir=config.feed_cache_dir)
    # Show last-known-good immediately; the first live refresh replaces it.
    await asyncio.to_thread(feeds.load_cache)
    presence = PresenceManager(config.presence)
    controller = DisplayController(config.display, presence, tz)
    importer = ImporterManager(config.importer, store, tz, controller.broadcast)

    app.state.config = config
    app.state.store = store
    app.state.feeds = feeds
    app.state.presence = presence
    app.state.controller = controller
    app.state.importer = importer

    await presence.start()
    await controller.start()
    await importer.start_with_loop()
    refresher = asyncio.create_task(_refresh_loop(feeds, controller, config.refresh_seconds))

    log.info(
        "wall display ready: %d calendar feed(s), presence backend %r, photos in %s",
        len(config.calendars),
        presence.backend,
        config.photo_dir,
    )
    try:
        yield
    finally:
        refresher.cancel()
        try:
            await refresher
        except asyncio.CancelledError:
            pass
        await importer.stop()
        await controller.stop()
        await presence.stop()
        store.close()


async def _refresh_loop(feeds: CalendarFeeds, controller: DisplayController, interval: int) -> None:
    while True:
        try:
            await feeds.refresh()
            controller.broadcast({"type": "events-changed"})
        except Exception:
            log.exception("calendar refresh failed")
        await asyncio.sleep(interval)


app = FastAPI(title="Wall Calendar Display", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Cheap hardening that matters once this is reachable from outside."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    # Without this, a token in the query string would ride along in the Referer
    # to any third-party resource the page loaded.
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


# --- API ----------------------------------------------------------------


@app.get("/api/settings")
async def get_settings():
    return app.state.config.client_settings()


@app.get("/api/state")
async def get_state():
    return app.state.controller.snapshot()


@app.post("/api/state/mode")
async def set_mode(request: ModeRequest):
    try:
        app.state.controller.set_override(request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return app.state.controller.snapshot()


@app.post("/api/presence")
async def post_presence(payload: dict = Body(default={})):
    """Anything that can make an HTTP call can be a presence sensor: a Home
    Assistant automation, a motion script, the touch screen itself."""
    source = str(payload.get("source", "http"))[:32]
    app.state.presence.trigger(source)
    return app.state.controller.snapshot()


@app.get("/api/events")
async def get_events(
    days: int = Query(default=14, ge=1, le=180),
    back: int = Query(default=1, ge=0, le=30),
):
    config: Config = app.state.config
    tz = config.timezone
    midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=back)
    end = midnight + timedelta(days=days)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "events": _merged_events(start, end),
        "feeds": app.state.feeds.status,
    }


@app.post("/api/events", status_code=201, dependencies=[Depends(require_write)])
async def create_event(payload: NewEvent):
    config: Config = app.state.config
    tz = config.timezone

    start = payload.start if payload.start.tzinfo else payload.start.replace(tzinfo=tz)
    if payload.end is None:
        end = start + timedelta(days=1 if payload.allDay else 0, hours=0 if payload.allDay else 1)
    else:
        end = payload.end if payload.end.tzinfo else payload.end.replace(tzinfo=tz)
    if end <= start:
        raise HTTPException(status_code=400, detail="end must be after start")

    common = dict(
        title=payload.title,
        all_day=payload.allDay,
        location=payload.location,
        notes=payload.notes,
        calendar=payload.calendar,
        color=payload.color,
        source=payload.source,
        confirmed=payload.confirmed,
    )

    if payload.repeat is not None:
        # A work schedule: materialise one row per occurrence. Rows are simple
        # to reason about, render, and delete one-at-a-time when a single
        # shift gets swapped -- which with real rosters is constant.
        try:
            wanted = {WEEKDAYS[d.lower()[:3]] for d in payload.repeat.days}
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"unknown weekday {exc}") from exc

        series_id = uuid.uuid4().hex
        duration = end - start
        created = 0
        first_day = start.date()
        for offset in range(payload.repeat.weeks * 7):
            day = first_day + timedelta(days=offset)
            if day.weekday() not in wanted:
                continue
            occurrence_start = start.replace(year=day.year, month=day.month, day=day.day)
            if app.state.store.add_event(
                start=occurrence_start, end=occurrence_start + duration,
                series_id=series_id, **common,
            ):
                created += 1
        if created == 0:
            raise HTTPException(status_code=400, detail="no occurrences in that range")
        app.state.controller.broadcast({"type": "events-changed"})
        return JSONResponse(
            status_code=201, content={"status": "series", "created": created, "seriesId": series_id}
        )

    event = app.state.store.add_event(
        start=start, end=end, source_ref=payload.sourceRef, **common
    )
    if event is None:
        # Already imported under this sourceRef -- not an error, just a no-op.
        return JSONResponse(status_code=200, content={"status": "duplicate"})

    app.state.controller.broadcast({"type": "events-changed"})
    return event


@app.delete("/api/events/{event_id}", dependencies=[Depends(require_write)])
async def delete_event(event_id: str, series: bool = Query(default=False)):
    store: Store = app.state.store
    if series:
        event = store.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="no such local event")
        if event.get("seriesId"):
            deleted = store.delete_series(event["seriesId"])
        else:
            deleted = int(store.delete_event(event_id))
        app.state.controller.broadcast({"type": "events-changed"})
        return {"status": "deleted", "count": deleted}

    if not store.delete_event(event_id):
        raise HTTPException(status_code=404, detail="no such local event")
    app.state.controller.broadcast({"type": "events-changed"})
    return {"status": "deleted"}


@app.post("/api/import/message", dependencies=[Depends(require_remote)])
async def import_message(payload: ImportMessage):
    """Phase 2 intake: an iPhone Shortcut or Android automation posts a
    forwarded text here; the extractor decides if there's an appointment in
    it. See docs/phase2-appointment-import.md."""
    result = await app.state.importer.process(
        text=payload.text, sender=payload.sender, subject=payload.subject, ref=payload.ref
    )
    if result["action"] == "disabled":
        raise HTTPException(status_code=503, detail="importer is disabled in config.yaml")
    if result["action"] == "unavailable":
        raise HTTPException(status_code=503, detail=result["reason"])
    return result


@app.post("/api/events/{event_id}/confirm", dependencies=[Depends(require_write)])
async def confirm_event(event_id: str):
    if not app.state.store.confirm_event(event_id):
        raise HTTPException(status_code=404, detail="no such local event")
    app.state.controller.broadcast({"type": "events-changed"})
    return {"status": "confirmed"}


@app.get("/api/photos")
async def list_photos():
    entries = photo_lib.list_photos(app.state.config.photo_dir)
    return {
        "photos": [entry["url"] for entry in entries],  # what the kiosk consumes
        "items": entries,  # what the caregiver page consumes
    }


@app.post("/api/photos", dependencies=[Depends(require_remote)])
async def upload_photos(files: list[UploadFile] = File(...)):
    """Accepts a batch, and reports per-file rather than failing the lot: a
    relative sending twenty holiday photos shouldn't lose nineteen of them
    because one was a screenshot of a PDF."""
    config: Config = app.state.config
    limit_bytes = int(config.remote["max_upload_mb"]) * 1024 * 1024
    resize = int(config.remote["resize_long_edge"])

    added, rejected = [], []
    for upload in files:
        data = await upload.read()
        if len(data) > limit_bytes:
            rejected.append(
                {
                    "name": upload.filename,
                    "reason": f"larger than {config.remote['max_upload_mb']} MB",
                }
            )
            continue
        try:
            result = await asyncio.to_thread(
                photo_lib.save_upload,
                config.photo_dir,
                upload.filename or "photo",
                data,
                resize_long_edge=resize,
            )
        except ValueError as exc:
            rejected.append({"name": upload.filename, "reason": str(exc)})
            continue
        added.append(result["name"])

    if added:
        app.state.controller.broadcast({"type": "photos-changed"})
    log.info("photo upload: %d added, %d rejected", len(added), len(rejected))
    return {"added": added, "rejected": rejected}


@app.delete("/api/photos/{name}", dependencies=[Depends(require_remote)])
async def delete_photo(name: str):
    if not photo_lib.delete_photo(app.state.config.photo_dir, name):
        raise HTTPException(status_code=404, detail="no such photo")
    app.state.controller.broadcast({"type": "photos-changed"})
    return {"status": "deleted"}


@app.patch("/api/photos/{name}", dependencies=[Depends(require_remote)])
async def caption_photo(name: str, payload: PhotoCaption):
    """A caption turns the frame from wallpaper into recognition support --
    "Margaret's graduation, June 2019" under the picture."""
    target = photo_lib.resolve_in(app.state.config.photo_dir, name)
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="no such photo")
    if not photo_lib.set_caption(app.state.config.photo_dir, name, payload.caption):
        raise HTTPException(status_code=500, detail="could not save the caption")
    app.state.controller.broadcast({"type": "photos-changed"})
    return {"status": "ok", "caption": (payload.caption or "").strip()[:200]}


@app.post("/api/medications/{dose_id}", dependencies=[Depends(require_write)])
async def mark_dose(dose_id: str, payload: DoseMark):
    """Tick a dose off. Idempotent -- the id encodes med, date, and time, so a
    double tap on a touch screen can't double-record anything."""
    if "|" not in dose_id:
        raise HTTPException(status_code=400, detail="malformed dose id")
    taken_at = app.state.store.mark_dose(dose_id, payload.taken)
    app.state.controller.broadcast({"type": "events-changed"})
    return {"status": "ok", "doseId": dose_id, "takenAt": taken_at}


@app.get("/api/status", dependencies=[Depends(require_remote)])
async def status(request: Request):
    """Everything the caregiver page needs to answer 'is it working?'."""
    config: Config = app.state.config
    photos = photo_lib.list_photos(config.photo_dir)
    now = datetime.now(config.timezone)
    upcoming = [
        event
        for event in _merged_events(now, now + timedelta(days=7))
        if event["end"] > now.isoformat()
    ][:8]

    return {
        "deviceName": config.remote["device_name"],
        "startedAt": STARTED_AT.isoformat(),
        "uptimeSeconds": int((datetime.now(timezone.utc) - STARTED_AT).total_seconds()),
        "localTime": now.isoformat(),
        "timezone": str(config.timezone),
        "display": app.state.controller.snapshot(),
        "feeds": app.state.feeds.status,
        "photoCount": len(photos),
        "photoBytes": sum(p["sizeBytes"] for p in photos),
        "diskFreeBytes": _disk_free(config.photo_dir),
        "upcoming": upcoming,
        "importer": app.state.importer.describe(),
        "medications": (
            occasions.adherence_today(config.medications, config.timezone,
                                      app.state.store.doses_taken())
            if config.medications else None
        ),
        "warnings": token_warnings(config),
    }


def _disk_free(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _merged_events(start: datetime, end: datetime) -> list[dict]:
    """Feed events + locally stored events + the ones we generate (birthdays,
    medication doses). Everything downstream sees one flat list."""
    config: Config = app.state.config
    store: Store = app.state.store
    tz = config.timezone

    events = app.state.feeds.events_between(start, end)
    events.extend(store.events_between(start, end))
    events.extend(occasions.birthday_events(config.birthdays, start, end, tz))

    if config.medications:
        days = [
            (start.date() + timedelta(days=n)).isoformat()
            for n in range((end.date() - start.date()).days + 1)
        ][:400]
        events.extend(occasions.medication_events(
            config.medications, start, end, tz, store.doses_taken(days)
        ))

    events.sort(key=lambda e: (e["start"], e["title"]))
    return events


@app.get("/api/health")
async def health():
    """Unauthenticated on purpose: it reports liveness, never content."""
    return {
        "status": "ok",
        "feeds": [
            {"name": f["name"], "ok": f["ok"], "cached": f.get("cached", False)}
            for f in app.state.feeds.status
        ],
        "mode": app.state.controller.mode,
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    controller: DisplayController = app.state.controller
    queue = controller.subscribe()
    await websocket.send_json({"type": "state", "state": controller.snapshot()})

    async def pump():
        while True:
            message = await queue.get()
            await websocket.send_json(message)

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "presence":
                app.state.presence.trigger(str(message.get("source", "touch"))[:32])
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        pump_task.cancel()
        controller.unsubscribe(queue)


# --- static kiosk UI ----------------------------------------------------
# Mounted last so neither mount shadows an /api route, and /photos is matched
# before the catch-all.


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/remote")
async def remote_page():
    """The caregiver page. Served without a token -- it asks for one itself,
    and every call it makes is guarded."""
    return FileResponse(WEB_DIR / "remote.html")


@app.get("/manifest.webmanifest")
async def manifest():
    # Served explicitly for the media type; mimetypes guesses wrong on some
    # systems and Chrome then refuses to treat the page as installable.
    return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")


app.mount("/photos", StaticFiles(directory=CONFIG.photo_dir), name="photos")
app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=CONFIG.server["host"],
        port=int(CONFIG.server["port"]),
        log_level=os.environ.get("WALLDISPLAY_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
