#!/usr/bin/env python3
"""PreToolUse hook (matcher: Read) — warn when a Read `offset` overshoots the
target file's actual length, which always empty-returns and forces a retry.

Background (see feedback_no_guessed_offset_on_injected_file.md):
  When editing a file whose content is already in context via the project's
  system-reminder injection (MEMORY.md / CLAUDE.md), Claude tends to (a) Edit
  before having Read it via the tool this session -> harness denies with
  "File has not been read yet", then (b) Read with an `offset` GUESSED from the
  remembered (often stale) line count. The injection is a past snapshot, so the
  live file is frequently shorter; the guessed offset overshoots and the Read
  warns `the file exists but is shorter than the provided offset (N)`, wasting a
  second round-trip. Observed 2026-06-06 session 58aacbd7: Edit MEMORY.md denied,
  then Read offset=175 against a 127-line file (overshoot), then offset=118 OK.

Contract (NON-BLOCKING, fail-quiet):
  - NEVER denies or blocks. Emits a stderr reminder (exit 0) only.
  - Fires ONLY when the Read carries a numeric `offset` strictly greater than the
    target file's measured line count (a deterministic overshoot — no judgement,
    zero false positives: that exact call WILL warn/empty-return).
  - Bails silently on any uncertainty: no offset, offset<=0, unreadable/missing
    file, non-Read tool, binary/oversized file (skip the line count rather than
    risk a slow or wrong reminder).

The hook cannot detect "offset was guessed vs measured" (judgement); it pins on
the one observable, always-wrong consequence: offset > file length.
"""
import json
import os.path
import sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _surface import surface_stderr  # noqa: E402  stderr の注意を Claude に届く形でも出す
surface_stderr()
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return

# Don't try to count lines on absurdly large files (cheap guard: byte cap).
MAX_BYTES = 16 * 1024 * 1024  # 16 MB


def _line_count(path: Path):
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_BYTES:
            return None
        n = 0
        with path.open("rb") as f:
            for _ in f:
                n += 1
        return n
    except (OSError, ValueError):
        return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Read":
        return
    inp = data.get("tool_input") or {}
    offset = inp.get("offset")
    fp = inp.get("file_path")
    if not isinstance(offset, int) or offset <= 0 or not isinstance(fp, str) or not fp:
        return

    n = _line_count(Path(fp))
    if n is None:
        return
    if offset <= n:
        return  # offset is within bounds — fine

    sys.stderr.write(
        f"[read-offset hint] offset={offset} exceeds the file's actual length "
        f"({n} lines) for {fp}; this Read will empty-return and force a retry. "
        "If you only saw this file via the project context injection (MEMORY.md / "
        "CLAUDE.md), that snapshot can be stale — Read it once WITHOUT an offset "
        "(or `wc -l` first) before guessing line numbers. See "
        "feedback_no_guessed_offset_on_injected_file.md.\n"
    )
    record_fire("feedback_no_guessed_offset_on_injected_file", "warn")
    sys.exit(0)


if __name__ == "__main__":
    main()
