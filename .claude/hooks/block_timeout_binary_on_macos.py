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
  - Quotes ('…' / "…"), comments and heredoc bodies are masked before the
    separators are found, with the same masks as block_unquoted_equals_word.
    Without it, `grep -E "a|timeout|b"` split at the quoted `|` and read the
    quoted `timeout` as a verb. Known gap: a
    `$(timeout ...)` inside double quotes is masked too and not caught (0 such
    commands in 30 days of replayed transcripts).
  - Skips an absolute-path invocation (`/usr/bin/timeout`, `/opt/.../gtimeout`):
    if coreutils is later installed and called by full path, that's legitimate.
  - Skips occurrences of the word "timeout" as a flag/value/path/substring
    (e.g. `--timeout=5`, `ConnectTimeout`, `/var/timeout/x`, `nc -G ... # timeout`)
    by only inspecting verb positions, never operands.
  - Skips when stdin is not a Bash tool_use.

Transform (opt-in with `--transform` in argv):
  - `timeout [--] <N>[smhd] <cmd...>` without other options is rewritten to
    `perl -e 'alarm shift; exec @ARGV; warn ...; exit 127' <seconds> <cmd...>` (m/h/d converted to
    seconds, fractions rounded up) and returned as a bare `updatedInput`
    (no permissionDecision — bare updatedInput is applied and does not bypass
    the auto-mode classifier, as measured). On time-out the exit is
    142 (SIGALRM), not GNU timeout's 124; a missing command exits 127 with
    `exec <cmd>: No such file or directory` (bare `exec @ARGV` exits 0 silently).
  - Any option (`-k`, `-s`, `--foreground`, `--preserve-status`, ...), a
    duration that is not a plain number, a quoted duration, or no command after
    it keeps the deny for the whole command.

Suggested replacements surfaced in the hint:
  - ssh:        ssh -o ConnectTimeout=<sec> ...
  - port probe: nc -z -G <sec> host port   (or nc -w <sec> ...)
  - arbitrary:  perl -e 'alarm shift; exec @ARGV; warn "exec $ARGV[0]: $!\n"; exit 127' <sec> <cmd...>
"""
import json
import math
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
try:
    from block_unquoted_equals_word import _mask_heredocs, _mask_quotes_and_comments  # noqa: E402
except Exception:  # masking is best-effort; fall back to the unmasked text
    def _mask_heredocs(cmd):  # type: ignore
        return cmd

    def _mask_quotes_and_comments(cmd):  # type: ignore
        return cmd

# Shell tokens that introduce a new command-verb position immediately after them.
_SEP = re.compile(r"(?:\|\||&&|;|\||\n|\(|\{)")
_WORD = re.compile(r"\S+")
_DURATION = re.compile(r"(\d+(?:\.\d+)?)([smhd]?)")
_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# exec の失敗（命令が無い）は、そのままだと何も出さず exit 0 になる。GNU timeout と同じ 127 にする。
_PERL_ALARM = "perl -e 'alarm shift; exec @ARGV; warn \"exec $ARGV[0]: $!\\n\"; exit 127'"


def _mask(cmd: str) -> str:
    # Heredocs first: their opener may carry a quoted delimiter (<<'EOF') that
    # quote-masking would erase. Both masks keep the length, so offsets match.
    masked = _mask_quotes_and_comments(_mask_heredocs(cmd))
    return masked if len(masked) == len(cmd) else cmd


def _glued(cmd: str, start: int, end: int) -> bool:
    """True when a quote or backslash touches the word, e.g. `timeout"x"` or
    `5"s"`: the shell word is longer than what survived the mask."""
    return (start > 0 and cmd[start - 1] in "'\"\\") or (end < len(cmd) and cmd[end] in "'\"\\")


def _verb_spans(cmd: str):
    """Yield (verb, start, end, words, seg_end) for the first shell word of the
    whole command and of each segment that follows a separator. Separators are
    found on the masked text, so a quoted `|` or `;` does not start a segment;
    offsets point into the original string. `words` is the rest of the segment
    as (text, start, end); a word glued to a quote is yielded with a trailing
    quote so it never equals `timeout`, `--` or a number."""
    masked = _mask(cmd)
    bounds = [0] + [p for m in _SEP.finditer(masked) for p in (m.start(), m.end())] + [len(masked)]
    for seg_start, seg_end in zip(bounds[::2], bounds[1::2]):
        words = [(m.group() + ('"' if _glued(cmd, seg_start + m.start(), seg_start + m.end()) else ""),
                  seg_start + m.start(), seg_start + m.end())
                 for m in _WORD.finditer(masked[seg_start:seg_end])]
        i = 0
        # Drop leading `VAR=val` env-assignment words to reach the verb.
        while i < len(words):
            w = words[i][0]
            # env assignment: NAME=value, NAME not starting with '-' and no slash in name
            if "=" in w and not w.startswith("-") and "/" not in w.split("=", 1)[0]:
                i += 1
                continue
            break
        if i < len(words):
            yield words[i][0], words[i][1], words[i][2], words[i + 1:], seg_end


def _verb_positions(cmd: str):
    for verb, *_ in _verb_spans(cmd):
        yield verb


def _seconds(token: str):
    m = _DURATION.fullmatch(token)
    if not m:
        return None
    return math.ceil(float(m.group(1)) * _UNIT[m.group(2)])


def transform_command(cmd: str):
    """Return (new_cmd, [(before, after), ...]). If any verb-position timeout
    cannot be rewritten, return (cmd, []) and let the deny run."""
    reps = []
    for verb, start, end, rest, seg_end in _verb_spans(cmd):
        if verb not in ("timeout", "gtimeout"):
            continue
        if rest and rest[0][0] == "--":
            rest = rest[1:]
        if not rest or rest[0][0].startswith("-"):
            return cmd, []
        secs = _seconds(rest[0][0])
        dur_end = rest[0][2]
        if secs is None or not cmd[dur_end:seg_end].strip():
            return cmd, []
        after = f"{_PERL_ALARM} {secs}"
        reps.append((start, dur_end, after))
    if not reps:
        return cmd, []
    new_cmd = cmd
    for start, end, after in reversed(reps):
        new_cmd = new_cmd[:start] + after + new_cmd[end:]
    if any(v in ("timeout", "gtimeout") for v in _verb_positions(new_cmd)):
        return cmd, []
    return new_cmd, [(cmd[s:e], a) for s, e, a in reps]


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
            if "--transform" in sys.argv[1:]:
                new_cmd, changes = transform_command(cmd)
                if changes:
                    done = ", ".join(f"{a} → {b}" for a, b in changes[:3])
                    record_fire(
                        "feedback_no_timeout_binary_on_macos", "transform",
                        context=", ".join(f"{a}→{b}" for a, b in changes[:3])[:200],
                    )
                    sys.stdout.write(json.dumps({"hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "updatedInput": {**data["tool_input"], "command": new_cmd},
                        "additionalContext": f"[no-timeout transform] この macOS に timeout は無い。"
                                             f"perl alarm に書き換えた: {done}"
                                             "（時間切れの exit は 124 でなく SIGALRM の 142）",
                    }}, ensure_ascii=False))
                    sys.stdout.flush()
                    sys.exit(0)
            sys.stderr.write(
                "[no-timeout hint] This macOS/zsh has no GNU `timeout`/`gtimeout` "
                "binary; `timeout N <cmd>` returns `command not found` (exit 127) "
                "and NEVER runs <cmd>. In a parallel batch that 127 also CANCELS "
                "every sibling call. Drop `timeout` and move the time limit "
                "inside the command instead: ssh -o ConnectTimeout=<sec> ... / "
                "nc -z -G <sec> host port / "
                f"{_PERL_ALARM} <sec> <cmd...>. "
                "See feedback_no_timeout_binary_on_macos.md.\n"
            )
            record_fire("feedback_no_timeout_binary_on_macos", "deny", context=verb)
            # 2026-10-20: 注意（exit 0）は Claude に届かず、必ず失敗する呼び出しがそのまま走っていた。
            # 必ず exit 127 になるコマンドなので、実行前に止める（exit 2 の stderr は Claude に届く）。
            sys.exit(2)


if __name__ == "__main__":
    main()
