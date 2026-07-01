#!/usr/bin/env python3
"""Contract test for audit_english_drift_in_japanese_reply (local-layer hook).

RED-first 根拠 (2026-06-19 発火位置再設計): 旧実装は最終 user 発話以降の current
turn だけを走査し、会話中盤 (連続技術作業の没入区間) で起きた英語ドリフトを構造的に
取りこぼした (memory feedback_no_foreign_script_drift の 4-7 回目再発の真因)。本テストの
`test_mid_turn_drift_detected` は旧実装に対して FAIL する (= RED)。全ターン遡及へ
直すと PASS する (= GREEN)。hook の stable な CLI 契約 (stdin payload → stderr) 越しに
検証するので内部関数名のリネームに頑健。
"""
from __future__ import annotations
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "local" / "audit_english_drift_in_japanese_reply.py"

_DRIFT = "Now wire it into the handler and run the full suite again to confirm green output."
_JA = "ハンドラに配線してテストを再実行しました。"

# [item 2 / 2026-06-20] Conservative marker-gated detection.
# Technical-narration drift the OLD "≥6 consecutive latin words" threshold MISSES
# because punctuation / parens / identifiers break the consecutive run below 6:
#   "Now the regression test passes."        -> 5 consecutive, <6      -> old MISS
#   "Let me check `x.py` first."             -> stripped to 4 words     -> old MISS
#   "I'll add a guard, then re-run."         -> comma splits the run    -> old MISS
# These are exactly the origin drift class (HIT 1/6 measured). The new signal flags
# a zero-CJK segment that OPENS with an English narration marker (I'll/Now/Let me/…)
# AND has >=4 total latin words. Conservative (FP-suppression priority, user choice):
# no opener marker, or <4 words, stays unflagged — see the two control cases below.
_DRIFT_FRAGMENTED = "Now the regression test passes."  # old MISS, new HIT
_EN_NO_MARKER = "see config json env value"            # zero-CJK, no opener marker -> stay unflagged
_EN_MARKER_TOO_SHORT = "Let me see."                   # opener but <4 words -> stay unflagged

# [item 3 / 2026-06-23] cite の英語逐語引用 (markdown blockquote `> ...`) は地の文ドリフトでなく
# cite 規範 (web-research の逐語引用必須) の正当出力。引用ブロックは言語ロックの射程外なので flag しない。
# 修正前 (_strip_code_and_paths が blockquote を除去しない) は _EN_PROSE にマッチして RED、
# blockquote 除去で GREEN。真ドリフトは `>` で始まらないので検出力は損なわれない。
_CITE_BLOCKQUOTE = (
    "確定 (cited): アプリ内ブラウザは実在します。\n"
    "> \"Browser use lets Codex operate the in-app browser directly and verify a fix in the page.\"\n"
    "次ターンも日本語で続けます。"
)


def _write_transcript(rows: list[dict]) -> str:
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _asst(text: str) -> dict:
    return {"message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _user(text: str) -> dict:
    return {"message": {"role": "user", "content": text}}


def _run(transcript: str, sid: str, state_dir: str) -> tuple[int, str]:
    env = dict(os.environ)
    # STATE_DIR (差分検出 state の置き場) を tmp 由来 slug へ逃がしてテストを隔離する。
    env["CLAUDE_PROJECT_DIR"] = state_dir
    payload = json.dumps({"session_id": sid, "transcript_path": transcript})
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )
    return p.returncode, p.stderr


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as proj:
        # [RED→GREEN] 中盤ドリフト + 末尾 user "ok" + 最終 assistant 日本語。
        # 旧実装は最終ターン (日本語) だけ見て drift を取りこぼす。
        t1 = _write_transcript([
            _user("最初のタスク"),
            _asst(_DRIFT),
            _user("ok"),
            _asst("完了しました。"),
        ])
        code, err = _run(t1, "sid-mid", proj)
        ok = code == 0 and "audit_english_drift" in err
        print(f"[{'PASS' if ok else 'FAIL'}] mid_turn_drift_detected: exit={code} stderr_has_warn={'audit_english_drift' in err}")
        if not ok:
            failures.append("mid_turn_drift_detected")

        # [RED→GREEN item 2] 断片化した技術ナレーション (句読点が連続語を割る) を
        # 中盤ターンで検出する。旧「≥6 連続語」閾値は 5 連続語の本例を取りこぼす (RED)。
        # 新マーカー gate (英文頭 + CJK ゼロ + ≥4 語) で HIT する (GREEN)。
        t_frag = _write_transcript([
            _user("最初のタスク"),
            _asst(_DRIFT_FRAGMENTED),
            _user("ok"),
            _asst("完了しました。"),
        ])
        code, err = _run(t_frag, "sid-frag", proj)
        ok = code == 0 and "audit_english_drift" in err
        print(f"[{'PASS' if ok else 'FAIL'}] fragmented_narration_detected: exit={code} stderr_has_warn={'audit_english_drift' in err}")
        if not ok:
            failures.append("fragmented_narration_detected")

        # [control: 保守性] マーカー無しの zero-CJK 英語片 (<6 連続語) は拾わない。
        # FP 抑制優先 (user 裁定) — 閾値を全体的に下げたのではないことを保証する。
        t_nomark = _write_transcript([
            _user("タスク"),
            _asst(_EN_NO_MARKER),
            _user("ok"),
            _asst("完了しました。"),
        ])
        code, err = _run(t_nomark, "sid-nomark", proj)
        ok = code == 0 and "audit_english_drift" not in err
        print(f"[{'PASS' if ok else 'FAIL'}] no_marker_short_english_not_flagged: exit={code} clean={'audit_english_drift' not in err}")
        if not ok:
            failures.append("no_marker_short_english_not_flagged")

        # [control: 保守性] マーカー有りでも <4 語の短片は拾わない (≥4 語 floor)。
        t_short = _write_transcript([
            _user("タスク"),
            _asst(_EN_MARKER_TOO_SHORT),
            _user("ok"),
            _asst("完了しました。"),
        ])
        code, err = _run(t_short, "sid-short", proj)
        ok = code == 0 and "audit_english_drift" not in err
        print(f"[{'PASS' if ok else 'FAIL'}] short_marker_segment_not_flagged: exit={code} clean={'audit_english_drift' not in err}")
        if not ok:
            failures.append("short_marker_segment_not_flagged")

        # [control] 全ターン日本語 → 警告しない。
        t2 = _write_transcript([
            _user("タスク"),
            _asst(_JA),
            _user("ok"),
            _asst("完了しました。"),
        ])
        code, err = _run(t2, "sid-ja", proj)
        ok = code == 0 and "audit_english_drift" not in err
        print(f"[{'PASS' if ok else 'FAIL'}] all_japanese_no_warning: exit={code} clean={'audit_english_drift' not in err}")
        if not ok:
            failures.append("all_japanese_no_warning")

        # [RED→GREEN item 3] cite の英語逐語引用 (blockquote) を地の文ドリフトと誤検出しない。
        # web-research の cite 規範は英語 verbatim を要求するので、引用ブロックは言語ロックの射程外。
        # 修正前は blockquote が未除去で _EN_PROSE にマッチし flag (RED)、blockquote 除去で clean (GREEN)。
        t_cite = _write_transcript([
            _user("これどう思う?"),
            _asst(_CITE_BLOCKQUOTE),
            _user("ok"),
            _asst("完了しました。"),
        ])
        code, err = _run(t_cite, "sid-cite", proj)
        ok = code == 0 and "audit_english_drift" not in err
        print(f"[{'PASS' if ok else 'FAIL'}] cite_blockquote_not_flagged: exit={code} clean={'audit_english_drift' not in err}")
        if not ok:
            failures.append("cite_blockquote_not_flagged")

        # [dedup] 同一 session で 2 回発火 → 2 回目は再警告しない (差分検出)。
        code1, err1 = _run(t1, "sid-dedup", proj)
        code2, err2 = _run(t1, "sid-dedup", proj)
        ok = ("audit_english_drift" in err1) and ("audit_english_drift" not in err2)
        print(f"[{'PASS' if ok else 'FAIL'}] dedup_no_rewarn_same_session: first_warn={'audit_english_drift' in err1} second_silent={'audit_english_drift' not in err2}")
        if not ok:
            failures.append("dedup_no_rewarn_same_session")

    total = 7
    passed = total - len(failures)
    print(f"\n{passed}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
