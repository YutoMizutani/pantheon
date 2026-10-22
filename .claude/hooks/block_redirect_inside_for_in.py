#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — warn when a command places a redirection
operator (`2>/dev/null`, `>file`, `2>&1`, `&>`) INSIDE the iterable word list
of a `for VAR in ...; do` or `while ...; do` loop. zsh (and bash) only permit
redirects after a simple command or after the compound `done`, not inside
`for VAR in <words>` — putting one there yields `parse error near '>'` and the
loop never runs.

Background (see feedback_no_redirect_inside_for_in_word_list.md):
  Observed 2026-06-08 session cd198c05: a 3-way parallel Bash batch died because
  one sibling contained
    `for f in dir/{a,b,c}/*.jsonl 2>/dev/null; do ...; done`
  The `2>/dev/null` after the glob was an attempt to suppress zsh nomatch, but
  shell grammar rejects redirects inside the iterable word list. parse error →
  the whole parallel batch was cancelled (other siblings: `parallel tool call
  errored`). Net cost: 3 wasted slots + 2 sequential re-runs.

Contract (NON-BLOCKING, fail-quiet):
  - NEVER denies. stderr reminder + exit 0 only.
  - Fires when the command contains the token sequence `for <name> in <...>
    <REDIRECT>; do` (or `while <cond> <REDIRECT>; do`) where REDIRECT is one of
    `2>`, `>`, `>>`, `2>&1`, `&>`, `<`, and the redirect appears BEFORE the `;`
    that introduces `do`.
  - Skips commands without a `for ... in` or `while ...` followed by `; do`.
  - Skips when stdin is not a Bash tool_use.
"""
import json
import re
import sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _surface import surface_stderr  # noqa: E402  stderr の注意を Claude に届く形でも出す
surface_stderr()


# Match: `for <ident> in <stuff with a redirect op> ; do`
# We require: keyword `for` + ident + `in` ... a redirect op (>, >>, 2>, &>, 2>&1, <)
# ... then `;` then `do`. The redirect must NOT be immediately after a `done` /
# `}` (those are valid loop-output redirects).
_FOR_IN_RE = re.compile(
    r"\bfor\s+\w+\s+in\s+[^;]*?(?<![\w\d])(?:2>|&>|>>|>|<)(?!\s*&\d*\s*;\s*do)[^;]*?;\s*do\b",
    re.MULTILINE,
)
_WHILE_RE = re.compile(
    r"\bwhile\s+[^;]*?(?<![\w\d])(?:2>|&>|>>|>|<)[^;]*?;\s*do\b",
    re.MULTILINE,
)


def _has_loop_with_redirect_in_head(cmd: str) -> bool:
    return bool(_FOR_IN_RE.search(cmd) or _WHILE_RE.search(cmd))


# --- telemetry instrumentation (best-effort; never breaks the hook) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry import must never break a hook
    def record_fire(*_a, **_k):  # type: ignore
        return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(cmd, str) or not cmd:
        return
    if "for " not in cmd and "while " not in cmd:
        return
    if not _has_loop_with_redirect_in_head(cmd):
        return

    record_fire("feedback_no_redirect_inside_for_in_word_list", "warn")
    sys.stderr.write(
        "[zsh-loop-redirect hint] Redirect operator (2>/dev/null, >file, ...) "
        "appears INSIDE the iterable word list of a `for ... in ...; do` or "
        "`while ...; do` loop. shell grammar only permits redirects after a "
        "simple command or after the compound `done` — putting one in the head "
        "yields `parse error near '>'` and the loop never runs. To suppress zsh "
        "glob nomatch, use the `(N)` qualifier (`*.jsonl(N)`) or `setopt -s "
        "NULL_GLOB`, or enumerate with `find ... -name '<glob>' -print0`. To "
        "redirect the whole loop's stderr, wrap it: `{ for ...; do ...; done; } "
        "2>/dev/null`. See feedback_no_redirect_inside_for_in_word_list.md.\n"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
