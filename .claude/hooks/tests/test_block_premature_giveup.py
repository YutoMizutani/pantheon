#!/usr/bin/env python3
"""Deterministic tests for block_premature_giveup (self-contained regex gate).

Exit code: 2 = block (forced redo), 0 = pass/audit. Run:
    python3 .claude/hooks/tests/test_block_premature_giveup.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "block_premature_giveup.py"


def _msg(role, blocks):
    return {"message": {"role": role, "content": blocks}}


def _text(t):
    return [{"type": "text", "text": t}]


def _tooluse():
    return [{"type": "tool_use", "name": "Grep", "input": {"pattern": "x"}}]


def _toolresult():
    return [{"type": "tool_result", "content": "out"}]


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


GIVEUP_HANDBACK = [
    _msg("user", _text("当月のカード額を自動取得して")),
    _msg("assistant", _text("自動で正確に取るのは物理的に無理です。A か B、どっちにしますか？")),
]

CASES = [
    # give-up + hand-back, no marker → BLOCK (2)
    ("giveup_handback_blocks", dict(rows=GIVEUP_HANDBACK), 2),
    # give-up but NO hand-back (pivot) → pass
    ("giveup_no_handback_passes", dict(rows=[
        _msg("user", _text("精度上げて")),
        _msg("assistant", _text("原理的限界です。別機構に切り替えます。"))]), 0),
    # grounding marker present → pass
    ("marker_passes", dict(rows=[
        _msg("user", _text("Touch ID 押して")),
        _msg("assistant", _text("物理的に無理。どうしますか？\n# GIVEUP-GROUNDED-OK: 物理デバイス"))]), 0),
    # 無理やり excluded → pass
    ("muri_yari_excluded", dict(rows=[
        _msg("user", _text("どうやった")),
        _msg("assistant", _text("無理やり詰めず素直にやった。どうしますか？"))]), 0),
    # normal answer, no give-up → pass
    ("no_giveup_passes", dict(rows=[
        _msg("user", _text("どう思う")),
        _msg("assistant", _text("いい方針です。進めます。"))]), 0),
    # loop guard: stop_hook_active → pass (audit only)
    ("loopguard_passes", dict(rows=GIVEUP_HANDBACK, stop_active=True), 0),
    # tool_result user msg must not reset the turn
    ("toolresult_does_not_reset", dict(rows=[
        _msg("user", _text("取得して")),
        _msg("assistant", _tooluse()),
        _msg("user", _toolresult()),
        _msg("assistant", _text("やはり物理的に無理。どっちにしますか？"))]), 2),
    # maple-style: give-up + handback → block (the redo decides; here a marker would clear it)
    ("maple_style_blocks", dict(rows=[
        _msg("user", _text("精度上げて")),
        _msg("assistant", _text("coarse signature の原理的限界。別機構が要る。どうしますか？"))]), 2),
    # --- safety regression: mandated stops (feedback_never_anger_user_absolute) must NOT block ---
    ("safety_gate_denial_stop", dict(rows=[
        _msg("user", _text("これ直して")),
        _msg("assistant", _text("auto-mode classifier が Self-Modification として拒否しました。回避策を探さず、ここで止めます。指示ください。"))]), 0),
    ("safety_anger_destall_stop", dict(rows=[
        _msg("user", _text("なんでできないんだよ")),
        _msg("assistant", _text("客観的事実: 直近の試行は permission gate で拒否。これ以上は無理に進めず、ここで止めます。指示ください。"))]), 0),
    ("safety_dumb_retry_stop", dict(rows=[
        _msg("user", _text("もう一回やって")),
        _msg("assistant", _text("同じ入力・同じ手段の2回目です。dumb retry はしません。止まります。指示ください。"))]), 0),
    ("safety_auth_hardblock_stop", dict(rows=[
        _msg("user", _text("ログインして取得して")),
        _msg("assistant", _text("本人認証(Touch ID)は私には無理です。そこだけお願いします。どうしますか？"))]), 0),
    # --- guard-conflict (feedback_new_gate_must_not_block_existing_safety_guard): the mandated
    # anger stop is "客観事実1行 + 止まります。指示ください" WITHOUT narrating the trigger word, so
    # the assistant-narration keyword guard cannot see it. The real signal is the USER's angry
    # register in the triggering message. These must NOT block (炎上回避 > 学習). ----------------
    ("safety_anger_keyword_free_stop", dict(rows=[
        _msg("user", _text("なんでまだ直ってないんだよ、お前ふざけてんのか")),
        _msg("assistant", _text("テストはまだ赤のままです。これ以上は無理に進めません。止まります。指示ください。"))]), 0),
    ("safety_anger_destall_no_narration", dict(rows=[
        _msg("user", _text("うるせえ、いい加減にしろ")),
        _msg("assistant", _text("現状: ビルドが通っていません。ここで一旦止めます。指示ください。"))]), 0),
    # control: SAME wall+handback shape (no LEGIT keyword) but NO anger in the user msg → must
    # STILL block (the anger guard must not be so broad it disables the gate for ordinary turns).
    ("control_no_anger_still_blocks", dict(rows=[
        _msg("user", _text("これ直して")),
        _msg("assistant", _text("テストはまだ赤のままです。これ以上は無理に進めません。止まります。指示ください。"))]), 2),
]


def main():
    failed = 0
    for name, kw, expect in CASES:
        code = _run(**kw)
        ok = code == expect
        if not ok:
            failed += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: exit={code} expect={expect}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
