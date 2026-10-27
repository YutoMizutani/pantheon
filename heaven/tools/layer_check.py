#!/usr/bin/env python3
"""layer_check — 置き場（ディレクトリ）と層の取り違えを検査する。

Failure it prevents
-------------------
層をディレクトリでなくファイル単位の除外で分けると、新しく作った物が判断されないまま配る層のディレクトリに残り、
見つかるまで気づけない（2026-10-22: poe2 専用の探索器が heaven/tools に置かれ、user の指摘まで放置）。
また依存はどちら向きにも張らない: heaven/ のコードがプロジェクトのパスを指すと置き場の誤りで、プロジェクトが
heaven/ やルートの .claude/ を実行時に当てにすると、Pantheon を外したとき（projects/<name> で単体起動したとき）に
そのプロジェクトが動かなくなる — ルーティングのラッパーがプラットフォームに変わる入口。

Observable signal
-----------------
違反を 1 行ずつ出し、1 件でもあれば exit 1。relink-local.sh が毎回走らせる。

検査
----
1. .git/info/exclude に、配る層のディレクトリ（.claude/・docs/・heaven/tools/・plugins/・.claude-plugin/）の中を
   1 ファイル・1 フォルダずつ指す行がある（「**/」で始まる実行時の汎用パターンは対象外）
2. heaven/tools・heaven/lib のコード（.py .sh .js .swift）が projects/<実在するプロジェクト名> を含む（雛形の example-infra は除く）
3. projects/<name>/ のコードと設定（.py .sh .js .ts .swift .json）が heaven/ を指す、または ../ でルートの .claude/ へ上がる
   （.md は対象外 — 説明として書くのは依存ではない）

使い方
------
  python3 heaven/tools/layer_check.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRAME_DIRS = (".claude/", "docs/", "heaven/tools/", "plugins/", ".claude-plugin/")
CODE_SUFFIXES = {".py", ".sh", ".js", ".swift"}
PROJECT_SUFFIXES = CODE_SUFFIXES | {".ts", ".json"}
# 生成物・依存物・実行時状態は見ない（プロジェクトのコードではない）
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "tmp", "logs", "outputs", ".local", "dist", "build"}
UPWARD = re.compile(r"\bheaven/|(?:\.\./)+\.claude\b")


def exclude_violations() -> list[str]:
    ex = ROOT / ".git" / "info" / "exclude"
    if not ex.exists():
        return []
    out = []
    for i, line in enumerate(ex.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("**/"):
            continue
        if s.lstrip("/").startswith(FRAME_DIRS):
            out.append(f".git/info/exclude:{i}: {s} — 配る層のディレクトリの中をファイル単位で隠している"
                       "（手元だけの物は手元の層のディレクトリへ移す）")
    return out


def dependency_violations() -> list[str]:
    # example-infra は配る層の雛形でユーザーのプロジェクトではない（precommit_privacy と同じ扱い）
    projects = sorted(p.name for p in (ROOT / "projects").iterdir()
                      if p.is_dir() and not p.name.startswith(".") and p.name != "example-infra") if (ROOT / "projects").exists() else []
    if not projects:
        return []
    pat = re.compile(r"projects/(" + "|".join(re.escape(p) for p in projects) + r")\b")
    out = []
    for base in ("heaven/tools", "heaven/lib"):
        d = ROOT / base
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix not in CODE_SUFFIXES or "__pycache__" in f.parts or not f.is_file():
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if line.lstrip().startswith(("#", "//")):  # コメントは依存ではない
                    continue
                m = pat.search(line)
                if m:
                    out.append(f"{f.relative_to(ROOT)}:{i}: projects/{m.group(1)} — heaven/ のコードがプロジェクトを指している"
                               "（そのプロジェクトの知識を含むなら projects/ へ移す）")
    return out


def project_dependency_violations() -> list[str]:
    root = ROOT / "projects"
    if not root.exists():
        return []
    out = []
    for proj in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        stack = [proj]
        while stack:
            d = stack.pop()
            for f in sorted(d.iterdir()):
                if f.is_symlink():
                    continue
                if f.is_dir():
                    if f.name not in SKIP_DIRS:
                        stack.append(f)
                    continue
                if f.suffix not in PROJECT_SUFFIXES or f.stat().st_size > 1_000_000:
                    continue
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if line.lstrip().startswith(("#", "//")):
                        continue
                    m = UPWARD.search(line)
                    if m:
                        out.append(f"{f.relative_to(ROOT)}:{i}: {m.group(0)} — プロジェクトが Pantheon 側を当てにしている"
                                   "（単体起動で動かなくなる。部品はプロジェクトに複製するか plugin として配る）")
    return out


def main() -> int:
    v = exclude_violations() + dependency_violations() + project_dependency_violations()
    for line in v:
        print(line)
    print(f"layer_check: 違反 {len(v)} 件")
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main())
