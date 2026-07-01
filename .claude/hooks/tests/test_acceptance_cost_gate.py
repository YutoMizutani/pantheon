#!/usr/bin/env python3
"""RED-first test for detect_acceptance_signal's cost gate.

Contract: when an exact acceptance signal ("ok"/"完了"/...) arrives, the hook must
NOT spawn the (expensive) reflection sub-agent if the work done in the current
window — messages since the last prior reflection fire, else session start — is
too small to plausibly yield a generalizable process lesson.

Gate (cheap, no LLM):
  - SKIP when window tool_use < _MIN_TOOLUSE_TO_REFLECT (default 4)
  - SKIP when the window has no real user task (cron/system-wakeup only) AND
    tool_use < _CRON_ONLY_TOOLUSE_CEIL (default 12)
  - FIRE otherwise; FAIL OPEN (fire) if the transcript is unreadable.

The hook emits the reminder (containing "AUTO-LEARN-META") on stdout when it
fires. When a gate suppresses a matched signal it now emits a distinct
[SELF-IMPROVE-SKIP] notice instead (user request 2026-06-22) — which does NOT
contain "AUTO-LEARN-META", so fire-detection on that substring still reads a
gated signal as no-fire. We assert fire/skip by stdout presence and, separately,
that the skip notice + a human reason are surfaced on the gated paths.
"""
from __future__ import annotations
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "detect_acceptance_signal.py"


def _text(t: str) -> list:
    return [{"type": "text", "text": t}]


def _tools(n: int) -> list:
    return [{"type": "tool_use", "name": "Bash", "input": {}} for _ in range(n)] + [
        {"type": "text", "text": "done"}
    ]


def _reflection_spawn() -> list:
    """An assistant turn that *manually* spawns the self-reflection sub-agent
    (Agent tool_use, subagent_type=self-reflection) — NO AUTO-LEARN-META marker.
    The window-boundary scan must treat this as a prior reflection fire, else the
    work before it gets re-mined by the next acceptance signal (the double-fire
    bug: ~96748 wasted subagent_tokens)."""
    return [
        {"type": "text", "text": "🔍 自己改善リフレクションをバックグラウンド起動"},
        {
            "type": "tool_use",
            "name": "Agent",
            "input": {"subagent_type": "self-reflection", "prompt": "Inputs: ..."},
        },
    ]


def _source_quote() -> list:
    """A tool_result that quotes this hook's own _REMINDER source verbatim — what a
    Read/Edit of detect_acceptance_signal.py produces. It carries the full
    AUTO-LEARN-META reminder anchor but is NOT a genuine injection, so it must NOT
    reset the mining window. Old bare-substring impl treated it as a boundary →
    editing this hook collapsed the window and skipped every later acceptance
    signal (self-referential false-positive fixed 2026-06-20)."""
    return [{
        "type": "tool_result",
        "content": (
            "240\t[AUTO-LEARN-META] User acceptance signal detected. "
            "Run a background meta-improvement reflection (source quote)."
        ),
    }]


def _msg(role: str, content) -> str:
    return json.dumps({"message": {"role": role, "content": content}})


def _write_transcript(lines: list[str]) -> str:
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="test_gate_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _run(transcript: str, sid: str, prompt: str = "ok") -> str:
    """Run the hook once and return its stdout (the injected reminder, if any)."""
    env = dict(os.environ)
    # Hermetic: a non-empty real correction queue would bypass the gate.
    env["CLAUDE_CORRECTION_QUEUE"] = f"/tmp/test_gate_empty_queue_{os.getpid()}.json"
    # Hermetic against the user's local vocabulary: pin the tracked example pack.
    env["FRAME_SIGNALS_FILE"] = str(HOOK.parent / "local" / "signals.json.example")
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"prompt": prompt, "transcript_path": transcript, "session_id": sid}),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    return proc.stdout


def _fired(transcript: str, sid: str, prompt: str = "ok") -> bool:
    return "AUTO-LEARN-META" in _run(transcript, sid, prompt)


# A line simulating a prior reflection injection in the same session. Must use
# the REAL reminder text — the boundary detector keys on the full directive anchor
# ("[AUTO-LEARN-META] User acceptance signal detected. Run a background ...") so a
# line merely mentioning the bare marker no longer counts (2026-06-20 fix).
_PRIOR_FIRE = _msg(
    "user",
    _text(
        "<system-reminder>\n[AUTO-LEARN-META] User acceptance signal detected. "
        "Run a background meta-improvement reflection (起動と完了を日本語1行で可視化する).\n"
        "</system-reminder>"
    ),
)


def main() -> int:
    if not HOOK.exists():
        print(f"RED: hook not found at {HOOK}")
        return 1

    pid = os.getpid()
    cases = [
        # name, transcript lines (must end with an assistant turn), expected_fire
        (
            "high_activity_fires",
            [_msg("user", _text("実装して")), _msg("assistant", _tools(6))],
            True,
        ),
        (
            "low_activity_skips",
            [_msg("user", _text("これ何?")), _msg("assistant", _tools(1))],
            False,
        ),
        (
            "zero_tool_conversational_skips",
            [_msg("user", _text("どう思う?")), _msg("assistant", _text("こう思います"))],
            False,
        ),
        (
            "cron_only_light_skips",
            [_msg("user", _text("<task-notification>wake")), _msg("assistant", _tools(2))],
            False,
        ),
        (
            "cron_only_heavy_fires",
            [_msg("user", _text("<system-reminder>wake")), _msg("assistant", _tools(20))],
            True,
        ),
        (
            "windowed_already_reflected_skips",
            [
                _msg("user", _text("大きいタスク")),
                _msg("assistant", _tools(10)),
                _PRIOR_FIRE,
                _msg("user", _text("小さい追従")),
                _msg("assistant", _tools(1)),
            ],
            False,
        ),
        (
            # [RED→GREEN 2026-06-20] A tool_result that QUOTES this hook's own
            # source (Read/Edit of detect_acceptance_signal.py) contains the full
            # reminder anchor verbatim. It must NOT reset the window — otherwise
            # working on this hook collapses the window to ~0 and skips every later
            # acceptance signal. Here the genuine boundary is _PRIOR_FIRE; the
            # source quote after it must be ignored, leaving 8+1 tool_use in-window.
            "windowed_source_quote_not_boundary_fires",
            [
                _msg("user", _text("大きいタスク")),
                _msg("assistant", _tools(10)),
                _PRIOR_FIRE,
                _msg("assistant", _tools(8)),
                _msg("user", _source_quote()),
                _msg("user", _text("追従")),
                _msg("assistant", _tools(1)),
            ],
            True,
        ),
        (
            # [RED→GREEN] A *manual* self-reflection spawn (no AUTO-LEARN-META
            # marker) must close the window just like the injected reminder does.
            # Old impl advanced the window only on AUTO-LEARN-META, so the big
            # pre-spawn work stayed in-window and re-fired = double launch.
            "windowed_manual_reflection_spawn_skips",
            [
                _msg("user", _text("大きいタスク")),
                _msg("assistant", _tools(10)),
                _msg("assistant", _reflection_spawn()),
                _msg("user", _text("小さい追従")),
                _msg("assistant", _tools(1)),
            ],
            False,
        ),
        (
            # Unreadable transcript: the existing _previous_turn_was_assistant
            # guard returns no-fire first (no assistant turn to reflect on). The
            # gate must NOT change this. (Fail-open is an in-code safety net for
            # an unexpected exception once prev-assistant has already passed.)
            "unreadable_transcript_no_fire",
            None,  # nonexistent transcript path
            False,
        ),
    ]

    failures = []
    tmp_paths = []
    for i, (name, lines, expected) in enumerate(cases):
        sid = f"test-gate-{pid}-{i}"
        if lines is None:
            transcript = f"/tmp/does-not-exist-{pid}-{i}.jsonl"
        else:
            transcript = _write_transcript(lines)
            tmp_paths.append(transcript)
        got = _fired(transcript, sid)
        status = "ok" if got == expected else "FAIL"
        if got != expected:
            failures.append(name)
        print(f"  [{status}] {name}: expected_fire={expected} got_fire={got}")

    # --- skip-notice surfacing (user request 2026-06-22) ------------------
    # A gated acceptance signal must no longer be silent. Assert the
    # [SELF-IMPROVE-SKIP] marker + a human reason fragment appear on the gated
    # paths (cost gate, debounce) and are ABSENT on the fire path.
    def _check(name: str, cond: bool) -> None:
        status = "ok" if cond else "FAIL"
        if not cond:
            failures.append(name)
        print(f"  [{status}] {name}")

    low_tx = _write_transcript([_msg("user", _text("これ何?")), _msg("assistant", _tools(1))])
    tmp_paths.append(low_tx)
    low_out = _run(low_tx, f"test-gate-{pid}-skip-low")
    _check("cost_gate_skip_emits_notice",
           "[SELF-IMPROVE-SKIP]" in low_out and "作業量" in low_out)

    fire_tx = _write_transcript([_msg("user", _text("実装して")), _msg("assistant", _tools(6))])
    tmp_paths.append(fire_tx)
    fire_out = _run(fire_tx, f"test-gate-{pid}-skip-fire")
    _check("fire_path_emits_no_skip_notice", "[SELF-IMPROVE-SKIP]" not in fire_out)

    # Debounce: same session + fire-eligible window, two signals within cooldown.
    # First fires; the second must surface the skip notice with the cooldown reason.
    db_tx = _write_transcript([_msg("user", _text("大きいタスク")), _msg("assistant", _tools(8))])
    tmp_paths.append(db_tx)
    db_sid = f"test-gate-{pid}-skip-debounce"
    db_first = _run(db_tx, db_sid)
    db_second = _run(db_tx, db_sid)
    _check("debounce_first_fires", "AUTO-LEARN-META" in db_first)
    _check("debounce_second_emits_notice",
           "[SELF-IMPROVE-SKIP]" in db_second and "クールダウン" in db_second)
    try:
        Path(f"/tmp/claude_acceptance_signal_last_{db_sid}.txt").unlink(missing_ok=True)
    except OSError:
        pass

    for p in tmp_paths:
        try:
            os.unlink(p)
        except OSError:
            pass

    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
