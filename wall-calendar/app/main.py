"""Wall calendar display server."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .calendars import CalendarFeeds
from .config import Config
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

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}

# Loaded at import rather than in lifespan so the static mounts below can be
# registered in the right order -- the catch-all "/" mount must come last.
CONFIG = Config.load(CONFIG_PATH)
CONFIG.photo_dir.mkdir(parents=True, exist_ok=True)


class NewEvent(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime | None = None
    allDay: bool = False
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)
    calendar: str = Field(default="Added here", max_length=80)
    color: str = Field(default="#8b93a7", max_length=32)
    # Reserved for the email/SMS importer: set these and re-imports dedupe.
    source: str = Field(default="manual", max_length=32)
    sourceRef: str | None = Field(default=None, max_length=300)
    confirmed: bool = True


class ModeRequest(BaseModel):
    mode: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = CONFIG
    if not CONFIG_PATH.exists():
        log.warning("no config at %s; running on defaults with no calendars", CONFIG_PATH)

    tz = config.timezone
    store = Store(config.db_path)
    feeds = CalendarFeeds(config.calendars, tz)
    presence = PresenceManager(config.presence)
    controller = DisplayController(config.display, presence, tz)

    app.state.config = config
    app.state.store = store
    app.state.feeds = feeds
    app.state.presence = presence
    app.state.controller = controller

    await presence.start()
    await controller.start()
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

    events = app.state.feeds.events_between(start, end)
    events.extend(app.state.store.events_between(start, end))
    events.sort(key=lambda e: (e["start"], e["title"]))
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "events": events,
        "feeds": app.state.feeds.status,
    }


@app.post("/api/events", status_code=201)
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

    event = app.state.store.add_event(
        title=payload.title,
        start=start,
        end=end,
        all_day=payload.allDay,
        location=payload.location,
        notes=payload.notes,
        calendar=payload.calendar,
        color=payload.color,
        source=payload.source,
        source_ref=payload.sourceRef,
        confirmed=payload.confirmed,
    )
    if event is None:
        # Already imported under this sourceRef -- not an error, just a no-op.
        return JSONResponse(status_code=200, content={"status": "duplicate"})

    app.state.controller.broadcast({"type": "events-changed"})
    return event


@app.delete("/api/events/{event_id}")
async def delete_event(event_id: str):
    if not app.state.store.delete_event(event_id):
        raise HTTPException(status_code=404, detail="no such local event")
    app.state.controller.broadcast({"type": "events-changed"})
    return {"status": "deleted"}


@app.post("/api/events/{event_id}/confirm")
async def confirm_event(event_id: str):
    if not app.state.store.confirm_event(event_id):
        raise HTTPException(status_code=404, detail="no such local event")
    app.state.controller.broadcast({"type": "events-changed"})
    return {"status": "confirmed"}


@app.get("/api/photos")
async def list_photos():
    photo_dir: Path = app.state.config.photo_dir
    names = sorted(
        p.name
        for p in photo_dir.iterdir()
        if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES
    )
    return {"photos": [f"/photos/{name}" for name in names]}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "feeds": app.state.feeds.status,
        "display": app.state.controller.snapshot(),
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
