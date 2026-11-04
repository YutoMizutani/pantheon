#!/usr/bin/env python3
"""hook_replay.py のテスト（偽の repo・hook 2 本・transcript 3 件で件数と標本を確かめる）。

実行:
    python3 heaven/tools/test_hook_replay.py
    python3 -m pytest heaven/tools/test_hook_replay.py -q
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("hook_replay", os.path.join(_HERE, "hook_replay.py"))
hr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hr)

# exit 0。"warnme" を含めば stderr に注意書き（warn）。書き先を確かめるため FRAME_TELEMETRY_DIR に 1 行書く。
HOOK_PASS = r'''
import json, os, sys
d = json.load(sys.stdin)
td = os.environ.get("FRAME_TELEMETRY_DIR")
if td:
    os.makedirs(td, exist_ok=True)
    open(os.path.join(td, "hook_fires.jsonl"), "a").write("x\n")
    open(os.environ["PROBE_FILE"], "a").write(td + "\n")
if "warnme" in d["tool_input"]["command"]:
    sys.stderr.write("注意: warnme\n")
sys.exit(0)
'''
# "rm" を含めば exit 2。"--transform" 付きで "fixme" を含めば updatedInput を返す。
HOOK_BLOCK = r'''
import json, sys
d = json.load(sys.stdin)
cmd = d["tool_input"]["command"]
if "--transform" in sys.argv and "fixme" in cmd:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                      "updatedInput": dict(d["tool_input"], command=cmd.replace("fixme", "fixed"))}}))
    sys.exit(0)
if "rm" in cmd.split():
    sys.stderr.write("止めた: rm " + d["session_id"] + "\n")
    sys.exit(2)
sys.exit(0)
'''


def _bash(ts: datetime, tid: str, cmd: str, sid: str = "s1") -> str:
    return json.dumps({"type": "assistant", "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "sessionId": sid,
                       "cwd": "/nonexistent-dir-for-test",
                       "message": {"content": [{"type": "tool_use", "id": tid, "name": "Bash",
                                                "input": {"command": cmd, "description": "d"}}]}})


def _world(td: str, transform: bool = False) -> tuple[str, str, str]:
    repo = os.path.join(td, "repo")
    hooks = os.path.join(repo, ".claude", "hooks")
    os.makedirs(hooks)
    os.makedirs(os.path.join(repo, ".local"))
    with open(os.path.join(hooks, "fake_pass.py"), "w") as f:
        f.write(HOOK_PASS)
    with open(os.path.join(hooks, "fake_block.py"), "w") as f:
        f.write(HOOK_BLOCK)
    tail = " --transform" if transform else ""
    settings = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command":
            'f="$CLAUDE_PROJECT_DIR/.claude/hooks/fake_pass.py"; [ -f "$f" ] || exit 0; python3 "$f"'}]},
        {"matcher": "Edit|Write", "hooks": [{"type": "command", "command":
            'f="$CLAUDE_PROJECT_DIR/.claude/hooks/never_bash.py"; python3 "$f"'}]},
        {"matcher": "*", "hooks": [{"type": "command", "command":
            'f="$CLAUDE_PROJECT_DIR/.claude/hooks/wildcard.py"; python3 "$f"'}]}]}}
    local = {"hooks": {"PreToolUse": [
        {"matcher": "Write|Bash", "hooks": [{"type": "command", "command":
            f'f="{hooks}/fake_block.py"; [ -f "$f" ] || exit 0; python3 "$f"{tail}'}]}]}}
    with open(os.path.join(repo, ".claude", "settings.json"), "w") as f:
        json.dump(settings, f)
    with open(os.path.join(repo, ".local", "settings.local.json"), "w") as f:
        json.dump(local, f)

    tdir = os.path.join(td, "transcripts")
    os.makedirs(os.path.join(tdir, "abc", "subagents"))
    now = datetime.now(timezone.utc)
    with open(os.path.join(tdir, "a.jsonl"), "w") as f:
        f.write(_bash(now - timedelta(hours=1), "t1", "ls -la") + "\n")
        f.write(_bash(now - timedelta(days=40), "old", "rm old") + "\n")  # 窓の外
        f.write(json.dumps({"type": "assistant", "timestamp": now.isoformat(), "message": {"content": [
            {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/x"}}]}}) + "\n")
        f.write("not json\n")
    with open(os.path.join(tdir, "b.jsonl"), "w") as f:
        f.write(_bash(now - timedelta(hours=2), "t2", "rm -rf build fixme", sid="s2") + "\n")
        f.write(_bash(now - timedelta(hours=2), "t2", "rm -rf build fixme", sid="s2") + "\n")  # 同じ id は 1 件
    with open(os.path.join(tdir, "abc", "subagents", "agent-1.jsonl"), "w") as f:
        f.write(_bash(now - timedelta(hours=3), "t3", "echo warnme") + "\n")
    return repo, tdir, os.path.join(td, "probe.txt")


def _run(argv: list[str]) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = hr.main(argv)
    assert rc == 0, rc
    return buf.getvalue()


def test_load_hooks_picks_bash_matchers_only():
    with tempfile.TemporaryDirectory() as td:
        repo, _, _ = _world(td, transform=True)
        hooks = hr.load_hooks(repo)
        assert [h["name"] for h in hooks] == ["fake_pass.py", "fake_block.py"], hooks
        assert hooks[0]["path"] == os.path.join(repo, ".claude", "hooks", "fake_pass.py")
        assert hooks[1]["args"] == ["--transform"]
        assert "wildcard.py" in [h["name"] for h in hr.load_hooks(repo, include_wildcard=True)]


def test_counts_and_samples():
    with tempfile.TemporaryDirectory() as td:
        repo, tdir, probe = _world(td)
        os.environ["PROBE_FILE"] = probe
        out = _run(["--repo", repo, "--transcripts", tdir, "--days", "7", "--limit", "100", "-k", "10"])
        assert "| fake_pass.py | - | 2 | 1 | 0 | 0 | 0 |" in out, out
        assert "| fake_block.py | - | 2 | 0 | 1 | 0 | 0 |" in out, out
        assert "### warn（1 件中 1 件）" in out and "`echo warnme`" in out, out
        assert "### block（1 件中 1 件）" in out and "止めた: rm s2" in out, out
        assert "HOOK_REPLAY_SUMMARY commands=3 hooks=2 pass=4 warn=1 block=1 transform=0 error=0" in out, out
        # 子の FRAME_TELEMETRY_DIR は使い捨てのフォルダで、終了後に消えている
        dirs = set(open(probe).read().split())
        assert len(dirs) == 1, dirs
        d = dirs.pop()
        assert "hook_replay_" in d and not os.path.exists(d), d


def test_limit_takes_newest_and_transform():
    with tempfile.TemporaryDirectory() as td:
        repo, tdir, probe = _world(td, transform=True)
        os.environ["PROBE_FILE"] = probe
        out = _run(["--repo", repo, "--transcripts", tdir, "--days", "7", "--limit", "2",
                    "--hook", "fake_block.py"])
        assert "| fake_block.py | --transform | 1 | 0 | 0 | 1 | 0 |" in out, out
        assert "commands=2 hooks=1" in out and "warnme" not in out, out


def test_json_output_and_unwired_hook():
    with tempfile.TemporaryDirectory() as td:
        repo, tdir, probe = _world(td)
        os.environ["PROBE_FILE"] = probe
        os.remove(os.path.join(repo, ".local", "settings.local.json"))
        res = json.loads(_run(["--repo", repo, "--transcripts", tdir, "--days", "7", "--json",
                               "--hook", "fake_block"]))
        assert len(res) == 3 and {r["category"] for r in res} == {"pass", "block"}, res
        assert [r for r in res if r["category"] == "block"][0]["exit"] == 2


def test_transcript_reading_hook_is_marked():
    with tempfile.TemporaryDirectory() as td:
        repo, tdir, probe = _world(td)
        os.environ["PROBE_FILE"] = probe
        with open(os.path.join(repo, ".claude", "hooks", "fake_pass.py"), "a") as f:
            f.write("# reads data.get('transcript_path')\n")
        out = _run(["--repo", repo, "--transcripts", tdir, "--days", "7"])
        assert "| fake_pass.py ※ | - |" in out and "| fake_block.py | - |" in out, out
        assert "※ transcript_path を読む hook" in out, out


def test_classify():
    assert hr.classify(0, "", "") == "pass"
    assert hr.classify(0, "", "hint") == "warn"
    assert hr.classify(0, json.dumps({"hookSpecificOutput": {"additionalContext": "x"}}), "") == "warn"
    assert hr.classify(0, json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}}), "") == "block"
    assert hr.classify(0, json.dumps({"hookSpecificOutput": {"updatedInput": {}}}), "") == "transform"
    assert hr.classify(2, "", "x") == "block"
    assert hr.classify(1, "", "Traceback") == "error"
    assert hr.classify(None, "", "") == "error"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {str(e)[:2000]}")
    print(f"all {len(tests)} passed" if not failed else f"{failed}/{len(tests)} failed")
    sys.exit(1 if failed else 0)
