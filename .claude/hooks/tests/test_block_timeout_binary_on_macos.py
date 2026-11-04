#!/usr/bin/env python3
"""Contract test for block_timeout_binary_on_macos.py.

The hook DENIES (exit 2, stderr hint) a command that calls the GNU
`timeout`/`gtimeout` binary in a COMMAND-VERB position (this macOS/zsh has no
such binary -> exit 127, and in a parallel batch that 127 cascade-cancels every
sibling). 'Fire' = the stderr hint appeared. It must:
  - fire on a bare `timeout`/`gtimeout` verb (first token, after a separator,
    after a `VAR=val` assignment, after a newline, inside a subshell);
  - NEVER fire on an absolute-path invocation (future coreutils install),
    a flag/value/path/operand/substring occurrence of the word "timeout",
    a command that merely mentions it, or a `timeout` inside quotes, a comment
    or a heredoc body (`grep -E "a|timeout|b"` was denied);
  - with `--transform`, rewrite `timeout [--] N[smhd] cmd...` to
    `perl -e 'alarm shift; exec @ARGV; warn ...; exit 127' <sec> cmd...` as a
    bare updatedInput (the rewritten command is run once below),
    and keep the deny for options, odd durations or a missing command.
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
LOG = Path(_hermetic.TELEMETRY_DIR) / "hook_fires.jsonl"
PERL = "perl -e 'alarm shift; exec @ARGV; warn \"exec $ARGV[0]: $!\\n\"; exit 127'"


def run(command: str, *args: str, **extra):
    p = subprocess.run(
        [sys.executable, str(HOOK), *args],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command, **extra}}),
        capture_output=True, text=True,
    )
    return p


def fires(command: str) -> bool:
    """'Fires' = the stderr hint appeared (and the hook denied with exit 2)."""
    p = run(command)
    return "[no-timeout hint]" in p.stderr and p.returncode == 2


def outcomes() -> list:
    if not LOG.exists():
        return []
    return [json.loads(line).get("outcome") for line in LOG.read_text(encoding="utf-8").splitlines()]


# (description, command, expect_fire)
CASES = [
    # ---- FIRE: bare timeout/gtimeout in a verb position ----
    ("bare timeout as first token", "timeout 30 echo hi", True),
    ("bare gtimeout as first token", "gtimeout 5 sleep 1", True),
    ("timeout after && separator", "echo a && timeout 3 foo", True),
    ("timeout after VAR=val assignment", "FOO=1 timeout 3 bar", True),
    ("timeout after newline", "echo a\\ntimeout 3 baz", True),
    ("timeout inside a subshell", "(timeout 5 foo)", True),
    ("timeout after a quoted arg and a real separator", 'echo "a|b" | timeout 5 cat', True),

    # ---- NO FIRE: absolute path is allowed (future coreutils by full path) ----
    ("absolute /usr/bin/timeout", "/usr/bin/timeout 5 sleep 1", False),
    ("absolute /opt/homebrew/bin/gtimeout", "/opt/homebrew/bin/gtimeout 5 sleep 1", False),

    # ---- NO FIRE: word "timeout" as flag/value/path/operand/substring ----
    ("ConnectTimeout flag value", "ssh -o ConnectTimeout=5 host", False),
    ("timeout as a path component", "cat /var/timeout/x", False),
    ("timeout as an echo operand", "echo timeout", False),
    ("timeout as a grep literal (verb=grep)", 'grep "timeout" file.txt', False),
    ("timeout in quoted code with parens (verb=python3)", "python3 -c \"print('timeout')\"", False),

    # ---- NO FIRE: timeout behind a separator that is itself quoted / commented ----
    ("quoted alternation in grep -E (false positive)",
     'grep -E "equals|timeout|symlink" f.jsonl', False),
    ("single-quoted ; before timeout", "echo 'x; timeout 5 y'", False),
    ("comment after ;", "ls # ; timeout 5 x", False),
    ("heredoc body line", "cat <<'E'\\ntimeout 5 x\\nE", False),
    ("word glued to a quote is not the bare verb", 'timeout"x" 5 y', False),

    # ---- guard-conflict: a safety-guard's legitimate stop-output echoed as text ----
    ("echoing 苛立ち停止 text with the word timeout does not fire (verb=echo)",
     'echo "止まります。指示ください # timeout"', False),
]

# (description, command, expected rewritten command or None for "still denied")
TRANSFORM_CASES = [
    ("plain seconds", "timeout 30 ssh host cmd", f"{PERL} 30 ssh host cmd"),
    ("gtimeout, fraction rounded up", "gtimeout 1.5 sleep 1", f"{PERL} 2 sleep 1"),
    ("minutes", "timeout 2m make test", f"{PERL} 120 make test"),
    ("seconds suffix", "timeout 10s curl -s x", f"{PERL} 10 curl -s x"),
    ("-- before the duration", "timeout -- 5 ls", f"{PERL} 5 ls"),
    ("after env and separators, rest kept",
     "echo a && FOO=1 timeout 3 bar | cat", f"echo a && FOO=1 {PERL} 3 bar | cat"),
    ("quoted command words kept", 'timeout 5 sh -c "echo hi; ls"', f'{PERL} 5 sh -c "echo hi; ls"'),
    ("two timeouts", "timeout 1 a; timeout 2 b", f"{PERL} 1 a; {PERL} 2 b"),
    ("-k option stays denied", "timeout -k 5 30 cmd", None),
    ("-s option stays denied", "timeout -s KILL 5 cmd", None),
    ("--foreground stays denied", "timeout --foreground 5 cmd", None),
    ("no command after the duration", "timeout 5", None),
    ("non-numeric duration", "timeout abc cmd", None),
    ("quoted duration", 'timeout "5" cmd', None),
    ("one unconvertible timeout denies the whole command", "timeout 1 a && timeout -k 1 2 b", None),
]


def check_transform(desc, cmd, want):
    before = len(outcomes())
    p = run(cmd, "--transform", description="d", timeout=60000)
    new = outcomes()[before:]
    if want is None:
        ok = p.returncode == 2 and "[no-timeout hint]" in p.stderr and "updatedInput" not in p.stdout and new == ["deny"]
        return ok, f"rc={p.returncode} outcomes={new}"
    try:
        hso = json.loads(p.stdout)["hookSpecificOutput"]
    except Exception:
        return False, f"rc={p.returncode} stdout={p.stdout!r} stderr={p.stderr[:80]!r}"
    ui = hso.get("updatedInput", {})
    ok = (p.returncode == 0 and ui.get("command") == want
          and ui.get("description") == "d" and ui.get("timeout") == 60000
          and "permissionDecision" not in hso and "142" in hso.get("additionalContext", "")
          and "[no-timeout hint]" not in p.stderr and new == ["transform"])
    return ok, f"rc={p.returncode} got={ui.get('command')!r} outcomes={new}"


# (description, command, expected exit code when the rewritten command is run by zsh)
RUN_CASES = [
    ("time-out exits 142 (SIGALRM)", "timeout 1 sleep 3", 142),
    ("the wrapped command's exit code passes through", "timeout 5 sh -c 'exit 7'", 7),
    ("a missing command exits 127, not 0", "timeout 5 nosuchcmd_zz", 127),
]


def check_run(cmd, want):
    p = run(cmd, "--transform")
    try:
        new_cmd = json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    except Exception:
        return False, f"not rewritten (rc={p.returncode})"
    rc = subprocess.run(["zsh", "-c", new_cmd], capture_output=True).returncode
    rc = 128 - rc if rc < 0 else rc  # killed by a signal: the shell's $? is 128 + signo
    return rc == want, f"rc={rc}"


def main() -> int:
    failed = []
    for desc, cmd, want in RUN_CASES:
        ok, info = check_run(cmd, want)
        print(f"[{'PASS' if ok else 'FAIL'}] run: {desc} ({info})")
        if not ok:
            failed.append(f"run: {desc}")
    for desc, cmd, expect in CASES:
        got = fires(cmd.replace("\\n", "\n"))
        ok = got == expect
        print(f"[{'PASS' if ok else 'FAIL'}] {desc} "
              f"(got={'FIRE' if got else 'pass'} want={'FIRE' if expect else 'pass'})")
        if not ok:
            failed.append(desc)
    for desc, cmd, want in TRANSFORM_CASES:
        ok, info = check_transform(desc, cmd, want)
        print(f"[{'PASS' if ok else 'FAIL'}] transform: {desc} ({info})")
        if not ok:
            failed.append(f"transform: {desc}")
    total = len(RUN_CASES) + len(CASES) + len(TRANSFORM_CASES)
    print(f"\n{total - len(failed)}/{total} passed")
    if failed:
        for d in failed:
            print(f"  FAILED: {d}")
        return 1
    return 0


def test_contract():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
