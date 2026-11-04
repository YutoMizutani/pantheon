#!/usr/bin/env python3
"""norm_review.py の試験 — 一時ディレクトリの偽の repo と偽の会話記録だけを使う（実の TODO.md・記録に触れない）。

  python3 .claude/skills/norm-review/tests/test_norm_review.py
"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "norm_review.py")
FAILS = []


def ok(cond, name):
    print(("ok  " if cond else "NG  ") + name)
    if not cond:
        FAILS.append(name)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)


def run(root, tx, *args):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root, NORM_REVIEW_TRANSCRIPTS=tx)
    r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def utter(tx, sid, ts, text):
    p = os.path.join(tx, "proj", f"{sid}.jsonl")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({"type": "user", "timestamp": ts, "message": {"content": text}}, ensure_ascii=False) + "\n")


def repo(root):
    write(f"{root}/.claude/hooks/block_a.py", "# a\n")
    write(f"{root}/.claude/hooks/_lib.py", "# 部品\n")
    write(f"{root}/.claude/hooks/test_block_a.py", "# 試験\n")
    write(f"{root}/.claude/hooks/.archive/old.py", "# 退役\n")
    write(f"{root}/.claude/rules/common/r.md", "# r\n")
    write(f"{root}/projects/p1/.claude/rules/common/x.md", "x\n")
    write(f"{root}/projects/p2/.claude/rules/common/x.md", "x\n")
    write(f"{root}/projects/p1/CLAUDE.md", "# P1\n## 役割\n本文\n```\n## コード内\n```\n## 禁止\n本文\n")
    write(f"{root}/CLAUDE.local.md", "# L\n## 原則A\n本文\n### 派生B\n本文\n")
    write(f"{root}/projects/demo/TODO.md", "# TODO\n\n## S-07 — 前の作業 | 調査 | fable | done\n- 範囲: x\n")
    write(f"{root}/.local/norm-review/config.json", json.dumps({
        "todo": "projects/demo/TODO.md", "state_dir": ".local/norm-review",
        "min_days": 2, "min_utterances": 3, "per_slice": 100}))


def main():
    with tempfile.TemporaryDirectory() as d:
        root, tx = f"{d}/repo", f"{d}/tx"
        repo(root)
        os.makedirs(tx)

        rc, out = run(root, tx, "inventory", "--json")
        ids = [i["id"] for i in json.loads(out.split("\n# ")[0])] if rc == 0 else []
        ok("hook:.claude/hooks/block_a.py" in ids, "一覧: hook を拾う")
        ok(not any("_lib" in i or "test_block" in i or ".archive" in i for i in ids), "一覧: 部品・試験・退役を除く")
        ok("rule:projects/*/.claude/rules/common/x.md" in ids and not any("p1/.claude/rules" in i for i in ids),
           "一覧: プロジェクト rules の写しを 1 件にまとめる")
        ok("section:projects/p1/CLAUDE.md#禁止" in ids and not any("コード内" in i for i in ids),
           "一覧: CLAUDE.md の節（コードブロック内の見出しは除く）")
        ok("section:CLAUDE.local.md#派生B" in ids, "一覧: CLAUDE.local.md は ### まで")

        cfg = ".local/norm-review/config.json"
        todo = f"{root}/projects/demo/TODO.md"
        before = open(todo).read()
        utter(tx, "s1", "2026-10-20T01:00:00Z", "これ直して")
        rc, out = run(root, tx, "gate", "--config", cfg, "--now", "2026-10-25T05:30:00+09:00")
        ok("skip activity" in out and open(todo).read() == before, "関門: 人の活動が足りない週は積まない")

        utter(tx, "s1", "2026-10-22T01:00:00Z", "続けて")
        utter(tx, "s2", "2026-10-23T01:00:00Z", "なんで止まった？")
        utter(tx, "r1", "2026-10-23T02:00:00Z", "ストーリー ID: S-09 を実行する")
        utter(tx, "r1", "2026-10-23T02:01:00Z", "これは無人の回の続き")
        rc, out = run(root, tx, "activity", "--since", "2026-10-18T00:00:00+09:00")
        ok(json.loads(out)["utterances"] == 3, "活動: 無人の回の発話を数えない")

        rc, out = run(root, tx, "gate", "--config", cfg, "--now", "2026-10-25T05:30:00+09:00")
        text = open(todo).read()
        ok("enqueued S-08" in out and "## S-08 — 規範の棚卸し（週次・区画 1/1・2026-10-25） | 調査 | fable | todo" in text,
           "積み込み: 最大の ID + 1 で Fable の調査を積む")
        ok("\n## 規範の棚卸し（週次）\n" in text, "積み込み: 節の見出しを 1 回だけ足す")
        listing = f"{root}/.local/norm-review/lists/2026-10-25.txt"
        ok(os.path.exists(listing), "積み込み: 審理一覧を書く")

        rc, out = run(root, tx, "gate", "--config", cfg, "--now", "2026-10-28T05:30:00+09:00")
        ok("skip interval" in out, "関門: 間隔の内は積まない")
        rc, out = run(root, tx, "gate", "--config", cfg, "--now", "2026-11-02T05:30:00+09:00")
        ok("skip previous" in out, "関門: 前回の審理が done でなければ積まない")

        text = open(todo).read()   # 読んでから開き直す（"w" で先に開くと空になる）
        open(todo, "w").write(text.replace("2026-10-25） | 調査 | fable | todo", "2026-10-25） | 調査 | fable | done"))
        ok("## S-07 — 前の作業" in open(todo).read(), "試験の前提: done への書き換えで TODO が消えていない")
        write(f"{root}/.claude/hooks/block_new.py", "# 新しい hook\n")
        for i, day in enumerate(("2026-10-29", "2026-10-30", "2026-10-31")):
            utter(tx, "s3", f"{day}T01:00:00Z", f"作業 {i}")
        rc, out = run(root, tx, "gate", "--config", cfg, "--now", "2026-11-02T05:30:00+09:00")
        new_list = open(f"{root}/.local/norm-review/lists/2026-11-02.txt").read() if "enqueued" in out else ""
        ok("hook:.claude/hooks/block_new.py\t新規・変更" in new_list, "積み込み: 新しく足された規範を翌週の審理に入れる")
        ok(open(todo).read().count("\n## 規範の棚卸し（週次）\n") == 1, "積み込み: 節の見出しを重ねない")

        # 止まりの通知: 前回の審理が done にならないまま stall_days を超えたら 1 回だけ送る
        sent = f"{d}/sent.txt"
        c = json.load(open(f"{root}/{cfg}"))
        c.update(notify=f"cat >> {sent}", stall_days=14)
        write(f"{root}/{cfg}", json.dumps(c))
        run(root, tx, "gate", "--config", cfg, "--now", "2026-11-20T05:30:00+09:00")
        run(root, tx, "gate", "--config", cfg, "--now", "2026-11-21T05:30:00+09:00")
        body = open(sent).read() if os.path.exists(sent) else ""
        ok(body.count("[norm-review]") == 1 and "S-09" in body, "止まり: 14 日を超えたら 1 回だけ人に届ける")

        # 落ちたときの通知: 例外を gate.log と通知の両方に残し、終了コード 1
        os.remove(todo)
        rc, out = run(root, tx, "gate", "--config", cfg, "--now", "2026-12-01T05:30:00+09:00")
        body = open(sent).read()
        last = open(f"{root}/.local/norm-review/gate.log").read().splitlines()[-1]
        ok(rc == 1 and "落ちました" in body and " error " in last, "例外: 記録と通知に残す")

        v = f"{d}/v.md"
        lst = f"{d}/l.txt"
        write(lst, "# 一覧\nhook:a\t巡回\tp\nhook:b\t巡回\tp\nhook:c\t巡回\tp\n")
        write(v, "### hook:a\n- 判定: 残す\n- 起訴: なし\n### hook:b\n- 判定: 消す\n- 観測: 140 日 発火 0・未使用\n"
                 "- 変更案: .archive へ\n- 状態: 提案中\n")
        rc, out = run(root, tx, "check", v, "--list", lst)
        ok(rc == 1 and "判定が無い: hook:c" in out, "検査: 一覧の全件に判定が要る")
        ok("沈黙" in out, "検査: 発火 0・未使用だけを根拠に消させない")

        write(v, "### hook:a\n- 判定: 残す\n- 起訴: なし\n### hook:b\n- 判定: 消す\n"
                 "- 観測: 発火 0。さらに 別の会話で user が「この警告は誤り」と上書き、hook_replay で誤検知 4/4\n"
                 "- 変更案: .archive へ移し配線を外す\n- 状態: 提案中\n### hook:c\n- 判定: 保留\n")
        rc, out = run(root, tx, "check", v, "--list", lst)
        ok(rc == 0, "検査: 沈黙以外の観測があれば通す")
        rc, out = run(root, tx, "check", v, "--list", lst, "--max-removals", "0")
        ok(rc == 1 and "上限" in out, "検査: 消す・下ろすの週の上限")

    print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} failed'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
