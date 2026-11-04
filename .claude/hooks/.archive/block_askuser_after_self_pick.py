#!/usr/bin/env python3
"""block_askuser_after_self_pick — intercept AskUserQuestion when it is being
used to delegate Claude's own self-generated picking back to the user.

Memory rule: ``feedback_no_user_pick_from_self_options.md`` §2.5
(2026-05-25 recurrence: heartbeat re-prevention session opened AskUserQuestion
to pick between A/B/C/D self-listed options after recommending B+C).

This is a PreToolUse hook with matcher ``AskUserQuestion``. It examines:

  1. Whether the latest assistant turn (since the last user message) contains
     enumeration markers (### N. / 1. / A./B./C. / table rows).
  2. Whether the same turn contains a self-picking phrase (推奨は X / top 1 は X
     / X を採用 / X+Y 併用 / 推奨 X).
  3. Whether the latest user prompt is imperative (〜して / 〜してください
     when paired with directive verbs like 修正 / 再発防止 / 実装 / 直して).

If all three conditions hold, the AskUserQuestion call is a confirmation-step
fake; the hook emits a stderr warning and (in strict mode) returns exit 2 to
block the tool call. Default mode is audit-only (exit 0 + warning) to avoid
breaking legitimate preference-gathering questions.

Exclusions (permitted contexts, see memory §3 + §2.5):
  - Authentication / physical operations
  - Fact retrieval (no enumeration in prior turn)
  - User prompt is itself a question / not imperative (e.g. "どれが良い？")

Note: a §2.6 branch (capability-question + opt-out option, 2026-06-05) was
added then reverted the same day. Its decisive condition — "the scope decision
needs no user-private info" — is a judgement call that cannot be pattern-matched,
so it false-fired on legitimate destructive-action confirmations (delete /
mass-send / spend: 9 of 13 review scenarios). That pattern stays judgement-only
in feedback_no_user_pick_from_self_options.md §2.6; do NOT re-mechanize it here.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

__import__("sys").path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _paths import RUNTIME_DIR  # noqa: E402
_AUDIT_LOG = RUNTIME_DIR / "block_askuser_after_self_pick.log"

# Strict mode (exit 2 to block tool call) vs audit-only (exit 0 + warning).
# Default to audit-only because false positives would break legitimate
# preference-gathering. Set BLOCK_ASKUSER_STRICT=1 to enforce.
_STRICT = os.environ.get("BLOCK_ASKUSER_STRICT", "0") == "1"

_ENUM_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^###\s+\d+\.", re.MULTILINE),
    re.compile(r"^\d+\.\s", re.MULTILINE),
    re.compile(r"^[A-D]\.\s", re.MULTILINE),
    re.compile(r"^\|.*\|.*\|", re.MULTILINE),  # markdown table row
    re.compile(r"\d+\s*(つ|件|候補|案|提案)"),
    re.compile(r"候補.{0,5}[:：]"),
)

_SELF_PICK_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"推奨は\s*[A-D0-9]"),
    re.compile(r"推奨\s*[:：]\s*[A-D0-9]"),
    re.compile(r"top\s*1\s*は"),
    re.compile(r"[A-D]\s*[\+＋]\s*[A-D]\s*併用"),
    re.compile(r"[A-D]\s*を採用"),
    re.compile(r"これにします"),
    re.compile(r"これを進めます"),
    re.compile(r"これで進めます"),
    re.compile(r"を選択します"),
)

# Imperative user prompt: verb-stem + して / してください, paired with action
# verbs that imply "do it, don't ask".
_IMPERATIVE_USER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(修正|直し|実装|追加|削除|再発防止|改善|対応|適用|入れ|やっ|進め|完遂|やって|片付け|片して|片付けて).{0,3}(て|てください|てくれ|てちょうだい)"),
    re.compile(r"(やって|直して|修正して|実装して|再発防止して|対応して|進めて|片付けて)"),
)


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _scan_turn(transcript_path: str) -> tuple[str, str]:
    """Return (assistant_text_this_turn, last_user_text)."""
    if not transcript_path:
        return "", ""
    p = Path(transcript_path)
    if not p.exists():
        return "", ""
    last_user = ""
    asst_parts: list[str] = []
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
            content = msg.get("content")
            text_parts: list[str] = []
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        text_parts.append(str(c.get("text", "")))
            text = "\n".join(text_parts).strip()
            if role == "user":
                # Skip tool_result entries (those use list content w/o text)
                if text:
                    last_user = text
                    asst_parts = []
            elif role == "assistant" and text:
                asst_parts.append(text)
    except OSError:
        return "", ""
    return "\n".join(asst_parts), last_user


def _any_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    hits: list[str] = []
    for pat in patterns:
        m = pat.search(text)
        if m:
            hits.append(m.group(0))
    return hits


def _log(entry: dict) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
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
    tool_name = data.get("tool_name") or ""
    if tool_name != "AskUserQuestion":
        return 0
    sid = data.get("session_id") or ""
    transcript = data.get("transcript_path") or ""
    asst_text, user_text = _scan_turn(transcript)
    if not asst_text or not user_text:
        return 0

    enum_hits = _any_match(asst_text, _ENUM_MARKERS)
    pick_hits = _any_match(asst_text, _SELF_PICK_MARKERS)
    imp_hits = _any_match(user_text, _IMPERATIVE_USER_PATTERNS)

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # §2.5 branch: enumeration + self-pick + imperative.
    if enum_hits and pick_hits and imp_hits:
        _log(
            {
                "ts": ts,
                "session_id": sid,
                "tool": tool_name,
                "branch": "2.5",
                "enum_hits": enum_hits[:3],
                "pick_hits": pick_hits[:3],
                "imperative_hits": imp_hits[:3],
                "user_prompt_excerpt": user_text[:200],
                "mode": "strict" if _STRICT else "audit",
            }
        )
        record_fire("feedback_no_user_pick_from_self_options", "block" if _STRICT else "warn", context=f"{enum_hits[0]}|{pick_hits[0]}"[:200])
        sys.stderr.write(
            "[block_askuser_after_self_pick] §2.5 AskUserQuestion delegates "
            f"self-generated picking: enumeration ({enum_hits[0]!r}) + self-pick "
            f"({pick_hits[0]!r}) + imperative ({imp_hits[0]!r}). Per "
            "feedback_no_user_pick_from_self_options §2.5: commit to the "
            "recommended option and start executing — do not open AskUserQuestion "
            "as a confirmation step.\n"
        )
        return 2 if _STRICT else 0

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[block_askuser_after_self_pick] error: {exc}\n")
        sys.exit(0)
