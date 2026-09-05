"""Runtime configuration for AAL.

Everything is environment-driven so the app can run with nothing but an
Anthropic key, and light up extra capabilities (image/video rendering) when
the corresponding provider keys are present.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("AAL_DATA_DIR", ROOT / "data"))
WEB_DIR = ROOT / "web"
DB_PATH = DATA_DIR / "aal.sqlite3"
RENDER_DIR = DATA_DIR / "renders"

# Claude Opus 5 is the brain. Override with AAL_MODEL if you want a cheaper
# model for high-volume work (claude-sonnet-5, claude-haiku-4-5).
MODEL = os.environ.get("AAL_MODEL", "claude-opus-5")

# Effort trades thoroughness against token spend. "medium" keeps the assistant
# snappy in conversation; raise to "high"/"xhigh" for strategy work.
EFFORT = os.environ.get("AAL_EFFORT", "medium")

# Server-side refusal fallbacks: if a safety classifier declines a request,
# the API routes it to a suitable fallback model instead of returning nothing.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
USE_FALLBACKS = os.environ.get("AAL_FALLBACKS", "1") not in ("0", "false", "no")

MAX_TOKENS = int(os.environ.get("AAL_MAX_TOKENS", "16000"))
MAX_TOOL_TURNS = int(os.environ.get("AAL_MAX_TOOL_TURNS", "12"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Optional render providers.
FAL_KEY = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")

HOST = os.environ.get("AAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("AAL_PORT", "8420"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
