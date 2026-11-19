#!/usr/bin/env python3
"""hook ごとの拒否と空回りを、transcript から数えて表にする。

hook_fires.jsonl は hook が自分で記録した発火しか持たず、記録しない hook や、止めた後に
Claude がどう動いたかは見えない。この道具は transcript の tool_result のうち、harness が
PreToolUse の拒否として返した行（本文が `PreToolUse:<Tool> hook error:` で始まる）を拾い、
止めた hook ごとに数える。

- hook の名前: 本文に入っている登録のコマンドから取る。新しい形は `[f="<path>/<名>.py"; ...]:`、
  古い形は `[python3 <path>/<名>.py]:`。どちらも無い行は、本文の先頭の句（`[...]` か、区切りまでの
  24 字）で束ねる。
- 持ち主: 今ある hook（`.claude/hooks/*.py`・`.local/hooks/*.py`）のうち、名前が一致する物。名前の無い束は、
  例文を句読点で切った 10 字以上の断片を各 hook の本文から探し、一致の多い物を持ち主とする。どれにも無ければ
  「見つからない」— 撤去済みか文面が変わった hook の行なので、今の hook の数字として読まない。
- 空回り: 同じ hook が、ツール名と引数が完全に一致する呼び出しを 2 回以上止めた回数（2 回目以降を数える）。
  止められた後に同じ入力で呼び直すこと自体は、読んでから書く・答えてから続けるなど正しい使い方でも
  起きるので数えない。止められた後も同じ入力で止められ続けたものだけが空回り。
- 最大連続: 1 つの入力が同じ hook に止められた回数の最大。
- API 要求の写しの行（type が `api-request-blob`）は数えない。同じ tool_result は 1 回だけ数える。
- 会話: 拒否のあった transcript の数と、空回りのあった transcript の数。
- 走査: `~/.claude/projects/<slug>/*.jsonl`（slug = repo の絶対パスの英数字以外を `-` にした物。
  repo は `CLAUDE_PROJECT_DIR`、無ければ作業フォルダ）。`--subagents` で subagent のサブフォルダも含める。
  entry の timestamp が `--since` より前の行は飛ばす（timestamp の無い行は数える）。

読むだけ。transcript も hook も書き換えない。

使い方:
    python3 heaven/tools/hook_denial_report.py [--since YYYY-MM-DD | --days N] [--top N] [--subagents] [--json]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone


def default_root() -> str:
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project))
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug)


def is_denial(text: str) -> bool:
    return text.startswith("PreToolUse:") and "hook error" in text[:60]


def sig(text: str) -> str:
    """拒否の本文から、止めた hook の名前（無ければ本文の先頭の句）を返す。"""
    t = re.sub(r"^PreToolUse:\w+ hook error:\s*", "", text.strip())
    m = re.search(r'f="[^"]*?([A-Za-z0-9_]+\.py)"', t[:300]) or re.search(r"python3 \S*?([A-Za-z0-9_]+\.py)\]", t[:300])
    if m:
        return m.group(1)
    m = re.match(r"\s*(\[[^\]]{2,40}\]|[^：:（(。\n]{2,24})", t)
    return (m.group(1) if m else t[:20]).strip()


def _body(text: str) -> str:
    """拒否の本文から、登録のコマンドの部分を外した hook の言い分だけを返す（例文用）。"""
    t = re.sub(r"^PreToolUse:\w+ hook error:\s*", "", text.strip())
    t = re.sub(r"^\[[^\n]*?\.py[^\n]*?\]:\s*", "", t)
    return re.sub(r"\s+", " ", t)


MISSING = "見つからない"


def load_hooks(repo: str) -> dict:
    """今ある hook の名前 → 本文。"""
    out = {}
    for d in (os.path.join(repo, ".claude", "hooks"), os.path.join(repo, ".local", "hooks")):
        for p in glob.glob(os.path.join(d, "*.py")):
            try:
                out.setdefault(os.path.basename(p), open(p, encoding="utf-8", errors="replace").read())
            except OSError:
                pass
    return out


def owner(name: str, sample: str, hooks: dict) -> str:
    """その行を出した、今ある hook の名前。無ければ MISSING。"""
    if name.endswith(".py"):
        return name if name in hooks else MISSING
    frags = [f for f in re.split(r"[「」『』（）()\[\]。、，,:：\s…]+", sample) if len(f) >= 10]
    best, hits = MISSING, 0
    for h, text in sorted(hooks.items()):
        n = sum(f in text for f in frags)
        if n > hits:
            best, hits = h, n
    return best


def _result_text(content) -> str:
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    return content or ""


def scan(files, since: str = "") -> dict:
    """transcript の並びを読み、hook ごとの集計を返す。"""
    stat = collections.defaultdict(lambda: {
        "denials": 0, "spins": 0, "sessions": set(), "spin_sessions": set(),
        "max_run": 0, "first": "", "last": "", "sample": "",
    })
    for f in files:
        seen = {}  # tool_use_id -> ツール名 + 引数
        cnt = collections.Counter()  # (hook, ツール名 + 引数) -> 止められた回数
        counted = set()  # 数えた tool_result の tool_use_id（同じ結果を 2 回数えない）
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = d.get("timestamp") or ""
                if since and ts and ts < since:
                    continue
                # API 要求の写しは、それまでの tool_use・tool_result を同じ id のまま含む。
                # 数えると 1 回の拒否が 2 回になり、同じ入力の再拒否（空回り）にも見える。
                if d.get("type") == "api-request-blob":
                    continue
                content = (d.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        seen[b.get("id")] = (b.get("name") or "") + json.dumps(b.get("input"), sort_keys=True, ensure_ascii=False)
                    elif b.get("type") == "tool_result":
                        rid = b.get("tool_use_id")
                        if rid is not None:
                            if rid in counted:
                                continue
                            counted.add(rid)
                        text = _result_text(b.get("content"))
                        if not is_denial(text):
                            continue
                        name = sig(text)
                        s = stat[name]
                        s["denials"] += 1
                        s["sessions"].add(f)
                        day = ts[:10]
                        if day:
                            s["first"] = min(s["first"] or day, day)
                            s["last"] = max(s["last"], day)
                        if not s["sample"]:
                            s["sample"] = _body(text)[:160]
                        key = seen.get(b.get("tool_use_id"))
                        if key is None:
                            continue
                        cnt[(name, key)] += 1
                        n = cnt[(name, key)]
                        s["max_run"] = max(s["max_run"], n)
                        if n >= 2:
                            s["spins"] += 1
                            s["spin_sessions"].add(f)
    return stat


def rows(stat: dict, top: int = 0, hooks: dict | None = None) -> list:
    out = []
    for name, s in sorted(stat.items(), key=lambda x: (-x[1]["denials"], x[0])):
        out.append({
            "hook": name,
            "owner": owner(name, s["sample"], hooks) if hooks is not None else "",
            "denials": s["denials"],
            "spins": s["spins"],
            "spin_rate": round(100 * s["spins"] / s["denials"]) if s["denials"] else 0,
            "sessions": len(s["sessions"]),
            "spin_sessions": len(s["spin_sessions"]),
            "max_run": s["max_run"],
            "first": s["first"],
            "last": s["last"],
            "sample": s["sample"],
        })
    return out[:top] if top else out


def render(table: list, since: str, total: int) -> str:
    lines = [
        f"期間: {since or '全期間'} 以降  拒否の合計 {total} 件",
        "",
        "| hook | 持ち主 | 拒否 | 同じ入力の再拒否（空回り） | 空回りのあった会話 / 拒否のあった会話 | 最大連続 | 期間 | 例文 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in table:
        sample = r["sample"].replace("|", "｜")[:80]
        own = r.get("owner") or ""
        own = own if own in ("", MISSING) else f"`{own}`"
        lines.append(
            f"| `{r['hook']}` | {own} | {r['denials']} | {r['spins']}（{r['spin_rate']}%） | {r['spin_sessions']} / {r['sessions']} | "
            f"{r['max_run']} | {r['first']}〜{r['last']} | {sample} |"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--since", help="この日付（YYYY-MM-DD）以降を数える")
    g.add_argument("--days", type=int, default=30, help="直近 N 日を数える（既定 30）")
    ap.add_argument("--root", default=None, help="transcript のフォルダ（既定は repo から導く）")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    help="持ち主を探す hook の repo（既定はこの道具の置き場の repo）")
    ap.add_argument("--top", type=int, default=20, help="上位 N 件だけ出す（0 で全部）")
    ap.add_argument("--subagents", action="store_true", help="subagent のサブフォルダも数える")
    ap.add_argument("--json", action="store_true", help="表の代わりに JSON で出す")
    a = ap.parse_args(argv)

    since = a.since or (datetime.now(timezone.utc) - timedelta(days=a.days)).strftime("%Y-%m-%d")
    root = a.root or default_root()
    pattern = os.path.join(root, "**", "*.jsonl") if a.subagents else os.path.join(root, "*.jsonl")
    files = sorted(glob.glob(pattern, recursive=a.subagents))
    if not files:
        print(f"transcript が見つかりません: {pattern}", file=sys.stderr)
        return 1
    stat = scan(files, since)
    total = sum(s["denials"] for s in stat.values())
    table = rows(stat, a.top, load_hooks(a.repo))
    if a.json:
        print(json.dumps({"since": since, "total": total, "rows": table}, ensure_ascii=False, indent=2))
    else:
        print(render(table, since, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
