#!/usr/bin/env python3
"""RED-first test for the correction→queue→acceptance batch-reflection flow.

Contract (2026-06-11 redesign, user feedback "やりとり中の自己発火は過剰実装"):

  detect_correction_signal_v2 (UserPromptSubmit):
    - On a correction-signal prompt it must NOT emit the [AUTO-LEARN] reminder
      (no mid-conversation sub-agent spawn, no stdout at all).
    - Instead it appends one item {session_id, ts, transcript_path,
      prompt_excerpt} to the queue file (env CLAUDE_CORRECTION_QUEUE).
    - Non-correction prompts append nothing.

  detect_acceptance_signal (UserPromptSubmit, exact "ok"/"完了"/...):
    - When the queue has items, it fires even if the cost gate says
      low-activity (queued corrections are guaranteed mineable), embeds the
      queued excerpts in the reminder, and drains the queue.
    - When the queue is empty, the cost gate behaves exactly as before
      (low-activity skips).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
CORRECTION_HOOK = HOOKS_DIR / "detect_correction_signal_v2.py"
ACCEPTANCE_HOOK = HOOKS_DIR / "detect_acceptance_signal.py"


def _text(t: str) -> list:
    return [{"type": "text", "text": t}]


def _tools(n: int) -> list:
    return [{"type": "tool_use", "name": "Bash", "input": {}} for _ in range(n)] + [
        {"type": "text", "text": "done"}
    ]


def _msg(role: str, content) -> str:
    return json.dumps({"message": {"role": role, "content": content}})


def _write_transcript(lines: list[str]) -> str:
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="test_cqf_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _run_hook(hook: Path, payload: dict, queue_path: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_CORRECTION_QUEUE"] = queue_path
    # Vocabulary is config now (_signals.py). Pin the tracked JA example pack so
    # the JA prompts below match in a fresh clone (no local/signals.json) and
    # the example file itself stays continuously verified.
    env["FRAME_SIGNALS_FILE"] = str(HOOKS_DIR / "local" / "signals.json.example")
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )


def _read_queue(queue_path: str) -> list:
    p = Path(queue_path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items")
    return items if isinstance(items, list) else []


def main() -> int:
    pid = os.getpid()
    failures: list[str] = []
    tmp_paths: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        status = "ok" if cond else "FAIL"
        if not cond:
            failures.append(name)
        print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))

    # --- correction hook: queues silently ---
    transcript = _write_transcript(
        [_msg("user", _text("これ直して")), _msg("assistant", _tools(3))]
    )
    tmp_paths.append(transcript)
    qfd, qpath = tempfile.mkstemp(suffix=".json", prefix="test_cqf_queue_")
    os.close(qfd)
    os.unlink(qpath)  # hook must handle a not-yet-existing queue file
    tmp_paths.append(qpath)

    proc = _run_hook(
        CORRECTION_HOOK,
        {
            "prompt": "それ違うでしょ？なんで全部やり直したの？",
            "transcript_path": transcript,
            "session_id": f"test-cqf-{pid}-corr",
        },
        qpath,
    )
    items = _read_queue(qpath)
    check(
        "correction_no_stdout_reminder",
        "AUTO-LEARN" not in proc.stdout and proc.stdout.strip() == "",
        f"stdout={proc.stdout[:120]!r}",
    )
    check("correction_queued_one_item", len(items) == 1, f"items={len(items)}")
    if items:
        check(
            "correction_item_fields",
            items[0].get("session_id") == f"test-cqf-{pid}-corr"
            and items[0].get("transcript_path") == transcript
            and "違う" in items[0].get("prompt_excerpt", "")
            and bool(items[0].get("ts")),
            f"item={items[0]}",
        )

    # --- correction hook: non-correction prompt appends nothing ---
    proc = _run_hook(
        CORRECTION_HOOK,
        {
            "prompt": "次のファイルも同様にお願いします",
            "transcript_path": transcript,
            "session_id": f"test-cqf-{pid}-corr2",
        },
        qpath,
    )
    check(
        "non_correction_not_queued",
        len(_read_queue(qpath)) == 1,
        f"items={len(_read_queue(qpath))}",
    )

    # --- acceptance hook: queued item bypasses low-activity gate, drains queue ---
    low_activity = _write_transcript(
        [_msg("user", _text("これ何?")), _msg("assistant", _tools(1))]
    )
    tmp_paths.append(low_activity)
    proc = _run_hook(
        ACCEPTANCE_HOOK,
        {
            "prompt": "ok",
            "transcript_path": low_activity,
            "session_id": f"test-cqf-{pid}-acc",
        },
        qpath,
    )
    check(
        "acceptance_fires_with_queue",
        "AUTO-LEARN-META" in proc.stdout,
        f"stdout={proc.stdout[:120]!r}",
    )
    check(
        "acceptance_embeds_correction",
        "違う" in proc.stdout,
        "queued excerpt must appear in reminder",
    )
    check(
        "acceptance_drains_queue",
        len(_read_queue(qpath)) == 0,
        f"items={len(_read_queue(qpath))}",
    )

    # --- acceptance hook: empty queue keeps the original low-activity skip ---
    proc = _run_hook(
        ACCEPTANCE_HOOK,
        {
            "prompt": "ok",
            "transcript_path": low_activity,
            "session_id": f"test-cqf-{pid}-acc2",
        },
        qpath,
    )
    check(
        "acceptance_empty_queue_still_gates",
        "AUTO-LEARN-META" not in proc.stdout,
        f"stdout={proc.stdout[:120]!r}",
    )

    for p in tmp_paths:
        try:
            os.unlink(p)
        except OSError:
            pass

    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
