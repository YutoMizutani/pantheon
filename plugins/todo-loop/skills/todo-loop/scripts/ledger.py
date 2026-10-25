#!/usr/bin/env python3
"""todo-loop の材料台帳 — ランナーが育つための入力（番号付きの材料）を貯め、積み足しが拾ったら外す。

材料 1 件 = JSON 1 行 {"id": "X-xxxxxxxx", "kind": "<種類>", "at": "<日時>", "text": "<中身>", "src": "<出所>"}。
  <state>/ledger/pending.jsonl   未処理の材料（積まれるか、直し済みと記録されるまで残る）
  <state>/ledger/resolved.jsonl  処理済み（{"id", "why", "at"}。手で足してもよい: 「もう直っている」「積まない」の理由つき）
  <state>/ledger/pending.md      積み足しのセッションが読む一覧（種類ごと）

集め方:
  - プロジェクトの道具: <state>/collect.sh が実行可能なら、プロジェクトのディレクトリで実行し、標準出力の JSON 行を材料にする。
    各行は少なくとも "text" を持つ。"id" が無ければ "kind" と "text" から M-xxxxxxxx を振る（同じ中身は同じ番号）。
  - 本体: <state>/runs/history.log から自動で足す（番号は H-xxxxxxxx）。
      同じストーリーの中断（→ doing）が 2 回以上 / blocked になった / 積み足しの失敗（refill ... fail）
外し方: TODO.md のストーリーの `- 範囲:` 欄に材料の番号を書くと、次の sweep で resolved に移る。

  python3 ledger.py collect --state <state> --todo <TODO.md>   # 集めて台帳に足し、sweep して pending.md を書く
  python3 ledger.py sweep   --state <state> --todo <TODO.md>   # 範囲 に番号が書かれた材料を resolved に移す
  python3 ledger.py list    --state <state>                    # 未処理を表示
  python3 ledger.py resolve --state <state> --id X-xxxxxxxx --why "<理由>"
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

ID_RE = re.compile(r"\b[A-Z]{1,3}-[0-9a-f]{8}\b")
HEAD_RE = re.compile(r"^## (S-\d+) — ", re.M)


def hid(prefix, *parts):
    return f"{prefix}-" + hashlib.sha1("\0".join(map(str, parts)).encode()).hexdigest()[:8]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def paths(state):
    d = os.path.join(state, "ledger")
    return d, os.path.join(d, "pending.jsonl"), os.path.join(d, "resolved.jsonl"), os.path.join(d, "pending.md")


def read_jsonl(p):
    out = []
    if os.path.exists(p):
        for l in open(p, encoding="utf-8"):
            try:
                d = json.loads(l)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("id"):
                out.append(d)
    return out


def write_jsonl(p, rows):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def from_collect(state, project):
    """<state>/collect.sh の出力（JSON 行）。失敗しても空で続ける"""
    sh = os.path.join(state, "collect.sh")
    if not os.access(sh, os.X_OK):
        return [], ""
    try:
        r = subprocess.run([sh], cwd=project, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], f"collect.sh を実行できない: {e}"
    out = []
    for l in r.stdout.splitlines():
        l = l.strip()
        if not l.startswith("{"):
            continue
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if not isinstance(d, dict) or not str(d.get("text", "")).strip():
            continue
        kind = str(d.get("kind", "材料"))
        d.setdefault("id", hid("M", kind, d["text"]))
        if not ID_RE.fullmatch(d["id"]):
            d["id"] = hid("M", d["id"])
        d.setdefault("kind", kind)
        d.setdefault("at", now())
        d.setdefault("src", "collect.sh")
        out.append(d)
    warn = "" if r.returncode == 0 else f"collect.sh rc={r.returncode}: {r.stderr.strip()[-200:]}"
    return out, warn


def from_history(state):
    """history.log から: 同じストーリーの中断 2 回以上・blocked 化・積み足しの失敗"""
    p = os.path.join(state, "runs", "history.log")
    if not os.path.exists(p):
        return []
    out, interrupts = [], {}
    story = re.compile(r"^(\S+) (S-\d+) \S+ \S+ (\S+) -> (\S+) (.*)$")
    refill = re.compile(r"^(\S+) refill (\S+) fail (.*)$")
    for l in open(p, encoding="utf-8", errors="replace"):
        l = l.rstrip("\n")
        m = story.match(l)
        if m:
            stamp, sid, before, after, rest = m.groups()
            if after == "doing":
                interrupts.setdefault(sid, []).append((stamp, rest))
                if len(interrupts[sid]) == 2:
                    out.append(dict(id=hid("H", "interrupt", sid, stamp), kind="中断の繰り返し", at=stamp, src="history.log",
                                    text=f"{sid} が終わらずに中断した回が 2 回以上（{interrupts[sid][0][0]}・{stamp}）。"
                                         f"1 SP の見積もり・指示書・道具のどれが原因かを見る。{rest}"))
            elif after == "blocked":
                out.append(dict(id=hid("H", "blocked", sid, stamp), kind="blocked 化", at=stamp, src="history.log",
                                text=f"{sid} が blocked になった（{before} → blocked）。runner の指示・道具で防げたかを見る。{rest}"))
            continue
        m = refill.match(l)
        if m:
            stamp, label, rest = m.groups()
            out.append(dict(id=hid("H", "refill-fail", stamp, label), kind="積み足しの失敗", at=stamp, src="history.log",
                            text=f"積み足し（{label}）が失敗し TODO.md を戻した: {rest}"))
    return out


def cited_in_scope(todo):
    """TODO.md の各ストーリーの 範囲 欄に書かれた材料の番号 → そのストーリー ID"""
    if not todo or not os.path.exists(todo):
        return {}
    text = open(todo, encoding="utf-8").read()
    out = {}
    heads = list(HEAD_RE.finditer(text))
    for i, m in enumerate(heads):
        body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        lines = body.splitlines()
        for j, l in enumerate(lines):
            if not l.startswith("- 範囲:"):
                continue
            scope = [l]
            for nl in lines[j + 1:]:
                if re.match(r"^- [^\s:]+:", nl) or nl.startswith("## "):
                    break
                scope.append(nl)
            for mid in ID_RE.findall("\n".join(scope)):
                out.setdefault(mid, m.group(1))
    return out


def sweep(state, todo):
    _, pend, res, _ = paths(state)
    pending = read_jsonl(pend)
    resolved_ids = {r["id"] for r in read_jsonl(res)}
    cited = cited_in_scope(todo)
    keep, moved = [], []
    for it in pending:
        if it["id"] in resolved_ids:
            continue
        if it["id"] in cited:
            moved.append(dict(id=it["id"], why=f"範囲に記載: {cited[it['id']]}", at=now()))
        else:
            keep.append(it)
    if moved:
        os.makedirs(os.path.dirname(res), exist_ok=True)
        with open(res, "a", encoding="utf-8") as f:
            for r in moved:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if len(keep) != len(pending) or not os.path.exists(pend):
        write_jsonl(pend, keep)
    return keep, moved


def render(state, pending):
    d, _, _, md = paths(state)
    os.makedirs(d, exist_ok=True)
    s = [f"# 材料台帳（未処理 {len(pending)} 件・{now()} 時点）", "",
         "積み足しのセッションが観点と合わせて読む。ストーリーの `- 範囲:` に番号を書くと次の回で外れる。",
         f"直し済み・積まないと決めた材料は `{os.path.join(d, 'resolved.jsonl')}` の末尾に "
         '`{"id": "X-xxxxxxxx", "why": "<理由>"}` を 1 行足す。', ""]
    groups = {}
    for it in pending:
        groups.setdefault(it.get("kind", "材料"), []).append(it)
    for kind, xs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        s.append(f"## {kind}（{len(xs)} 件）")
        s.append("")
        for it in sorted(xs, key=lambda x: str(x.get("at", "")), reverse=True):
            s.append(f"- **{it['id']}** {it.get('at', '')}（{it.get('src', '')}）: {str(it.get('text', ''))[:400]}")
        s.append("")
    if not pending:
        s.append("（未処理の材料は無い）")
    open(md, "w", encoding="utf-8").write("\n".join(s) + "\n")
    return md


def collect(state, todo):
    _, pend, res, _ = paths(state)
    project = os.path.dirname(os.path.abspath(todo)) if todo else os.getcwd()
    got, warn = from_collect(state, project)
    got += from_history(state)
    have = {it["id"]: it for it in read_jsonl(pend)}
    resolved_ids = {r["id"] for r in read_jsonl(res)}
    new = 0
    for it in got:
        if it["id"] in resolved_ids:
            continue
        if it["id"] not in have:
            new += 1
        have[it["id"]] = {**have.get(it["id"], {}), **it}
    write_jsonl(pend, list(have.values()))
    keep, moved = sweep(state, todo)
    md = render(state, keep)
    return dict(pending=len(keep), new=new, resolved=len(moved), md=md, warn=warn)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["collect", "sweep", "list", "resolve"])
    ap.add_argument("--state", required=True)
    ap.add_argument("--todo", default="")
    ap.add_argument("--id")
    ap.add_argument("--why", default="")
    a = ap.parse_args()
    if a.cmd == "collect":
        r = collect(a.state, a.todo)
        print(f"[ledger] 未処理 {r['pending']} 件（新規 {r['new']}・範囲に書かれて外した {r['resolved']}）: {r['md']}")
        if r["warn"]:
            print(f"[ledger] 注意: {r['warn']}", file=sys.stderr)
    elif a.cmd == "sweep":
        keep, moved = sweep(a.state, a.todo)
        render(a.state, keep)
        print(f"[ledger] 範囲に書かれて外した {len(moved)} 件: {' '.join(m['id'] for m in moved) or 'なし'}／未処理 {len(keep)} 件")
    elif a.cmd == "list":
        for it in read_jsonl(paths(a.state)[1]):
            print(f"{it['id']} [{it.get('kind', '')}] {it.get('at', '')}: {str(it.get('text', ''))[:160]}")
    else:
        if not a.id or not a.why:
            ap.error("resolve には --id と --why が要る")
        _, _, res, _ = paths(a.state)
        os.makedirs(os.path.dirname(res), exist_ok=True)
        with open(res, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(id=a.id, why=a.why, at=now()), ensure_ascii=False) + "\n")
        keep, _ = sweep(a.state, a.todo)
        render(a.state, keep)
        print(f"[ledger] {a.id} を処理済みにした／未処理 {len(keep)} 件")


if __name__ == "__main__":
    main()
