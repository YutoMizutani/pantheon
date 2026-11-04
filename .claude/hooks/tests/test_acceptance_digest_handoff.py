#!/usr/bin/env python3
"""受諾検知で振り返りの係を起こすとき、係を土台の小さい別プロセスで起こす（往復ごとの送り直しを減らす）。

背景: 係を親の Agent ツールで起こすと、repo の CLAUDE.md 系・規則・memory の索引（≒ 44K）が
往復ごとに送り直されていた（1 往復の土台 ≒ 61K）。repo 外向けの起動フォルダから
`claude -p` で起こすと ≒ 18K になる（実測）。起こし方は reflect_launch.py が持つ。

契約:
  - 注入文に `python3 <repo>/heaven/tools/reflect_launch.py <transcript> <sid> --start <窓の始まり>` が入り、
    Bash の run_in_background で起こすよう指示する
  - 窓の始まりは hook の費用ゲートが数えた窓と同じ行（前回の振り返りの起動の直後）
  - 窓が読めず fail-open で起こすときは --start を付けない（道具が自分で窓を求める）
"""
from __future__ import annotations
import _hermetic  # noqa: F401

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "detect_acceptance_signal.py"
# 係は projects/<name> で開いたセッションからも起動されるので、道具は repo ルートからの絶対パスで渡す
LAUNCH = HOOK.parent.parent.parent / "heaven" / "tools" / "reflect_launch.py"


def _msg(role, content):
    return json.dumps({"message": {"role": role, "content": content}})


def _spawn():
    return [{"type": "tool_use", "name": "Agent", "input": {"subagent_type": "self-reflection", "prompt": "x"}}]


def _tools(n):
    return [{"type": "tool_use", "name": "Bash", "input": {}} for _ in range(n)] + [{"type": "text", "text": "done"}]


def _run(lines, cwd=None):
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="test_digest_handoff_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    env = dict(os.environ)
    env["FRAME_SIGNALS_FILE"] = str(HOOK.parent / "local" / "signals.json.example")
    sid = f"test-dh-{uuid.uuid4().hex}"
    proc = subprocess.run(
        [sys.executable, str(HOOK)], cwd=cwd,
        input=json.dumps({"prompt": "ok", "transcript_path": path, "session_id": sid}),
        capture_output=True, text=True, timeout=20, env=env,
    )
    return path, sid, proc.stdout


def test_reminder_hands_launch_command_with_window_start():
    lines = [
        _msg("user", "最初の依頼"),
        _msg("assistant", _tools(5)),
        _msg("assistant", _spawn()),          # 行 2: 前回の振り返りの起動 → 窓は行 3 から
        _msg("user", "次の依頼"),
        _msg("assistant", _tools(6)),
    ]
    path, sid, out = _run(lines)
    assert "AUTO-LEARN-META" in out
    assert f"python3 {LAUNCH} {path} {sid} --start 3" in out
    assert "run_in_background" in out
    # 親に Agent ツールで起こさせない（土台が repo の CLAUDE.md 系ごと乗るため）
    assert "spawn a background sub-agent with the Agent tool" not in out


def test_reminder_without_prior_fire_starts_at_zero():
    path, sid, out = _run([_msg("user", "依頼"), _msg("assistant", _tools(6))])
    assert f"python3 {LAUNCH} {path} {sid} --start 0" in out


def test_failopen_omits_start():
    sys.path.insert(0, str(HOOK.parent))
    import detect_acceptance_signal as d
    out = d._render_reminder("/t/x.jsonl", "sid", None)
    line = next(l for l in out.splitlines() if "reflect_launch.py" in l)
    assert f"python3 {LAUNCH} /t/x.jsonl sid`" in line
    assert "--start" not in line


def test_launch_path_resolves_from_project_cwd():
    """projects/<name> を作業フォルダにして hook が走っても、渡すパスが実在する。"""
    _, _, out = _run([_msg("user", "依頼"), _msg("assistant", _tools(6))], cwd=tempfile.gettempdir())
    cmd = next(l for l in out.splitlines() if "reflect_launch.py" in l)
    tool = cmd.split("`python3 ", 1)[1].split(" ", 1)[0]
    assert Path(tool).is_absolute() and Path(tool).exists()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
