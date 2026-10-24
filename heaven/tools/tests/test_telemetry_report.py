#!/usr/bin/env python3
"""telemetry_report の UNWIRED 区分と test_ 除外の回帰テスト。

偽の CLAUDE_PROJECT_DIR (hooks ディレクトリ + settings) と偽の HOME (発火ログ) で
本体を実行し、出力の区分を確かめる。

実行:
    python3 heaven/tools/tests/test_telemetry_report.py
    python3 -m pytest heaven/tools/tests/test_telemetry_report.py -q
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

_TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "telemetry_report.py")
_INSTR = "from _x import record_fire\nrecord_fire('h', 'block')\n"


def _run(with_settings=True):
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "proj")
        home = os.path.join(tmp, "home")
        hooks = os.path.join(proj, ".claude/hooks")
        os.makedirs(os.path.join(hooks, "local"))
        for name, src in {
            "wired_fired.py": _INSTR,
            "wired_cold.py": _INSTR,
            "unwired_hook.py": _INSTR,
            "local/local_wired.py": _INSTR,
            "test_wired_fired.py": "def test(): pass\n",
            "_helper.py": "",
        }.items():
            with open(os.path.join(hooks, name), "w") as f:
                f.write(src)
        if with_settings:
            cmd = 'f="$CLAUDE_PROJECT_DIR/.claude/hooks/{}.py"; python3 "$f"'
            settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": cmd.format("wired_fired")},
                {"type": "command", "command": cmd.format("wired_cold")},
            ]}]}}
            local = {"hooks": {"Stop": [{"hooks": [
                {"type": "command",
                 "command": 'python3 "/abs/.claude/hooks/local/local_wired.py"'},
            ]}]}}
            with open(os.path.join(proj, ".claude/settings.json"), "w") as f:
                json.dump(settings, f)
            os.makedirs(os.path.join(proj, ".local"))
            with open(os.path.join(proj, ".local/settings.local.json"), "w") as f:
                json.dump(local, f)
        slug = re.sub(r"[^A-Za-z0-9]", "-", proj)
        telem = os.path.join(home, ".claude/projects", slug, "telemetry")
        os.makedirs(telem)
        with open(os.path.join(telem, "hook_fires.jsonl"), "w") as f:
            for h in ("wired_fired", "local_wired"):
                f.write(json.dumps({"hook": h, "outcome": "block"}) + "\n")
        env = dict(os.environ, HOME=home, CLAUDE_PROJECT_DIR=proj)
        out = subprocess.run([sys.executable, _TOOL, "--days", "30"], env=env,
                             capture_output=True, text=True, check=True).stdout
    return out


def _section(out, title):
    m = re.search(rf"^-- {title} \(\d+\).*?\n((?:   · .*\n)*)", out, re.M)
    return set(re.findall(r"   · (\S+)", m.group(1))) if m else set()


def test_unwired_separated_from_cold():
    out = _run()
    assert _section(out, "UNWIRED") == {"unwired_hook"}
    assert _section(out, "COLD") == {"wired_cold"}
    assert "unwired=1" in out and "cold=1" in out


def test_test_files_and_helpers_not_listed():
    out = _run()
    assert "hook scripts found:     4" in out
    assert "test_wired_fired" not in out and "_helper" not in out


def test_summary_line_keeps_existing_fields():
    line = [l for l in _run().splitlines() if l.startswith("TELEMETRY_SUMMARY")][0]
    for key in ("hooks=4", "instrumented=4", "uninstrumented=0", "cold=1",
                "unwired=1", "fires_30d=2", "viol_30d=2", "activity_30d=0"):
        assert key in line, (key, line)


def test_no_settings_means_no_unwired_judgement():
    out = _run(with_settings=False)
    assert "-- UNWIRED" not in out and "unwired=0" in out
    assert _section(out, "COLD") == {"wired_cold", "unwired_hook"}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"all {len(tests)} passed")
