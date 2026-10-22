#!/usr/bin/env python3
"""audit_user_pick_from_self_options — detect "you pick from these options" framings.

Memory rule: ``feedback_no_user_pick_from_self_options.md``

When Claude lists 2+ self-generated improvement candidates / proposals / plans
and then asks the user to pick which one to prioritize, that is a role-reversal
saying-fault: the user already paid 1 action to ask, Claude must complete the
prioritization itself.

This hook is a Stop event audit-only scanner: it logs hits and emits a stderr
warning so the next turn can self-correct. It does NOT block the response —
Stop fires after the assistant text is already displayed.

Detection strategy:
  - Scan the latest assistant turn text (since the last user message) for any
    of the ``_PICK_PATTERNS`` regexes. These are the specific phrasings that
    delegate prioritization to the user.
  - Optionally elevate to "high-confidence" if the same turn also contains
    enumeration markers (### N / 1. / N つの / 候補) — i.e. Claude listed
    items and then asked the user to pick.

Exclusions (permitted contexts, see memory §3):
  - Authentication / physical operation requests (handled by
    block_hedged_concerns.py user-action patterns, separate concern)
  - Pure information-gathering questions about user environment state
    ("X の現状を教えてほしい" — fact retrieval, not preference picking)

Test entries (verified manually):
  TP (should fire):
    "上記の 4 つから user 視点で最も投資効果が高そうなのはどれか教えてください"
    "どれを優先しますか?"
    "どちらを進めますか"
    "他にもっと深刻な失敗パターンがあれば教えてください"  (after a list)
  FP guard:
    "X の値を教えてほしい" — single fact request, no enumeration marker
    "どれが正解か分からない" — Claude self-talk, not a request to user
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Pattern set: "you pick from these options" framings.
# All patterns assume the assistant already enumerated something earlier in
# the same turn. The enumeration check happens in main() before logging.
_PICK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"どれが.{0,20}(効果的|高そう|重要|優先|ROI|投資効果|大きい|深刻)"),
    re.compile(r"どれを.{0,10}(優先|選|やり|進め|実装)"),
    re.compile(r"どれにしますか"),
    re.compile(r"どちらを.{0,10}(進め|選|やり|実装)"),
    re.compile(r"どちらにしますか"),
    re.compile(r"(user|あなた|ユーザー).{0,20}視点で.{0,30}(どれ|どちら)"),
    re.compile(r"最も.{0,20}(効果的|高そう|重要|投資効果).{0,20}のはどれ"),
    re.compile(r"他に.{0,30}(深刻|重要|大きい|あれば).{0,20}教えて"),
    re.compile(r"ご判断ください"),
    re.compile(r"ご指示ください"),
    # 2026-05-25 追加: confirmation-step 偽装 phrase (AskUserQuestion 直前に
    # よく出るバインダー)
    re.compile(r"優先度を確認させてください"),
    re.compile(r"(どれ|どちら|どの).{0,10}にするか確認"),
    re.compile(r"進める前に.{0,5}(確認|相談)"),
    re.compile(r"先に決めてください"),
)

# Enumeration markers — if any appears in the same turn, the pick request is
# high-confidence (Claude listed options and then asked user to pick).
_ENUM_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^###\s+\d+\.", re.MULTILINE),
    re.compile(r"^\d+\.\s", re.MULTILINE),
    re.compile(r"^-\s+\*\*", re.MULTILINE),
    re.compile(r"\d+\s*(つ|件|候補|案|提案)"),
    re.compile(r"候補.{0,5}[:：]"),
)

__import__("sys").path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _paths import RUNTIME_DIR  # noqa: E402
_AUDIT_LOG = RUNTIME_DIR / "audit_user_pick_from_self_options.log"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _scan_current_turn_assistant_text(transcript_path: str) -> str | None:
    """Return concatenated assistant text since the last user message."""
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        return None
    turn_parts: list[str] = []
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
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "user":
                turn_parts = []
                continue
            if role != "assistant":
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
                turn_parts.append(text)
    except OSError:
        return None
    if not turn_parts:
        return None
    return "\n".join(turn_parts)


def _find_hits(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
) -> list[tuple[str, int, int]]:
    hits: list[tuple[str, int, int]] = []
    for pat in patterns:
        for m in pat.finditer(text):
            hits.append((m.group(0), m.start(), m.end()))
    return hits


def _has_enumeration(text: str) -> bool:
    for pat in _ENUM_MARKERS:
        if pat.search(text):
            return True
    return False


def _log_hits(
    sid: str,
    hits: list[tuple[str, int, int]],
    text: str,
    enum_present: bool,
) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for token, s, e in hits:
            start = max(0, s - 40)
            end = min(len(text), e + 40)
            entry = {
                "ts": ts,
                "session_id": sid,
                "token": token,
                "context": text[start:end],
                "enum_present": enum_present,
                "confidence": "high" if enum_present else "low",
            }
            with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# --- telemetry instrumentation (best-effort; never breaks the hook) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry import must never break a hook
    def record_fire(*_a, **_k):  # type: ignore
        return None


def main() -> int:
    data = _read_payload()
    sid = data.get("session_id") or ""
    transcript = data.get("transcript_path") or ""
    if not sid or not transcript:
        return 0
    text = _scan_current_turn_assistant_text(transcript)
    if not text:
        return 0

    pick_hits = _find_hits(text, _PICK_PATTERNS)
    if not pick_hits:
        return 0

    enum_present = _has_enumeration(text)
    _log_hits(sid, pick_hits, text, enum_present)
    record_fire("feedback_no_user_pick_from_self_options", "warn", context="high_conf" if enum_present else "low_conf")

    if enum_present:
        tokens = ", ".join(sorted({t for t, _, _ in pick_hits}))
        sys.stderr.write(
            f"[audit_user_pick_from_self_options] high-confidence hit: enumeration + pick-framing detected: {tokens}. "
            "Per feedback_no_user_pick_from_self_options: Claude must pick the top option from its own list and start "
            "implementing, not delegate prioritization to the user. Remove the question and commit to one path.\n"
        )
    else:
        tokens = ", ".join(sorted({t for t, _, _ in pick_hits}))
        sys.stderr.write(
            f"[audit_user_pick_from_self_options] low-confidence hit (no enumeration markers): {tokens}. "
            "Verify this is genuine fact-retrieval, not preference delegation.\n"
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[audit_user_pick_from_self_options] error: {exc}\n")
        sys.exit(0)
