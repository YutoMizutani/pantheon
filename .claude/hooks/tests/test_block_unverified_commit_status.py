#!/usr/bin/env python3
"""Deterministic tests for block_unverified_commit_status (Stop gate).

Exit code: 2 = block (forced redo), 0 = pass/audit. Run:
    python3 .claude/hooks/tests/test_block_unverified_commit_status.py

The gate fires ONLY on the recurring failure: the assistant VOLUNTEERS a
commit / tracking STATUS claim (未コミット / commit 不要 / gitignored / 'commit するなら言って'
/ 'gitignored の可能性が高い') when (a) the user did NOT raise git/commit this turn and
(b) no `git check-ignore`/`git ls-files`/`git status` was run this turn. Everything else
passes — crucially the guard-conflict cases (anger-stop, on-topic git discussion).
"""
from __future__ import annotations
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "block_unverified_commit_status.py"


def _msg(role, blocks):
    return {"message": {"role": role, "content": blocks}}


def _text(t):
    return [{"type": "text", "text": t}]


def _bash(cmd):
    return [{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]


def _write(rows):
    fd = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in rows:
        fd.write(json.dumps(r, ensure_ascii=False) + "\n")
    fd.close()
    return fd.name


def _run(rows, stop_active=False):
    tp = _write(rows)
    payload = {"session_id": "t", "transcript_path": tp, "stop_hook_active": stop_active}
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True
    )
    Path(tp).unlink(missing_ok=True)
    return proc.returncode


CASES = [
    # ── BLOCK: SPECULATIVE commit/tracking status (guess you could have verified) ──
    ("possibility_guess_blocks", dict(rows=[
        _msg("user", _text("保存しておいて")),
        _msg("assistant", _text("置きました。これは gitignored の可能性が高いです。"))]), 2),
    ("uncommitted_kamo_blocks", dict(rows=[
        _msg("user", _text("y")),
        _msg("assistant", _text("完了。たぶん未コミットのままかもしれません。"))]), 2),
    ("tracked_hazu_blocks", dict(rows=[
        _msg("user", _text("できた？")),
        _msg("assistant", _text("はい。これは tracked のはずです。"))]), 2),

    # ── PASS: ASSERTIVE/grounded mention — left to instruction (too FP-prone to block) ──
    ("assertive_uncommitted_passes", dict(rows=[
        _msg("user", _text("y")),
        _msg("assistant", _text("完了しました。変更はいずれも未コミットで置いています。"))]), 0),
    ("grounded_gitignored_passes", dict(rows=[
        _msg("user", _text("続けて")),
        _msg("assistant", _text("保護対象を heaven/（gitignored・git 復旧不能）にも拡張しました。"))]), 0),
    ("filename_gitignored_passes", dict(rows=[
        _msg("user", _text("記録して")),
        _msg("assistant", _text("本文: feedback_grep_residual_refs_blind_to_gitignored_config.md を書きました。"))]), 0),

    # ── PASS: a verification command actually ran this turn (確認してから事実を言う) ──
    ("verified_after_check_passes", dict(rows=[
        _msg("user", _text("y")),
        _msg("assistant", _bash("git check-ignore -v projects/obs/x.py")),
        _msg("user", [{"type": "tool_result", "content": "projects/obs/x.py"}]),
        _msg("assistant", _text("projects/obs/x.py はおそらく gitignored の可能性が高いです。"))]), 0),

    # ── PASS: the user raised git/commit this turn → on-topic, don't police ──
    ("user_raised_commit_passes", dict(rows=[
        _msg("user", _text("コミットって必要だっけ")),
        _msg("assistant", _text("未コミットの可能性が高いですが確認します。"))]), 0),
    ("meta_rule_discussion_passes", dict(rows=[
        _msg("user", _text("この commit ルール直して")),
        _msg("assistant", _text("ルールを『gitignored は確認して言う・可能性が高い等の推測を出さない』に直しました。"))]), 0),

    # ── PASS (guard-conflict): anger-stop must never be blocked ──
    ("anger_stop_plain_passes", dict(rows=[
        _msg("user", _text("ふざけるな")),
        _msg("assistant", _text("止まります。指示ください。"))]), 0),
    ("anger_stop_with_status_passes", dict(rows=[
        _msg("user", _text("ふざけるな commit すんな")),
        _msg("assistant", _text("客観事実: たぶん未コミットの可能性が高い。止まります。指示ください。"))]), 0),

    # ── PASS: override marker (legit verified / meta) ──
    ("ok_marker_passes", dict(rows=[
        _msg("user", _text("y")),
        _msg("assistant", _text("gitignored の可能性が高いです。\n# COMMIT-STATUS-OK: 確認経路を別途提示済み"))]), 0),

    # ── PASS: loop guard (already a forced continuation) ──
    ("loop_guard_passes", dict(rows=[
        _msg("user", _text("y")),
        _msg("assistant", _text("gitignored の可能性が高いです。"))], stop_active=True), 0),

    # ── PASS: ordinary turns with no speculative commit/tracking claim ──
    ("normal_no_commit_passes", dict(rows=[
        _msg("user", _text("天気は？")),
        _msg("assistant", _text("晴れです。"))]), 0),
    ("bare_word_commit_passes", dict(rows=[
        _msg("user", _text("git の使い方教えて")),
        _msg("assistant", _text("`git commit -m \"msg\"` で記録します。"))]), 0),
]


def main() -> int:
    failures = []
    for name, kwargs, expected in CASES:
        rc = _run(**kwargs)
        ok = rc == expected
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: rc={rc} (expected {expected})")
        if not ok:
            failures.append(name)
    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
