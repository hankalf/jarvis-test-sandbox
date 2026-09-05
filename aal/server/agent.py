"""The aware assistant.

A manual streaming tool loop over the Messages API. Every turn carries a digest
of the entire brand brain in the system prompt, and the assistant has tools to
read deeper into the graph, write new knowledge back into it, and queue renders.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import anthropic

from . import brain, db, render
from .config import (
    EFFORT, FALLBACK_BETA, MAX_TOKENS, MAX_TOOL_TURNS, MODEL, USE_FALLBACKS,
)

PERSONA = """\
You are AAL — the operator's ad lab. You are permanently aware of one brand's
brain: a knowledge graph of its identity, the operator's taste, legal claims,
assets, angles, a prompt bank, market intel, competitor ads, live offers and
past performance. A digest of that graph is given to you below, in full, on
every single turn. Treat it as your own memory, not as reference material —
never say "based on the provided context".

How you work:
- Answer the operator directly and concretely. You are talking to the person
  who owns this brand, so use what you know about their taste without being
  asked, and say when something conflicts with a Claim or with their taste.
- Ground creative work in the brain. A recommendation cites the angle, the
  intel or the competitor ad it came from. If the brain is thin on something,
  say what is missing rather than inventing it.
- Learn. When the operator tells you something durable about the brand, their
  taste, an offer, a result or a competitor, call `remember` to write it into
  the right category — and mention in one short clause that you did. Do not
  remember one-off chatter, questions, or your own opinions.
- Write render prompts like a director: exact product, exact materials, exact
  layout, exact typography, aspect ratio last. Save the ones worth reusing to
  the prompt bank.
- Be concise. Short paragraphs, no filler preamble, no restating the question.
  Markdown is fine; keep tables small.

Safety: if a request is ambiguous or destructive — deleting swathes of memory,
spending real money on renders in bulk, publishing a claim you cannot
substantiate — confirm before acting. Never state a claim that is not backed by
the Claims category, and never take an irreversible action without a clear
go-ahead. If you cannot do something or do not know, say so plainly.\
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_brain",
        "description": "Full-text search across every entry in the brand brain. Use when the "
                       "digest mentions more entries than it shows, or to pull the exact "
                       "wording of something before relying on it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to match in titles, bodies and tags."},
                "category": {"type": "string", "enum": brain.CATEGORY_ORDER,
                             "description": "Optional category to restrict the search to."},
                "limit": {"type": "integer", "description": "Max results, default 20."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "list_category",
        "description": "List every entry in one category of the brain, newest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": brain.CATEGORY_ORDER},
                "limit": {"type": "integer", "description": "Max entries, default 50."},
            },
            "required": ["category"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "remember",
        "description": "Write a new durable fact into the brand brain. Use for anything the "
                       "operator states about the brand, their taste, an offer, a result or a "
                       "competitor that should still be true next week.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": brain.CATEGORY_ORDER},
                "title": {"type": "string", "description": "Short label, under ~60 characters."},
                "body": {"type": "string", "description": "The fact itself, stated plainly."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "meta": {"type": "string",
                         "description": "Optional JSON object as a string for structured fields, "
                                        "e.g. {\"days_live\": 281, \"roas\": 2.4}."},
            },
            "required": ["category", "title", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_memory",
        "description": "Revise an existing brain entry in place. Prefer this over remembering a "
                       "near-duplicate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "category": {"type": "string", "enum": brain.CATEGORY_ORDER},
            },
            "required": ["node_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "forget",
        "description": "Delete one entry from the brain. Confirm with the operator first unless "
                       "they explicitly asked for this exact deletion.",
        "input_schema": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "link",
        "description": "Draw a relationship between two brain entries, e.g. an angle that came "
                       "from a piece of intel, or a prompt that produced a performance result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "src_id": {"type": "string"},
                "dst_id": {"type": "string"},
                "kind": {"type": "string", "description": "e.g. derived_from, supports, contradicts"},
            },
            "required": ["src_id", "dst_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "render_ad",
        "description": "Queue an image or video render in the studio. Costs real money when a "
                       "provider key is configured, so confirm before queueing more than one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["image", "video"]},
                "prompt": {"type": "string", "description": "The full render prompt."},
                "aspect": {"type": "string", "enum": render.ASPECTS},
                "count": {"type": "integer", "description": "How many variants, 1-4. Default 1."},
            },
            "required": ["kind", "prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_renders",
        "description": "List recent studio renders with their status, prompt and cost.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


# --------------------------------------------------------------------------- tools

def _node_line(node: dict[str, Any]) -> str:
    tags = f" tags={node['tags']}" if node["tags"] else ""
    return f"[{node['id']}] ({node['category']}) {node['title']} — {node['body']}{tags}"


def execute_tool(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Run one tool. Returns (text for the model, side-effect info for the UI)."""
    if name == "search_brain":
        hits = db.search_nodes(args["query"], args.get("category"), int(args.get("limit") or 20))
        body = "\n".join(_node_line(h) for h in hits) or "No matches."
        return body, {"hits": len(hits)}

    if name == "list_category":
        cat = args["category"]
        nodes = db.list_nodes(cat, limit=int(args.get("limit") or 50))
        body = "\n".join(_node_line(n) for n in nodes) or f"{cat} is empty."
        return body, {"category": cat, "count": len(nodes)}

    if name == "remember":
        meta: dict[str, Any] = {}
        raw = args.get("meta")
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, dict):
                    meta = parsed
            except json.JSONDecodeError:
                meta = {"note": str(raw)[:200]}
        node = db.create_node(
            category=args["category"], title=args["title"], body=args["body"],
            tags=args.get("tags") or [], meta=meta, source="assistant",
        )
        return f"Remembered as {node['id']} in {node['category']}.", {"brain_changed": True, "node": node}

    if name == "update_memory":
        node = db.update_node(
            args["node_id"], title=args.get("title"), body=args.get("body"),
            tags=args.get("tags"), category=args.get("category"),
        )
        if not node:
            return f"No entry with id {args['node_id']}.", {}
        return f"Updated {node['id']}.", {"brain_changed": True, "node": node}

    if name == "forget":
        ok = db.delete_node(args["node_id"])
        return ("Deleted." if ok else "No such entry."), {"brain_changed": ok}

    if name == "link":
        src, dst = args["src_id"], args["dst_id"]
        if not db.get_node(src) or not db.get_node(dst):
            return "One of those ids does not exist.", {}
        edge = db.create_edge(src, dst, args.get("kind") or "relates_to")
        return f"Linked {src} -> {dst} ({edge['kind']}).", {"brain_changed": True}

    if name == "render_ad":
        status = render.provider_status()
        engine = status["default_engine"]
        key = "video_models" if args.get("kind") == "video" else "image_models"
        model = render.ENGINES[engine][key][0]["id"]
        rows = render.queue_render(
            kind=args.get("kind", "image"), prompt=args["prompt"], engine=engine, model=model,
            aspect=args.get("aspect") or "9:16", resolution="1024",
            count=int(args.get("count") or 1),
        )
        ids = [r["id"] for r in rows]
        done = [render.run_render(rid) for rid in ids]
        lines = [
            f"{r['id']}: {r['status']}" + (f" {r['url']}" if r["url"] else "")
            + (f" error={r['error']}" if r["error"] else "")
            for r in done
        ]
        note = "" if status["fal"] else " (no FAL_KEY set — local placeholder cards were written)"
        return "Queued and rendered:\n" + "\n".join(lines) + note, {"renders_changed": True}

    if name == "list_renders":
        rows = db.list_renders(int(args.get("limit") or 20))
        if not rows:
            return "The studio is empty.", {}
        body = "\n".join(
            f"[{r['id']}] {r['kind']} {r['status']} ${r['cost']:.2f} — {r['prompt'][:120]}"
            for r in rows
        )
        return body, {}

    return f"Unknown tool {name}.", {}


# --------------------------------------------------------------------------- loop

def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _system_blocks() -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": PERSONA},
        {
            "type": "text",
            "text": "=== BRAND BRAIN (live, complete digest) ===\n" + brain.digest(),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _request_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": _system_blocks(),
        "tools": TOOLS,
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": EFFORT},
    }
    if USE_FALLBACKS:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
    return kwargs


def _blocks(message: Any) -> list[dict[str, Any]]:
    return [b.model_dump(exclude_none=True) for b in message.content]


def converse(session_id: str, user_text: str) -> Iterator[dict[str, Any]]:
    """Stream one exchange. Yields UI events; persists the turn as it goes."""
    client = _client()
    db.add_message(session_id, "user", [{"type": "text", "text": user_text}])
    messages: list[dict[str, Any]] = db.list_messages(session_id)
    messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    side_effects: dict[str, Any] = {}
    kwargs = _request_kwargs()

    for _turn in range(MAX_TOOL_TURNS):
        try:
            with client.beta.messages.stream(messages=messages, **kwargs) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield {"type": "text", "text": event.delta.text}
                        elif event.delta.type == "thinking_delta":
                            yield {"type": "thinking", "text": event.delta.thinking}
                    elif event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            yield {"type": "tool_start", "name": event.content_block.name}
                final = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            yield {"type": "error", "message": f"{exc.status_code}: {str(exc)[:400]}"}
            return
        except anthropic.APIConnectionError as exc:
            yield {"type": "error", "message": f"connection failed: {exc}"}
            return

        if final.stop_reason == "refusal":
            detail = getattr(final, "stop_details", None)
            reason = getattr(detail, "explanation", None) or "the request was declined"
            yield {"type": "error", "message": f"Declined: {reason}"}
            return

        content = _blocks(final)
        messages.append({"role": "assistant", "content": content})
        db.add_message(session_id, "assistant", content)

        tool_uses = [b for b in final.content if b.type == "tool_use"]
        if not tool_uses:
            yield {"type": "done", "usage": {
                "input": final.usage.input_tokens, "output": final.usage.output_tokens,
            }, **side_effects}
            return

        results = []
        for block in tool_uses:
            args = block.input if isinstance(block.input, dict) else json.loads(block.input or "{}")
            try:
                text, info = execute_tool(block.name, args)
                is_error = False
            except Exception as exc:  # a broken tool must not kill the turn
                text, info, is_error = f"Tool failed: {exc}", {}, True
            side_effects.update({k: v for k, v in info.items() if k.endswith("_changed")})
            yield {"type": "tool_end", "name": block.name, "summary": text[:200], "info": info}
            results.append({
                "type": "tool_result", "tool_use_id": block.id, "content": text,
                **({"is_error": True} if is_error else {}),
            })

        user_block = {"role": "user", "content": results}
        messages.append(user_block)
        db.add_message(session_id, "user", results)

    yield {"type": "error", "message": f"Stopped after {MAX_TOOL_TURNS} tool turns."}


def ask_once(prompt: str, system: str | None = None, max_tokens: int = 4000) -> str:
    """Single non-streaming call used by /api/recommendations."""
    client = _client()
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system or PERSONA,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": EFFORT},
        "messages": [{"role": "user", "content": prompt}],
    }
    if USE_FALLBACKS:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
        message = client.beta.messages.create(**kwargs)
    else:
        message = client.messages.create(**kwargs)
    if message.stop_reason == "refusal":
        return "The model declined this request."
    return "\n".join(b.text for b in message.content if b.type == "text").strip()
