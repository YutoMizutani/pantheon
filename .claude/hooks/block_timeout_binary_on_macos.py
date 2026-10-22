#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — warn when a command uses the GNU `timeout`
(or `gtimeout`) binary, which is NOT installed on this macOS / zsh environment.

Background (see feedback_no_timeout_binary_on_macos.md):
  macOS ships no coreutils `timeout`, and `gtimeout` is not installed here either.
  A `timeout N <cmd>` invocation returns `zsh: command not found: timeout` =>
  exit 127, and the wrapped command (e.g. a remote-run wrapper over ssh) never runs.
  Worse: when that call rides in a PARALLEL batch with read-only diagnostics, the
  127 failure cancels the WHOLE batch (`Cancelled: parallel tool call ... errored`),
  discarding every sibling's result. Observed 2026-06-01 session 681a110c: a
  `timeout`-wrapped remote-run call exit-127'd and cascade-cancelled 6 sibling
  diagnostic calls, then spun ~10 noop calls before the cause was found.

Contract (BLOCKING since 2026-10-20, fail-quiet):
  - Denies (exit 2) a verb-position `timeout`/`gtimeout`: it always fails here. Was warn-only (exit 0),
    which never reached Claude.
  - Fires ONLY when a bare `timeout` or `gtimeout` appears in a COMMAND-VERB
    position: the first token of the whole command, or the first token right
    after a shell separator (`&&`, `||`, `;`, `|`, newline). Leading `VAR=val`
    assignments are skipped so `FOO=1 timeout ...` still fires.
  - Skips an absolute-path invocation (`/usr/bin/timeout`, `/opt/.../gtimeout`):
    if coreutils is later installed and called by full path, that's legitimate.
  - Skips occurrences of the word "timeout" as a flag/value/path/substring
    (e.g. `--timeout=5`, `ConnectTimeout`, `/var/timeout/x`, `nc -G ... # timeout`)
    by only inspecting verb positions, never operands.
  - Skips when stdin is not a Bash tool_use.

Suggested replacements surfaced in the hint:
  - ssh:        ssh -o ConnectTimeout=<sec> ...
  - port probe: nc -z -G <sec> host port   (or nc -w <sec> ...)
  - arbitrary:  perl -e 'alarm shift; exec @ARGV' <sec> <cmd...>
"""
import json
import os.path
import re
import sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _surface import surface_stderr  # noqa: E402  stderr の注意を Claude に届く形でも出す
surface_stderr()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return

# Shell tokens that introduce a new command-verb position immediately after them.
_SEP = re.compile(r"(?:\|\||&&|;|\||\n|\(|\{)")


def _verb_positions(cmd: str):
    """Yield the first shell word of the whole command and of each segment that
    follows a separator. Returns raw word strings (may include trailing noise we
    re-tokenize). We split on separators textually (not full shell parsing) which
    is safe here because we only need the *verb* of each segment."""
    segments = _SEP.split(cmd)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # Drop leading `VAR=val` env-assignment words to reach the verb.
        words = seg.split()
        i = 0
        while i < len(words):
            w = words[i]
            # env assignment: NAME=value, NAME not starting with '-' and no slash in name
            if "=" in w and not w.startswith("-") and "/" not in w.split("=", 1)[0]:
                i += 1
                continue
            break
        if i < len(words):
            yield words[i]


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(cmd, str) or "timeout" not in cmd:
        return

    for verb in _verb_positions(cmd):
        # Absolute-path invocation is allowed (future coreutils install by full path).
        if verb in ("timeout", "gtimeout"):
            sys.stderr.write(
                "[no-timeout hint] This macOS/zsh has no GNU `timeout`/`gtimeout` "
                "binary; `timeout N <cmd>` returns `command not found` (exit 127) "
                "and NEVER runs <cmd>. In a parallel batch that 127 also CANCELS "
                "every sibling call. Drop `timeout` and move the time limit "
                "inside the command instead: ssh -o ConnectTimeout=<sec> ... / "
                "nc -z -G <sec> host port / "
                "perl -e 'alarm shift; exec @ARGV' <sec> <cmd...>. "
                "See feedback_no_timeout_binary_on_macos.md.\n"
            )
            record_fire("feedback_no_timeout_binary_on_macos", "deny", context=verb)
            # 2026-10-20: 注意（exit 0）は Claude に届かず、必ず失敗する呼び出しがそのまま走っていた。
            # 必ず exit 127 になるコマンドなので、実行前に止める（exit 2 の stderr は Claude に届く）。
            sys.exit(2)


if __name__ == "__main__":
    main()
