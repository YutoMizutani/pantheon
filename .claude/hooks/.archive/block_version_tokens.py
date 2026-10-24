#!/usr/bin/env python3
"""block_version_tokens — deterministic v1-declaration guard (Hook 3).

Memory rule: ``feedback_no_v1_declaration_before_git_remote.md``.

Intercept PreToolUse Edit/Write calls. If the target file path lives under
spec / docs / src trees of a project AND the content (``content`` field
for Write, ``new_string`` field for Edit) contains a "stability declaration"
token AND the current cwd's git repo has no remote configured, deny the
operation with a clear remediation message.

Detection tokens (case-sensitive, kept exact so we don't false-positive on
e.g. "v1.0 release notes" appearing in unrelated content — but the memory
rule says these specific strings should not be written under any of these
paths until the repo has a remote, so the check is intentionally strict).

This hook only fires when the project setting wires it into PreToolUse
with matcher ``Edit|Write``. Other tool calls pass through unchanged.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Path globs that this hook polices. Matched against the absolute target path.
_GUARDED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/projects/[^/]+/spec(?:/|$)"),
    re.compile(r"/projects/[^/]+/docs(?:/|$)"),
    re.compile(r"/projects/[^/]+/src(?:/|$)"),
    re.compile(r"/apps/[^/]+/spec(?:/|$)"),
)

# Token patterns. Each is a compiled regex. Case-sensitive by design.
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bv1\.0\b"),
    re.compile(r"\bv0\.1\b"),
    re.compile(r"version 1\.0"),
    re.compile(r"Status:\s*Stable"),
    re.compile(r"Status:\s*Frozen"),
    re.compile(r"Core freeze"),
    re.compile(r"RFC \d{4}-\d{2}-\d{2}"),
    re.compile(r"を移管した"),
    re.compile(r"フリーズ完了"),
    re.compile(r"仕様凍結"),
)


sys.path.insert(0, str(Path(__file__).parent))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # never let telemetry import break a hook
    def record_fire(*_a, **_k) -> None:  # type: ignore
        return None


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _emit_decision(decision: str, reason: str) -> None:
    """Emit a PreToolUse permissionDecision response."""
    obj = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _path_is_guarded(file_path: str) -> bool:
    if not file_path:
        return False
    norm = file_path.replace("\\", "/")
    return any(p.search(norm) for p in _GUARDED_PATTERNS)


def _find_token_hit(text: str) -> str | None:
    if not text:
        return None
    for pat in _TOKEN_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def _git_remote_missing(cwd: str) -> bool:
    """Return True iff `git remote -v` in `cwd` yields no remotes.

    Considers all error cases (not a git repo, fatal output, command missing)
    as "no remote" since the spirit of the rule is "don't declare v1 in a
    project that has no upstream to publish to".
    """
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return True
    if result.returncode != 0:
        return True
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if "fatal" in err.lower():
        return True
    return not out


def _extract_text_for_check(tool: str, tool_input: dict) -> str:
    """Pick the field that carries the actual content being written."""
    if not isinstance(tool_input, dict):
        return ""
    if tool == "Write":
        return str(tool_input.get("content") or "")
    if tool == "Edit":
        # MultiEdit-style payloads carry a list of {old_string,new_string};
        # we concatenate every new_string. Plain Edit just has new_string.
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            return "\n".join(
                str(e.get("new_string") or "")
                for e in edits
                if isinstance(e, dict)
            )
        return str(tool_input.get("new_string") or "")
    return ""


def main() -> int:
    data = _read_payload()
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()

    if tool not in ("Edit", "Write"):
        return 0  # not our concern

    file_path = ""
    if isinstance(tool_input, dict):
        file_path = str(tool_input.get("file_path") or "")
    if not _path_is_guarded(file_path):
        return 0

    text = _extract_text_for_check(tool, tool_input)
    token = _find_token_hit(text)
    if token is None:
        return 0

    if not _git_remote_missing(cwd):
        # Remote IS configured — declaration is OK.
        return 0

    record_fire(
        "feedback_no_v1_declaration_before_git_remote",
        "block",
        context=f"{token}|{file_path}"[:200],
    )
    _emit_decision(
        "deny",
        (
            "git remote が未設定のため、バージョン/凍結トークンの記入を拒否しました。\n"
            f"  - 検出トークン: {token!r}\n"
            f"  - 対象パス: {file_path}\n"
            "対処:\n"
            "  1) `git remote add origin <url>` で upstream を設定する\n"
            "  2) 上記が済んでから本書き込みを再試行する\n"
            "ルート由来: memory feedback_no_v1_declaration_before_git_remote.md"
        ),
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[block_version_tokens] error: {exc}\n")
        sys.exit(0)
