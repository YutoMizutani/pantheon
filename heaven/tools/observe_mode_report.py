#!/usr/bin/env python3
"""観察モード (observe) 宣言のある hook の棚卸し — 「昇格判断の主体」の受け皿。

docs/self-improvement-loop.md は「observe モード 1 日: 新 hook は block でなく audit
(観測のみ) で 1 日走らせ、false positive を見てから blocking に昇格する」と宣言するが、
**昇格させる主体とトリガがどこにも定義されていなかった**。しかも条件が「1〜2 日後」で、
これは単一セッションからは観測できない量 (自分が何日前に hook を足したかを知らない)。
結果、宣言は「1 日の観察」でも実態は「無期限の観察」になり、
audit_fetch_vs_sources は 122 日 / 36 発火を未消費のまま走り続けていた (2026-10-06 監査)。

tmp-retention の「削除する主体が未定義で 111GB 蓄積」/ rule-auditor の「週次に実行主体が
無かった」と同型の欠陥なので、同じ手当て — backstop がマーカーを出し、観測したセッションが
片付ける — に揃える。本スクリプトは **判断しない**: 日数と発火数を surface するだけで、
昇格 (enforce 化) も据え置き確定も user 承認を要する。

出力の読み方:
  - `observe_days`   : 最初の発火からの経過日数 (発火ゼロなら hook ファイルの mtime 起点)
  - `fires`          : hook_fires.jsonl 上の総発火数
  - `outcome` 内訳   : enforce レンズ (block/warn/transform) に入っているかの判別用
  - DUE 行           : 観察が十分長く (既定 14 日) 発火も溜まっている = 昇格判断が未了のもの

使い方:
  python3 heaven/tools/observe_mode_report.py            # 既定 (DUE 閾値 14 日)
  python3 heaven/tools/observe_mode_report.py --days 30  # 閾値を変える
  python3 heaven/tools/observe_mode_report.py --all      # 宣言のある hook を全部出す
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

# パス解決は telemetry_report.py と同じ env 由来の形に揃える
# (リポジトリ絶対パス・slug を直書きしない — .claude/rules/common/no-hardcoded-repo-path.md)。
HOME = os.path.expanduser("~")
REPO = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SLUG = re.sub(r"[^A-Za-z0-9]", "-", REPO)
TELEM = os.path.join(HOME, ".claude/projects", _SLUG, "telemetry/hook_fires.jsonl")
HOOK_DIRS = [
    os.path.join(REPO, ".claude/hooks"),
    os.path.join(REPO, ".claude/hooks/local"),
]

# 観察モードを宣言している印。docstring / コメントのどこかにあればよい。
OBSERVE_MARKERS = [
    r"observe\s*モード",
    r"OBSERVE_MODE",
    r"観測のみ",
    r"誤検知率を測って",
    r"enforce\s*化を判断",
    r"enforcing\s*に切り替え",
]
_OBSERVE_RE = re.compile("|".join(OBSERVE_MARKERS), re.IGNORECASE)

# 再発レンズ (rule_adoption_report.ENFORCING_OUTCOMES) に入る outcome。
ENFORCING_OUTCOMES = {"block", "warn", "transform"}


def _iter_fires():
    if not os.path.exists(TELEM):
        return
    with open(TELEM, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _parse_ts(raw: str):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def source_outcomes(src: str) -> set[str]:
    """hook のソースから record_fire(...) の第 2 引数 (outcome リテラル) を集める。

    過去の発火記録 (hook_fires.jsonl) の outcome は**書き換わらない**ので、
    ラベルを是正しても古い行は旧ラベルのまま残る。「今この hook が出すラベル」は
    記録ではなくソースを見ないと分からない (是正直後に『レンズ外』と誤報しないため)。
    """
    import ast as _ast

    out: set[str] = set()
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        return out
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name != "record_fire" or len(node.args) < 2:
            continue
        second = node.args[1]
        if isinstance(second, _ast.Constant) and isinstance(second.value, str):
            out.add(second.value)
    return out


def collect_observe_hooks() -> dict[str, dict]:
    """観察モードを宣言している hook を {name: {path, mtime}} で返す。"""
    found: dict[str, dict] = {}
    for d in HOOK_DIRS:
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(d, fname)
            try:
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
            except OSError:
                continue
            if not _OBSERVE_RE.search(src):
                continue
            name = fname[:-3]
            found[name] = {
                "path": os.path.relpath(path, REPO),
                "mtime": datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc),
                "source_outcomes": source_outcomes(src),
            }
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14,
                    help="この日数以上 観察が続いていたら DUE として挙げる (既定 14)")
    ap.add_argument("--all", action="store_true", help="DUE でないものも出す")
    args = ap.parse_args()

    hooks = collect_observe_hooks()
    if not hooks:
        print("observe-mode: 観察モード宣言のある hook は見つかりませんでした")
        return 0

    # 発火を hook 名で集計。
    first_seen: dict[str, datetime] = {}
    last_seen: dict[str, datetime] = {}
    counts: Counter = Counter()
    outcomes: dict[str, Counter] = {}
    for rec in _iter_fires():
        name = rec.get("hook") or rec.get("hook_name")
        if name not in hooks:
            continue
        ts = _parse_ts(rec.get("ts", ""))
        counts[name] += 1
        outcomes.setdefault(name, Counter())[rec.get("outcome", "?")] += 1
        if ts:
            if name not in first_seen or ts < first_seen[name]:
                first_seen[name] = ts
            if name not in last_seen or ts > last_seen[name]:
                last_seen[name] = ts

    now = datetime.now(timezone.utc)
    rows = []
    for name, meta in hooks.items():
        start = first_seen.get(name) or meta["mtime"]
        days = (now - start).days
        oc = outcomes.get(name, Counter())
        # 判定はソース (= 今これから出すラベル) で行う。記録側 oc は過去の実績で、
        # ラベル是正後も古い行は旧ラベルのまま残るため判定に使えない。
        declared = meta["source_outcomes"]
        in_lens = bool(declared & ENFORCING_OUTCOMES) if declared else any(
            k in ENFORCING_OUTCOMES for k in oc
        )
        rows.append({
            "hook": name,
            "path": meta["path"],
            "observe_days": days,
            "fires": counts.get(name, 0),
            "outcomes": dict(oc),
            "declared_outcomes": sorted(declared),
            "in_recurrence_lens": in_lens,
            "start_basis": "first_fire" if name in first_seen else "file_mtime",
        })
    rows.sort(key=lambda r: (-r["observe_days"], -r["fires"]))

    due = [r for r in rows if r["observe_days"] >= args.days and r["fires"] > 0]

    print(f"observe-mode report ({now:%Y-%m-%d}): 宣言 {len(rows)} 件 / DUE {len(due)} 件 "
          f"(閾値 {args.days} 日・発火 1 件以上)")
    print("  判断はしない — 昇格 (enforce 化) も据え置き確定も user 承認が要る")
    print()

    shown = rows if args.all else (due or rows[:3])
    for r in shown:
        flag = "⚠️ DUE" if r in due else "      "
        lens = "計測対象" if r["in_recurrence_lens"] else "★レンズ外 (再発集計に乗らない)"
        now_label = ",".join(r["declared_outcomes"]) or "?"
        print(f"{flag} {r['hook']}")
        print(f"        観察 {r['observe_days']} 日 ({r['start_basis']}) / 発火 {r['fires']} 件 "
              f"/ 現ラベル {now_label} ({lens})")
        print(f"        過去の記録 {r['outcomes']} ※是正前の行は旧ラベルのまま残る")
        print(f"        {r['path']}")

    if due:
        print()
        print("🔍 [observe-mode-due] 上記の hook は観察モードのまま閾値を超えています。"
              "観測したセッションは (a) 発火の真陽性/偽陽性を逐語で標本抽出し "
              "(b) enforce 昇格・据え置き確定・退役のいずれかを user へ提案すること "
              "(判断のみ・変更は user 承認後)。"
              "『★レンズ外』が付いた hook は現ラベルが再発集計 (block/warn/transform) に乗らないため、"
              "まず outcome ラベルの是正を検討する。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
