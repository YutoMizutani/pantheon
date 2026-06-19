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

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "local" / "audit_english_drift_in_japanese_reply.py"

_DRIFT = "Now wire it into the handler and run the full suite again to confirm green output."
_JA = "ハンドラに配線してテストを再実行しました。"


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

        # [dedup] 同一 session で 2 回発火 → 2 回目は再警告しない (差分検出)。
        code1, err1 = _run(t1, "sid-dedup", proj)
        code2, err2 = _run(t1, "sid-dedup", proj)
        ok = ("audit_english_drift" in err1) and ("audit_english_drift" not in err2)
        print(f"[{'PASS' if ok else 'FAIL'}] dedup_no_rewarn_same_session: first_warn={'audit_english_drift' in err1} second_silent={'audit_english_drift' not in err2}")
        if not ok:
            failures.append("dedup_no_rewarn_same_session")

    total = 3
    passed = total - len(failures)
    print(f"\n{passed}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
