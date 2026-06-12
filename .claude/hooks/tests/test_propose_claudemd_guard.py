#!/usr/bin/env python3
"""RED-first test for propose_claudemd_updates system-generated guard (#3).

Bug: the Stop hook treats a <task-notification> (a workflow completion message,
role=user) as a genuine user prompt. When the embedded <result> JSON contains a
codify token (e.g. 明文化), it fires and queues a bogus CLAUDE.md proposal — the
exact false-positive sitting as item 1 in pending_claudemd_updates.json.

This tests the detection layer directly (no queue side effects): _collect_turn_text
+ _detect_signals must NOT fire when the latest user message is system-generated,
but MUST still fire on a genuine codify request.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "propose_claudemd_updates.py"

# hook 本体は hooks dir が sys.path に載った状態で実行される前提 (_paths import)
sys.path.insert(0, str(HOOK.parent))

spec = importlib.util.spec_from_file_location("propose_claudemd_updates", HOOK)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _transcript(lines: list[dict]) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for obj in lines:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    f.close()
    return f.name


def _fires(transcript_path: str) -> bool:
    user, asst = mod._collect_turn_text(transcript_path)
    return mod._detect_signals(user, asst)["fire"]


TASK_NOTIF = (
    "<task-notification>\n<task-id>wcbu777gb</task-id>\n<status>completed</status>\n"
    "<result>{\"verdict\":\"...明文化された手順に従い...\"}</result>\n</task-notification>"
)


def main() -> int:
    # Case 1 (the bug): real user request had NO codify intent; the latest user
    # message is a task-notification whose result text contains 明文化 → must NOT fire.
    t1 = _transcript([
        {"message": {"role": "user", "content": "behavior の論文収集が終わっていたか調査して"}},
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "調査します"}]}},
        {"message": {"role": "user", "content": TASK_NOTIF}},
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "完了しました"}]}},
    ])
    # Case 2 (must still work): a genuine codify request → must fire.
    t2 = _transcript([
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "対応しました"}]}},
        {"message": {"role": "user", "content": "この手順はCLAUDE.mdに追加して恒久化して"}},
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "追記します"}]}},
    ])

    results = {
        "task_notification_does_not_fire": (_fires(t1), False),
        "genuine_codify_still_fires": (_fires(t2), True),
    }
    failures = []
    for name, (got, expected) in results.items():
        status = "ok" if got == expected else "FAIL"
        if got != expected:
            failures.append(name)
        print(f"  [{status}] {name}: expected={expected} got={got}")
    if failures:
        print(f"\n{len(failures)} FAILED")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
