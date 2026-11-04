#!/usr/bin/env python3
"""週ごとの tool 呼び出し数（発火率の分母）を transcript から増分で数える。

hook_fires.jsonl の発火数だけでは、活動量の増減と規則の効き目を分けられない。
この道具は transcript の assistant 行に含まれる tool_use を ISO 週 × tool 名で数え、
発火率（発火 ÷ 呼び出し）の分母を出す。

- 週は UTC の ISO 週（hook_fires.jsonl の ts が UTC なので、それに合わせる）。
- 同じ tool_use が重複して書かれても（1 ファイル内でも、resume で前の履歴が別ファイルへ
  写された場合でも）、tool_use の id で 1 回に数える。実測で、ファイルをまたぐ
  同一 id は 80,268 件中 4,398 件（5.5%）あった。
- 走査するのは `~/.claude/projects/<slug>/*.jsonl`（トップレベルのみ。subagent の
  サブフォルダは含めない）。
- ファイルごとの mtime と size と件数をキャッシュに書き、変わったファイルだけ読み直す。
  消えたファイルの分はキャッシュから外す。
- 読むだけ。transcript は書き換えない。

Usage:
    python3 heaven/tools/tool_use_weekly.py            # JSON
    python3 heaven/tools/tool_use_weekly.py --table    # 人が読む表（直近 8 週）
    python3 heaven/tools/tool_use_weekly.py --table --weeks 0   # 全週
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

# 既定は道具の置き場（heaven/tools/）から求めた repo ルート。CLAUDE_PROJECT_DIR があればそれを優先する。
REPO = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSONL_DIR = os.path.expanduser("~/.claude/projects/" + re.sub(r"[^a-zA-Z0-9]", "-", REPO))
CACHE = os.path.expanduser("~/.claude/runtime/tool_use_weekly.cache.json")
CACHE_VERSION = 2


def iso_week(ts: str) -> str | None:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    y, w, _ = dt.astimezone(timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


def read_file(path: str) -> list[list[str]]:
    """1 ファイルの tool_use を [id, 週, tool 名] の並びで返す（ファイル内の同じ id は 1 回）。"""
    out: list[list[str]] = []
    seen: set[str] = set()
    with open(path, "rb") as f:
        for raw in f:
            if b'"tool_use"' not in raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            week = iso_week(rec.get("timestamp", ""))
            if week is None:
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tid = block.get("id") or ""
                if tid:
                    if tid in seen:
                        continue
                    seen.add(tid)
                out.append([tid, week, block.get("name") or "?"])
    return out


def count_file(path: str) -> dict[str, dict[str, int]]:
    """1 ファイルの {週: {tool 名: 件数}}。"""
    return aggregate([read_file(path)])


def aggregate(per_file: list[list[list[str]]]) -> dict[str, dict[str, int]]:
    """ファイルごとの [id, 週, tool 名] を合算する。同じ id は最初の 1 回だけ数える。"""
    total: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seen: set[str] = set()
    for rows in per_file:
        for tid, week, name in rows:
            if tid:
                if tid in seen:
                    continue
                seen.add(tid)
            total[week][name] += 1
    return {w: dict(sorted(total[w].items())) for w in sorted(total)}


def load_cache(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("version") == CACHE_VERSION:
            return data
    except (OSError, ValueError):
        pass
    return {"version": CACHE_VERSION, "files": {}}


def save_cache(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def collect(jsonl_dir: str, cache_path: str) -> tuple[dict[str, dict[str, int]], dict]:
    """全ファイルを合算した {週: {tool: 件数}} と、読み直した件数などの統計。"""
    cache = load_cache(cache_path)
    old = cache["files"]
    new: dict[str, dict] = {}
    stats = {"files": 0, "reread": 0, "reused": 0}
    for fp in sorted(glob.glob(os.path.join(jsonl_dir, "*.jsonl"))):
        try:
            st = os.stat(fp)
        except OSError:
            continue
        stats["files"] += 1
        ent = old.get(fp)
        if ent and ent.get("mtime") == st.st_mtime and ent.get("size") == st.st_size:
            new[fp] = ent
            stats["reused"] += 1
            continue
        try:
            rows = read_file(fp)
        except OSError:
            continue
        new[fp] = {"mtime": st.st_mtime, "size": st.st_size, "tools": rows}
        stats["reread"] += 1
    cache["files"] = new
    save_cache(cache_path, cache)

    # 並びをファイル名順に固定し、重複の数え先を実行ごとに変えない
    return aggregate([new[fp]["tools"] for fp in sorted(new)]), stats


def render_table(weekly: dict[str, dict[str, int]], weeks: int, top: int) -> str:
    keys = sorted(weekly)
    if weeks > 0:
        keys = keys[-weeks:]
    grand: dict[str, int] = defaultdict(int)
    for w in keys:
        for name, n in weekly[w].items():
            grand[name] += n
    cols = [n for n, _ in sorted(grand.items(), key=lambda x: -x[1])[:top]]
    header = ["週", "合計"] + cols + ["その他"]
    rows = [header]
    for w in keys:
        t = weekly[w]
        shown = sum(t.get(c, 0) for c in cols)
        total = sum(t.values())
        rows.append([w, str(total)] + [str(t.get(c, 0)) for c in cols] + [str(total - shown)])
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    return "\n".join("  ".join(c.rjust(widths[i]) for i, c in enumerate(r)) for r in rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--table", action="store_true", help="人が読む表で出す")
    ap.add_argument("--weeks", type=int, default=8, help="表に出す直近の週数（0 で全週）")
    ap.add_argument("--top", type=int, default=8, help="表の列に出す tool の数")
    ap.add_argument("--dir", default=JSONL_DIR, help="transcript のフォルダ")
    ap.add_argument("--cache", default=CACHE, help="キャッシュの置き場")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.dir):
        print(f"transcript のフォルダが無い: {args.dir}", file=sys.stderr)
        return 1
    weekly, stats = collect(args.dir, args.cache)
    if args.table:
        print(render_table(weekly, args.weeks, args.top))
        print(f"\nファイル {stats['files']} 本（読み直し {stats['reread']}・キャッシュ再利用 {stats['reused']}）", file=sys.stderr)
    else:
        print(json.dumps(weekly, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
