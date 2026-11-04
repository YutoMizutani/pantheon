#!/usr/bin/env python3
"""hint_unquoted_glob_in_grep: `--transform` は flag 値の glob だけを引用し、既定は注意書きのまま。

RED は「書き換えてはいけない命令が書き換わらない」こと（既存の正当な命令を壊さない）。

Run: python3 -m pytest .claude/hooks/tests/test_hint_unquoted_glob_in_grep.py -q
"""
from __future__ import annotations

import _hermetic  # noqa: F401 — 発火の記録を使い捨ての場所へ向ける
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "hint_unquoted_glob_in_grep.py"
LOG = Path(_hermetic.TELEMETRY_DIR) / "hook_fires.jsonl"
RULE = "feedback_quote_glob_in_grep_under_zsh"


def run(cmd: str, transform: bool = True, **extra):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd, **extra}})
    args = [sys.executable, str(HOOK)] + (["--transform"] if transform else [])
    p = subprocess.run(args, input=payload, capture_output=True, text=True, timeout=30)
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    return p.returncode, out.get("hookSpecificOutput", {}), p.stderr


def outcomes() -> list[str]:
    if not LOG.exists():
        return []
    return [r.get("outcome") for r in map(json.loads, LOG.read_text(encoding="utf-8").splitlines())
            if r.get("rule_id") == RULE]


def fired(cmd: str, **kw):
    before = len(outcomes())
    rc, hso, err = run(cmd, **kw)
    return rc, hso, err, outcomes()[before:]


# --- RED: --transform 付きでも書き換わらない ---

def test_r1_positional_glob_is_left_alone_and_warns():
    rc, hso, err, new = fired('grep -rn "pat" src/*.py | head')
    assert rc == 0 and "updatedInput" not in hso
    assert "[zsh-glob hint]" in err and new == ["warn"]


@pytest.mark.parametrize("cmd", [
    "grep -rn pat . --include='*.py'",
    "find . -name \"*.py\" -o -name '*.md'",
])
def test_r2_already_quoted_is_silent(cmd):
    rc, hso, err, new = fired(cmd)
    assert rc == 0 and hso == {} and err == "" and new == []


def test_r3_heredoc_body_is_silent():
    rc, hso, err, new = fired("python3 - <<'EOF'\nimport glob; glob.glob(\"--include=*.py\")\nEOF")
    assert rc == 0 and hso == {} and err == "" and new == []


def test_r4_no_search_verb_is_silent():
    rc, hso, err, new = fired("ls *.jsonl | wc -l")
    assert rc == 0 and hso == {} and err == "" and new == []


def test_r5_text_inside_quotes_is_never_touched():
    rc, hso, err, new = fired('echo "grep --include=*.py is wrong"')
    assert hso == {} and new == []
    rc, hso, err, new = fired('grep -r pat --include=*.py . && echo "--include=*.py"')
    assert hso["updatedInput"]["command"] == \
        "grep -r pat --include='*.py' . && echo \"--include=*.py\""
    assert new == ["transform"]


def test_r6_dollar_value_gets_double_quotes():
    rc, hso, err, new = fired("rg -g $PAT*.lua pat")
    assert hso["updatedInput"]["command"] == 'rg -g "$PAT*.lua" pat'


@pytest.mark.parametrize("cmd", [
    "rg -g $P'x'*.lua pat",          # ' と $ の両方
    "grep -r pat --include=*.{py,md} .",  # {a,b} の展開は引用で消える
    "find ~ -path ~/x/* -print",     # 先頭の ~ は引用で展開されなくなる
    "grep -r pat --include=\\*.py .",  # 既に \ で逃がしてある
])
def test_r6_values_whose_meaning_quoting_changes_are_left_alone(cmd):
    rc, hso, err, new = fired(cmd)
    assert rc == 0 and "updatedInput" not in hso


def test_ambiguous_position_is_left_alone():
    # 同じ並びが検索 verb でない命令にもあると、置換位置が決められないので書き換えない
    rc, hso, err, new = fired("find . -name *.md; echo -name *.md")
    assert "updatedInput" not in hso and new == ["warn"]


# --- GREEN ---

def test_g1_include_value_is_quoted_and_other_keys_kept():
    rc, hso, err, new = fired("grep -rn pat src --include=*.py", description="検索")
    assert rc == 0 and err == ""
    assert hso["updatedInput"] == {"command": "grep -rn pat src --include='*.py'",
                                   "description": "検索"}
    assert "permissionDecision" not in hso
    assert "--include=*.py → --include='*.py'" in hso["additionalContext"]
    assert new == ["transform"]


@pytest.mark.parametrize("cmd, want", [
    ("find . -name *.py -newer x", "find . -name '*.py' -newer x"),
    ("rg -g *.lua pat", "rg -g '*.lua' pat"),
    ("cd /tmp && find . -iname *.MD -o -path ./a/* | head",
     "cd /tmp && find . -iname '*.MD' -o -path './a/*' | head"),
])
def test_g2_find_and_rg_value_flags(cmd, want):
    rc, hso, err, new = fired(cmd)
    assert hso["updatedInput"]["command"] == want and new == ["transform"]


def test_g3_default_is_still_the_hint():
    rc, hso, err, new = fired("grep -rn pat src --include=*.py", transform=False)
    assert rc == 0 and "updatedInput" not in hso
    assert "[zsh-glob hint]" in err and new == ["warn"]


def test_g4_only_flag_value_rewritten_positional_noted():
    rc, hso, err, new = fired("grep -rn pat dir/*.py --include=*.py")
    assert hso["updatedInput"]["command"] == "grep -rn pat dir/*.py --include='*.py'"
    assert "dir/*.py" in hso["additionalContext"] and "位置引数" in hso["additionalContext"]
    assert err == "" and new == ["transform"]
