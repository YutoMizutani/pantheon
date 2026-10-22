#!/usr/bin/env python3
"""block_repo_root_artifacts — keep the llm/ repo root clean.

PreToolUse hook. Blocks Write/Edit/MultiEdit/NotebookEdit calls — and Bash
commands that redirect, copy, move, touch, or download — whose target sits
directly at <リポジトリ>/<file>, unless the basename is in the
allowlist below.

Goal: stop screenshots and ad-hoc artifacts (char_*.png, equip_*.png,
section_*.png, FireShot Capture *.png …) from accumulating at the repo
root. They belong under projects/<name>/outputs/, projects/<name>/tmp/, or
the top-level tmp/ — never the root itself.

Pass-through:
  - subdirectories of the repo root (projects/, tmp/, app/, scripts/, …)
  - basenames in ALLOWLIST or whose name starts with "<allowed>." (backup
    suffixes like .mcp.json.bak.YYYYMMDDhhmmss)
  - any path outside <リポジトリ>/
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # never let telemetry import break a hook
    def record_fire(*_a, **_k) -> None:  # type: ignore
        return None

from _paths import PROJECT_DIR  # noqa: E402
REPO_ROOT = str(PROJECT_DIR).rstrip("/")

# Files that legitimately live at the repo root. Anything else is rejected.
ALLOWLIST: frozenset[str] = frozenset(
    {
        # config / metadata
        "CLAUDE.md",
        "CLAUDE.local.md",
        "CLAUDE.local.md.example",
        "LICENSE",
        ".git",
        "PROJECTS.md",
        "README.md",
        "GEMINI.md",
        "AGENTS.md",
        "Taskfile.yml",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "main.py",
        ".gitignore",
        ".ignore",
        ".envrc",
        ".mcp.json",
        ".DS_Store",
        # Claude's own area (dir at repo root, user-approved 2026-06-11).
        # Bare-path references (`ls …/llm/heaven`) must not be denied.
        "heaven",
    }
)


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _emit_decision(reason: str) -> None:
    obj = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _is_root_artifact(path: str) -> bool:
    if not path:
        return False
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
    except Exception:
        return False
    if os.path.dirname(abs_path) != REPO_ROOT:
        return False
    name = os.path.basename(abs_path)
    # Globs aren't file creation — `ls <リポジトリ>/*.png` is fine.
    if any(c in name for c in "*?["):
        return False
    # Existing directories are navigable repo structure, not artifacts —
    # bare-path references (`ls …/llm/docs`, `find …/llm/projects`) must not
    # be denied. Generalizes the "heaven" ALLOWLIST entry, which was added
    # for exactly this false-positive class; new FILE creation at the root
    # is still blocked because a not-yet-existing path is never a directory.
    if os.path.isdir(abs_path):
        return False
    if name in ALLOWLIST:
        return False
    for ok in ALLOWLIST:
        # allow dotfile/config backups like ".mcp.json.bak.20260519024232"
        if name.startswith(ok + "."):
            return False
    return True


# Bash command tokens we care about. We don't try to parse shell — we just
# pick out anything that looks like an absolute path pointing at the repo
# root (one level deep). False positives are acceptable because the user
# can always relocate the target.
_PATH_RE = re.compile(
    r"(?:^|[\s'\"=>(){}])"
    r"(" + re.escape(REPO_ROOT) + r"/[^/\s'\";|&<>(){}]+)"
    r"(?=$|[\s'\";|&<>(){}])"
)


# --- read-only pass-through -------------------------------------------------
# The hook's goal is to stop file *creation* at the repo root (see docstring:
# redirect / copy / move / touch / download). A command whose every pipeline
# segment starts with a read-only program and that shows no write indicator
# cannot create files, so referencing a root path (grep …/TODO.md, ls …/.claude)
# must pass. 2026-06-11/12: bare-path reads were denied 3x in one session
# (grep TODO.md, find .claude, ls .claude) — same false-positive class that
# earlier forced the "heaven" ALLOWLIST entry. Generalize instead of growing
# the allowlist one basename at a time.
_READONLY_CMDS: frozenset[str] = frozenset(
    {
        "grep", "rg", "cat", "head", "tail", "ls", "find", "wc", "stat",
        "file", "du", "df", "lsof", "ps", "diff", "cmp", "shasum", "md5",
        "tree", "less", "more", "sort", "uniq", "cut", "tr", "jq", "awk",
        "sed", "echo", "printf", "basename", "dirname", "readlink",
        "realpath", "which", "type", "test", "[",
    }
)

# Write indicators that disqualify the read-only fast path. Harmless
# redirects to /dev/null and fd dups (2>&1) are stripped first.
_HARMLESS_REDIR_RE = re.compile(r"\d?>{1,2}\s*/dev/null|\d?>&\d")
_WRITE_HINT_RE = re.compile(r">|\btee\b|\bsed\s+(-[a-zA-Z]*\s+)*-i\b|-delete\b|-exec\b|-ok\b")


def _is_readonly_command(command: str) -> bool:
    """True iff every segment starts with a read-only program and no write
    indicator (redirect / tee / sed -i / find -delete/-exec) appears."""
    stripped = _HARMLESS_REDIR_RE.sub(" ", command)
    if _WRITE_HINT_RE.search(stripped):
        return False
    for seg in re.split(r"[;|&]+|\n", command):
        seg = seg.strip().lstrip("(").strip()
        if not seg:
            continue
        tokens = seg.split()
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens.pop(0)
        if not tokens:
            continue
        if os.path.basename(tokens[0]) not in _READONLY_CMDS:
            return False
    return True


# Relative write targets (`cat >> TODO.md`, `cp a TODO.md`, `tee TODO.md`).
# The payload `cwd` cannot be trusted: after a sibling call prints
# "Shell cwd was reset", the real shell sits at the repo root while the
# payload still reports the previous subdir (2026-10-15: payload
# cwd=projects/<name>, a relative `cat >> TODO.md` landed in <root>/TODO.md
# and silently appended to an unrelated gitignored file). So a bare relative
# basename that names an existing non-allowlisted root file is treated as a
# root target unless the command itself starts with `cd /abs`.
_REL_WRITE_RE = re.compile(
    r"(?:\d?>{1,2}\s*|\btee\s+(?:-a\s+)?|\b(?:cp|mv)\s+(?:-\S+\s+)*\S+\s+)"
    r"([A-Za-z0-9_.][^/\s'\";|&<>(){}]*)(?=$|[\s'\";|&<>(){}])"
)
_LEADING_ABS_CD_RE = re.compile(r"^\s*cd\s+['\"]?/")


def _relative_root_hits(command: str) -> list[str]:
    if _LEADING_ABS_CD_RE.match(command):
        return []
    hits = []
    for m in _REL_WRITE_RE.finditer(_HARMLESS_REDIR_RE.sub(" ", command)):
        cand = os.path.join(REPO_ROOT, m.group(1))
        if os.path.isfile(cand):
            hits.append(cand)
    return hits


def _bash_targets(command: str) -> list[str]:
    if not command:
        return []
    if _is_readonly_command(command):
        return []
    return [m.group(1) for m in _PATH_RE.finditer(command)] + _relative_root_hits(command)


def _extract_targets(tool: str, tool_input: dict) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    if tool in ("Write", "Edit", "MultiEdit"):
        p = tool_input.get("file_path")
        return [str(p)] if p else []
    if tool == "NotebookEdit":
        p = tool_input.get("notebook_path") or tool_input.get("file_path")
        return [str(p)] if p else []
    if tool == "Bash":
        return _bash_targets(str(tool_input.get("command") or ""))
    return []


def main() -> int:
    data = _read_payload()
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}

    if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"):
        return 0

    targets = _extract_targets(tool, tool_input)
    offenders = [p for p in targets if _is_root_artifact(p)]
    if not offenders:
        return 0

    record_fire(
        "feedback_no_repo_root_artifacts",
        "block",
        count=len(offenders),
        context=",".join(os.path.basename(p) for p in offenders)[:200],
    )
    bullets = "\n".join(f"  - {os.path.basename(p)}  ({p})" for p in offenders)
    _emit_decision(
        "llm リポジトリ直下へのファイル生成を拒否しました。\n"
        f"{bullets}\n"
        "対処:\n"
        "  - 相対パスで書いたつもりがルート直下の同名ファイルに当たっている場合 → "
        "書き込み先を絶対パスで書き直す（shell の cwd はルートに戻っていることがある）\n"
        "  - スクリーンショット/中間成果物 → projects/<name>/outputs/ または "
        "projects/<name>/tmp/\n"
        "  - リポジトリ横断の一時ファイル → " + REPO_ROOT + "/tmp/\n"
        "  - 真にルート直下が正当な設定ファイルなら "
        ".claude/hooks/block_repo_root_artifacts.py の ALLOWLIST に追記する。"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never let the hook itself break a tool call
        sys.stderr.write(f"[block_repo_root_artifacts] error: {exc}\n")
        sys.exit(0)
