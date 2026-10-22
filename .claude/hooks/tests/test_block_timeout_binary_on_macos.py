#!/usr/bin/env python3
"""Contract test for block_timeout_binary_on_macos.py.

The hook emits a NON-BLOCKING stderr hint (per memory
feedback_no_timeout_binary_on_macos — it NEVER denies/blocks, exits 0) when a
command calls the GNU `timeout`/`gtimeout` binary in a COMMAND-VERB position
(this macOS/zsh has no such binary -> exit 127, and in a parallel batch that 127
cascade-cancels every sibling). 'Fire' = the stderr hint appeared. It must:
  - fire on a bare `timeout`/`gtimeout` verb (first token, after a separator,
    after a `VAR=val` assignment, after a newline, inside a subshell);
  - NEVER fire on an absolute-path invocation (future coreutils install),
    a flag/value/path/operand/substring occurrence of the word "timeout",
    or a command that merely mentions it.
Includes the guard-conflict check ([[feedback_new_gate_must_not_block_existing_safety_guard]]):
a command echoing a top-level safety guard's legitimate stop-output ("止まります。
指示ください") must NOT fire — the hook only inspects Bash command verbs, and the
guard's real output is assistant text that never reaches a PreToolUse(Bash) hook.
Run: python3 .claude/hooks/tests/test_block_timeout_binary_on_macos.py
"""
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "block_timeout_binary_on_macos.py"


def fires(command: str) -> bool:
    """The hook is NON-BLOCKING: it writes a stderr hint and exits 0 — it never
    returns a deny JSON (memory feedback_no_timeout_binary_on_macos). 'Fires' =
    the stderr hint appeared."""
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True, text=True,
    )
    return "[no-timeout hint]" in p.stderr


# (description, command, expect_fire)
CASES = [
    # ---- FIRE: bare timeout/gtimeout in a verb position ----
    ("bare timeout as first token", "timeout 30 echo hi", True),
    ("bare gtimeout as first token", "gtimeout 5 sleep 1", True),
    ("timeout after && separator", "echo a && timeout 3 foo", True),
    ("timeout after VAR=val assignment", "FOO=1 timeout 3 bar", True),
    ("timeout after newline", "echo a\\ntimeout 3 baz", True),
    ("timeout inside a subshell", "(timeout 5 foo)", True),

    # ---- NO FIRE: absolute path is allowed (future coreutils by full path) ----
    ("absolute /usr/bin/timeout", "/usr/bin/timeout 5 sleep 1", False),
    ("absolute /opt/homebrew/bin/gtimeout", "/opt/homebrew/bin/gtimeout 5 sleep 1", False),

    # ---- NO FIRE: word "timeout" as flag/value/path/operand/substring ----
    ("ConnectTimeout flag value", "ssh -o ConnectTimeout=5 host", False),
    ("timeout as a path component", "cat /var/timeout/x", False),
    ("timeout as an echo operand", "echo timeout", False),
    ("timeout as a grep literal (verb=grep)", 'grep "timeout" file.txt', False),
    ("timeout in quoted code with parens (verb=python3)", "python3 -c \"print('timeout')\"", False),

    # ---- guard-conflict: a safety-guard's legitimate stop-output echoed as text ----
    ("echoing 苛立ち停止 text with the word timeout does not fire (verb=echo)",
     'echo "止まります。指示ください # timeout"', False),
]


def main() -> int:
    failed = []
    for desc, cmd, expect in CASES:
        got = fires(cmd.replace("\\n", "\n"))
        ok = got == expect
        print(f"[{'PASS' if ok else 'FAIL'}] {desc} "
              f"(got={'FIRE' if got else 'pass'} want={'FIRE' if expect else 'pass'})")
        if not ok:
            failed.append(desc)
    print(f"\n{len(CASES) - len(failed)}/{len(CASES)} passed")
    if failed:
        for d in failed:
            print(f"  FAILED: {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
