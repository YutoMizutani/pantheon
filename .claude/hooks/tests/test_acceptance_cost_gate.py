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
fires, and nothing when it gates. We assert fire/skip by stdout presence.
"""
from __future__ import annotations

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


def _fired(transcript: str, sid: str, prompt: str = "ok") -> bool:
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
    return "AUTO-LEARN-META" in proc.stdout


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

    for p in tmp_paths:
        try:
            os.unlink(p)
        except OSError:
            pass

    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        return 1
    print(f"\nall {len(cases)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
