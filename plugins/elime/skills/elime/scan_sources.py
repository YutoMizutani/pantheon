#!/usr/bin/env python3
"""elime update の材料を集める: projects の一覧と、user が Claude に送った発話の抜粋。

判断はしない。何を「知っている」とみなすかは skill を動かすモデルが決める。

    python3 scan_sources.py --root <プロジェクト root> [--days 0=全期間] [--per-cwd 30] [--chars 160]
"""
import argparse
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path


def projects_index(root: Path) -> list[str]:
    lines = []
    idx = root / "PROJECTS.md"
    if idx.is_file():
        lines.append(f"## {idx.name}")
        lines.extend(idx.read_text(encoding="utf-8", errors="replace").splitlines()[:80])
    pdir = root / "projects"
    if pdir.is_dir():
        lines.append("## projects/*/CLAUDE.md の見出し")
        for md in sorted(pdir.glob("*/CLAUDE.md")):
            head = next((l for l in md.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()), "")
            lines.append(f"- {md.parent.name}: {head.lstrip('# ').strip()}")
    return lines


def user_memories() -> list[str]:
    """Claude Code の memory のうち user 自身についての物（frontmatter の type: user）の name と description。"""
    out = []
    for md in sorted((Path.home() / ".claude" / "projects").glob("*/memory/*.md")):
        head = md.read_text(encoding="utf-8", errors="replace")[:1500]
        if not re.search(r"^\s*type:\s*user\s*$", head, re.M):
            continue
        desc = re.search(r"^description:\s*(.+)$", head, re.M)
        out.append(f"- {md.stem}: {desc.group(1).strip() if desc else ''}")
    return out


def user_prompts(days: int, chars: int):
    """~/.claude/projects/*/*.jsonl から user が打った文字列の発話だけを返す（道具の返り・meta は除く）。"""
    since = time.time() - days * 86400 if days else 0
    base = Path.home() / ".claude" / "projects"
    for f in base.glob("*/*.jsonl"):
        if f.stat().st_mtime < since:
            continue
        try:
            fh = f.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
                    continue
                c = (d.get("message") or {}).get("content")
                if not isinstance(c, str):
                    continue
                text = " ".join(c.split())
                # harness が差し込む command / reminder / 自動投入の定型は user 本人の語ではない
                if not text or text.startswith(("<", "[BOT-E2E]", "Caveat:")):
                    continue
                yield d.get("cwd") or "?", d.get("timestamp") or "", text[:chars]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--days", type=int, default=0, help="0 = 残っている全期間")
    ap.add_argument("--per-cwd", type=int, default=30)
    ap.add_argument("--chars", type=int, default=160)
    a = ap.parse_args()

    print("# projects")
    print("\n".join(projects_index(Path(a.root).expanduser().resolve())) or "(なし)")

    print("\n# memory（type: user）")
    print("\n".join(user_memories()) or "(なし)")

    rows = list(user_prompts(a.days, a.chars))
    # 自動投入（todo-loop・定時の役割プロンプト等）は同じ書き出しを繰り返すので、数字をならした書き出しで定型を外す
    # （「ok」のような短い合図も外れるが、知識の材料にはならない）。
    # 人はパス・URL から書き出すことが多く、中継の道具が付ける [From Discord] のような札も先頭に入るので、
    # それらを外してから比べる。定型は「書き出しが同じで中身の違う文が 5 種以上」か「全く同じ文が 10 回以上」で見分ける
    # （人が同じ書き出しで言い換える・同じ文を送り直すのは数回なので残す。定型は数百種・数百回出る）
    lead = re.compile(r"^(?:\s*(?:\[[^\]]*\]|\S*/\S*))+\s*")
    prefix = lambda t: re.sub(r"\d+", "0", lead.sub("", t))[:20]
    exact = Counter(t for _, _, t in rows)
    variants = Counter(prefix(t) for t in exact)
    templated = lambda t: variants[prefix(t)] >= 5 or exact[t] >= 10
    by_cwd = defaultdict(list)
    for cwd, ts, text in rows:
        # 一時フォルダでの配線確認・実験は user の関心でなく道具の検査
        if not templated(text) and not cwd.startswith(("/tmp", "/private/tmp", "/private/var")):
            by_cwd[cwd].append((ts, text))
    span = f"直近 {a.days} 日" if a.days else "全期間"
    print(f"\n# user の発話（{span}・作業フォルダごと・新しい順・各 {a.per_cwd} 件まで・定型 {len(rows) - sum(map(len, by_cwd.values()))} 件を除外）")
    for cwd, items in sorted(by_cwd.items(), key=lambda kv: -len(kv[1])):
        items.sort(reverse=True)
        print(f"\n## {cwd}（{len(items)} 件）")
        seen = set()
        n = 0
        for ts, text in items:
            if text in seen:
                continue
            seen.add(text)
            print(f"- {ts[:10]} {text}")
            n += 1
            if n >= a.per_cwd:
                break


if __name__ == "__main__":
    main()
