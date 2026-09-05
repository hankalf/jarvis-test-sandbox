"""HTTP surface for AAL."""

from __future__ import annotations

import json
import threading
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agent, brain, db, render, seed
from .config import (
    ANTHROPIC_API_KEY, EFFORT, MODEL, RENDER_DIR, WEB_DIR, ensure_dirs,
)

ensure_dirs()

app = FastAPI(title="AAL", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    counts = db.category_counts()
    return {
        "brand": db.kv_get("brand_name", "Your brand"),
        "model": MODEL,
        "effort": EFFORT,
        "anthropic_key": bool(ANTHROPIC_API_KEY),
        "nodes": sum(counts.values()),
        "providers": render.provider_status(),
    }


# --------------------------------------------------------------------------- brain

@app.get("/api/brain")
def get_brain() -> dict[str, Any]:
    return brain.graph()


@app.get("/api/brain/digest")
def get_digest() -> dict[str, str]:
    """Exactly what the assistant sees on every turn. Useful for debugging drift."""
    return {"digest": brain.digest()}


@app.get("/api/brain/category/{category}")
def get_category(category: str) -> dict[str, Any]:
    if not brain.is_category(category):
        raise HTTPException(404, f"unknown category {category}")
    return {"category": category, "meta": brain.CATEGORIES[category], "nodes": db.list_nodes(category)}


@app.post("/api/brain/node")
def post_node(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    category = payload.get("category", "")
    if not brain.is_category(category):
        raise HTTPException(400, f"unknown category {category!r}")
    if not (payload.get("title") or "").strip():
        raise HTTPException(400, "title is required")
    return db.create_node(
        category=category, title=payload["title"], body=payload.get("body", ""),
        tags=payload.get("tags") or [], meta=payload.get("meta") or {},
        source=payload.get("source", "manual"), pinned=bool(payload.get("pinned")),
    )


@app.patch("/api/brain/node/{node_id}")
def patch_node(node_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    node = db.update_node(node_id, **payload)
    if not node:
        raise HTTPException(404, "no such node")
    return node


@app.delete("/api/brain/node/{node_id}")
def del_node(node_id: str) -> dict[str, bool]:
    return {"deleted": db.delete_node(node_id)}


@app.post("/api/brain/edge")
def post_edge(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    src, dst = payload.get("src"), payload.get("dst")
    if not (src and dst and db.get_node(src) and db.get_node(dst)):
        raise HTTPException(400, "src and dst must both be existing node ids")
    return db.create_edge(src, dst, payload.get("kind") or "relates_to")


@app.post("/api/brain/seed")
def post_seed(reset: bool = Query(False)) -> dict[str, Any]:
    return {"counts": seed.load(reset=reset), "brand": db.kv_get("brand_name")}


@app.post("/api/brain/brand")
def post_brand(payload: dict[str, str] = Body(...)) -> dict[str, str]:
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    db.kv_set("brand_name", name)
    return {"brand": name}


# --------------------------------------------------------------------------- chat

@app.get("/api/chat/{session_id}/history")
def chat_history(session_id: str) -> dict[str, Any]:
    """Only the parts a human wants to re-read: text the two sides exchanged."""
    turns = []
    for message in db.list_messages(session_id):
        text = "\n".join(
            b.get("text", "") for b in message["content"]
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
        if text:
            turns.append({"role": message["role"], "text": text})
    return {"session": session_id, "turns": turns}


@app.delete("/api/chat/{session_id}")
def chat_clear(session_id: str) -> dict[str, bool]:
    db.clear_messages(session_id)
    return {"cleared": True}


@app.post("/api/chat")
def chat(payload: dict[str, Any] = Body(...)) -> StreamingResponse:
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    session_id = payload.get("session") or "default"

    def events():
        try:
            for event in agent.converse(session_id, text):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # never leave the client hanging on an open stream
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)[:400]})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- studio

@app.get("/api/studio/config")
def studio_config() -> dict[str, Any]:
    return render.provider_status()


@app.get("/api/studio/renders")
def studio_renders(limit: int = 60) -> dict[str, Any]:
    return {"renders": db.list_renders(limit)}


@app.post("/api/studio/estimate")
def studio_estimate(payload: dict[str, Any] = Body(...)) -> dict[str, float]:
    return {"cost": render.estimate_cost(
        payload.get("engine", "fal"), payload.get("model", ""),
        payload.get("kind", "image"), int(payload.get("count") or 1),
    )}


@app.post("/api/studio/render")
def studio_render(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    rows = render.queue_render(
        kind=payload.get("kind", "image"), prompt=prompt,
        engine=payload.get("engine") or render.provider_status()["default_engine"],
        model=payload.get("model") or "",
        aspect=payload.get("aspect") or "9:16",
        resolution=str(payload.get("resolution") or "1024"),
        count=int(payload.get("count") or 1),
    )
    for row in rows:
        threading.Thread(target=render.run_render, args=(row["id"],), daemon=True).start()
    return {"renders": rows}


# --------------------------------------------------------------------------- recommendations

RECS_SYSTEM = (
    agent.PERSONA
    + "\n\nYou are producing a ranked list of next ads to ship. Reply with JSON only: "
      '{"recommendations":[{"title":str,"angle":str,"why":str,"prompt":str,"aspect":str}]}. '
      "`why` cites the specific brain entries it came from. `prompt` is a complete, "
      "directable render prompt. No prose outside the JSON."
)


@app.post("/api/recommendations")
def recommendations(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    count = int(payload.get("count") or 4)
    prompt = (
        f"=== BRAND BRAIN ===\n{brain.digest()}\n\n"
        f"Recommend the {count} ads to ship next. Weigh what has performed, what "
        f"competitors are still running (long days-live means proven spend), the "
        f"live offers, and the operator's taste. Do not repeat a creative that is "
        f"already a known winner unless you are proposing a specific variant."
    )
    try:
        raw = agent.ask_once(prompt, system=RECS_SYSTEM, max_tokens=6000)
    except Exception as exc:
        raise HTTPException(502, f"model call failed: {exc}") from exc

    body = raw.strip()
    if body.startswith("```"):
        body = body.split("```")[1].removeprefix("json").strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"recommendations": [], "raw": raw}


app.mount("/renders", StaticFiles(directory=RENDER_DIR), name="renders")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
