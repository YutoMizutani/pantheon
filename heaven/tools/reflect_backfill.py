#!/usr/bin/env python3
"""reflect_backfill — 「ok」で閉じなかった過去セッションの自己改善を遡って回すための洗い出し。

detect_acceptance_signal.py と同じ窓判定（前回の振り返り以降の tool_use 数）を過去の
transcript に当て、まだ振り返っていないセッションを列挙する。後から手で起動した
self-reflection は対象 transcript に印を残さないので、台帳（ledger）で二重実行を防ぐ。

  python3 heaven/tools/reflect_backfill.py list --days 7 [--limit 10] [--exclude <sid>]
  python3 heaven/tools/reflect_backfill.py mark <sid> [--note ...]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude/hooks"))
import detect_acceptance_signal as das  # noqa: E402
from _paths import STATE_DIR, TELEMETRY_DIR  # noqa: E402

TRANSCRIPTS = STATE_DIR  # セッションの transcript (*.jsonl) は STATE_DIR 直下に並ぶ
LEDGER = TELEMETRY_DIR / "reflect_backfill_ledger.jsonl"
LIVE_SECONDS = 3600  # 最終更新 1 時間以内は進行中とみなして除外


def _done() -> set[str]:
    if not LEDGER.exists():
        return set()
    out = set()
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            out.add(json.loads(line)["session"])
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def _key(path: str) -> str | None:
    """同じ相談が別 session id に複製されている（再開・分岐）のを束ねる鍵 = 先頭 3 件の
    実 user 発話。短すぎる・定型（skill 展開・画像だけ）なら束ねない（None）。"""
    texts = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    msg = json.loads(line).get("message") or {}
                except (json.JSONDecodeError, AttributeError):
                    continue
                if msg.get("role") != "user":
                    continue
                c = msg.get("content")
                t = c if isinstance(c, str) else "".join(
                    b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
                ) if isinstance(c, list) else ""
                t = t.strip()
                if not t or t.startswith(("<", "Base directory", "[Image")):
                    continue
                texts.append(t[:200])
                if len(texts) == 3:
                    break
    except OSError:
        return None
    joined = "\n".join(texts)
    return joined if len(joined) >= 20 else None


def _done_keys(done: set[str]) -> set[str]:
    keys = set()
    for sid in done:
        p = TRANSCRIPTS / f"{sid}.jsonl"
        if p.exists() and (k := _key(str(p))):
            keys.add(k)
    return keys


def cmd_list(a) -> int:
    now = time.time()
    done = _done()
    done_keys = _done_keys(done)
    best: dict[str, dict] = {}  # 束ねた鍵ごとに一番大きい（＝一番続きまである）記録だけ残す
    rows = []
    for p in glob.glob(str(TRANSCRIPTS / "*.jsonl")):
        sid = Path(p).stem
        if sid in done or any(sid.startswith(x) for x in a.exclude):
            continue
        age = now - os.path.getmtime(p)
        if age < LIVE_SECONDS or age > a.days * 86400:
            continue
        feat = das._gate_features(p)
        if feat is None:
            continue
        fire, reason = das._gate_decision(feat)
        if not fire:
            continue
        row = {"session": sid, "transcript": p, "age_h": round(age / 3600, 1),
               "tool_use": feat["tool_use"], "real_user": feat["real_user"]}
        k = _key(p)
        if k is None:
            rows.append(row)
        elif k in done_keys:
            continue  # 同じ相談の別記録を既に振り返った
        elif k not in best or os.path.getsize(p) > os.path.getsize(best[k]["transcript"]):
            best[k] = row
    rows += best.values()
    rows.sort(key=lambda r: r["age_h"])  # 新しい順
    print(json.dumps({"remaining": len(rows), "batch": rows[: a.limit]}, ensure_ascii=False, indent=1))
    return 0


def cmd_mark(a) -> int:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": a.session,
                            "note": a.note}, ensure_ascii=False) + "\n")
    print(f"marked {a.session}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list")
    l.add_argument("--days", type=float, default=7)
    l.add_argument("--limit", type=int, default=10)
    l.add_argument("--exclude", action="append", default=[])
    l.set_defaults(fn=cmd_list)
    m = sub.add_parser("mark")
    m.add_argument("session")
    m.add_argument("--note", default="")
    m.set_defaults(fn=cmd_mark)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
