#!/usr/bin/env python3
"""self-reflection 係の 1 起動あたりの費用を、週ごと（または日ごと）に数える。

## なぜ存在するか
振り返り係の費用の大半は「土台（係の定義・規則・CLAUDE 系）× 往復回数」の送り直しで決まる。
係の定義や入力（transcript_digest.py）を変えたとき、往復の回数と送り直しの量が実際に減ったかを
前後で比べるための物差し。

## 数え方
- 対象: <Claude の projects ディレクトリ>/<slug>/*/subagents/*.meta.json のうち agentType=self-reflection（形=agent）と、
  reflect_launch.py が起動フォルダ heaven/.self-improvement/reflection/ から起こした回の記録（形=別プロセス）。
- usage は assistant のメッセージ id ごとに最後の記録を 1 回だけ数える（途中の記録の重複を除く）。
- 出力トークンは Claude Code 2.1.281〜2.1.306 の背景の係で少なく記録されていた（1 メッセージ 10〜30）。
  その期間の出力の値は比べない。往復・読み込み・書き込みは影響を受けていない。

    python3 heaven/tools/reflection_cost_report.py [--by day|week] [--since YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path


def _project_dir() -> Path:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or str(Path(__file__).resolve().parents[2])
    slug = "".join(c if c.isalnum() else "-" for c in root)
    return Path.home() / ".claude" / "projects" / slug


def _home_dir() -> Path:
    home = Path(__file__).resolve().parents[2] / "heaven" / ".self-improvement" / "reflection"
    slug = "".join(c if c.isalnum() else "-" for c in str(home))
    return Path.home() / ".claude" / "projects" / slug


def _usage(j: str, kind: str):
    last, ts = {}, None
    for line in open(j, encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ts = ts or d.get("timestamp")
        m = d.get("message") or {}
        if d.get("type") == "assistant" and m.get("usage"):
            last[m.get("id")] = m["usage"]
    if not last or not ts:
        return None
    u = list(last.values())
    return {
        "ts": ts,
        "kind": kind,
        "turns": len(u),
        "read": sum(x.get("cache_read_input_tokens", 0) for x in u),
        "write": sum(x.get("cache_creation_input_tokens", 0) + x.get("input_tokens", 0) for x in u),
        "out": sum(x.get("output_tokens", 0) for x in u),
        "first": u[0].get("cache_read_input_tokens", 0) + u[0].get("cache_creation_input_tokens", 0)
        + u[0].get("input_tokens", 0),
    }


def runs(base: Path, home: Path | None = None):
    for meta in glob.glob(str(base / "*" / "subagents" / "*.meta.json")):
        try:
            if json.load(open(meta)).get("agentType") != "self-reflection":
                continue
        except (OSError, ValueError):
            continue
        j = meta[: -len(".meta.json")] + ".jsonl"
        if os.path.exists(j) and (r := _usage(j, "agent")):
            yield r
    for j in glob.glob(str((home or _home_dir()) / "*.jsonl")):
        if r := _usage(j, "別プロセス"):
            yield r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--by", choices=("day", "week"), default="week")
    ap.add_argument("--since")
    args = ap.parse_args()
    groups = defaultdict(list)
    for r in runs(_project_dir()):
        if args.since and r["ts"][:10] < args.since:
            continue
        d = dt.date.fromisoformat(r["ts"][:10])
        key = d.isoformat() if args.by == "day" else f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        groups[(key, r["kind"])].append(r)
    if not groups:
        print("該当する起動なし")
        return 0
    print("期間       形          起動  往復(中央) 1往復目K  読込K(中央)  書込K(中央)  出力K(中央)")
    for k in sorted(groups):
        g = groups[k]
        med = lambda f: st.median(x[f] for x in g)
        print(f"{k[0]:10s} {k[1]:10s} {len(g):5d} {med('turns'):10.0f} {med('first') / 1e3:8.0f} "
              f"{med('read') / 1e3:11.0f} {med('write') / 1e3:11.0f} {med('out') / 1e3:11.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
