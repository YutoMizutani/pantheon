#!/usr/bin/env python3
"""reflect_launch.py のテスト（係を土台の小さい別プロセスで起こす形が崩れていないか）。

実行:
    python3 -m pytest heaven/tools/test_reflect_launch.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("reflect_launch", _HERE / "reflect_launch.py")
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)
REPO = _HERE.parent.parent


def _dry(*args, home):
    env = dict(os.environ, REFLECT_HOME=str(home))
    out = subprocess.run([sys.executable, str(_HERE / "reflect_launch.py"), *args, "--no-hook-review", "--dry-run"],
                         capture_output=True, text=True, env=env, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_default_home_is_dot_dir_in_heaven():
    assert rl.default_home() == REPO / "heaven" / ".self-improvement" / "reflection"


def test_home_has_only_agent_def_as_claudemd(tmp_path):
    home = tmp_path / "reflection"
    _dry("/t/a.jsonl", "sid", "--start", "3", home=home)
    md = home / "CLAUDE.md"
    assert md.is_symlink()
    assert md.resolve() == (REPO / ".claude" / "agents" / "self-reflection.md").resolve()


def test_settings_cut_repo_context_hooks_and_memory(tmp_path):
    d = _dry("/t/a.jsonl", "sid", home=tmp_path / "r")
    s = json.loads(Path(d["settings_path"]).read_text())
    assert s["disableAllHooks"] is True          # 係の中で受諾検知や通知の hook が動かない
    assert s["autoMemoryEnabled"] is False       # memory の索引を土台に載せない
    ex = s["claudeMdExcludes"]
    for p in ("CLAUDE.md", "CLAUDE.local.md"):
        assert str(REPO / p) in ex
    assert str(REPO / ".claude" / "rules") + "/**" in ex


def test_argv_runs_in_home_with_repo_and_transcript_dirs(tmp_path):
    d = _dry("/t/proj/a.jsonl", "sid", home=tmp_path / "r")
    argv = d["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert d["cwd"] == str(tmp_path / "r")
    dirs = [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
    assert str(REPO) in dirs and "/t/proj" in dirs   # repo を読み、memory・telemetry に書ける
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--settings") + 1] == d["settings_path"]


def test_prompt_carries_inputs_digest_and_repo_root(tmp_path):
    d = _dry("/t/a.jsonl", "sid-1", "--start", "7", home=tmp_path / "r")
    p = d["prompt"]
    assert "transcript_path: /t/a.jsonl" in p and "session_id: sid-1" in p
    assert f"python3 {REPO}/heaven/tools/transcript_digest.py /t/a.jsonl --start 7" in p
    assert str(REPO) in p and "相対パス" in p


def test_prompt_without_start_lets_digest_find_window(tmp_path):
    p = _dry("/t/a.jsonl", "sid", home=tmp_path / "r")["prompt"]
    assert f"transcript_digest.py /t/a.jsonl`" in p


def test_review_rows_keep_live_top5_and_spinning_ones():
    def r(hook, owner, spin_rate):
        return {"hook": hook, "owner": owner, "spin_rate": spin_rate}
    table = [r("言語ロック", "見つからない", 12)] + [r(f"h{i}.py", f"h{i}.py", 0) for i in range(7)] + [r("x.py", "x.py", 15)]
    got = [x["hook"] for x in rl.review_rows(table)]
    assert got == ["h0.py", "h1.py", "h2.py", "h3.py", "h4.py", "x.py"]  # 撤去済みの行は外し、上位 5 件＋空回り 10% 以上


def test_due_only_when_lineup_changes_or_a_week_passes(tmp_path):
    import datetime as dt
    rows = [{"owner": "b.py"}, {"owner": "a.py"}]
    seen = tmp_path / "seen.json"
    now = dt.datetime(2026, 11, 4, 12)
    assert rl.due(rows, seen, now)  # 記録が無ければ載せる
    seen.write_text(json.dumps({"hooks": ["a.py", "b.py"], "at": now.isoformat()}))
    assert not rl.due(rows, seen, now + dt.timedelta(days=6))
    assert rl.due(rows + [{"owner": "c.py"}], seen, now + dt.timedelta(days=1))
    assert rl.due(rows, seen, now + dt.timedelta(days=7))


def test_prompt_carries_hook_table_only_when_given():
    assert "hook の見直し" not in rl.prompt("/t/a.jsonl", "sid", None)
    p = rl.prompt("/t/a.jsonl", "sid", None, "| hook | 持ち主 |")
    assert "hook の見直し" in p and "| hook | 持ち主 |" in p


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
