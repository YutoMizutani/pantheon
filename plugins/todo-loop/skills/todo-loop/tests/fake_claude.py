#!/usr/bin/env python3
"""試験用の偽の claude（TODO_LOOP_CLAUDE に渡す）。最後の引数の指示書を読み、FAKE_PLAN（JSON）の指定どおりに TODO.md を書き換える。

FAKE_PLAN = {"refill": {"<観点の回>": {"do": "append" | "break" | "nothing", "n": 1, "cite": ["Q-..."], "report": true}},
             "story":  {"<ID>": {"state": "done", "report": true}}}
FAKE_TODO = 書き換える TODO.md。FAKE_LOG = 指示書を 1 回ずつ保存するディレクトリ。
"""
import json
import os
import re
import sys

prompt = sys.argv[-1]
plan = json.load(open(os.environ["FAKE_PLAN"], encoding="utf-8"))
todo = os.environ["FAKE_TODO"]
logd = os.environ["FAKE_LOG"]
os.makedirs(logd, exist_ok=True)
n = len(os.listdir(logd))
open(os.path.join(logd, f"{n:02d}.txt"), "w", encoding="utf-8").write(prompt)


def header(name):
    m = re.search(rf"^{name}: (.*)$", prompt, re.M)
    return m.group(1).strip() if m else ""


def report(path, sid_list, title):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump([{"施策": title, "ストーリー": sid_list, "課題": "試験の課題", "やったこと": "試験でやったこと", "まとめ": "試験のまとめ",
                "つまづいたこと": "無し", "工夫したところ": "試験の工夫", "何が嬉しいのか": "試験が嬉しい", "発展可能性": "試験の先",
                "完了したかどうか": "完了"}], open(path, "w", encoding="utf-8"), ensure_ascii=False)


text = open(todo, encoding="utf-8").read()
sid = header("ストーリー ID")
if sid:
    act = plan.get("story", {}).get(sid, {"state": "done"})
    text = re.sub(rf"^(## {re.escape(sid)} — .+? \| )([\w-]+)(\s*)$", lambda m: m.group(1) + act["state"] + m.group(3), text, count=1, flags=re.M)
    open(todo, "w", encoding="utf-8").write(text)
    if act.get("report"):
        report(os.path.join(header("報告の置き場"), f"{sid}.json"), [sid], f"{sid} の施策")
    print(f"{sid} {act['state']} 試験")
    sys.exit(0)

label = header("観点の回")
act = plan.get("refill", {}).get(label, {"do": "nothing"})
nums = [int(x) for x in re.findall(r"^## S-(\d+) — ", text, re.M)]
nxt = max(nums or [0]) + 1
added = []
if act["do"] in ("append", "break"):
    if act["do"] == "break":
        text = "壊した先頭行\n" + text
    cite = " ".join(act.get("cite", []))
    for k in range(act.get("n", 1)):
        sid = f"S-{nxt + k:02d}"
        added.append(sid)
        text += (f"\n## {sid} — 積んだ物 {label} {k} | 実装 | opus | todo\n- 範囲: 試験 {cite}\n- 完了条件: true\n- 依存: なし\n"
                 f"- 外部作用: なし\n- 証跡:\n- 引き継ぎ:\n")
    open(todo, "w", encoding="utf-8").write(text)
if act.get("report") and added:
    report(os.path.join(header("報告の置き場"), header("報告のファイル名")), added, f"{label} の施策")
print(f"LENS-SUMMARY\n- 選んだ 3 件: {label} / x / y\n- 積んだ番号: {' '.join(added)}")
