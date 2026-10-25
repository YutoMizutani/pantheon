#!/usr/bin/env python3
"""試験用の偽の採点者（TODO_LOOP_GRADE_CLAUDE か TODO_LOOP_GRADE_CODEX に渡す）。指示文の `## S-NN — ` から ID を取り、
FAKE_GRADE（JSON {"S-01": "合格", "S-02": "不一致", "S-03": null}）の値を `判定: <値>` の最終行として出す。null なら判定行を出さない。

claude の形: `-p --model <m> ... <指示文>` → 標準出力に書く。
codex の形: `exec ... -o <file> -` → 標準入力の指示文を読み、<file> に書く。`--version` には 1 行で答える。
FAKE_GRADE_LOG（無ければ FAKE_LOG）= 呼ばれた引数（--model の値と形）を 1 行ずつ追記するファイル。
runner 経由の試験では FAKE_LOG が偽の claude の指示書のディレクトリなので FAKE_GRADE_LOG を使う。
"""
import json
import os
import re
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("fake-codex 0.0")
    sys.exit(0)
codex = "-o" in args
prompt = sys.stdin.read() if codex else args[-1]
model = args[args.index("--model") + 1] if "--model" in args else ""
m = re.search(r"^## (S-\d+) — ", prompt, re.M)
sid = m.group(1) if m else ""
log = os.environ.get("FAKE_GRADE_LOG") or os.environ.get("FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"{sid} form={'codex' if codex else 'claude'} model={model}\n")
verdict = json.loads(os.environ.get("FAKE_GRADE", "{}")).get(sid)
body = f"### 主張と観測\n| # | 証跡 の主張 | 実行したコマンド | 観測 | 判定 |\n|---|---|---|---|---|\n| 1 | 試験 | true | 試験 | 一致 |\n\n### 完了条件\n試験\n"
if verdict is not None:
    body += f"\n判定: {verdict}\n"
if codex:
    open(args[args.index("-o") + 1], "w", encoding="utf-8").write(body)
else:
    sys.stdout.write(body)
