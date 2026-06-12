"""Shared layer: stamp `last_reinforced:` on a memory file's frontmatter.

Triggered by `touch_memory_on_read.py` (a PostToolUse hook on Read) when
Claude opens a file under the auto-memory directory. The aggregator can
later use `last_reinforced` (paired with file mtime) to find rules that
have not been pulled into context in months — candidates for archival.

Rate-limited: if `last_reinforced` is already today's date the file is
left untouched, so the worst case is one write per memory file per day.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from _paths import MEMORY_DIR as _MEMORY_ROOT  # noqa: E402
from _paths import TELEMETRY_DIR as _TELEMETRY_DIR  # noqa: E402

_TOUCHES_LOG = _TELEMETRY_DIR / "memory_touches.jsonl"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_LAST_REINFORCED_RE = re.compile(r"^last_reinforced:\s*(\S+)\s*$", re.MULTILINE)


def is_memory_path(path: str | Path) -> bool:
    """True if `path` lives directly under the memory root (excluding archived/)."""
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    if not str(p).endswith(".md"):
        return False
    try:
        rel = p.relative_to(_MEMORY_ROOT.resolve())
    except ValueError:
        return False
    # archived/ subtree is excluded by design — touching a tomb resurrects it.
    if rel.parts and rel.parts[0] == "archived":
        return False
    # MEMORY.md is the index, not a rule; skip.
    if rel.parts == ("MEMORY.md",):
        return False
    return True


def touch_memory_file(path: str | Path) -> bool:
    """Stamp today's date as `last_reinforced` in the file's frontmatter.

    Returns True iff the file was written (i.e. the date actually changed).
    No-op if `last_reinforced` already equals today's date.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return False

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        # No frontmatter — log the touch but don't synthesize structure.
        _log_touch(p, "no_frontmatter")
        return False

    fm_body = fm_match.group(1)
    lr_match = _LAST_REINFORCED_RE.search(fm_body)
    if lr_match and lr_match.group(1) == today:
        _log_touch(p, "already_today")
        return False

    if lr_match:
        new_fm = (
            fm_body[: lr_match.start()]
            + f"last_reinforced: {today}"
            + fm_body[lr_match.end() :]
        )
    else:
        new_fm = fm_body.rstrip() + f"\nlast_reinforced: {today}"

    new_text = f"---\n{new_fm}\n---\n" + text[fm_match.end() :]
    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError:
        return False
    _log_touch(p, "stamped")
    return True


def _log_touch(path: Path, status: str) -> None:
    try:
        _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "memory": path.stem,
            "status": status,
        }
        with open(_TOUCHES_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return
