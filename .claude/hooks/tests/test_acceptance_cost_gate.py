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
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"prompt": prompt, "transcript_path": transcript, "session_id": sid}),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    return "AUTO-LEARN-META" in proc.stdout


# A line simulating a prior reflection injection in the same session.
_PRIOR_FIRE = _msg("user", _text("<system-reminder>[AUTO-LEARN-META] earlier fire</system-reminder>"))


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
