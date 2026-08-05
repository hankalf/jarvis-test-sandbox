"""SQLite store for locally-owned events.

Feed events (ICS) are never written here -- they're re-fetched and live in
memory. This table is for events the panel itself owns: ones typed in on the
touch screen, and later the ones parsed out of email or forwarded texts.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_events (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    start       TEXT NOT NULL,
    end         TEXT NOT NULL,
    all_day     INTEGER NOT NULL DEFAULT 0,
    location    TEXT,
    notes       TEXT,
    calendar    TEXT NOT NULL DEFAULT 'Added here',
    color       TEXT NOT NULL DEFAULT '#8b93a7',
    source      TEXT NOT NULL DEFAULT 'manual',
    source_ref  TEXT,
    confirmed   INTEGER NOT NULL DEFAULT 1,
    series_id   TEXT,
    created_at  TEXT NOT NULL
);

-- Auto-import dedupe: re-parsing the same email must not add a second copy.
CREATE UNIQUE INDEX IF NOT EXISTS idx_local_events_source_ref
    ON local_events(source_ref) WHERE source_ref IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_local_events_start ON local_events(start);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """CREATE IF NOT EXISTS skips existing tables, so databases from
        before a column existed need it added by hand."""
        columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(local_events)")}
        if "series_id" not in columns:
            self._conn.execute("ALTER TABLE local_events ADD COLUMN series_id TEXT")

    def close(self) -> None:
        self._conn.close()

    def add_event(
        self,
        *,
        title: str,
        start: datetime,
        end: datetime,
        all_day: bool = False,
        location: str | None = None,
        notes: str | None = None,
        calendar: str = "Added here",
        color: str = "#8b93a7",
        source: str = "manual",
        source_ref: str | None = None,
        confirmed: bool = True,
        series_id: str | None = None,
    ) -> dict | None:
        """Insert an event. Returns None if source_ref was already imported."""
        row = {
            "id": uuid.uuid4().hex,
            "title": title.strip(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "all_day": int(all_day),
            "location": location,
            "notes": notes,
            "calendar": calendar,
            "color": color,
            "source": source,
            "source_ref": source_ref,
            "confirmed": int(confirmed),
            "series_id": series_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._conn.execute(
                "INSERT INTO local_events "
                "(id, title, start, end, all_day, location, notes, calendar, color,"
                " source, source_ref, confirmed, series_id, created_at) "
                "VALUES (:id, :title, :start, :end, :all_day, :location, :notes,"
                " :calendar, :color, :source, :source_ref, :confirmed, :series_id, :created_at)",
                row,
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            return None
        return self._to_event(row)

    def get_event(self, event_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM local_events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._to_event(dict(row)) if row else None

    def delete_series(self, series_id: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM local_events WHERE series_id = ?", (series_id,)
        )
        self._conn.commit()
        return cur.rowcount

    def delete_event(self, event_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM local_events WHERE id = ?", (event_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def confirm_event(self, event_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE local_events SET confirmed = 1 WHERE id = ?", (event_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def events_between(self, start: datetime, end: datetime) -> list[dict]:
        """Events overlapping [start, end). Compared as ISO strings, which sort
        correctly only when both sides carry an offset -- add_event guarantees
        that by storing tz-aware datetimes."""
        rows = self._conn.execute(
            "SELECT * FROM local_events WHERE start < ? AND end > ? ORDER BY start",
            (end.isoformat(), start.isoformat()),
        ).fetchall()
        return [self._to_event(dict(r)) for r in rows]

    @staticmethod
    def _to_event(row: dict) -> dict:
        return {
            "id": row["id"],
            "title": row["title"],
            "start": row["start"],
            "end": row["end"],
            "allDay": bool(row["all_day"]),
            "location": row["location"],
            "notes": row["notes"],
            "calendar": row["calendar"],
            "color": row["color"],
            "source": row["source"],
            "confirmed": bool(row["confirmed"]),
            "seriesId": row.get("series_id"),
            "editable": True,
        }
