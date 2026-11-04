#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — warn when a command uses GNU sed's
`0,/re/` line-address range, which BSD/macOS sed does NOT support.

Background (see feedback_batch_inplace_edit_macos_perl_not_sed.md):
  `sed '0,/re/s/.../.../' file` means "from the start of the file up to the
  first match of /re/" — GNU sed's idiom for replacing only the FIRST match.
  BSD/macOS sed rejects the `0` start address (it requires a 1-based line
  number) and on `-i ''` in-place edits this commonly results in a SILENT
  NO-OP: nothing is replaced, no error code that the surrounding script
  notices. In a batch in-place edit this hides until a downstream verify
  catches it, costing round-trips.

  Observed 2026-06-08 session e194e5c2 (til findability): a batch script with
  `sed -i '' '0,/^# /s/^# /## /' "$f"` silently failed on 13 files; the cause
  was found only after a session resume, and the fix was `perl -i`.

Replacement that works on macOS:
  perl -i -pe 'if(!$d && /re/){s/re/repl/; $d=1}' file
  (or use the Edit/Write tool for a small, known set of files).

Contract (NON-BLOCKING, fail-quiet):
  - NEVER denies or blocks. Emits a stderr reminder (exit 0) only.
  - Fires only when the command invokes `sed` AND contains a `0,/.../`
    address range (the GNU-only first-match idiom). Matches `0,/re/` and the
    alternate-delimiter form using a backslash before the delimiter char.
  - Skips when stdin is not a Bash tool_use, or `sed` / `0,` absent.
"""
import json
import re
import sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _surface import surface_stderr  # noqa: E402  stderr の注意を Claude に届く形でも出す
surface_stderr()

# GNU-only address range: a literal `0` start address followed by a regex
# address `/re/` (or `\#re#` alternate delimiter). We only need to detect the
# `0,` immediately preceding a regex-address opener.
_ZERO_RANGE = re.compile(r"\b0\s*,\s*(?:/|\\.)")
# `sed` as a command verb (start, or after a shell separator / pipe), tolerating
# leading VAR=val and an optional absolute path prefix.
_SED_VERB = re.compile(r"(?:^|[|&;\n(){}]|\bxargs\s+)\s*(?:[A-Za-z_][\w]*=\S+\s+)*(?:/\S*/)?sed\b")


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
    if not isinstance(cmd, str) or "sed" not in cmd or "0," not in cmd:
        return
    if not _SED_VERB.search(cmd):
        return
    if not _ZERO_RANGE.search(cmd):
        return

    record_fire("feedback_batch_inplace_edit_macos_perl_not_sed", "warn")
    sys.stderr.write(
        "[bsd-sed hint] `sed '0,/re/...'` is a GNU-only address range; BSD/macOS "
        "sed rejects the `0` start address and on `-i ''` typically SILENT "
        "no-ops (replaces nothing, no obvious error). In a batch in-place edit "
        "this hides until a downstream verify catches it. Use "
        "`perl -i -pe 'if(!$d && /re/){s/re/repl/;$d=1}' file` for first-match-"
        "only, or the Edit/Write tool. After any batch in-place edit, grep one "
        "file to confirm the change actually applied (staged count can mask a "
        "no-op). See feedback_batch_inplace_edit_macos_perl_not_sed.md.\n"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
