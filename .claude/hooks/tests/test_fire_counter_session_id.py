#!/usr/bin/env python3
"""record_fire の session_id 欄: 渡されたら書く・環境変数に落ちる・どちらも無ければ欄を省く。

同一セッション内の再発（同じ規則が 1 セッションで何度も発火するか）を
hook_fires.jsonl から数えるための欄。既存行（欄なし）との互換は欄の省略で保つ。
payload の session_id を渡す hook（hint_unquoted_glob_in_grep・block_unquoted_equals_word）は
実際に起動して、書かれた行に payload の値が入ることも確かめる。

Run: python3 -m pytest .claude/hooks/tests/test_fire_counter_session_id.py -q
"""
from __future__ import annotations

import _hermetic  # noqa: F401 — sets FRAME_TELEMETRY_DIR before anything else
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS_DIR))

_ENV_KEYS = ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAME_TELEMETRY_DIR", str(tmp_path))
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    import _paths
    import _fire_counter
    importlib.reload(_paths)
    return importlib.reload(_fire_counter)


def _last(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


def test_explicit_session_id_is_written(tmp_path, monkeypatch):
    fc = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-should-lose")
    fc.record_fire("rule_x", "warn", hook_name="h", session_id="sid-payload")
    assert _last(fc.fires_log_path())["session_id"] == "sid-payload"


def test_env_fallback_order(tmp_path, monkeypatch):
    fc = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-code")
    fc.record_fire("rule_x", "warn", hook_name="h")
    assert _last(fc.fires_log_path())["session_id"] == "sid-code"
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-plain")
    fc.record_fire("rule_x", "warn", hook_name="h")
    assert _last(fc.fires_log_path())["session_id"] == "sid-plain"


def test_no_session_id_omits_field(tmp_path, monkeypatch):
    fc = _fresh(tmp_path, monkeypatch)
    fc.record_fire("rule_x", "warn", hook_name="h")
    rec = _last(fc.fires_log_path())
    assert "session_id" not in rec
    assert rec["rule_id"] == "rule_x"


def _run_hook(name: str, command: str, tmp_path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    env["FRAME_TELEMETRY_DIR"] = str(tmp_path)
    payload = {"tool_name": "Bash", "tool_input": {"command": command},
               "session_id": "sid-from-payload"}
    subprocess.run([sys.executable, str(HOOKS_DIR / name)], input=json.dumps(payload),
                   env=env, capture_output=True, text=True, timeout=20)
    return _last(tmp_path / "hook_fires.jsonl")


def test_glob_hook_passes_payload_session_id(tmp_path):
    rec = _run_hook("hint_unquoted_glob_in_grep.py", "grep -rn x --include=*.py .", tmp_path)
    assert rec["rule_id"] == "feedback_quote_glob_in_grep_under_zsh"
    assert rec["session_id"] == "sid-from-payload"


def test_equals_hook_passes_payload_session_id(tmp_path):
    rec = _run_hook("block_unquoted_equals_word.py", "echo ===LABEL===", tmp_path)
    assert rec["rule_id"] == "feedback_no_unquoted_equals_word_under_zsh"
    assert rec["session_id"] == "sid-from-payload"
