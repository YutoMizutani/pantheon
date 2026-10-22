#!/usr/bin/env python3
"""audit_dangling_script_announcement — flag tool/script creation reports that
omit either a copy-pasteable invocation one-liner or an explicit "no user
action required" statement.

Memory rule:
  - ``feedback_no_dangling_script_announcement.md`` (saying-fault, 2026-05-23)

Stop event hook. Scans the latest assistant turn (since the most recent user
message). If any "I made/added/installed a script/file/tool" phrase is
present, the same turn MUST contain either:

  1. A code block (fenced or inline) that includes an invocation like
     ``python3 /abs/path`` / ``bash /abs/path`` / ``./abs/path`` /
     ``/abs/path``, OR
  2. An explicit "user action not required" phrase such as
     「あなた側でやることは無い」「user action は不要」「何もしなくてよい」.

Failing both => stderr feedback + audit log entry. Exit 0 always (audit only;
Stop fires post-display so blocking is not possible — the value is closing
the self-recall gap with a deterministic post-turn signal).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Phrases announcing file/script/tool placement (Japanese, the user's language).
_ANNOUNCEMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"スクリプトを?(追加|作成|配置|置き|用意)し"),
    re.compile(r"ファイルを?(追加|作成|配置|置き|用意)し"),
    re.compile(r"(tool|ツール)を?(追加|作成|配置|置き|用意)し"),
    re.compile(r"hookを?(追加|作成|配置|登録)し"),
    re.compile(r"(セットアップ|設置|設定)しました"),
    re.compile(r"を?(導入|配置)しました"),
    # "残してあります / 残してあるので" — leaving the script in place is also an
    # announcement that requires next-step disambiguation.
    re.compile(r"スクリプト(本体)?は[^\n]{0,40}に残してあ"),
    re.compile(r"ファイル(本体)?は[^\n]{0,40}に残してあ"),
    re.compile(r"スクリプト(本体)?は[^\n]{0,40}に置いて"),
    re.compile(r"ファイル(本体)?は[^\n]{0,40}に置いて"),
)

# Code-block / inline-code invocation markers. Any of these in the turn text
# satisfies criterion (1): a runnable copy-pasteable invocation is present.
_INVOCATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"`python3?\s+/[^`\n]+`"),
    re.compile(r"`bash\s+/[^`\n]+`"),
    re.compile(r"`sh\s+/[^`\n]+`"),
    re.compile(r"`node\s+/[^`\n]+`"),
    re.compile(r"`/[A-Za-z0-9_./\-]+\.(py|sh|ts|js|mjs)`"),
    # Fenced code block containing an absolute-path invocation.
    re.compile(r"```[a-z]*\s*\n[^`]*?(python3?|bash|sh|node)\s+/[^\n`]+\n[^`]*?```", re.MULTILINE),
    re.compile(r"```[a-z]*\s*\n\s*/[A-Za-z0-9_./\-]+\.(py|sh|ts|js|mjs)[^\n`]*\n[^`]*?```", re.MULTILINE),
)

# Explicit "no user action required" disambiguation phrases.
_NO_ACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"あなた側でやること(は|が)?(無い|ない|ありません)"),
    re.compile(r"あなたがやること(は|が)?(無い|ない|ありません)"),
    re.compile(r"user\s*action\s*(は|が)?\s*不要"),
    re.compile(r"ユーザー(の|側の)?(操作|アクション)(は|が)?(不要|要らな|いらな)"),
    re.compile(r"(何も|なにも)\s*(しなくて|せずに)\s*(よい|良い|OK|大丈夫)"),
    re.compile(r"あなたの操作(は|が)?(不要|要らな|いらな)"),
)

__import__("sys").path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _paths import RUNTIME_DIR  # noqa: E402
_AUDIT_LOG = RUNTIME_DIR / "audit_dangling_script_announcement.log"


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


def _log_hit(sid: str, announcement_tokens: list[str], text: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "ts": ts,
            "session_id": sid,
            "category": "dangling_announcement",
            "tokens": announcement_tokens,
            "text_tail": text[-400:],
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


sys.path.insert(0, str(Path(__file__).parent))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # never let telemetry import break a hook
    def record_fire(*_a, **_k) -> None:  # type: ignore
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

    announcement_hits = _any_match(text, _ANNOUNCEMENT_PATTERNS)
    if not announcement_hits:
        return 0

    has_invocation = bool(_any_match(text, _INVOCATION_PATTERNS))
    has_no_action = bool(_any_match(text, _NO_ACTION_PATTERNS))
    if has_invocation or has_no_action:
        return 0

    record_fire(
        "feedback_no_dangling_script_announcement",
        "warn",
        count=len(announcement_hits),
        context=", ".join(sorted(set(announcement_hits))[:5])[:200],
    )
    _log_hit(sid, announcement_hits, text)
    tokens = ", ".join(sorted(set(announcement_hits))[:5])
    sys.stderr.write(
        f"[audit_dangling_script_announcement] script/file placement announced "
        f"({tokens}) without a copy-pasteable one-liner or explicit "
        "'no user action required'. Per feedback_no_dangling_script_announcement: "
        "add either a fenced code block with an absolute-path invocation, "
        "or write 'あなた側でやることは無い'.\n"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[audit_dangling_script_announcement] error: {exc}\n")
        sys.exit(0)
