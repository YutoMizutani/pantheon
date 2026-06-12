#!/usr/bin/env python3
# RECIPE (非配線): 原環境の自己改善ループが育てた規範の実体。
# 採用条件 = 中継 transport を持つ環境のみ価値あり。
# FRAME_NAME_TOKENS_RE / FRAME_NAME_TOKENS_CJK 環境変数で個人名トークンを与えること。
# 採用手順と再校正の注意は ../README.md (docs/recipes/README.md)。
"""strip_user_names — deterministic name-leak guard (Hook 2).

Memory rule: ``feedback_do_not_address_user_by_name.md``.

This hook is the standalone observer counterpart to active enforcement:
if you relay assistant output to an external surface (chat bridge, bot),
mask names at that chokepoint — this hook only audits the transcript.

It scans the most recent assistant turn in the Claude Code transcript and
logs any unmasked hit to ``.claude/runtime/strip_user_names_audit.log`` so
regressions are visible.

Detected tokens are supplied via environment variables (see below) so the
distributed frame ships with no personal names baked in:
  * ``FRAME_NAME_TOKENS_RE``  — ASCII regex (case-insensitive)
  * ``FRAME_NAME_TOKENS_CJK`` — comma-separated literal tokens

Never blocks, never mutates. Exit 0 always.
"""

from __future__ import annotations

import json
import os.path
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

# EDIT ME (環境変数で指定): 自分の実名・ハンドルなど出力に漏れてはいけないトークン。
# 未設定なら何にもマッチしない no-op として安全に動く。
import os  # noqa: E402

_NAME_ASCII_RE = re.compile(
    os.environ.get("FRAME_NAME_TOKENS_RE", r"$^"),
    re.IGNORECASE,
)
_NAME_CJK_TOKENS: tuple[str, ...] = tuple(
    t.strip() for t in os.environ.get("FRAME_NAME_TOKENS_CJK", "").split(",") if t.strip()
)
from _paths import RUNTIME_DIR as _RUNTIME_DIR  # noqa: E402

_AUDIT_LOG = _RUNTIME_DIR / "strip_user_names_audit.log"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _scan_latest_assistant_text(transcript_path: str) -> str | None:
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


def _find_name_hit(text: str) -> tuple[str, int, int] | None:
    """Return (matched_token, start, end) for the first name hit, or None."""
    m = _NAME_ASCII_RE.search(text)
    if m:
        return (m.group(0), m.start(), m.end())
    for tok in _NAME_CJK_TOKENS:
        idx = text.find(tok)
        if idx != -1:
            return (tok, idx, idx + len(tok))
    return None


def _log_hit(sid: str, token: str, sample: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "ts": ts,
            "session_id": sid,
            "token": token,
            "sample": sample[:200],
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
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
    hit = _find_name_hit(text)
    if hit is None:
        return 0
    token, s, e = hit
    start = max(0, s - 30)
    end = min(len(text), e + 30)
    _log_hit(sid, token, text[start:end])
    record_fire("feedback_do_not_address_user_by_name", "audit", context=token)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[strip_user_names] error: {exc}\n")
        sys.exit(0)
