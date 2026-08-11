"""Targeted edits to config.yaml that keep the comments.

Round-tripping through a YAML library would strip every comment out of a file
whose comments are most of its value. So this edits the text, but scoped to
one top-level block -- `token:` appears under `remote:` and could plausibly
appear elsewhere later, and a script that rewrites the wrong secret is worse
than one that does nothing.
"""

from __future__ import annotations

import re
from pathlib import Path


def _block_bounds(lines: list[str], block: str) -> tuple[int, int] | None:
    """Line range [start, end) of a top-level `block:` mapping."""
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(block)}:\s*(#.*)?$", line):
            start = i + 1
            break
    if start is None:
        return None
    for j in range(start, len(lines)):
        line = lines[j]
        # A non-indented, non-blank, non-comment line ends the block.
        if line.strip() and not line.startswith((" ", "\t")) and not line.lstrip().startswith("#"):
            return start, j
    return start, len(lines)


def set_in_remote(path: str | Path, key: str, value: str) -> None:
    """Set `key: value` inside the top-level `remote:` block.

    Rewrites the key if it is already there (preserving its indentation and
    any trailing comment position), inserts it at the top of the block if not,
    and creates the block if the file has none.
    """
    path = Path(path)
    lines = path.read_text().splitlines(keepends=True)
    bounds = _block_bounds(lines, "remote")

    if bounds is None:
        text = "".join(lines).rstrip("\n")
        path.write_text(f"{text}\n\nremote:\n  {key}: {value}\n")
        return

    start, end = bounds
    pattern = re.compile(rf"^(\s+){re.escape(key)}:\s*.*$")
    for i in range(start, end):
        match = pattern.match(lines[i].rstrip("\n"))
        if match:
            lines[i] = f"{match.group(1)}{key}: {value}\n"
            path.write_text("".join(lines))
            return

    # Not present: insert at the top of the block, matching sibling indent.
    indent = "  "
    for i in range(start, end):
        stripped = lines[i].lstrip()
        if stripped and not stripped.startswith("#"):
            indent = lines[i][: len(lines[i]) - len(stripped)]
            break
    lines.insert(start, f"{indent}{key}: {value}\n")
    path.write_text("".join(lines))
