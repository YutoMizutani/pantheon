#!/usr/bin/env python3
"""作業中に届いた訂正（queued_command）にも correction-signal を差し込む。

作業の途中に届いた「違う」は UserPromptSubmit を通らないので差し込みが付かず、
Claude は訂正と扱わないまま同じ読みで調べ続けた。
PreToolUse で記録の queued_command を読み、訂正の語があれば 1 発話につき 1 回だけ差し込む。
"""
import _hermetic  # noqa: F401
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "inject_correction_nudge.py"
SIGNALS = HOOK.parent / "local" / "signals.json"


def _queued(text, delivery_id):
    # 実際の会話記録から取った形
    return {"type": "attachment", "attachment": {
        "type": "queued_command", "prompt": [{"type": "text", "text": text}],
        "delivery_id": delivery_id, "commandMode": "prompt",
        "origin": {"kind": "human"}, "humanTurn": True}}


def _transcript(*entries):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": "調べます"}]}}) + "\n")
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def _pretool(transcript, sid):
    env = dict(os.environ, FRAME_SIGNALS_FILE=str(SIGNALS))
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "transcript_path": transcript, "session_id": sid}),
        capture_output=True, text=True, env=env,
    ).stdout


def test_queued_correction_gets_nudge_once():
    sid = f"queued-{os.getpid()}-1"
    t = _transcript(_queued("違う", "d-1"))
    out = _pretool(t, sid)
    assert "[correction-signal]" in out
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "違う" in out  # どの発話への差し込みかを示す
    assert "[correction-signal]" not in _pretool(t, sid)  # 同じ発話には 2 回目を出さない


def test_new_queued_correction_after_first_gets_its_own_nudge():
    sid = f"queued-{os.getpid()}-2"
    path = _transcript(_queued("違う", "d-1"))
    assert "[correction-signal]" in _pretool(path, sid)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(_queued("違うって。止めろ", "d-2"), ensure_ascii=False) + "\n")
    assert "[correction-signal]" in _pretool(path, sid)


def test_queued_non_correction_no_nudge():
    sid = f"queued-{os.getpid()}-3"
    assert _pretool(_transcript(_queued("次のタスクお願い", "d-1")), sid) == ""


def test_no_queued_no_nudge():
    sid = f"queued-{os.getpid()}-4"
    assert _pretool(_transcript(), sid) == ""


def test_nudge_has_misreading_class():
    # 分類が 2 つだけで「違う」が事実の訂正に押し込まれ、同じ読みで調べ続けた
    sid = f"queued-{os.getpid()}-5"
    out = _pretool(_transcript(_queued("違う", "d-1")), sid)
    assert "依頼の読み違い" in out
