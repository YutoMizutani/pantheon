#!/usr/bin/env python3
"""Suppression-only dedup ledger for self-improvement PROPOSALs (re-proposal churn guard).

## なぜ存在するか
2026-06-20 の user 裁定で、自己改善エージェント由来の提案は永続キューを廃し in-session
決着 (未採用のままセッションが閉じたら却下・痕跡を残さない) に一本化した。これは
「消費主体未定義 → 滞留 → セッション越しに残る」欠陥を正しく断ったが、副作用として
**却下の記録も消えた** — self-reflection は前セッションで却下された提案を知らず、同じ提案を
毎セッション再生成して user の注意を繰り返し奪いうる (churn)。本台帳はその churn だけを断つ。

## キューではない (2026-06-20 の懸念を再生しない)
- **抑制専用**: 台帳を読むのは「同じ提案を*出さない*」ためだけ。drain する主体が要らず、
  新規 action を一切生まない。退役したキューの「滞留物が将来エージェントに拾われ *実行* される」
  懸念は、本台帳が action を生まない以上当たらない。
- **TTL 失効**: TTL を過ぎた記録は数えない & 書き込み時に prune するのでファイルが単調増加しない。
- 抑制は **silent でない** — self-reflection は抑制時に `proposed-suppressed:` の 1 行 note を出すので
  user は「以前提案され抑制された」ことに気づき、望むなら明示的に再依頼できる (逃げ道あり)。

## 使い方 (self-reflection が PROPOSAL を出す *前* に呼ぶ)
    python3 heaven/tools/proposal_ledger.py check-and-record \\
        --source "feedback_a,feedback_b" --target CLAUDE.local.md [--ttl-days 14] [--threshold 2]
  → 標準出力 1 行: `PROCEED count=<N>` か `SUPPRESS count=<N>`
     N = TTL 窓内に同 fingerprint が既に記録された回数 (今回分を含まない)。
     N >= threshold なら SUPPRESS (= これが (N+1) 回目の提案 → churn とみなし出さない)。
  この呼び出しは現提案の fingerprint を必ず台帳へ追記する (記録と判定を 1 手で行う)。

fingerprint = sha1( sorted(normalized source slugs) + "|" + basename(target) )。
source_memories と target が同じなら「同じ提案」とみなす (本文の言い回し差は無視)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
_SLUG = re.sub(r"[^A-Za-z0-9]", "-", _PROJECT_DIR)
# Same hermetic seam as the hooks (_paths.FRAME_TELEMETRY_DIR) so the test can
# redirect the ledger to a tmp dir.
_TELEM_DIR = os.environ.get("FRAME_TELEMETRY_DIR") or os.path.join(
    HOME, ".claude/projects", _SLUG, "telemetry")
LEDGER = os.path.join(_TELEM_DIR, "proposal_ledger.jsonl")
# Hard prune horizon — entries older than this are dropped on write regardless of
# the (shorter) suppression TTL, so the file never grows unbounded.
_PRUNE_DAYS = 90


def _norm_slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", s.strip().lower())


def fingerprint(source: str, target: str) -> str:
    slugs = sorted(_norm_slug(s) for s in source.split(",") if s.strip())
    key = ",".join(slugs) + "|" + os.path.basename(target.strip())
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _parse_ts(rec: dict):
    try:
        return datetime.fromisoformat(str(rec.get("ts", "")).replace("Z", "+00:00"))
    except Exception:
        return None


def _load():
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _now():
    return datetime.now(timezone.utc)


def check_and_record(source: str, target: str, ttl_days: int, threshold: int,
                     now=None) -> tuple[str, int]:
    now = now or _now()
    fp = fingerprint(source, target)
    recs = _load()
    ttl_cut = now - timedelta(days=ttl_days)
    prune_cut = now - timedelta(days=_PRUNE_DAYS)

    prior = 0
    kept = []
    for r in recs:
        ts = _parse_ts(r)
        if ts is None or ts >= prune_cut:  # keep unparseable + within prune horizon
            kept.append(r)
        if r.get("fp") == fp and ts is not None and ts >= ttl_cut:
            prior += 1

    kept.append({"ts": now.isoformat().replace("+00:00", "Z"), "fp": fp,
                 "source": source, "target": os.path.basename(target.strip())})
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    decision = "SUPPRESS" if prior >= threshold else "PROCEED"
    return decision, prior


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check-and-record")
    c.add_argument("--source", required=True, help="comma-separated source memory slugs")
    c.add_argument("--target", required=True, help="proposal target_file")
    c.add_argument("--ttl-days", type=int, default=14)
    c.add_argument("--threshold", type=int, default=2)
    args = ap.parse_args()

    if args.cmd == "check-and-record":
        decision, prior = check_and_record(
            args.source, args.target, args.ttl_days, args.threshold)
        print(f"{decision} count={prior}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
