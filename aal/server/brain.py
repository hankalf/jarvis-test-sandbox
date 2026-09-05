"""The brand brain: the knowledge graph the assistant is aware of at all times.

Ten categories hang off a central brand node. Every answer the assistant gives
is grounded in a digest of this graph, and the assistant can write back into it
(`remember`) so awareness compounds over time.
"""

from __future__ import annotations

from typing import Any

from . import db

CATEGORIES: dict[str, dict[str, str]] = {
    "identity": {
        "label": "Identity",
        "color": "#7aa2ff",
        "glyph": "◉",
        "about": "Who the brand is: name, category, positioning, tone of voice, "
                 "non-negotiables, visual signature, who it is for.",
    },
    "taste": {
        "label": "Your taste",
        "color": "#f5c451",
        "glyph": "✦",
        "about": "The operator's own preferences: what they always approve, what "
                 "they always kill, pacing, copy length, colour and type bias.",
    },
    "claims": {
        "label": "Claims",
        "color": "#8be0c0",
        "glyph": "⛨",
        "about": "What may legally and factually be said. Substantiated claims, "
                 "banned phrasing, disclaimers that must appear.",
    },
    "assets": {
        "label": "Assets",
        "color": "#63d2a6",
        "glyph": "▦",
        "about": "Product shots, logos, packshots, fonts, brand colours, "
                 "b-roll — the raw material a render can reference.",
    },
    "angles": {
        "label": "Angles",
        "color": "#ff7a90",
        "glyph": "◎",
        "about": "Creative angles and hooks: the argument an ad makes. "
                 "Problem/solution, status, ritual, gifting, seasonal.",
    },
    "prompts": {
        "label": "Prompt bank",
        "color": "#c88bff",
        "glyph": "❑",
        "about": "Render prompts that worked, with the settings that produced "
                 "them. Reusable, parameterised, versioned.",
    },
    "intel": {
        "label": "Intel file",
        "color": "#b6a1ff",
        "glyph": "❑",
        "about": "Market and audience intelligence: who buys, why, objections, "
                 "seasonality, price sensitivity, channel behaviour.",
    },
    "adspy": {
        "label": "Ad Spy",
        "color": "#ff6ec7",
        "glyph": "◇",
        "about": "Competitor ads observed in the wild, with how long each has "
                 "been running. Long-running ads are proven spend.",
    },
    "offers": {
        "label": "Offers",
        "color": "#5fd0e8",
        "glyph": "🏷",
        "about": "Live promotions, bundles, price points and the dates they run.",
    },
    "performance": {
        "label": "Performance",
        "color": "#9fb3c8",
        "glyph": "▤",
        "about": "What actually performed: CTR, hook rate, ROAS, spend, and the "
                 "creative each number belongs to.",
    },
}

CATEGORY_ORDER = list(CATEGORIES)


def is_category(name: str) -> bool:
    return name in CATEGORIES


def graph() -> dict[str, Any]:
    """The full graph the UI draws: brand hub -> categories -> leaf nodes."""
    counts = db.category_counts()
    nodes = db.list_nodes(limit=2000)
    brand = db.kv_get("brand_name", "Your brand")
    return {
        "brand": brand,
        "categories": [
            {
                "id": f"cat:{slug}",
                "slug": slug,
                "count": counts.get(slug, 0),
                **meta,
            }
            for slug, meta in CATEGORIES.items()
        ],
        "nodes": nodes,
        "edges": db.list_edges(),
        "total": len(nodes),
    }


def _fmt(node: dict[str, Any]) -> str:
    line = f"- [{node['id']}] {node['title']}"
    if node["body"]:
        body = " ".join(node["body"].split())
        line += f" — {body}"
    if node["tags"]:
        line += f"  (tags: {', '.join(node['tags'])})"
    meta = node.get("meta") or {}
    if meta:
        bits = ", ".join(f"{k}={v}" for k, v in meta.items() if v not in (None, "", []))
        if bits:
            line += f"  [{bits}]"
    return line


def digest(max_per_category: int = 14, body_chars: int = 320) -> str:
    """A compact, stable rendering of the whole brain for the system prompt.

    Stable ordering matters: this block sits in the cached prefix of every
    request, so it must not reshuffle between turns.
    """
    brand = db.kv_get("brand_name", "Your brand")
    out = [f"BRAND: {brand}", ""]
    for slug in CATEGORY_ORDER:
        meta = CATEGORIES[slug]
        nodes = db.list_nodes(slug, limit=max_per_category + 5)
        out.append(f"## {meta['label']}  ({len(nodes)} entries)")
        if not nodes:
            out.append("- (empty)")
        for node in nodes[:max_per_category]:
            trimmed = dict(node)
            if len(trimmed["body"]) > body_chars:
                trimmed["body"] = trimmed["body"][:body_chars].rstrip() + "…"
            out.append(_fmt(trimmed))
        if len(nodes) > max_per_category:
            out.append(f"- …{len(nodes) - max_per_category} more, use search_brain/list_category")
        out.append("")
    return "\n".join(out).strip()
