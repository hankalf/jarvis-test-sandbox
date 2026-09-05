"""SQLite storage for the brand brain, conversations and renders."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterable

from .config import DB_PATH, ensure_dirs

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS node (
    id          TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',      -- json array
    meta        TEXT NOT NULL DEFAULT '{}',      -- json object
    source      TEXT NOT NULL DEFAULT 'manual',  -- manual | assistant | seed | import
    confidence  REAL NOT NULL DEFAULT 1.0,
    pinned      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS node_category_idx ON node(category);

CREATE TABLE IF NOT EXISTS edge (
    id      TEXT PRIMARY KEY,
    src     TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    dst     TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    kind    TEXT NOT NULL DEFAULT 'relates_to',
    UNIQUE(src, dst, kind)
);

CREATE TABLE IF NOT EXISTS message (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,      -- user | assistant
    content     TEXT NOT NULL,      -- json: list of Anthropic content blocks
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS message_session_idx ON message(session_id, created_at);

CREATE TABLE IF NOT EXISTS render (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,      -- image | video
    prompt      TEXT NOT NULL,
    engine      TEXT NOT NULL,
    model       TEXT NOT NULL,
    aspect      TEXT NOT NULL,
    resolution  TEXT NOT NULL,
    cost        REAL NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,      -- queued | generating | done | failed
    url         TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def conn() -> sqlite3.Connection:
    """One connection per thread; FastAPI runs sync handlers on a threadpool."""
    c = getattr(_local, "conn", None)
    if c is None:
        ensure_dirs()
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        _local.conn = c
    return c


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def row_to_node(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["meta"] = json.loads(d.get("meta") or "{}")
    d["pinned"] = bool(d["pinned"])
    return d


# --------------------------------------------------------------------------- nodes

def create_node(
    category: str,
    title: str,
    body: str = "",
    tags: Iterable[str] | None = None,
    meta: dict[str, Any] | None = None,
    source: str = "manual",
    confidence: float = 1.0,
    pinned: bool = False,
    node_id: str | None = None,
) -> dict[str, Any]:
    now = time.time()
    nid = node_id or new_id("n")
    conn().execute(
        "INSERT INTO node (id, category, title, body, tags, meta, source, confidence,"
        " pinned, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            nid, category, title, body,
            json.dumps(list(tags or [])), json.dumps(meta or {}),
            source, confidence, int(pinned), now, now,
        ),
    )
    conn().commit()
    return get_node(nid)  # type: ignore[return-value]


def get_node(node_id: str) -> dict[str, Any] | None:
    row = conn().execute("SELECT * FROM node WHERE id = ?", (node_id,)).fetchone()
    return row_to_node(row) if row else None


def update_node(node_id: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {"category", "title", "body", "tags", "meta", "confidence", "pinned", "source"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key in ("tags", "meta"):
            value = json.dumps(value)
        if key == "pinned":
            value = int(bool(value))
        sets.append(f"{key} = ?")
        vals.append(value)
    if not sets:
        return get_node(node_id)
    sets.append("updated_at = ?")
    vals.extend([time.time(), node_id])
    conn().execute(f"UPDATE node SET {', '.join(sets)} WHERE id = ?", vals)
    conn().commit()
    return get_node(node_id)


def delete_node(node_id: str) -> bool:
    cur = conn().execute("DELETE FROM node WHERE id = ?", (node_id,))
    conn().commit()
    return cur.rowcount > 0


def list_nodes(category: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    if category:
        rows = conn().execute(
            "SELECT * FROM node WHERE category = ? ORDER BY pinned DESC, updated_at DESC LIMIT ?",
            (category, limit),
        ).fetchall()
    else:
        rows = conn().execute(
            "SELECT * FROM node ORDER BY pinned DESC, updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [row_to_node(r) for r in rows]


def search_nodes(query: str, category: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    like = f"%{query.strip()}%"
    sql = "SELECT * FROM node WHERE (title LIKE ? OR body LIKE ? OR tags LIKE ?)"
    args: list[Any] = [like, like, like]
    if category:
        sql += " AND category = ?"
        args.append(category)
    sql += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
    args.append(limit)
    return [row_to_node(r) for r in conn().execute(sql, args).fetchall()]


def category_counts() -> dict[str, int]:
    rows = conn().execute("SELECT category, COUNT(*) c FROM node GROUP BY category").fetchall()
    return {r["category"]: r["c"] for r in rows}


# --------------------------------------------------------------------------- edges

def create_edge(src: str, dst: str, kind: str = "relates_to") -> dict[str, Any]:
    eid = new_id("e")
    conn().execute(
        "INSERT OR IGNORE INTO edge (id, src, dst, kind) VALUES (?,?,?,?)", (eid, src, dst, kind)
    )
    conn().commit()
    return {"id": eid, "src": src, "dst": dst, "kind": kind}


def list_edges() -> list[dict[str, Any]]:
    return [dict(r) for r in conn().execute("SELECT * FROM edge").fetchall()]


# --------------------------------------------------------------------------- messages

def add_message(session_id: str, role: str, content: list[dict[str, Any]]) -> str:
    mid = new_id("m")
    conn().execute(
        "INSERT INTO message (id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
        (mid, session_id, role, json.dumps(content), time.time()),
    )
    conn().commit()
    return mid


def list_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM message WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        out.append({"role": r["role"], "content": json.loads(r["content"]), "id": r["id"]})
    return out


def clear_messages(session_id: str) -> None:
    conn().execute("DELETE FROM message WHERE session_id = ?", (session_id,))
    conn().commit()


# --------------------------------------------------------------------------- renders

def create_render(**fields: Any) -> dict[str, Any]:
    rid = new_id("r")
    conn().execute(
        "INSERT INTO render (id, kind, prompt, engine, model, aspect, resolution, cost,"
        " status, url, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rid, fields["kind"], fields["prompt"], fields["engine"], fields["model"],
            fields["aspect"], fields["resolution"], fields.get("cost", 0.0),
            fields.get("status", "queued"), fields.get("url", ""), fields.get("error", ""),
            time.time(),
        ),
    )
    conn().commit()
    return get_render(rid)  # type: ignore[return-value]


def update_render(render_id: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {"status", "url", "error", "cost"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return get_render(render_id)
    vals.append(render_id)
    conn().execute(f"UPDATE render SET {', '.join(sets)} WHERE id = ?", vals)
    conn().commit()
    return get_render(render_id)


def get_render(render_id: str) -> dict[str, Any] | None:
    row = conn().execute("SELECT * FROM render WHERE id = ?", (render_id,)).fetchone()
    return dict(row) if row else None


def list_renders(limit: int = 60) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM render ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- kv

def kv_get(key: str, default: str | None = None) -> str | None:
    row = conn().execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(key: str, value: str) -> None:
    conn().execute(
        "INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn().commit()
