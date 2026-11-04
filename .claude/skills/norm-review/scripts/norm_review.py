#!/usr/bin/env python3
"""規範の棚卸し（norm-review）の決まった手順の部分 — 一覧・活動の関門・週次の積み込み・判定ファイルの検査。

判断（何を消す・置き換える）はしない。判断は Skill norm-review の手順で親（Fable）が行う。

  python3 norm_review.py inventory [--json]
      規範の一覧（hook・rules・CLAUDE 系の節・Skill・係）を出す。各行に区画番号。
  python3 norm_review.py activity --since <ISO>
      その時刻以降の、人の発話の件数と日数を数える（無人の回・下請けの指示は除く）。
  python3 norm_review.py gate --config <config.json> [--now <ISO>] [--dry-run]
      間隔・前回の未完了・人の活動・runner の稼働を確かめ、通れば今週の審理を TODO.md に 1 件積む。
  python3 norm_review.py check <判定ファイル> --list <審理一覧> [--max-removals N]
      判定ファイルの形を検査する（全件に判定・消す/下ろすの上限・沈黙だけを根拠にしていない）。

時間が経っただけで規範が減らないように、関門は「人の活動が無い週は積まない」、検査は
「消す・下ろすには発火 0 / 未使用以外の観測が要る」を機械で強制する。

config.json（ローカル層。repo ルートからの相対パス）:
  {"todo": "projects/<name>/TODO.md", "todo_state": "projects/<name>/.local/todo-loop",
   "state_dir": ".local/norm-review", "section": "規範の棚卸し（週次）",
   "interval_days": 7, "per_slice": 40, "min_days": 2, "min_utterances": 20,
   "max_removals": 3, "model": "fable"}
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import math
import os
import re
import sys

ROOT = os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR") or
                       os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
TRANSCRIPTS = os.path.expanduser(os.environ.get("NORM_REVIEW_TRANSCRIPTS", "~/.claude/projects"))

VERDICTS = ("残す", "置き換える", "まとめる", "下ろす", "消す", "保留")
STATES = ("提案中", "反映済み", "却下", "不要")
REMOVALS = ("下ろす", "消す")
NEEDS_EVIDENCE = ("置き換える", "まとめる", "下ろす", "消す")
# 沈黙（発火しない・読まれない）だけの根拠。これしか書いていない「観測」は消す・下ろすの根拠にならない
SILENCE = re.compile(r"(発火\s*0|0\s*(件|回)?\s*発火|未使用|使われていない|読まれていない|参照\s*0|touch\s*0|"
                     r"cold|COLD|zero-signal|発火なし|発火が無い|[0-9]+\s*日)")
RUNNER_MARKS = ("ストーリー ID:", "無人で起動", "人が見ていない", "送り出す係", "開始時刻:", "[BOT-E2E]")
JA = re.compile(r"[぀-ヿ一-鿿]")


# ---------- 一覧 ----------

def _rel(p):
    return os.path.relpath(p, ROOT)


def _sections(path, levels):
    """見出し（levels の深さ）ごとに (見出し, 本文) を返す。コードブロック内の # は見出しにしない。"""
    out, cur, buf, fence = [], None, [], False
    pat = re.compile(r"^(#{%d,%d}) (.+?)\s*$" % (min(levels), max(levels)))
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith("```"):
            fence = not fence
        m = None if fence else pat.match(line)
        if m and len(m.group(1)) in levels:
            if cur is not None:
                out.append((cur, "".join(buf)))
            cur, buf = m.group(2), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur, "".join(buf)))
    return out


def _sha(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def inventory():
    """[{id, kind, path, sha}]。id は区画の割り当てと前回との比較に使う安定な名前。"""
    items, seen = [], set()

    def add(kind, ident, path, text):
        if ident in seen:
            return
        seen.add(ident)
        items.append({"id": ident, "kind": kind, "path": _rel(path), "sha": _sha(text)})

    def files(pattern):
        return sorted(p for p in glob.glob(os.path.join(ROOT, pattern)) if "/.archive/" not in p)

    for pat in (".claude/hooks/*.py", ".local/hooks/*.py"):
        for p in files(pat):
            b = os.path.basename(p)
            if b.startswith("_") or b.startswith("test_"):
                continue
            add("hook", "hook:" + _rel(p), p, open(p, errors="replace").read())
    for pat in (".claude/rules/common/*.md", ".local/rules/*.md",
                ".claude/rules-ondemand/*.md", ".local/rules-ondemand/*.md"):
        for p in files(pat):
            if os.path.basename(p) == "README.md":
                continue
            add("rule", "rule:" + _rel(p), p, open(p, errors="replace").read())
    # プロジェクトの rules は雛形の写しが多い。rules/ からの相対パスが同じ物は 1 件にまとめ、
    # 写しの中身を全部つないだ sha にする（写し同士が食い違っても「変更」として拾う）
    groups = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "projects/*/.claude/rules/**/*.md"), recursive=True)):
        if "/.archive/" in p or os.path.basename(p) == "README.md":
            continue
        sub = _rel(p).split("/.claude/rules/", 1)[1]
        groups.setdefault(sub, []).append(p)
    for sub, ps in sorted(groups.items()):
        text = "".join(open(p, errors="replace").read() for p in ps)
        if len(ps) == 1:
            add("rule", "rule:" + _rel(ps[0]), ps[0], text)
        else:
            add("rule", f"rule:projects/*/.claude/rules/{sub}", ps[0], text)
            items[-1]["path"] = f"{_rel(ps[0])} ほか写し {len(ps) - 1} 本"
    for name, levels in (("CLAUDE.local.md", (2, 3)), ("CLAUDE.md", (2,))):
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            for h, body in _sections(p, levels):
                add("section", f"section:{name}#{h}", p, body)
    for p in files("projects/*/CLAUDE.md"):
        for h, body in _sections(p, (2,)):
            add("section", f"section:{_rel(p)}#{h}", p, body)
    for pat in (".claude/skills/*/SKILL.md", "plugins/*/skills/*/SKILL.md", "projects/*/.claude/skills/*/SKILL.md"):
        for p in files(pat):
            real = os.path.realpath(p)
            if real in seen:
                continue
            seen.add(real)
            add("skill", "skill:" + _rel(real), real, open(real, errors="replace").read())
    for p in files(".claude/agents/*.md"):
        add("agent", "agent:" + _rel(p), p, open(p, errors="replace").read())
    return items


def slice_of(ident, n):
    return int(hashlib.sha1(ident.encode()).hexdigest(), 16) % n


# ---------- 人の活動 ----------

def _parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _text(o):
    c = o.get("message", {}).get("content")
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return c.strip() if isinstance(c, str) else ""


def human_utterance(text):
    """人が打った発話か。無人の回の指示書・下請けへの英語の指示・system の挿入は除く。"""
    if not text:
        return False
    if text.startswith("<") and not text.startswith("<pasted_content"):
        return False
    if any(m in text[:400] for m in RUNNER_MARKS):
        return False
    t = re.sub(r"^\[From Discord\]\s*", "", text)
    return bool(JA.search(t)) or len(t) <= 20


def activity(since):
    """since 以降の人の発話 {utterances, days, sessions}。会話記録の直下（subagent のサブフォルダを除く）だけを見る。"""
    cut = since.timestamp()
    n, days, sessions = 0, set(), set()
    for p in glob.glob(os.path.join(TRANSCRIPTS, "*", "*.jsonl")):
        if os.path.getmtime(p) < cut:
            continue
        runner = None
        for line in open(p, errors="ignore"):
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") != "user" or o.get("isMeta") or o.get("toolUseResult"):
                continue
            t = _text(o)
            if not t:
                continue
            if runner is None and not t.startswith("<"):
                runner = any(m in t[:400] for m in RUNNER_MARKS)   # 書き出しが無人の回なら会話ごと除く
            if runner:
                break
            ts = _parse_ts(o.get("timestamp", ""))
            if ts is None or ts < since or not human_utterance(t):
                continue
            n += 1
            days.add(ts.astimezone().date().isoformat())
            sessions.add(os.path.basename(p))
    return {"utterances": n, "days": len(days), "sessions": len(sessions)}


# ---------- 週次の関門と積み込み ----------

HEAD_RE = re.compile(r"^## (S-(\d+)) — .+ \| (?:調査|実装) \| (?:fable|opus) \| ([\w-]+)\s*$", re.M)


def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def _save(path, st):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(st, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def notify(cfg, body):
    """config の notify（本文を標準入力で受けるコマンド。repo ルートで実行）へ送る。無ければ標準エラーに出すだけ。"""
    cmd = cfg.get("notify")
    print(body, file=sys.stderr)
    if not cmd:
        return
    import subprocess
    try:
        subprocess.run(cmd, shell=True, cwd=ROOT, input=body, text=True, timeout=120, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[norm-review] 通知に失敗: {e}", file=sys.stderr)


def _pid_alive(path):
    try:
        pid = int(open(path).read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _story(sid, cfg, k, n, date, listing, count, verdict):
    rel_list, rel_verdict = _rel(listing), _rel(verdict)
    return (
        f"\n## {sid} — 規範の棚卸し（週次・区画 {k + 1}/{n}・{date}） | 調査 | {cfg['model']} | todo\n"
        f"- 範囲: Skill `norm-review` の「週次の審理」の手順で、審理一覧 `{rel_list}`（{count} 件）を審理し、"
        f"判定を `{rel_verdict}` に書く。規範のファイルそのものは書き換えない（変更案は判定ファイルに差分で書く）。\n"
        f"- 完了条件: `python3 .claude/skills/norm-review/scripts/norm_review.py check {rel_verdict} "
        f"--list {rel_list} --max-removals {cfg['max_removals']}` が OK。\n"
        f"- 依存: なし\n- 外部作用: なし\n- 証跡:\n- 引き継ぎ:\n")


def gate(cfg, now, dry):
    state_dir = os.path.join(ROOT, cfg["state_dir"])
    sp = os.path.join(state_dir, "state.json")
    st = _load(sp, {})
    todo = os.path.join(ROOT, cfg["todo"])
    text = open(todo, encoding="utf-8").read()

    def log(msg):
        line = f"{now.isoformat(timespec='seconds')} {msg}"
        print(line)
        if not dry:
            os.makedirs(state_dir, exist_ok=True)
            open(os.path.join(state_dir, "gate.log"), "a").write(line + "\n")

    last = _parse_ts(st.get("last_enqueued", ""))
    if last and now - last < dt.timedelta(days=cfg["interval_days"]):
        return log(f"skip interval（前回 {last.date()}）")
    prev = st.get("last_story")
    if prev:
        m = [x for x in HEAD_RE.finditer(text) if x.group(1) == prev]
        if m and m[0].group(3) != "done":
            # 上限・失敗で審理が進まないまま放置されないよう、止まりが長引いたら 1 回だけ人に届ける
            if last and now - last > dt.timedelta(days=cfg["stall_days"]) and st.get("stall_notified") != prev:
                notify(cfg, f"[norm-review] 規範の審理 {prev} が {m[0].group(3)} のまま {(now - last).days} 日経ち、"
                            f"次の週の審理を積めていません。{cfg['todo']} の {prev} の 引き継ぎ 欄を見て、続行なら todo に戻してください。")
                if not dry:
                    st["stall_notified"] = prev
                    _save(sp, st)
            return log(f"skip previous（{prev} が {m[0].group(3)}）")
    since = last or now - dt.timedelta(days=cfg["interval_days"])
    act = activity(since)
    if act["days"] < cfg["min_days"] or act["utterances"] < cfg["min_utterances"]:
        return log(f"skip activity（{since.date()} 以降 人の発話 {act['utterances']} 件・{act['days']} 日。"
                   f"必要 {cfg['min_utterances']} 件・{cfg['min_days']} 日）")
    if cfg.get("todo_state") and _pid_alive(os.path.join(ROOT, cfg["todo_state"], "running.pid")):
        return log("skip busy（todo-loop が実行中。翌日に再試行）")

    items = inventory()
    n = max(1, math.ceil(len(items) / cfg["per_slice"]))
    k = st.get("next_slice", 0) % n
    prev_sha = st.get("sha", {})
    changed = [i for i in items if prev_sha and prev_sha.get(i["id"]) != i["sha"]]
    rotation = [i for i in items if slice_of(i["id"], n) == k]
    chosen = {i["id"]: i for i in changed}
    for i in rotation:
        chosen.setdefault(i["id"], i)
    date = now.astimezone().date().isoformat()
    listing = os.path.join(state_dir, "lists", f"{date}.txt")
    verdict = os.path.join(state_dir, "verdicts", f"{date}.md")
    lines = [f"# 審理一覧 {date}（区画 {k + 1}/{n}・全 {len(items)} 件中）",
             f"# 新規・変更 {len(changed)} 件（前回の積み込み以降）を先頭に、区画の {len(rotation)} 件を続ける。"]
    lines += [f"{i['id']}\t{'新規・変更' if i in changed else '巡回'}\t{i['path']}" for i in chosen.values()]
    ids = [int(x.group(2)) for x in HEAD_RE.finditer(text)]
    sid = f"S-{(max(ids) if ids else 0) + 1:02d}"
    story = _story(sid, cfg, k, n, date, listing, len(chosen), verdict)
    if dry:
        print("\n".join(lines[:5]))
        print(story)
        return log(f"dry-run {sid} 区画 {k + 1}/{n} 件数 {len(chosen)}")
    os.makedirs(os.path.dirname(listing), exist_ok=True)
    os.makedirs(os.path.dirname(verdict), exist_ok=True)
    open(listing, "w").write("\n".join(lines) + "\n")
    new = text.rstrip("\n") + "\n"
    if f"\n## {cfg['section']}\n" not in "\n" + text:
        new += f"\n## {cfg['section']}\n"
    new += story
    tmp = todo + ".norm-review.tmp"
    open(tmp, "w", encoding="utf-8").write(new)
    os.replace(tmp, todo)
    if sid not in [x.group(1) for x in HEAD_RE.finditer(open(todo, encoding="utf-8").read())]:
        return log(f"error 積んだ {sid} が TODO.md から読めない")
    st.update(last_enqueued=now.isoformat(timespec="seconds"), last_story=sid, next_slice=k + 1,
              sha={i["id"]: i["sha"] for i in items}, last_activity=act)
    _save(sp, st)
    return log(f"enqueued {sid} 区画 {k + 1}/{n} 件数 {len(chosen)}（新規・変更 {len(changed)}）"
               f" 人の発話 {act['utterances']} 件・{act['days']} 日")


# ---------- 判定ファイルの検査 ----------

def parse_verdicts(path):
    blocks, cur = {}, None
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^### (\S+.*?)\s*$", line)
        if m:
            cur = m.group(1)
            blocks[cur] = {}
            continue
        m = re.match(r"^- (判定|起訴|観測|弁護|理由|変更案|状態): ?(.*)$", line)
        if cur and m:
            blocks[cur][m.group(1)] = m.group(2).strip()
        elif cur and line.startswith("  ") and blocks[cur]:
            last = list(blocks[cur])[-1]
            blocks[cur][last] += " " + line.strip()
    return blocks


def check(path, listing, max_removals):
    want = [l.split("\t")[0] for l in open(listing, encoding="utf-8") if l.strip() and not l.startswith("#")]
    blocks = parse_verdicts(path)
    errs = []
    for ident in want:
        if ident not in blocks:
            errs.append(f"判定が無い: {ident}")
    removals = 0
    for ident, b in blocks.items():
        v = b.get("判定", "")
        if v not in VERDICTS:
            errs.append(f"{ident}: 判定「{v}」は {'/'.join(VERDICTS)} のどれでもない")
            continue
        if v in REMOVALS:
            removals += 1
        if v in NEEDS_EVIDENCE:
            obs = b.get("観測", "")
            if not obs:
                errs.append(f"{ident}: {v} に 観測 が無い")
            elif not SILENCE.sub("", obs).strip(" 　、。・,./()（）-:：0123456789"):
                errs.append(f"{ident}: {v} の 観測 が沈黙（発火 0・未使用・経過日数）だけ。"
                            "誤検知・user の上書き・食い違い・置き換え済みのどれかを逐語で示すか、保留にする")
            if not b.get("変更案"):
                errs.append(f"{ident}: {v} に 変更案 が無い")
            if b.get("状態", "") not in STATES:
                errs.append(f"{ident}: 状態「{b.get('状態', '')}」は {'/'.join(STATES)} のどれでもない")
    if removals > max_removals:
        errs.append(f"消す・下ろすが {removals} 件（上限 {max_removals}）。証拠の強い順に残し、残りは保留にする")
    for e in errs:
        print("NG", e)
    print("OK" if not errs else f"NG {len(errs)} 件", f"（判定 {len(blocks)} 件・一覧 {len(want)} 件・消す/下ろす {removals} 件）")
    return 0 if not errs else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("inventory")
    a.add_argument("--json", action="store_true")
    a.add_argument("--per-slice", type=int, default=40)
    a = sub.add_parser("activity")
    a.add_argument("--since", required=True)
    a = sub.add_parser("gate")
    a.add_argument("--config", required=True)
    a.add_argument("--now")
    a.add_argument("--dry-run", action="store_true")
    a = sub.add_parser("check")
    a.add_argument("verdict")
    a.add_argument("--list", required=True)
    a.add_argument("--max-removals", type=int, default=3)
    args = ap.parse_args()

    if args.cmd == "inventory":
        items = inventory()
        n = max(1, math.ceil(len(items) / args.per_slice))
        for i in items:
            i["slice"] = slice_of(i["id"], n)
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=1))
        else:
            for i in items:
                print(f"{i['slice']}\t{i['kind']}\t{i['id']}")
            kinds = {}
            for i in items:
                kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
            print(f"# 全 {len(items)} 件・区画 {n}（1 区画 {args.per_slice} 件目安） " +
                  " ".join(f"{k}={v}" for k, v in sorted(kinds.items())), file=sys.stderr)
    elif args.cmd == "activity":
        since = _parse_ts(args.since)
        if since is None or since.tzinfo is None:
            sys.exit("--since は時差つきの ISO（例 2026-10-18T00:00:00+09:00）")
        print(json.dumps(activity(since), ensure_ascii=False))
    elif args.cmd == "gate":
        cfg = {"todo_state": None, "state_dir": ".local/norm-review", "section": "規範の棚卸し（週次）",
               "interval_days": 7, "per_slice": 40, "min_days": 2, "min_utterances": 20,
               "max_removals": 3, "model": "fable", "stall_days": 14, "notify": None}
        now = _parse_ts(args.now) if args.now else dt.datetime.now().astimezone()
        try:
            cfg.update(json.load(open(os.path.join(ROOT, args.config))))
            gate(cfg, now, args.dry_run)
        except Exception as e:
            # 定時の無人実行なので、落ちたことを記録と通知の両方に残す（launchd.out だけでは誰も見ない）
            msg = f"{now.isoformat(timespec='seconds')} error {type(e).__name__}: {e}"
            try:
                d = os.path.join(ROOT, cfg["state_dir"])
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, "gate.log"), "a").write(msg + "\n")
            except Exception:
                pass
            notify(cfg, f"[norm-review] 週次の関門が落ちました: {msg}")
            sys.exit(1)
    elif args.cmd == "check":
        sys.exit(check(args.verdict, args.list, args.max_removals))


if __name__ == "__main__":
    main()
