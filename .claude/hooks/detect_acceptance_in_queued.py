#!/usr/bin/env python3
"""PreToolUse（全ツール）・Stop hook — 作業中に割り込みで届いた user の発話（queued_command）にも
自己改善ループの acceptance 判定を走らせる。

背景 (2026-10-21 sid 88fca185):
  detect_acceptance_signal.py は UserPromptSubmit にだけ付いている。作業中に届いた「ok」「lok」は
  queued_command の attachment として取り込まれ（queue-operation reason=absorbed_mid_turn）、
  UserPromptSubmit を通らない。そのため 1 セッションで ok×2・lok×2 が届いても reflection_gate.jsonl に
  そのセッションの行が 0 件で、振り返りが一度も起動しなかった（user から、自己改善が起動していないと指摘された）。

判定:
  transcript の末尾から、まだ処理していない human の queued_command を拾い、本文が acceptance 語なら
  detect_acceptance_signal と同じ gate（作業量・debounce・ログ）を通して起動の指示を出す。
  処理した source_uuid はセッション別に覚え、同じ発話で 2 回は出さない。
  PreToolUse では additionalContext で指示を渡す（ツールは止めない）。Stop では、未処理の acceptance が
  あるときだけ exit 2 で 1 回だけ続行させる（ターンの最後のツールの後に届いた ok を取りこぼさないため）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import detect_acceptance_signal as das  # noqa: E402

TAIL_LINES = 600


def _state_file(sid: str) -> Path:
    from _paths import RUNTIME_DIR  # noqa: E402
    safe = "".join(c for c in (sid or "") if c.isalnum() or c in "-_") or "nosession"
    return RUNTIME_DIR / "queued_acceptance_seen" / f"{safe}.txt"


def queued_acceptances(transcript: Path) -> list[tuple[str, str]]:
    """未処理かどうかに関係なく、末尾 TAIL_LINES 行にある human queued_command の (source_uuid, text) のうち acceptance 語のもの。"""
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]
    except Exception:
        return []
    out = []
    for raw in lines:
        try:
            d = json.loads(raw)
        except Exception:
            continue
        if d.get("type") != "attachment":
            continue
        att = d.get("attachment") or {}
        if att.get("type") != "queued_command" or (att.get("origin") or {}).get("kind") != "human":
            continue
        p = att.get("prompt")
        txt = p if isinstance(p, str) else " ".join(x.get("text", "") for x in (p or []) if isinstance(x, dict))
        if das._has_acceptance_signal(txt):
            out.append((att.get("source_uuid") or d.get("uuid") or txt, txt))
    return out


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    event = data.get("hook_event_name") or ""
    transcript = data.get("transcript_path") or ""
    sid = data.get("session_id") or ""
    if not transcript:
        return 0
    found = queued_acceptances(Path(transcript))
    if not found:
        return 0
    sf = _state_file(sid)
    seen = set(sf.read_text().split()) if sf.exists() else set()
    new = [(u, t) for u, t in found if u not in seen]
    if not new:
        return 0
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text("\n".join(sorted(seen | {u for u, _ in new})))

    feat = das._gate_features(transcript)
    if feat is not None:
        ok, reason = das._gate_decision(feat)
        if not ok:
            das._log_gate(sid, "skip", f"queued:{reason}", feat)
            return 0
    else:
        reason = "fire-failopen-no-features"
    if not das._check_and_set_debounce(sid):
        das._log_gate(sid, "debounce", f"queued:{reason}", feat)
        return 0
    das._log_gate(sid, "fire", f"queued:{reason}", feat)
    das.record_fire("feedback_classify_failure_saying_vs_judgement", "audit",
                    context="acceptance-signal reflection (queued mid-turn)")
    msg = ("作業中に届いた「" + new[-1][1] + "」は自己改善ループの acceptance 合図。\n"
           + das._REMINDER.format(transcript=transcript, sid=sid))
    if event == "Stop":
        sys.stderr.write(msg + "\n")
        return 2
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 起動役の失敗で作業を止めない
        sys.stderr.write(f"[detect_acceptance_in_queued] error: {exc}\n")
        sys.exit(0)
