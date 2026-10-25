#!/usr/bin/env python3
"""todo-loop の承認待ちを、プロジェクトの承認の口 <state>/approve.sh へ渡す（opt-in）。

  python3 approvals_out.py --todo <TODO.md> --state <状態ディレクトリ> --project <プロジェクトのディレクトリ>

<state>/approve.sh が実行可能なときだけ動く。TODO.md の awaiting-approval 全件のうち、
<state>/approvals_sent.json に送信済みと記録が無い件（または 外部作用 欄が送った時から変わった件）を、
1 件ずつ `approve.sh <ID>` に次の形の標準入力で渡す:

  ID: S-37
  プロジェクト: <絶対パス>
  TODO: <TODO.md の絶対パス>
  見出し: ## S-37 — <題> | 実装 | opus | awaiting-approval
  手順: <TODO.md の状態語を approved に書き換える手順>
  外部作用:
  <外部作用 欄の全文（続きの行を含む）>

approve.sh は exit 0 で標準出力 1 行目に承認番号（A-…）、2 行目に配送の確認（sent / unconfirmed）を返す。
記録: approvals_sent.json = {"<ID>": {"approval", "sent_at", "ext_sha", "delivery"}}。失敗した件は記録せず次の起動で送り直す。
出力は 1 行（[approvals] sent=N skipped=M failed=K ...）。常に exit 0（runner の結果に影響させない）。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import field, stories  # noqa: E402


def heading(text, sid):
    m = re.search(rf"^## {re.escape(sid)} — .*$", text, re.M)
    return m.group(0).rstrip() if m else f"## {sid}"


def load_sent(path):
    if not os.path.exists(path):
        return {}
    try:
        d = json.load(open(path, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError) as e:
        print(f"[approvals] {path} が読めない（空として扱う）: {e}", file=sys.stderr)
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--todo", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--project", required=True)
    a = ap.parse_args()
    approve = os.path.join(a.state, "approve.sh")
    if not os.access(approve, os.X_OK):
        print("[approvals] approve.sh が無い（送らない）")
        return
    todo = os.path.abspath(a.todo)
    proj = os.path.abspath(a.project)
    waiting = [s for s in stories(todo) if s["状態"] == "awaiting-approval"]
    if not waiting:
        print("[approvals] 承認待ち 0 件")
        return
    text = open(todo, encoding="utf-8").read()
    sent_path = os.path.join(a.state, "approvals_sent.json")
    sent = load_sent(sent_path)
    n_sent = n_skip = n_fail = 0
    notes = []
    for s in waiting:
        sid = s["id"]
        ext = field(s["body"], "外部作用", multiline=True) or "（外部作用 欄が無い）"
        sha = hashlib.sha1(ext.encode("utf-8")).hexdigest()[:12]
        if sent.get(sid, {}).get("ext_sha") == sha:
            n_skip += 1
            continue
        head = heading(text, sid)
        step = (f"{todo} の見出し行「{head}」の末尾の状態語 awaiting-approval を approved に書き換える（Edit。他の行は変えない）。"
                f"外部作用の実行は次の runner（todo-loop）に任せる。書き換えたら grep -n '^## {sid} ' {todo} で approved になったことを確かめる。")
        stdin = (f"ID: {sid}\nプロジェクト: {proj}\nTODO: {todo}\n見出し: {head}\n手順: {step}\n外部作用:\n{ext}\n")
        try:
            r = subprocess.run([approve, sid], input=stdin, cwd=proj, capture_output=True, text=True, timeout=90)
            out = r.stdout.splitlines()
            ok = r.returncode == 0 and out and out[0].strip().startswith("A-")
            why = f"rc={r.returncode}" if r.returncode else "承認番号が無い"
            err = r.stderr
        except (OSError, subprocess.TimeoutExpired) as e:
            ok, out, why, err = False, [], type(e).__name__, str(e)
        if ok:
            delivery = out[1].strip() if len(out) > 1 and out[1].strip() else "unconfirmed"
            sent[sid] = {"approval": out[0].strip(), "sent_at": datetime.datetime.now().isoformat(timespec="seconds"),
                         "ext_sha": sha, "delivery": delivery}
            n_sent += 1
            notes.append(f"{sid}→{out[0].strip()}({delivery})")
        else:
            n_fail += 1
            notes.append(f"{sid}→失敗({why})")
            if err:
                print(f"[approvals] {sid}: {err.strip()[-200:]}", file=sys.stderr)
    if n_sent:
        tmp = sent_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sent, f, ensure_ascii=False, indent=1)
        os.replace(tmp, sent_path)
    print(" ".join([f"[approvals] sent={n_sent} skipped={n_skip} failed={n_fail}"] + notes))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 結果に影響させない
        print(f"[approvals] 失敗: {type(e).__name__}: {e}")
