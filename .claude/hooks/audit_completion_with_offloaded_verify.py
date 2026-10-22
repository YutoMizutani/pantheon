#!/usr/bin/env python3
"""audit_completion_with_offloaded_verify — flag a turn that declares completion
("完了" / "検証済み" / "Done" / "直った" / GREEN) while simultaneously offloading
the FINAL end-state verification to the user ("あなたが〜してください" /
"投稿してくれたら確認します" / "次の一手はあなたの〜").

Memory rule:
  - ``feedback_completion_means_user_visible_end_state.md`` (saying-fault,
    第5違反 2026-06-08 session 89b39a61): declared "原因と対処（完了・検証済み）"
    with socket owner = native Mac, while telling the user "次の一手はあなたの
    実投稿テスト1本だけ … 投稿してくれたら私も確認します". The owner state was a
    transient mechanism state that reverted when the native window closed; the
    durable fix landed only after the user's "ok". The co-occurrence of a
    completion token + an offloaded final-verify phrase is the deterministic
    signature of "I claimed done but haven't observed the end state myself".

Stop event hook. Scans the latest assistant turn (since the most recent user
message). Fires (stderr + audit log) only when BOTH are present in the turn:

  1. A completion token (完了 / 検証済み / Done / 直った / GREEN確定 / 対処完了 …)
     that is NOT explicitly scoped as a remaining step (「残り」「次のステップ」),
     AND
  2. An offloaded-final-verify phrase: the user must perform an action and then
     Claude will confirm the result ("投稿してくれたら確認", "あなたが〜してください",
     "次の一手はあなたの〜", "実投稿テスト").

Exit 0 always (audit only; Stop fires post-display so blocking is impossible —
value is closing the self-recall gap with a deterministic post-turn signal).
False positives are tolerated low: the AND of two phrase families plus the
"残り/次のステップ" suppressor keeps it narrow.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# (1) Completion / GREEN tokens.
_COMPLETION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"完了・検証済み"),
    re.compile(r"対処(は)?完了"),
    re.compile(r"検証済み(です|でした)?"),
    re.compile(r"GREEN\s*(確定|です)"),
    re.compile(r"(根本原因|恒久(対策|修正|fix))[^\n]{0,12}(完了|確定|済)"),
    re.compile(r"直りました"),
    re.compile(r"修正(は)?完了"),
)

# (2) Offloaded-final-verify phrases: user must act, then Claude confirms.
_OFFLOAD_VERIFY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"次の一手は\s*あなた"),
    re.compile(r"あなた(が|の)?[^\n]{0,20}(実投稿|投稿|押して|試して|開いて|やって)[^\n]{0,12}(ください|くれたら|もらえれば|ください。)"),
    re.compile(r"(投稿|実行|クリック|押下|操作)してくれ(たら|れば)[^\n]{0,20}(確認|追跡|チェック)し"),
    re.compile(r"実投稿(テスト)?[^\n]{0,8}(1本|一本|してみて)"),
    re.compile(r"してみてください[^\n]{0,30}(完了|確認します|GREEN)"),
    re.compile(r"(投稿|試)してくれたら(私も|こちらでも)?[^\n]{0,12}(確認|ログ)"),
)

# Suppressor: completion token framed as a *remaining* step is fine.
_REMAINING_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"残り[:：]"),
    re.compile(r"次のステップ"),
    re.compile(r"完了を絶対に使わない"),  # quoting the rule itself
)

__import__("sys").path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _paths import RUNTIME_DIR  # noqa: E402
_AUDIT_LOG = RUNTIME_DIR / "audit_completion_with_offloaded_verify.log"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _scan_current_turn_assistant_text(transcript_path: str) -> str | None:
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


def _any_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    hits: list[str] = []
    for pat in patterns:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    return hits


def _log_hit(sid: str, completion_tokens: list[str], offload_tokens: list[str], text: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "ts": ts,
            "session_id": sid,
            "category": "completion_with_offloaded_verify",
            "completion_tokens": completion_tokens,
            "offload_tokens": offload_tokens,
            "text_tail": text[-400:],
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

    completion_hits = _any_match(text, _COMPLETION_PATTERNS)
    if not completion_hits:
        return 0
    offload_hits = _any_match(text, _OFFLOAD_VERIFY_PATTERNS)
    if not offload_hits:
        return 0
    # Suppress when the completion is explicitly framed as a remaining step.
    if _any_match(text, _REMAINING_SCOPE_PATTERNS):
        return 0

    _log_hit(sid, completion_hits, offload_hits, text)
    ctok = ", ".join(sorted(set(completion_hits))[:3])
    otok = ", ".join(sorted(set(offload_hits))[:3])
    record_fire("feedback_completion_means_user_visible_end_state", "warn", context=f"{ctok}|{otok}"[:200])
    sys.stderr.write(
        f"[audit_completion_with_offloaded_verify] completion claimed ({ctok}) "
        f"while the FINAL end-state verify is offloaded to the user ({otok}). "
        "Per feedback_completion_means_user_visible_end_state 第5違反: a "
        "completion token that co-occurs with 'あなたが〜してください / 投稿して"
        "くれたら確認します' means you have NOT observed the user-visible end "
        "state yourself. Replace 「完了」with 「残り: <user step>。それが済めば確認"
        "します」, or reach a structural guarantee that holds without the user "
        "step before declaring done.\n"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[audit_completion_with_offloaded_verify] error: {exc}\n")
        sys.exit(0)
