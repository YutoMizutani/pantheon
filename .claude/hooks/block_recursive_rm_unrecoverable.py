#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — HARD-DENY a recursive `rm` whose target
resolves into the git-unrecoverable `projects/` tree, unless the command carries
a verbatim acknowledgement marker.

Root cause this prevents (see feedback_verify_move_landed_before_rm.md):
  2026-06-13, a multi-line script did `git mv projects/mac-remote-desktop ...`
  (which failed with `fatal: source directory is empty`) and then, on a later
  line, `rm -rf projects/mac-remote-desktop`. The script relied on `set -e` to
  abort after the failed move — but `set -e` is STRUCTURALLY INERT in the Bash
  tool: the harness wraps the script as `... && eval '<script>' && pwd -P ...`,
  placing the eval in a NON-FINAL position of an AND-OR list, where POSIX/zsh
  errexit is ignored for the whole eval body. So the `rm -rf` ran unconditionally
  and deleted the project. `projects/*` is `.gitignore`d (local layer, never
  committed) with no Time Machine / APFS snapshot → unrecoverable by any means.

Why a TARGET-based block (not a "move-then-rm" detector):
  The failure is mechanism-agnostic — a recursive rm can reach the unrecoverable
  tree via inert set -e, a typo, bad branching, or copy-paste. Guarding the
  *target* (any recursive rm into projects/) stops the catastrophe regardless of
  how the rm got there, and is far more robust than trying to prove an rm is
  "unguarded".

Also covered (same unrecoverable class, agreed 2026-06-13 review):
  - `git clean -f -x` (or -X) with no pathspec, or a pathspec under projects/:
    `-x` removes IGNORED files too, and `projects/*` is entirely ignored, so a
    "cleanup" git clean wipes the same tree as the rm incident — same cost, and
    easy to type by reflex. Dry-run (`-n`) is never blocked.

NOT covered (deferred per generalize-on-recurrence — see memory How-to-apply):
  `find projects/ -delete`, `rm -rf *` after a cd into projects/ (cwd is
  unknowable from the command string), and non-recursive single-file rm. These
  are noted in feedback_verify_move_landed_before_rm.md; harden only on recurrence.

Contract:
  - Fires when an actual command VERB is `rm` (bare / /bin/rm / abs path),
    a recursive flag is present (-r / -R / --recursive), AND a path operand
    resolves under ~/Developer/llm/projects/ but NOT inside a `tmp/` or
    `__pycache__` component (those are scratch/regenerable; allow_tmp_rm.py owns
    them). Also covers the projects/ root itself and the llm root itself.
  - Also fires for a dangerous `git clean` (force + ignored-removal, not dry-run)
    that endangers projects/.
  - A grep/echo/find whose ARGUMENT merely contains the text "rm -rf projects/..."
    is NOT flagged — detection is verb-based, parsed per shell statement.
  - DENY is lifted iff the command contains the verbatim marker
    `# RM-PROJECTS-OK:` (followed by a reason). That is the explicit human-
    acknowledged escape hatch, mirroring the repo's `# TDD-RED-OK:` convention.
  - FAIL-SAFE toward blocking on this specific tree: if a statement clearly
    invokes `rm` recursively and tokenization is ambiguous but a `projects/`
    operand is visible, deny. Never denies anything outside the projects/ tree.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return

LLM_ROOT = Path("/Users/ym/Developer/llm").resolve()
PROJECTS_ROOT = LLM_ROOT / "projects"
ACK_MARKER = "# RM-PROJECTS-OK:"
# Prefixes that may legitimately precede the command verb in a statement.
_SKIP_PREFIX_VERBS = {"sudo", "time", "nice", "nohup", "command", "builtin", "exec"}
_RECURSIVE_LONG = {"--recursive", "--recursive=true"}


def _emit_deny(reason: str) -> None:
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _split_statements(cmd: str):
    """Approximate shell-statement split on newlines and ; && || operators.
    Good enough to isolate an `rm` statement from neighbours; rm invocations do
    not span these separators in practice."""
    # Normalise the AND-OR / sequence operators to a single sentinel, then split.
    parts = re.split(r"(?:\n|;|&&|\|\||\|)", cmd)
    return [p.strip() for p in parts if p.strip()]


def _verb_and_args(stmt: str):
    """Return (verb_basename, arg_list) for a statement, skipping leading
    VAR=val assignments and wrapper verbs (sudo/time/...). Returns (None, None)
    if the statement does not invoke a command we can read."""
    try:
        toks = shlex.split(stmt, comments=False)
    except ValueError:
        # Unbalanced quotes etc. — fall back to a coarse whitespace split so we
        # can still recognise an rm verb (fail-safe on the protected tree).
        toks = stmt.split()
    i = 0
    # drop leading env assignments  VAR=val
    while i < len(toks) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[i]):
        i += 1
    # drop wrapper verbs
    while i < len(toks) and os.path.basename(toks[i]) in _SKIP_PREFIX_VERBS:
        i += 1
    if i >= len(toks):
        return None, None
    return os.path.basename(toks[i]), toks[i + 1:]


def _is_recursive(args):
    for a in args:
        if a == "--":
            break
        if a in _RECURSIVE_LONG:
            return True
        if a.startswith("-") and not a.startswith("--"):
            if "r" in a[1:] or "R" in a[1:]:
                return True
    return False


def _operands(args):
    paths, after_ddash = [], False
    for a in args:
        if after_ddash:
            paths.append(a)
            continue
        if a == "--":
            after_ddash = True
            continue
        if a.startswith("-"):
            continue
        paths.append(a)
    return paths


def _resolve(operand: str) -> Path:
    """Resolve an operand to an absolute path. Relative operands are taken
    relative to the canonical llm root (the Bash tool's working dir for this
    repo). Globs are reduced to their directory portion."""
    base = operand
    if any(c in operand for c in "*?["):
        base = os.path.dirname(operand) or operand
    p = Path(base)
    if not p.is_absolute():
        p = LLM_ROOT / p
    try:
        return Path(os.path.normpath(str(p)))
    except Exception:
        return p


def _is_protected(operand: str) -> bool:
    """True if a recursive rm of this operand would hit the git-unrecoverable
    projects/ tree (excluding scratch tmp/ and regenerable __pycache__)."""
    if ".." in Path(operand).parts:
        # Ambiguous traversal into the tree — treat conservatively as protected
        # only if it textually targets projects/ (avoid blocking unrelated rm).
        return "projects" in Path(operand).parts
    resolved = _resolve(operand)
    try:
        rel = resolved.relative_to(LLM_ROOT)
    except ValueError:
        # operand is the llm root itself, or outside the tree
        return resolved == LLM_ROOT
    parts = rel.parts
    if not parts:
        return True  # rm -rf <llm root>
    if parts[0] != "projects":
        return False
    # under projects/. Skip scratch / regenerable subtrees (allow_tmp_rm owns tmp).
    if "tmp" in parts or "__pycache__" in parts:
        return False
    return True


def _git_clean_hits(args):
    """Return endangered-target labels for a dangerous `git clean`, else [].
    Dangerous = force present AND ignored-removal (-x/-X) AND not dry-run, with
    either no pathspec (whole repo, projects/* included) or a pathspec resolving
    under projects/ or the repo root."""
    if "clean" not in args:
        return []
    rest = args[args.index("clean") + 1:]
    has_force = dry = ignored = False
    pathspecs, after_ddash = [], False
    for a in rest:
        if after_ddash:
            pathspecs.append(a)
            continue
        if a == "--":
            after_ddash = True
            continue
        if a.startswith("--"):
            if a == "--force":
                has_force = True
            elif a == "--dry-run":
                dry = True
            continue
        if a.startswith("-"):
            cl = a[1:]
            has_force = has_force or "f" in cl
            dry = dry or "n" in cl
            ignored = ignored or "x" in cl or "X" in cl
            continue
        pathspecs.append(a)
    if dry or not has_force or not ignored:
        return []
    if not pathspecs:
        return ["git clean -x (whole repo — wipes ignored projects/*)"]
    hits = []
    for ps in pathspecs:
        resolved = _resolve(ps)
        try:
            parts = resolved.relative_to(LLM_ROOT).parts
        except ValueError:
            if resolved == LLM_ROOT:
                hits.append("git clean " + ps)
            continue
        if not parts or parts[0] == "projects":
            hits.append("git clean " + ps)
    return hits


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(cmd, str) or not cmd.strip():
        return

    # Explicit acknowledged escape hatch — verbatim marker lifts the block.
    if ACK_MARKER in cmd:
        return

    hits = []
    for stmt in _split_statements(cmd):
        verb, args = _verb_and_args(stmt)
        if args is None:
            continue
        if verb == "rm" and _is_recursive(args):
            hits.extend(op for op in _operands(args) if _is_protected(op))
        elif verb == "git":
            hits.extend(_git_clean_hits(args))

    if not hits:
        return

    targets = ", ".join(dict.fromkeys(hits))  # de-dup, preserve order
    _emit_deny(
        "復旧不能ツリー(projects/)への破壊操作を停止しました.\n"
        f"対象: {targets}\n\n"
        "`projects/*` は .gitignore 済み(ローカル層・未 commit, snapshot 無し)で、"
        "誤った `rm -r` は git でも何でも復旧できません。"
        "Bash ツール内の `set -e` は構造的に無効(eval が AND-OR 非末尾位置)なので、"
        "move の失敗を errexit で止める前提は成立しません。\n\n"
        "進め方:\n"
        "  1) move(`mv`/`git mv`)の着地を独立ステップで検証してから消す。"
        "rm は move 成功に直結(`mv A B && rm -rf A`)するか、別コマンドに分けて間で `ls`/`find` 確認。\n"
        "  2) 本当に消すと確定したら、コマンドに verbatim で\n"
        "     `# RM-PROJECTS-OK: <消してよい根拠>` を含めて再実行(このブロックが外れる)。\n\n"
        "参照: feedback_verify_move_landed_before_rm.md"
    )
    record_fire("feedback_verify_move_landed_before_rm", "deny", count=len(hits))


if __name__ == "__main__":
    main()
