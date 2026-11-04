#!/usr/bin/env python3
"""hook_facts.py のテスト（合成した repo で、登録・試験・状態ファイル・docstring の拾い方を確かめる）。

実行:
    python3 heaven/tools/test_hook_facts.py
    python3 -m pytest heaven/tools/test_hook_facts.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("hook_facts", os.path.join(_HERE, "hook_facts.py"))
hf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hf)

BODY = '''#!/usr/bin/env python3
"""block_x — 何かを止める。

2026-10-16 sid aaaa: 新設。
10-19: 誤って止めたので直した。
"""
from _paths import RUNTIME_DIR
STATE = RUNTIME_DIR / "x_state"
'''


def _repo(td):
    def w(rel, text):
        p = os.path.join(td, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    w(".claude/hooks/block_x.py", BODY)
    w(".claude/hooks/tests/test_block_x.py", "import block_x\n")
    w(".claude/hooks/tests/test_other.py", "import block_y\n")
    w(".claude/runtime/x_state/a.json", "{}")
    w(".claude/runtime/x_state/b.json", "{}")
    cmd = 'f="$CLAUDE_PROJECT_DIR/.claude/hooks/block_x.py"; [ -f "$f" ] || exit 0; python3 "$f"'
    other = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/xblock_x.py"'  # 名前の一部だけ同じ物は数えない
    settings = {"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}, {"type": "command", "command": other}]}],
        "Stop": [{"hooks": [{"type": "command", "command": cmd}]}],
    }}
    w(".local/settings.local.json", json.dumps(settings, indent=2))
    return td


def test_collects_registrations_tests_state_and_dated_lines():
    with tempfile.TemporaryDirectory() as td:
        old = hf.REPO
        hf.REPO = _repo(td)
        try:
            body = hf.find_body("block_x.py")
            assert body == ".claude/hooks/block_x.py"
            regs = hf.registrations("block_x.py", td)
            assert [(r["event"], r["matcher"]) for r in regs] == [("PreToolUse", "Bash"), ("Stop", "")]
            assert regs[0]["line"] != regs[1]["line"] and all(r["line"] for r in regs)
            assert hf.tests_for("block_x.py", body) == [".claude/hooks/tests/test_block_x.py"]
            st = hf.state_files(BODY, body)
            assert st == [{"path": os.path.join(".claude", "runtime", "x_state"), "exists": True, "entries": 2}]
            fx = hf.fixes(body, BODY)
            assert len(fx["docstring_dated"]) == 2 and fx["commits"] == []
        finally:
            hf.REPO = old


def test_missing_hook_reports_no_body():
    with tempfile.TemporaryDirectory() as td:
        old = hf.REPO
        hf.REPO = _repo(td)
        try:
            f = hf.facts("gone_hook")
            assert f["missing"] and f["registrations"] == []
            assert "見つからない" in hf.render(f)
        finally:
            hf.REPO = old


def _hook(td, name, code):
    p = os.path.join(td, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(code)
    return p


def _measure(td, code, **kw):
    box = hf.make_sandbox(os.path.join(td, "box"))
    return hf.measure(_hook(td, "h.py", code), "Stop", "", os.path.join(td, "t.jsonl"), box, **kw), box


def test_measure_tells_a_heavy_hook_from_a_light_one():
    with tempfile.TemporaryDirectory() as td:
        heavy, _ = _measure(td, "n = 200 * 2**20\nb = bytearray(n)\nfor i in range(0, n, 4096):\n    b[i] = 1\n")
        light, _ = _measure(td, "pass\n")
        assert heavy["mb"] >= 190, heavy
        assert light["mb"] < 100, light
        assert not heavy["stopped"] and heavy["rc"] == 0


def test_measure_stops_at_the_time_limit():
    with tempfile.TemporaryDirectory() as td:
        r, _ = _measure(td, "while True:\n    pass\n", limit=0.5)
        assert "上限" in r["stopped"] and r["sec"] < 5, r


def test_measure_stops_at_the_memory_limit():
    with tempfile.TemporaryDirectory() as td:
        code = "n = 400 * 2**20\nb = bytearray(n)\nfor i in range(0, n, 4096):\n    b[i] = 1\nimport time\ntime.sleep(30)\n"
        r, _ = _measure(td, code, mem_limit_mb=100)
        assert "100MB" in r["stopped"] and r["sec"] < 15, r


def test_measure_keeps_writes_and_commands_in_the_sandbox():
    with tempfile.TemporaryDirectory() as td:
        real_home = os.path.expanduser("~")
        mark = "hook_cost_test_mark"
        code = (
            "import json, os, shutil, sys\n"
            "from pathlib import Path\n"
            "d = json.load(sys.stdin)\n"
            f"(Path.home() / {mark!r}).write_text(shutil.which('claude') + '\\n' + d['hook_event_name'])\n"
        )
        r, box = _measure(td, code)
        assert r["rc"] == 0, r
        written = os.path.join(box["env"]["HOME"], mark)
        lines = open(written, encoding="utf-8").read().splitlines()
        assert lines[0].startswith(td) or lines[0].startswith(os.path.realpath(td)), lines
        assert lines[1] == "Stop"
        assert not os.path.exists(os.path.join(real_home, mark))


def test_measure_tells_whether_the_transcript_was_opened():
    with tempfile.TemporaryDirectory() as td:
        t = os.path.join(td, "t.jsonl")
        open(t, "w").close()
        code = "import json, sys\nd = json.load(sys.stdin)\nopen(d['transcript_path']).read()\n"
        opened, _ = _measure(td, code)
        assert opened["opened"] and opened["rc"] == 0, opened
        skipped, _ = _measure(td, "# transcript_path\n")
        assert not skipped["opened"], skipped


def test_whole_read_is_found_and_streaming_is_not():
    text = "a = p.read_text(errors='replace').splitlines()\nfor l in iter_lines(p):\n    pass\nb = f.readlines()\n"
    assert hf.whole_read_lines(text) == [1, 4]


def test_registered_hooks_skip_session_start_and_list_what_cannot_be_measured():
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, ".claude", "hooks"))
        os.makedirs(os.path.join(td, ".local"))
        _hook(os.path.join(td, ".claude", "hooks"), "a.py", "pass\n")
        one = lambda c: [{"hooks": [{"command": c}]}]  # noqa: E731
        conf = {"hooks": {"Stop": one("python3 x/.claude/hooks/a.py") + one("python3 x/.claude/hooks/a.py"),
                          "SessionStart": one("python3 x/.claude/hooks/a.py"),
                          "UserPromptSubmit": one("x/tools/trigger.sh")}}
        with open(os.path.join(td, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(conf, fh)
        rows, unmeasured = hf.registered_hooks(td)
        assert [(r["hook"], r["event"]) for r in rows] == [("a.py", "Stop")]
        assert [u["event"] for u in unmeasured] == ["UserPromptSubmit"]


def test_cost_report_measures_a_copy_given_by_path():
    with tempfile.TemporaryDirectory() as td:
        t = os.path.join(td, "t.jsonl")
        open(t, "w").close()
        p = _hook(td, "copy_of_hook.py", "from _transcript import iter_lines\nimport json, sys\nlist(iter_lines(json.load(sys.stdin)['transcript_path']))\n")
        c = hf.cost_report([p], t, event="PreToolUse", matcher="Edit")
        assert [(r["hook"], r["event"], r["opened"], r["rc"]) for r in c["rows"]] == [("copy_of_hook.py", "PreToolUse", True, 0)]


def test_plugin_hooks_are_listed_but_not_started():
    with tempfile.TemporaryDirectory() as td:
        d = os.path.join(td, "plugins", "p", "hooks")
        os.makedirs(d)
        with open(os.path.join(d, "hooks.json"), "w", encoding="utf-8") as fh:
            json.dump({"hooks": {"Stop": [{"hooks": [{"command": "python3 run.py"}]}]}}, fh)
        rows, unmeasured = hf.registered_hooks(td)
        assert rows == [] and "plugins/p/hooks/hooks.json" in unmeasured[0]["command"]


if __name__ == "__main__":
    fails = 0
    for k, v in list(globals().items()):
        if k.startswith("test_") and callable(v):
            try:
                v()
                print("ok  ", k)
            except AssertionError as e:
                fails += 1
                print("FAIL", k, e)
    sys.exit(1 if fails else 0)
