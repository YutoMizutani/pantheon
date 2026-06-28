#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — DENY a COMPOUND command whose recursive `rm`
targets a scratch `tmp/` path, steering it to the SINGLE, STANDALONE, ABSOLUTE-path
form that allow_tmp_rm.py auto-approves (so scratch deletion never stalls at the
auto-mode permission gate, and the user is never asked to approve a tmp wipe).

Root cause this prevents (recurrent — measured across dozens of sessions; a prior
session even tallied "tmp rm gate/denied" recurrence):
  Claude reflexively bundles a scratch reset into ONE Bash call, e.g.
      cd "$CLAUDE_PROJECT_DIR"
      ROOT="projects/llm/tmp/h2h-duration"
      rm -rf "$ROOT"; mkdir -p "$ROOT"/{A1,A2}; cat > "$ROOT/TASK.md" <<'EOF' ...
  allow_tmp_rm.py auto-approves a tmp `rm` ONLY when it is a SINGLE simple `rm`
  with ABSOLUTE operands (it bails on ANY shell operator / variable / heredoc —
  by design: a compound-command APPROVAL parser was prototyped and reverted
  2026-06-17 because variable/quote/cwd handling is an attack surface, and an
  APPROVAL false-positive = an unauthorised deletion). So a compound tmp `rm -rf`
  falls through to auto-mode, which prompts on `rm -rf`, and Claude historically
  STOPPED and asked the user — a role-reversal the user explicitly rejected.

Why a DENY here is safe where an ALLOW would not be (the asymmetry):
  This hook never APPROVES anything; it only DENIES, telling Claude to re-issue
  the deletion as a standalone absolute `rm` (which allow_tmp_rm then approves).
  A false-positive here = at worst one extra "split the command" round-trip,
  never an unauthorised deletion. Because the cost of a wrong guess is mild, we
  can use a heuristic (substring + variable-assignment tracking) that an APPROVAL
  hook could not safely use. This generalises block_recursive_rm_unrecoverable.py's
  philosophy ("a recursive rm bundled in a compound script is the dangerous form")
  from the unrecoverable projects/ tree to scratch tmp/, with a soft, escape-
  hatched deny instead of a hard block.

Contract:
  - Fires ONLY for a COMPOUND command (contains a shell operator / newline / heredoc
    / substitution). A single standalone `rm` is left to allow_tmp_rm.py.
  - Fires ONLY when a recursive `rm` (-r/-R/--recursive) in that command targets a
    scratch path: a literal operand containing a `tmp` path component or starting
    with /tmp, OR a `$VAR`/`${VAR}` operand whose in-command assignment value
    contains a `tmp` path component.
  - Does NOT fire for non-tmp compound `rm` (those are the normal permission flow's
    or block_recursive_rm_unrecoverable.py's concern — never weakens that guard).
  - Escape hatch (verbatim, mirrors `# RM-PROJECTS-OK:` / `# TDD-RED-OK:`): include
    `# RM-COMPOUND-OK: <reason>` to keep an intentionally-bundled rm and fall through
    to the normal permission flow.
  - FAIL-SAFE: on any parse/error, emit nothing → normal permission flow. The hook
    can only ever upgrade a clearly-scratch compound rm to "deny+rewrite"; it never
    crashes the flow and never touches non-Bash tools or non-rm commands.

This hook matches Bash tool_input *commands*; it cannot see assistant *text*, so it
structurally cannot block the legitimate stop-outputs of the top-level safety guards
(苛立ち停止「止まります。指示ください」/ gate 拒否後の停止 / 役割逆転回避) — those are
Stop-surface text, a different hook event. (guard-conflict check, per
feedback_new_gate_must_not_block_existing_safety_guard.)
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

try:
    from _paths import PANTHEON_ROOT  # noqa: E402  env-derived resolved repo root (no hardcoded home)
except Exception:
    PANTHEON_ROOT = ""

ACK_MARKER = "# RM-COMPOUND-OK:"
# Any of these makes the command "compound" (allow_tmp_rm bails on the same set).
_OPS = ("\n", ";", "&&", "||", "|", "`", "$(", ">", "<", "&", "\\")
_RECURSIVE_LONG = {"--recursive", "--recursive=true"}
_VAR_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_VAR_REF = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")


def _emit_deny(reason: str) -> None:
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _looks_scratch(text: str) -> bool:
    """True if a path string points at a scratch tmp tree: a `tmp` path component
    or a system /tmp path. Component-wise so `tmpfoo`/`x.tmp` do not match."""
    if not text:
        return False
    if text.startswith("/tmp/") or text == "/tmp":
        return True
    # strip quotes/globs to a directory-ish base, then check parts component-wise
    base = text.strip().strip('"').strip("'")
    if any(c in base for c in "*?["):
        base = os.path.dirname(base) or base
    parts = [p for p in re.split(r"/+", base) if p]
    return "tmp" in parts


def _collect_assignments(cmd: str) -> dict:
    """Map VAR -> value for simple `VAR=value` assignments anywhere in the command
    (statement-leading; the common `ROOT="...tmp..."` idiom). Best-effort."""
    out = {}
    parts = re.split(r"(?:\n|;|&&|\|\||\|)", cmd)
    for stmt in parts:
        stmt = stmt.strip()
        # allow a single leading assignment per statement
        m = _VAR_ASSIGN.match(stmt.split()[0]) if stmt.split() else None
        if m:
            val = m.group(2).strip().strip('"').strip("'")
            out[m.group(1)] = val
    return out


def _split_statements(cmd: str):
    parts = re.split(r"(?:\n|;|&&|\|\||\|)", cmd)
    return [p.strip() for p in parts if p.strip()]


def _verb_and_args(stmt: str):
    try:
        toks = shlex.split(stmt, comments=False)
    except ValueError:
        toks = stmt.split()
    i = 0
    while i < len(toks) and _VAR_ASSIGN.match(toks[i]):
        i += 1
    while i < len(toks) and os.path.basename(toks[i]) in {
            "sudo", "time", "nice", "nohup", "command", "builtin", "exec"}:
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
        if a.startswith("-") and not a.startswith("--") and ("r" in a[1:] or "R" in a[1:]):
            return True
    return False


def _operands(args):
    paths, after = [], False
    for a in args:
        if after:
            paths.append(a)
            continue
        if a == "--":
            after = True
            continue
        if a.startswith("-"):
            continue
        paths.append(a)
    return paths


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
    if ACK_MARKER in cmd:
        return
    # Only COMPOUND commands are our concern — a standalone rm is allow_tmp_rm's.
    if not any(tok in cmd for tok in _OPS):
        return

    assigns = _collect_assignments(cmd)
    scratch_targets = []
    for stmt in _split_statements(cmd):
        verb, args = _verb_and_args(stmt)
        if verb != "rm" or args is None or not _is_recursive(args):
            continue
        for op in _operands(args):
            if _looks_scratch(op):
                scratch_targets.append(op)
                continue
            m = _VAR_REF.match(op)
            if m and _looks_scratch(assigns.get(m.group(1), "")):
                scratch_targets.append(f"{op} (= {assigns.get(m.group(1))})")

    if not scratch_targets:
        return

    tgt = ", ".join(dict.fromkeys(scratch_targets))
    _emit_deny(
        "複合コマンド内の scratch tmp への `rm -rf` を止めました（auto-mode の承認ゲートで"
        "止まる原因 — allow_tmp_rm は単独・絶対パスの rm しか自動承認できません）。\n"
        f"対象: {tgt}\n\n"
        "fallback（user に承認を頼まず自己完結する手順）:\n"
        "  1) 削除だけを単独・絶対パスで実行 → allow_tmp_rm が自動承認:\n"
        f"       rm -rf {PANTHEON_ROOT}/<...>/tmp/<dir>\n"
        "     （repo 内 tmp も system /tmp 配下も、単独・絶対パスなら allow_tmp_rm が自動承認。"
        "変数や相対でなく絶対パスを直書きする）\n"
        "  2) mkdir / heredoc などの再構築は rm を含まない別コマンドで実行（ゲート不要）。\n\n"
        "意図的に束ねたい場合だけ、コマンドに verbatim で `# RM-COMPOUND-OK: <根拠>` を含めて再実行。"
    )
    record_fire("block_compound_scratch_rm", "deny", count=len(scratch_targets))


if __name__ == "__main__":
    main()
