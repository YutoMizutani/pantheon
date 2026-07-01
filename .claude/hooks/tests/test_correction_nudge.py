#!/usr/bin/env python3
"""Contract test for inject_correction_nudge.py (stateless correction nudge).

Verifies: (a) correction phrases emit the nudge; (b) topic/improvement words
(自己改善 / 次から / ...) do NOT (self-feed regression, INC-2026-06-17-01);
(c) third-party-negation and non-corrections do not; (d) prev-turn=user and
system prompts do not; (e) the hook produces NO durable side effect (never
recreates the retired correction queue). Run:
  python3 .claude/hooks/tests/test_correction_nudge.py
"""
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "inject_correction_nudge.py"
SIGNALS = HOOK.parent / "local" / "signals.json"
QUEUE = Path.home() / ".claude/runtime/pending_correction_reflections.json"


def _transcript(last_role: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"message": {"role": "user", "content": "hi"}}) + "\n")
        f.write(json.dumps({"message": {"role": last_role, "content": "x"}}) + "\n")
    return path


def emits(prompt: str, transcript: str, sid: str) -> bool:
    env = dict(os.environ, FRAME_SIGNALS_FILE=str(SIGNALS))
    out = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"prompt": prompt, "transcript_path": transcript, "session_id": sid}),
        capture_output=True, text=True, env=env,
    ).stdout
    return "[correction-signal]" in out


def main() -> int:
    asst = _transcript("assistant")
    user = _transcript("user")
    # (desc, prompt, transcript, expect_nudge)
    cases = [
        ("correction: 違う", "それは違うよ", asst, True),
        ("correction: 間違ってる", "それ間違ってると思う", asst, True),
        ("correction: おかしいでしょ。 (period, not ?)", "これおかしいでしょ。", asst, True),
        ("topic word 自己改善 alone — NO nudge (self-feed regression)",
         "自己改善エージェントもこの点を検知できないか", asst, False),
        ("topic word 次から — NO nudge", "次から気をつけて", asst, False),
        ("third-party negation — NO nudge", "Xがないとだめなんだね", asst, False),
        ("plain acceptance — NO nudge", "完了", asst, False),
        ("no correction word — NO nudge", "次のタスクお願い", asst, False),
        ("prev turn = user — NO nudge", "それ違うよ", user, False),
        ("system prompt — NO nudge", "<task-notification> 違う", asst, False),
    ]
    failed = []
    for i, (desc, prompt, tr, expect) in enumerate(cases):
        got = emits(prompt, tr, f"test-sid-{i}")  # unique sid → no debounce collision
        ok = got == expect
        print(f"[{'PASS' if ok else 'FAIL'}] {desc} "
              f"(got={'NUDGE' if got else 'silent'} want={'NUDGE' if expect else 'silent'})")
        if not ok:
            failed.append(desc)

    # Side-effect assert: the stateless nudge must never recreate the retired queue.
    q_before = QUEUE.exists()
    emits("それ違うよ", asst, "test-sid-sideeffect")
    if QUEUE.exists() and not q_before:
        print("[FAIL] hook created pending_correction_reflections.json (must be stateless)")
        failed.append("stateless side-effect")
    else:
        print("[PASS] no correction queue created (stateless)")

    os.unlink(asst)
    os.unlink(user)
    total = len(cases) + 1
    print(f"\n{total - len(failed)}/{total} passed")
    if failed:
        for d in failed:
            print(f"  FAILED: {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
