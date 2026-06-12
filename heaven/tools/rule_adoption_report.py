#!/usr/bin/env python3
"""Telemetry consumer: read enforcement-rule fires through an *adoption* lens.

Motivation (自己改善ループの棚卸し機構の一部 (規範の adoption / 再発率の計測)):
    自動化の本質は「ヒット数」ではなく「実際に採用/内面化されたか」。
    既存の telemetry_report.py は fire 数で COLD/HOT を出し、**HOT を good (rule が
    active) として扱う**。だが enforcement rule にとって HOT は逆の意味を持つ:

      HOT (同じ rule が何度も発火) = その lesson が内面化されていない
                                    = 同じ behavior が再発し続けている = 低 adoption

    本レポートは fire を「再発パターン(recurrence)」で読み直し、enforcement rule の
    採用度を判定する。fire 数そのものでなく「再発が続いているか / 沈静化したか」が signal。
    これは empirical-tuning ledger + audit_regressions.py の『14日 hook 静粛=採用成功 /
    再発=regressed』判定を、tune 済みに限らず全 fired rule へ広げた standing report。

telemetry_report.py との違い (重複しない):
    telemetry_report = hook が計装/COLD/HOT か (生の fire 数の view)
    rule_adoption_report = fired rule の lesson が adopted(沈静化)か not-internalized(再発中)か

なぜ fire→user採用 の join をやらないか (観測に基づく判断):
    fire 記録に session_id が無く、transcript は数百 session 並行のため ts だけの
    retro-join は false-join 多発で不健全。fire は全て warn/audit (block 無し) で
    user でなく agent への nudge。よって「再発」という session 不要で健全な signal に絞る。
    fire→採用 の per-session join は forward 計装 (fire 記録に session_id 付与) が前提で、
    疎な現データ (11 fires) では時期尚早。

Deterministic — NO LLM。Read-only。破壊しない (再 tune/書き直しは提案のみ、人間判断)。

Usage:
    python3 tools/rule_adoption_report.py [--days 30] [--recent 7] [--repeat-days 3] [--quiet 14]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
_SLUG = re.sub(r"[^A-Za-z0-9]", "-", _PROJECT_DIR)
HOOKS_DIR = os.path.join(_PROJECT_DIR, ".claude/hooks")
TELEM = os.path.join(HOME, ".claude/projects", _SLUG, "telemetry/hook_fires.jsonl")
LEDGER = os.path.join(HOME, ".claude/projects", _SLUG, "memory/log_empirical_tuning_ledger.md")

# enforcement outcomes は agent の振る舞いを正そうとする (再発=lesson 不採用)。
# audit は観測のみ (再発は情報であって friction ではない)。
ENFORCING_OUTCOMES = frozenset({"block", "warn", "transform"})


def _load_fires():
    fires = []
    if not os.path.exists(TELEM):
        return fires
    with open(TELEM, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                fires.append(rec)
    return fires


def _parse_ts(ts: str):
    try:
        t = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def classify(stats: dict, now: datetime, recent: int, repeat_days: int, quiet: int) -> tuple[str, str]:
    """fired rule の adoption verdict を返す (verdict, note)。純関数。

    stats: {distinct_days, days_since_last, enforcing}
    """
    dd = stats["distinct_days"]
    since = stats["days_since_last"]
    enforcing = stats["enforcing"]

    if not enforcing:
        return ("audit-only", "audit outcome — 観測のみ。再発は friction でなく情報")
    if dd >= repeat_days and since <= recent:
        return ("not-internalized",
                f"{dd}日にわたり再発・直近{since}日に発火 = lesson が定着していない。"
                "rule 文言の再 tune か構造書き直し (規範→hook 化) の候補")
    if dd >= repeat_days and since >= quiet:
        return ("decaying",
                f"{dd}日再発したが直近{since}日は沈黙 = 内面化が進んだ可能性。"
                "もう数週沈黙が続けば adopted 確定")
    if dd == 1:
        return ("single-incident", f"1日のみ・{since}日前 = 判定材料不足 (fluke か新規)")
    return ("watch", f"{dd}日発火・直近{since}日前 = 経過観察")


def _ledger_tuned_rules() -> set[str]:
    """empirical-tuning ledger の Rows から tune 済み rule (target/category) を拾う。
    任意入力: empirical tuning の ledger があれば加味する。無ければ skip (exists() ガード済み)。
    現状 Rows は空のはずだが、将来 join できるよう読む。"""
    tuned: set[str] = set()
    if not os.path.exists(LEDGER):
        return tuned
    in_rows = False
    for line in open(LEDGER, errors="replace"):
        if line.strip().startswith("## Rows"):
            in_rows = True
            continue
        if in_rows and line.strip().startswith("|") and "---" not in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # schema: tune_id|tune_date|target_prompt|failure_categories|...
            if len(cells) >= 4 and cells[0] and cells[0].lower() != "tune_id":
                for tok in re.split(r"[,\s/]+", cells[2] + " " + cells[3]):
                    tok = tok.strip()
                    if tok:
                        tuned.add(tok)
    return tuned


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="集計ウィンドウ")
    ap.add_argument("--recent", type=int, default=7, help="直近この日数内の発火を『まだ再発中』とみなす")
    ap.add_argument("--repeat-days", type=int, default=3, help="この distinct 日数以上の発火を『再発』とみなす")
    ap.add_argument("--quiet", type=int, default=14, help="この日数以上沈黙で『内面化が進んだ』とみなす")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.days)

    per: dict[str, dict] = defaultdict(
        lambda: {"events": 0, "hits": 0, "days": set(), "first": None, "last": None,
                 "outcomes": set(), "hook": ""}
    )
    for rec in _load_fires():
        t = _parse_ts(rec.get("ts", ""))
        if t is None or t < cutoff:
            continue
        rid = rec.get("rule_id") or rec.get("hook") or "?"
        p = per[rid]
        p["events"] += 1
        p["hits"] += int(rec.get("count", 1) or 1)
        p["days"].add(t.date())
        p["outcomes"].add(rec.get("outcome", "?"))
        p["hook"] = rec.get("hook", p["hook"])
        if p["first"] is None or t < p["first"]:
            p["first"] = t
        if p["last"] is None or t > p["last"]:
            p["last"] = t

    tuned = _ledger_tuned_rules()

    rows = []
    for rid, p in per.items():
        distinct_days = len(p["days"])
        days_since_last = (now - p["last"]).days if p["last"] else 9999
        enforcing = bool(p["outcomes"] & ENFORCING_OUTCOMES)
        verdict, note = classify(
            {"distinct_days": distinct_days, "days_since_last": days_since_last, "enforcing": enforcing},
            now, args.recent, args.repeat_days, args.quiet,
        )
        rows.append({
            "rule_id": rid, "hook": p["hook"], "events": p["events"], "hits": p["hits"],
            "distinct_days": distinct_days, "days_since_last": days_since_last,
            "outcomes": "/".join(sorted(p["outcomes"])), "verdict": verdict, "note": note,
            "tuned": rid in tuned,
        })

    # 並び: not-internalized を最上位 (最優先で見るべき)、その中で再発多い順
    order = {"not-internalized": 0, "watch": 1, "decaying": 2, "single-incident": 3, "audit-only": 4}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), -r["distinct_days"], -r["hits"]))

    print(f"=== Rule adoption report (last {args.days}d) ===")
    print("解釈: enforcement rule の HOT(高発火) は good ではない。"
          "再発し続ける = lesson 未内面化 = 低 adoption。\n")
    print(f"fired rules: {len(rows)}\n")

    not_int = [r for r in rows if r["verdict"] == "not-internalized"]
    if not_int:
        print(f"-- NOT-INTERNALIZED ({len(not_int)}) — 再発中・lesson が定着していない "
              "(再 tune / 構造書き直し候補。実行は人間判断):")
        for r in not_int:
            tflag = " [tuned]" if r["tuned"] else ""
            print(f"   ● {r['rule_id']}{tflag}")
            print(f"       {r['hits']} hits / {r['distinct_days']}日 / 直近{r['days_since_last']}日前 "
                  f"[{r['outcomes']}]")
            print(f"       → {r['note']}")
        print()

    other = [r for r in rows if r["verdict"] != "not-internalized"]
    if other:
        print("-- その他 (verdict 別):")
        for r in other:
            print(f"   · {r['rule_id']:42} {r['verdict']:16} "
                  f"{r['hits']}hits/{r['distinct_days']}d/last{r['days_since_last']}d [{r['outcomes']}]")
        print()

    print(f"RULE_ADOPTION_SUMMARY fired={len(rows)} not_internalized={len(not_int)} "
          f"decaying={sum(1 for r in rows if r['verdict']=='decaying')} "
          f"single={sum(1 for r in rows if r['verdict']=='single-incident')} "
          f"audit_only={sum(1 for r in rows if r['verdict']=='audit-only')} days={args.days}")


if __name__ == "__main__":
    main()
