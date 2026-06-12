#!/usr/bin/env python3
# RECIPE (非配線): 原環境の自己改善ループが育てた規範の実体。
# 採用条件 = 中継 transport (Discord bot 等) を持つ環境のみ価値あり。中継なし環境では hook のみで運用。
# 採用手順と再校正の注意は ../README.md (docs/recipes/README.md)。
"""mask_home_in_text — deterministic outbound text guard (Hook 1).

Memory rule: ``feedback_mask_home_in_user_text.md``.

This hook runs on PostToolUse and Stop. Its job is *not* to mutate the
Claude Code transcript (we don't own that file format). If you relay
assistant output to an external surface (chat bridge, bot), do the real
masking at that chokepoint — this hook is the observer side.

This standalone hook scans the transcript for an unmasked home directory
path in the most recent assistant turn and writes a one-line audit note
to ``.claude/runtime/mask_home_audit.log`` so we can spot drift if the
masking ever regresses. It never blocks, never mutates state — pure
observability.

Output: nothing on stdout (hook protocol allows empty). Exit 0 always.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return

from _paths import HOME_HIT_RE as _HOME_HIT_RE  # noqa: E402
from _paths import RUNTIME_DIR as _RUNTIME_DIR  # noqa: E402

_AUDIT_LOG = _RUNTIME_DIR / "mask_home_audit.log"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _scan_latest_assistant_text(transcript_path: str) -> str | None:
    """Return the most recent assistant text turn, or None."""
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        return None
    latest: str | None = None
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            parts: list[str] = []
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(str(c.get("text", "")))
            text = "\n".join(parts).strip()
            if text:
                latest = text
    except OSError:
        return None
    return latest


def _log_hit(sid: str, sample: str) -> None:
    """Append an audit line for any unmasked home-path reference."""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "ts": ts,
            "session_id": sid,
            "sample": sample[:200],
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Best-effort. Never block on audit failure.
        pass


def main() -> int:
    data = _read_payload()
    sid = data.get("session_id") or ""
    transcript = data.get("transcript_path") or ""
    if not sid or not transcript:
        return 0
    text = _scan_latest_assistant_text(transcript)
    if not text:
        return 0
    m = _HOME_HIT_RE.search(text)
    if m:
        # Include 60 chars of context around the hit for triage.
        start = max(0, m.start() - 30)
        end = min(len(text), m.end() + 30)
        _log_hit(sid, text[start:end])
        record_fire("feedback_mask_home_in_user_text", "audit")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never crash the hook chain
        sys.stderr.write(f"[mask_home_in_text] error: {exc}\n")
        sys.exit(0)
