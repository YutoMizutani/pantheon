#!/usr/bin/env python3
# RECIPE (非配線): 原環境の自己改善ループが育てた規範の実体。
# 採用条件 = bug 修正で RED 観測なしに修正を始める失敗を自環境で観測したこと。
# 採用手順と再校正の注意は ../README.md (docs/recipes/README.md)。
# 採用時は本ファイルを .claude/hooks/ へ、対の docs/recipes/workflows/bug-fix.js は .claude/workflows/ へ配置する。
"""block_red_first_violation — bug 修正編集に RED ファーストを enforce する PreToolUse hook.

bug-fix workflow (.claude/workflows/bug-fix.js) の決定論側の対。2 段階ルール:
  - RED 観測ゼロでの自走は禁止 (deny "no attempt")
  - RED を試行したが再現不能 → ユーザーに続行可否を問う (deny "unreproducible")
  - RED 観測済み (Bash の is_error / ブラウザ state 注入) → pass

判定は current turn の transcript から行う:
  * user 発話に bug 報告らしい語彙が無ければ対象外 (feature 追加 / doc 編集を巻き込まない)
  * Bash tool_result の is_error=true、または Playwright 系 MCP での state 注入
    (localStorage / classList / cookie 書き込み) を RED 観測とみなす
  * 例外マーカー (typo 等の不変編集 / user 承認済み再現不能) は assistant 本文の
    verbatim マーカーで通す
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _paths import PROJECT_DIR, RUNTIME_DIR  # noqa: E402
except Exception:
    import os

    PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    RUNTIME_DIR = PROJECT_DIR / ".claude" / "runtime"
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # never let telemetry import break a hook
    def record_fire(*_a, **_k) -> None:  # type: ignore
        return None

_CODE_EXTS: frozenset[str] = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".sh", ".bash", ".zsh", ".rb", ".go", ".rs", ".swift",
    ".kt", ".java", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".vue", ".svelte",
})

_MARKER_TYPO_RE = re.compile(r"#\s*TDD-RED-OK:\s*(\S[^\n]*)")
_MARKER_USER_OK_RE = re.compile(r"#\s*RED-UNREPRODUCIBLE-USER-OK:\s*(\S[^\n]*)")

_BUG_INDICATORS: tuple[str, ...] = (
    "バグ", "不具合",
    "動かない", "動いてない", "動作しない", "効かない",
    "壊れて", "壊れた", "おかしい",
    "直して", "なおして",
    "失敗してる", "失敗している",
    "止まってる", "止まっている", "止まった",
    "落ちる", "落ちた",
    "エラーが", "エラーを", "エラー出",
    "bug", "broken", "doesn't work", "does not work",
    "not working", "failing", "fix this", "please fix",
)

_AUDIT_LOG = RUNTIME_DIR / "block_red_first_audit.log"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _emit_decision(decision: str, reason: str) -> None:
    obj = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _is_code_file(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in _CODE_EXTS


def _is_test_file(file_path: str) -> bool:
    p = file_path.replace("\\", "/")
    name = Path(p).name
    stem = Path(p).stem
    if any(seg in p for seg in ("/tests/", "/test/", "/spec/", "/__tests__/")):
        return True
    if name.startswith("test_"):
        return True
    if stem.endswith("_test"):
        return True
    if ".test." in name or ".spec." in name:
        return True
    return False


def _is_in_scope(file_path: str) -> bool:
    if not file_path:
        return False
    try:
        Path(file_path).resolve().relative_to(PROJECT_DIR.resolve())
    except ValueError:
        return False
    return True


def _looks_like_bugfix_request(user_text: str) -> bool:
    if not user_text:
        return False
    lower = user_text.lower()
    return any(kw.lower() in lower for kw in _BUG_INDICATORS)


def _extract_user_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                texts.append(str(c.get("text") or ""))
        return "\n".join(texts)
    return ""


def _scan_turn(transcript_path: str) -> dict:
    default = {
        "user_text": "",
        "bash_count": 0,
        "bash_error": False,
        "marker_typo": None,
        "marker_user_ok": None,
        "playwright_state_mutation": False,
    }
    if not transcript_path:
        return default
    p = Path(transcript_path)
    if not p.exists():
        return default

    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return default

    user_text = ""
    user_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
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
        if msg.get("role") != "user":
            continue
        text = _extract_user_text(msg.get("content"))
        if text.strip():
            user_text = text
            user_idx = i
            break

    bash_tool_use_ids: set[str] = set()
    error_tool_use_ids: set[str] = set()
    assistant_texts: list[str] = []
    playwright_state_mutation = False

    start = user_idx + 1 if user_idx >= 0 else 0
    for i in range(start, len(lines)):
        line = lines[i].strip()
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

        if role == "assistant" and isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                ctype = c.get("type")
                if ctype == "text":
                    assistant_texts.append(str(c.get("text") or ""))
                elif ctype == "tool_use" and c.get("name") == "Bash":
                    tu_id = c.get("id")
                    if isinstance(tu_id, str) and tu_id:
                        bash_tool_use_ids.add(tu_id)
                elif ctype == "tool_use" and str(c.get("name") or "").startswith("mcp__playwright"):
                    name = str(c.get("name") or "")
                    inp = c.get("input") or {}
                    if isinstance(inp, dict):
                        # state 注入 = browser_evaluate / browser_run_code_unsafe 経由の
                        # localStorage.setItem / classList 操作 / data-属性 / cookie 書き込み。
                        # 「user の実 state を再現して RED を観測した」シグナルとして扱う。
                        if name.endswith("browser_evaluate") or name.endswith("browser_run_code_unsafe"):
                            code = str(inp.get("function") or inp.get("code") or inp.get("expression") or "")
                            if re.search(r"localStorage\s*\.\s*setItem|sessionStorage\s*\.\s*setItem|classList\s*\.\s*(?:add|toggle|remove)|setAttribute\s*\(\s*['\"]data-|document\.cookie\s*=", code):
                                playwright_state_mutation = True
        elif role == "user" and isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_result" and c.get("is_error") is True:
                    tu_id = c.get("tool_use_id")
                    if isinstance(tu_id, str) and tu_id:
                        error_tool_use_ids.add(tu_id)

    joined_assistant = "\n".join(assistant_texts)
    marker_typo_m = _MARKER_TYPO_RE.search(joined_assistant)
    marker_user_ok_m = _MARKER_USER_OK_RE.search(joined_assistant)

    return {
        "user_text": user_text,
        "bash_count": len(bash_tool_use_ids),
        "bash_error": bool(bash_tool_use_ids & error_tool_use_ids),
        "marker_typo": marker_typo_m.group(1).strip() if marker_typo_m else None,
        "marker_user_ok": marker_user_ok_m.group(1).strip() if marker_user_ok_m else None,
        "playwright_state_mutation": playwright_state_mutation,
    }


def _log(sid, outcome, tool, file_path, marker_reason):
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": sid,
            "outcome": outcome,
            "tool": tool,
            "file_path": file_path,
            "marker_reason": marker_reason,
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    data = _read_payload()
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    sid = data.get("session_id") or ""

    if tool not in ("Edit", "Write", "MultiEdit"):
        return 0

    file_path = ""
    if isinstance(tool_input, dict):
        file_path = str(tool_input.get("file_path") or "")
    if not file_path:
        return 0

    if not _is_in_scope(file_path):
        return 0
    if not _is_code_file(file_path):
        return 0
    if _is_test_file(file_path):
        return 0

    if tool == "Write" and not Path(file_path).exists():
        return 0

    transcript_path = data.get("transcript_path") or ""
    signals = _scan_turn(transcript_path)

    if not _looks_like_bugfix_request(signals["user_text"]):
        _log(sid, "pass_not_bugfix", tool, file_path, None)
        return 0

    if signals["marker_typo"]:
        _log(sid, "pass_marker_typo", tool, file_path, signals["marker_typo"])
        return 0
    if signals["marker_user_ok"]:
        _log(sid, "pass_marker_user_ok", tool, file_path, signals["marker_user_ok"])
        return 0
    if signals["bash_error"]:
        _log(sid, "pass_red_observed", tool, file_path, None)
        return 0
    if signals["playwright_state_mutation"]:
        _log(sid, "pass_red_observed_via_playwright_state", tool, file_path, None)
        return 0

    if signals["bash_count"] == 0:
        _log(sid, "deny_no_attempt", tool, file_path, None)
        record_fire(
            "red_first_before_bugfix",
            "block",
            context=f"no_attempt|{file_path}"[:200],
        )
        _emit_decision(
            "deny",
            (
                "RED 観測ゼロでの bug 修正自走は禁止 (RED ファースト原則 — "
                ".claude/workflows/bug-fix.js と同じ規約の決定論側).\n"
                f"対象: {file_path}\n\n"
                "次の順で再試行:\n"
                "  1) 不具合を再現する Bash コマンド or 既存テストを実行し、FAIL を観測\n"
                "  2) 観測した RED の出力 (失敗 output / exit code / 壊れている位置) を\n"
                "     1 段落でユーザーに共有\n"
                "  3) その後に本編集へ戻る\n\n"
                "例外 (typo / コメント / 振る舞いに影響しない編集):\n"
                "  直前 assistant メッセージに `# TDD-RED-OK: <1 行根拠>` を含めて再試行"
            ),
        )
        return 0

    _log(sid, "deny_unreproducible", tool, file_path, None)
    record_fire(
        "red_first_before_bugfix",
        "block",
        context=f"unreproducible|{file_path}"[:200],
    )
    _emit_decision(
        "deny",
        (
            "RED 試行したが再現できていない. ユーザーに続行可否を確認してから再試行.\n"
            f"対象: {file_path}\n\n"
            "次の手順:\n"
            "  1) 試した再現手順と「再現できなかった」事実・範囲を 1 段落で共有\n"
            "  2) 「再現不能だが続行してよいか」を 1 度だけ確認\n"
            "  3) ユーザー承認後、assistant メッセージに\n"
            "     `# RED-UNREPRODUCIBLE-USER-OK: <承認根拠>` を含めて本編集を再試行"
        ),
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[block_red_first_violation] error: {exc}\n")
        sys.exit(0)
