#!/usr/bin/env python3
"""Contract test for block_red_first_violation.py (RED-first gate on bug-fix edits).

Subagent contract (2026-10-22): the harness passes the PARENT session's transcript as
`transcript_path` even when the tool call comes from a subagent, and identifies the
subagent only by `agent_id`. The subagent's own turn lives at
`<dirname(transcript_path)>/<session_id>/subagents/agent-<agent_id>.jsonl`. The hook must
judge a subagent's edit by the subagent's own transcript (its task prompt, its Bash REDs,
its markers), not the parent's. Main-session behavior is unchanged. Run:
  python3 .claude/hooks/tests/test_block_red_first_violation.py
"""
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "block_red_first_violation.py"
LLM = str(Path(__file__).resolve().parents[3])  # repo root (env-derived, no hardcoded user path)
TARGET = LLM + "/heaven/tools/_red_first_contract_target.py"  # never created; Edit needs no existence
SID = "test-red-first-contract"
AGENT = "a0123456789abcdef"


def user_text(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def bash_call(tu_id):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tu_id, "name": "Bash", "input": {"command": "pytest"}}]}}


def bash_result(tu_id, is_error):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tu_id, "is_error": is_error, "content": "out"}]}}


def bash_masked(tu_id, out):
    """`| tail` で終了コードが 0 に握り潰された結果（is_error=false、出力にだけ失敗が残る）"""
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tu_id, "is_error": False, "content": out}]}}


def say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


def decision(parent, sub=None, agent_id=None) -> str:
    with tempfile.TemporaryDirectory() as d:
        parent_path = Path(d) / f"{SID}.jsonl"
        write_jsonl(parent_path, parent)
        if sub is not None:
            write_jsonl(Path(d) / SID / "subagents" / f"agent-{AGENT}.jsonl", sub)
        payload = {"session_id": SID, "transcript_path": str(parent_path),
                   "tool_name": "Edit", "tool_input": {"file_path": TARGET}}
        if agent_id is not None:
            payload["agent_id"] = agent_id
        out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                             capture_output=True, text=True).stdout.strip()
    if not out:
        return "pass"
    return json.loads(out).get("hookSpecificOutput", {}).get("permissionDecision", "pass")


PARENT_BUG_NO_RED = [user_text("この不具合を直して")]
PARENT_PLAIN = [user_text("TODO を 5 件消化して")]
# 作業の途中で harness が注入する文（読んだ先の CLAUDE.md・通知）。本文に「不具合」「直して」等を含むが user の依頼ではない
INJECTED = user_text("<system-reminder>\nContents of projects/<name>/CLAUDE.md:\n不具合を直して…バグ…\n</system-reminder>")
# サブエージェントの完了報告・別セッションからの連絡（記録上は isMeta: true）と、裏の処理の完了通知
HANDBACK = {**user_text("Another Claude session sent a message:\n<agent-message from=\"a1\">\n[Subagent hand-back] "
                        "直せなかった不具合: hook が止めた\n</agent-message>"), "isMeta": True}
TASK_NOTE = user_text("<task-notification>\n<summary>Agent \"P-03\" finished</summary>\n不具合を直して\n</task-notification>")

# (description, parent, subagent transcript or None, agent_id, expected)
CASES = [
    ("main: bug-fix request, no RED attempt -> deny",
     PARENT_BUG_NO_RED, None, None, "deny"),
    ("main: bug-fix request, RED observed -> pass",
     PARENT_BUG_NO_RED + [bash_call("t1"), bash_result("t1", True)], None, None, "pass"),
    # 終了コードが握り潰されても、出力に残った失敗の要約・例外は RED として数える
    ("main: exit masked by | tail, pytest summary shows failure -> pass",
     PARENT_BUG_NO_RED + [bash_call("t2"), bash_masked("t2", "FAILED a.py::test_x\n1 failed, 15 passed in 0.96s")], None, None, "pass"),
    ("main: exit masked, output has no failure -> deny",
     PARENT_BUG_NO_RED + [bash_call("t3"), bash_masked("t3", "16 passed in 0.44s")], None, None, "deny"),
    ("main: not a bug-fix request -> pass",
     PARENT_PLAIN, None, None, "pass"),
    ("subagent: RED observed in own transcript, parent has none -> pass",
     PARENT_BUG_NO_RED,
     [user_text("集計の不具合を直す"), bash_call("s1"), bash_result("s1", True)], AGENT, "pass"),
    ("subagent: TDD-RED-OK marker in own transcript -> pass",
     PARENT_BUG_NO_RED,
     [user_text("集計の不具合を直す"), say("# TDD-RED-OK: 区切り文字 1 行の変更")], AGENT, "pass"),
    ("subagent: own prompt is not a bug fix, parent's is -> pass",
     PARENT_BUG_NO_RED, [user_text("ストーリー S-19 を実装する")], AGENT, "pass"),
    ("subagent: own bug-fix prompt, no RED attempt -> deny (gate still applies)",
     PARENT_PLAIN, [user_text("集計の不具合を直す")], AGENT, "deny"),
    ("subagent: transcript file missing -> falls back to parent",
     PARENT_BUG_NO_RED, None, AGENT, "deny"),
    # 注入文（<system-reminder>）は依頼として読まない（無人のストーリーで、新規スクリプトの編集を止めていた）
    ("subagent: injected CLAUDE.md after a non-bugfix prompt -> pass",
     PARENT_PLAIN, [user_text("ストーリー S-1 を実装する"), bash_call("s2"), bash_result("s2", False), INJECTED],
     AGENT, "pass"),
    ("main: injected text after a real bug-fix request -> still gated (deny)",
     PARENT_BUG_NO_RED + [INJECTED], None, None, "deny"),
    ("main: real bug-fix request alongside injected text in one message -> deny",
     [user_text("<system-reminder>\nnotice\n</system-reminder>\nこの不具合を直して")], None, None, "deny"),
    # 完了報告・連絡（isMeta）と完了通知は user の依頼ではない
    ("main: subagent hand-back mentioning 不具合 after a plain request -> pass",
     PARENT_PLAIN + [HANDBACK], None, None, "pass"),
    ("main: task-notification mentioning 不具合 after a plain request -> pass",
     PARENT_PLAIN + [TASK_NOTE], None, None, "pass"),
    ("main: hand-back after a real bug-fix request -> still gated (deny)",
     PARENT_BUG_NO_RED + [HANDBACK, TASK_NOTE], None, None, "deny"),
]


def main() -> int:
    failed = 0
    for desc, parent, sub, agent_id, expected in CASES:
        got = decision(parent, sub, agent_id)
        ok = got == expected
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {desc}  (got {got})")
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
