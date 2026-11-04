#!/usr/bin/env python3
"""hooks_doc_check の回帰テスト。

偽の hooks 文書（文字列）と偽の CLAUDE_PROJECT_DIR（settings + hooks）で本体を実行し、
1 行目・続く行・exit code を確かめる。HOME は一時ディレクトリに向け、本番の runtime に書かない。

実行:
    python3 heaven/tools/tests/test_hooks_doc_check.py
    python3 -m pytest heaven/tools/tests/test_hooks_doc_check.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL = os.path.join(os.path.dirname(_HERE), "hooks_doc_check.py")
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_REAL_DOC = os.environ.get("HOOKS_DOC_CHECK_REAL_DOC", "")  # 手元に保存した実物の文書（任意）

FAKE_DOC = """# Hooks reference

## Hook input and output

### Common input fields

| Field | Description |
|---|---|
| `session_id` | id |
| `transcript_path` | path |

### JSON output

| Field | Description |
|---|---|
| `continue` | keep going |

## Hook events

### PreToolUse

#### PreToolUse input

PreToolUse hooks receive `tool_name`, `tool_input`.

```bash
# a comment that looks like a heading
```

##### Bash

| Field | Description |
|---|---|
| `command` | the command |

#### PreToolUse decision control

| Field | Description |
|---|---|
| `permissionDecision` | allow / deny |
| `permissionDecisionReason` | why |

### Stop

#### Stop input

Stop hooks receive `stop_hook_active`.

#### Stop decision control

| Field | Description |
|---|---|
| `decision` | block |
| `reason` | why |

## Other section
"""

PRE_OK = '''import json, sys
data = json.load(sys.stdin)
if data.get("tool_name") == "Bash":
    cmd = (data.get("tool_input") or {}).get("command")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": "x"}}))
'''
STOP_OK = '''import json, sys
def _read():
    return json.loads(sys.stdin.read() or "{}")
p = _read()
if not p.get("stop_hook_active"):
    print(json.dumps({"decision": "block", "reason": "x"}))
'''


def _cmd(name):
    return {"type": "command", "command": f'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/{name}.py"'}


def _run(hooks, wiring, doc=FAKE_DOC, extra_args=()):
    """hooks = {name: src}、wiring = {event: [(matcher, [name, ...])]}。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj, home = os.path.join(tmp, "proj"), os.path.join(tmp, "home")
        os.makedirs(os.path.join(proj, ".claude/hooks"))
        os.makedirs(home)
        for name, src in hooks.items():
            with open(os.path.join(proj, ".claude/hooks", f"{name}.py"), "w") as f:
                f.write(src)
        settings = {"hooks": {ev: [dict(({"matcher": m} if m else {}), hooks=[_cmd(n) for n in names])
                                   for m, names in groups]
                              for ev, groups in wiring.items()}}
        with open(os.path.join(proj, ".claude/settings.json"), "w") as f:
            json.dump(settings, f)
        doc_p = os.path.join(tmp, "hooks.md")
        with open(doc_p, "w") as f:
            f.write(doc)
        env = dict(os.environ, HOME=home, CLAUDE_PROJECT_DIR=proj)
        r = subprocess.run([sys.executable, _TOOL, "--offline", doc_p, *extra_args],
                           capture_output=True, text=True, env=env)
        assert not os.path.exists(os.path.join(home, ".claude")), "offline で runtime に書いた"
        return r.returncode, r.stdout.splitlines()


# ---- 必須 3 件

def test_reads_key_not_in_doc():
    src = PRE_OK.replace('data.get("tool_name")', 'data.get("tool_imput")')
    rc, out = _run({"fake_hook": src}, {"PreToolUse": [("Bash", ["fake_hook"])]})
    assert rc == 1, out
    assert out[0].startswith("hooks_doc_check: NG n=1 "), out
    assert "NG fake_hook reads tool_imput not in doc(PreToolUse)" in out, out


def test_settings_event_not_in_doc():
    rc, out = _run({"pre_ok": PRE_OK}, {"PreToolUsee": [("Bash", ["pre_ok"])]})
    assert rc == 1, out
    assert "NG settings event PreToolUsee not in doc (.claude/settings.json)" in out, out


def test_all_match_is_ok():
    rc, out = _run({"pre_ok": PRE_OK, "stop_ok": STOP_OK},
                   {"PreToolUse": [("Bash", ["pre_ok"])], "Stop": [(None, ["stop_ok"])]})
    assert rc == 0, out
    assert out[0].startswith("hooks_doc_check: OK events=2 hooks=2 "), out
    assert len(out) == 1, out


# ---- 任意 5 件

def test_stop_writes_pre_tool_key():
    src = STOP_OK.replace('{"decision": "block", "reason": "x"}',
                          '{"decision": "block", "permissionDecision": "deny"}')
    rc, out = _run({"stop_bad": src}, {"Stop": [(None, ["stop_bad"])]})
    assert rc == 1, out
    assert "NG stop_bad writes permissionDecision not in doc(Stop)" in out, out


def test_mcp_matcher_nested_is_skip():
    src = PRE_OK.replace('.get("command")', '.get("foo")')
    rc, out = _run({"mcp_hook": src}, {"PreToolUse": [("mcp__x__.*", ["mcp_hook"])]})
    assert rc == 0, out
    assert any(l.startswith("SKIP mcp_hook reads tool_input.foo") for l in out), out


def test_stop_reads_transcript_is_warn():
    src = STOP_OK.replace('if not p.get("stop_hook_active"):',
                          'tp = p.get("transcript_path")\nif not p.get("stop_hook_active"):')
    rc, out = _run({"stop_tp": src}, {"Stop": [(None, ["stop_tp"])]})
    assert rc == 0, out
    assert "WARN stop_tp Stop reads transcript_path; doc recommends last_assistant_message" in out, out


def test_doc_shape_changed_is_unavailable():
    doc = FAKE_DOC.replace("## Hook events", "## Events renamed")
    rc, out = _run({"pre_ok": PRE_OK}, {"PreToolUse": [("Bash", ["pre_ok"])]}, doc=doc)
    assert rc == 2, out
    assert out[0] == "hooks_doc_check: UNAVAILABLE doc shape changed: ## Hook events", out


def test_real_doc_against_real_wiring():
    if not os.path.exists(_REAL_DOC):
        print("  (skip: 実物の文書が無い)")
        return
    env = dict(os.environ, CLAUDE_PROJECT_DIR=_REPO)
    r = subprocess.run([sys.executable, _TOOL, "--offline", _REAL_DOC], capture_output=True, text=True, env=env)
    assert r.returncode in (0, 1), r.stdout + r.stderr
    assert r.stdout.startswith("hooks_doc_check: "), r.stdout


# ---- 道具の作りに対する追加

def test_payload_passed_as_argument_is_tracked():
    """関数の引数で渡る payload と、その入れ子の変数を 1 段追う。"""
    src = '''import json, sys
def check(payload, tin):
    payload.get("tool_nam")
    return tin.get("comand")
def main():
    data = json.loads(sys.stdin.read() or "{}")
    tool_input = data.get("tool_input") or {}
    check(data, tool_input)
main()
'''
    rc, out = _run({"argy": src}, {"PreToolUse": [("Bash", ["argy"])]})
    assert rc == 1, out
    assert "NG argy reads tool_nam not in doc(PreToolUse)" in out, out
    assert "NG argy reads tool_input.comand not in doc(PreToolUse/Bash)" in out, out


def test_log_record_dict_is_not_output():
    """`decision` を含むが出力ではない記録用の dict を書きキーに数えない。"""
    src = STOP_OK + 'rec = {"ts": 1, "session": "s", "decision": "d", "turns": 2, "real_user": 0}\n'
    rc, out = _run({"stop_log": src}, {"Stop": [(None, ["stop_log"])]})
    assert rc == 0, out


def test_missing_and_unwired():
    with_missing = {"PreToolUse": [("Bash", ["pre_ok", "gone_hook"])]}
    rc, out = _run({"pre_ok": PRE_OK, "lonely": PRE_OK}, with_missing)
    assert rc == 0, out
    assert "MISSING gone_hook wired in .claude/settings.json but file not found" in out, out
    assert "UNWIRED lonely" in out, out


# ---- --quiet-if-unchanged: file:// の URL で「取得」させ、HOME の一時ディレクトリに保存させる

def _run_fetch_twice(touch_between=False):
    with tempfile.TemporaryDirectory() as tmp:
        proj, home = os.path.join(tmp, "proj"), os.path.join(tmp, "home")
        os.makedirs(os.path.join(proj, ".claude/hooks"))
        os.makedirs(home)
        hook_p = os.path.join(proj, ".claude/hooks", "pre_ok.py")
        with open(hook_p, "w") as f:
            f.write(PRE_OK)
        with open(os.path.join(proj, ".claude/settings.json"), "w") as f:
            json.dump({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [_cmd("pre_ok")]}]}}, f)
        doc_p = os.path.join(tmp, "hooks.md")
        with open(doc_p, "w") as f:
            f.write(FAKE_DOC)
        env = dict(os.environ, HOME=home, CLAUDE_PROJECT_DIR=proj)
        argv = [sys.executable, _TOOL, "--url", "file://" + doc_p, "--quiet-if-unchanged"]
        first = subprocess.run(argv, capture_output=True, text=True, env=env)
        if touch_between:
            st = os.stat(hook_p)
            os.utime(hook_p, (st.st_atime, st.st_mtime + 10))
        second = subprocess.run(argv, capture_output=True, text=True, env=env)
        return first, second


def test_quiet_if_unchanged_second_run_is_one_line():
    first, second = _run_fetch_twice()
    assert first.returncode == 0 and first.stdout.startswith("hooks_doc_check: OK "), first.stdout
    out = second.stdout.splitlines()
    assert second.returncode == 0, second.stdout
    assert len(out) == 1 and out[0].startswith("hooks_doc_check: unchanged sha256="), out
    assert " fetched=" in out[0], out


def test_quiet_if_unchanged_hook_mtime_change_reruns():
    _first, second = _run_fetch_twice(touch_between=True)
    assert second.returncode == 0, second.stdout
    assert second.stdout.startswith("hooks_doc_check: OK "), second.stdout


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {n}: {e}")
    print(f"{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
