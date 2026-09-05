"""Image / video rendering.

Fal AI is the default provider: model ids are passed straight through to
`https://fal.run/<model>`, so any Fal model works without a code change. With
no provider key configured the studio still functions — it writes a local
placeholder card so the whole loop (prompt -> studio -> brain) is testable
offline.
"""

from __future__ import annotations

import html
import json
import textwrap
import time
from typing import Any

import httpx2 as httpx

from . import db
from .config import FAL_KEY, RENDER_DIR, ensure_dirs

# Catalogue shown in the Image Settings panel. `cost` is a per-unit estimate in
# USD used only for the "estimated cost" readout — the provider bills actual.
ENGINES: dict[str, dict[str, Any]] = {
    "fal": {
        "label": "Fal AI",
        "image_models": [
            {"id": "fal-ai/gpt-image-1/text-to-image", "label": "GPT Image 1", "cost": 0.12},
            {"id": "fal-ai/flux-pro/v1.1", "label": "FLUX 1.1 Pro", "cost": 0.04},
            {"id": "fal-ai/flux/dev", "label": "FLUX dev", "cost": 0.025},
            {"id": "fal-ai/nano-banana", "label": "Nano Banana", "cost": 0.039},
        ],
        "video_models": [
            {"id": "fal-ai/kling-video/v2/master/image-to-video", "label": "Kling 2 Master", "cost": 1.40},
            {"id": "fal-ai/minimax/hailuo-02/standard/image-to-video", "label": "Hailuo 02", "cost": 0.48},
        ],
    },
    "local": {
        "label": "Local preview",
        "image_models": [{"id": "placeholder", "label": "Placeholder card", "cost": 0.0}],
        "video_models": [{"id": "placeholder", "label": "Placeholder card", "cost": 0.0}],
    },
}

ASPECTS = ["9:16", "1:1", "4:5", "16:9", "3:2"]
RESOLUTIONS = ["1024", "1536", "2048"]

_ASPECT_WH = {
    "9:16": (720, 1280), "1:1": (1024, 1024), "4:5": (1024, 1280),
    "16:9": (1280, 720), "3:2": (1200, 800),
}


def provider_status() -> dict[str, Any]:
    return {
        "fal": bool(FAL_KEY),
        "engines": ENGINES,
        "aspects": ASPECTS,
        "resolutions": RESOLUTIONS,
        "default_engine": "fal" if FAL_KEY else "local",
    }


def estimate_cost(engine: str, model: str, kind: str, count: int) -> float:
    key = "video_models" if kind == "video" else "image_models"
    for entry in ENGINES.get(engine, {}).get(key, []):
        if entry["id"] == model:
            return round(entry["cost"] * max(1, count), 4)
    return 0.0


def _placeholder(render_id: str, prompt: str, aspect: str, note: str) -> str:
    """Write an SVG card so the studio has something real to show offline."""
    ensure_dirs()
    w, h = _ASPECT_WH.get(aspect, (720, 1280))
    wrapped = textwrap.wrap(" ".join(prompt.split()), width=max(18, w // 22))[:14]
    lines = "".join(
        f'<text x="48" y="{int(h * 0.30) + i * 34}" fill="#c9d4e4" '
        f'font-family="ui-sans-serif,system-ui" font-size="22">{html.escape(line)}</text>'
        for i, line in enumerate(wrapped)
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0b1220"/><stop offset="100%" stop-color="#141b2e"/>
  </linearGradient></defs>
  <rect width="{w}" height="{h}" fill="url(#g)"/>
  <rect x="16" y="16" width="{w - 32}" height="{h - 32}" fill="none" stroke="#2b3category" stroke-opacity="0.4"/>
  <text x="48" y="{int(h * 0.18)}" fill="#7aa2ff" font-family="ui-sans-serif,system-ui"
        font-size="14" letter-spacing="4">{html.escape(note.upper())}</text>
  {lines}
  <text x="48" y="{h - 48}" fill="#55637a" font-family="ui-monospace,monospace"
        font-size="14">{aspect} · {html.escape(render_id)}</text>
</svg>"""
    svg = svg.replace("#2b3category", "#2b3550")
    path = RENDER_DIR / f"{render_id}.svg"
    path.write_text(svg, encoding="utf-8")
    return f"/renders/{render_id}.svg"


def _fal_call(model: str, payload: dict[str, Any], timeout: float = 300.0) -> dict[str, Any]:
    headers = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"https://fal.run/{model}", headers=headers, content=json.dumps(payload))
        if resp.status_code >= 400:
            raise RuntimeError(f"Fal {resp.status_code}: {resp.text[:400]}")
        return resp.json()


def _first_url(result: dict[str, Any]) -> str:
    for key in ("images", "video", "videos", "image", "output"):
        value = result.get(key)
        if isinstance(value, dict) and value.get("url"):
            return value["url"]
        if isinstance(value, list) and value and isinstance(value[0], dict) and value[0].get("url"):
            return value[0]["url"]
    raise RuntimeError(f"no media url in provider response: {list(result)[:6]}")


def run_render(render_id: str) -> dict[str, Any]:
    """Execute a queued render. Blocking; called from a worker thread."""
    row = db.get_render(render_id)
    if not row:
        raise KeyError(render_id)
    db.update_render(render_id, status="generating")

    engine, model, prompt = row["engine"], row["model"], row["prompt"]
    try:
        if engine == "local" or not FAL_KEY:
            note = "local preview" if engine == "local" else "no FAL_KEY — preview only"
            url = _placeholder(render_id, prompt, row["aspect"], note)
            return db.update_render(render_id, status="done", url=url, cost=0.0)  # type: ignore[return-value]

        payload: dict[str, Any] = {"prompt": prompt}
        if row["kind"] == "image":
            payload["image_size"] = _fal_image_size(row["aspect"], row["resolution"])
        result = _fal_call(model, payload)
        return db.update_render(render_id, status="done", url=_first_url(result))  # type: ignore[return-value]
    except Exception as exc:  # surfaced in the studio card, not swallowed
        return db.update_render(render_id, status="failed", error=str(exc)[:500])  # type: ignore[return-value]


def _fal_image_size(aspect: str, resolution: str) -> dict[str, int]:
    ratio_w, ratio_h = (int(x) for x in aspect.split(":"))
    long_edge = int(resolution)
    if ratio_w >= ratio_h:
        return {"width": long_edge, "height": int(long_edge * ratio_h / ratio_w)}
    return {"width": int(long_edge * ratio_w / ratio_h), "height": long_edge}


def queue_render(
    kind: str, prompt: str, engine: str, model: str, aspect: str, resolution: str, count: int = 1
) -> list[dict[str, Any]]:
    rows = []
    for _ in range(max(1, min(count, 4))):
        rows.append(
            db.create_render(
                kind=kind, prompt=prompt, engine=engine, model=model, aspect=aspect,
                resolution=resolution, cost=estimate_cost(engine, model, kind, 1), status="queued",
            )
        )
    return rows


def wait_for(render_ids: list[str], timeout: float = 420.0) -> list[dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = [db.get_render(r) for r in render_ids]
        if all(r and r["status"] in ("done", "failed") for r in rows):
            return [r for r in rows if r]
        time.sleep(0.5)
    return [r for r in (db.get_render(r) for r in render_ids) if r]
