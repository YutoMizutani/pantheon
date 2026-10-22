#!/usr/bin/env python3
"""別の Claude セッションが「いま何をしているか」を一覧にする（SendMessage で伝える相手を決めるため）。

背景（2026-10-18）: 別セッションの並列作業について user に訊かれ、答えるのに、どのセッションがその作業をしているかを
transcript の grep で探し、相手のサブエージェントの進み具合も jsonl を手で読んだ。送る経路（SendMessage・ListAgents）は
harness にあるが、ListAgents は名前と busy/idle しか出さず、「何をしているか」が分からない。

使い方:
  python3 heaven/tools/peers.py                 # 生きているセッション全部: 名前・状態・最後の user の発話・最後の発言・動いている係
  python3 heaven/tools/peers.py --grep <語>     # transcript（係の分も）にその語が出るセッションだけ（例: --grep 01_索引）
  python3 heaven/tools/peers.py <名前>          # 1 つを詳しく: 直近の発話・道具の呼び出し・動いている係の直近の動き
  --all で busy でない（idle の）セッションも詳しく出す。既定の一覧は全部出す。

データ: ~/.claude/sessions/<pid>.json（名前・状態・sessionId。pid が死んでいれば除く）と
       ~/.claude/projects/*/<sessionId>.jsonl・<sessionId>/subagents/agent-*.jsonl / *.meta.json（finished が無い係＝動いている）。
内部形式なので CLI の版で変わりうる。読めない欄は「?」で出し、落ちない。
"""
import glob, json, os, sys, time

HOME = os.path.expanduser("~")
SESS = os.path.join(HOME, ".claude", "sessions")
PROJ = os.path.join(HOME, ".claude", "projects")
TAIL = 3 * 1024 * 1024


def alive(pid):
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def tail_rows(path, size=TAIL):
    """jsonl の末尾 size バイトを行ごとに読む（先頭の切れた行は捨てる）"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); n = f.tell(); f.seek(max(0, n - size))
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if n > size:
        lines = lines[1:]
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return ""


def is_human(t):
    t = t.lstrip()
    return bool(t) and not t.startswith("<") and not t.startswith("Another Claude session") and not t.startswith("Base directory for this skill")


def short(s, n=110):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def ago(ts):
    """ISO の時刻 → 「N 分前」"""
    try:
        t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        m = int((time.time() - t) / 60)
        return f"{m} 分前" if m < 120 else f"{m // 60} 時間前"
    except Exception:
        return "?"


def events(rows):
    """(時刻, 種類, 文) の並び。種類 = user / peer / text / tool"""
    ev = []
    for r in rows:
        ts = r.get("timestamp", "")
        m = r.get("message") or {}
        if r.get("type") == "user":
            t = text_of(m.get("content"))
            if is_human(t):
                ev.append((ts, "user", t))
            elif "cross-session-message" in t:
                ev.append((ts, "peer", t))
        elif r.get("type") == "assistant":
            for x in m.get("content") or []:
                if x.get("type") == "text" and x.get("text", "").strip():
                    ev.append((ts, "text", x["text"]))
                elif x.get("type") == "tool_use":
                    i = x.get("input") or {}
                    d = i.get("description") or i.get("file_path") or i.get("command") or i.get("prompt") or ""
                    ev.append((ts, "tool", f"{x.get('name')}: {d}"))
    return ev


def transcript(sid):
    hits = glob.glob(os.path.join(PROJ, "*", sid + ".jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def running_agents(tp):
    """動いている係（meta に finished が無い）: (説明, 種類, 最後の動きの時刻, 最後の道具)"""
    out = []
    d = os.path.join(tp[:-6], "subagents")
    for meta in glob.glob(os.path.join(d, "*.meta.json")):
        try:
            mj = json.load(open(meta))
        except Exception:
            continue
        if mj.get("finished"):
            continue
        jl = meta[:-10] + ".jsonl"
        ev = events(tail_rows(jl, 256 * 1024))
        last = ev[-1] if ev else ("", "", "")
        out.append((mj.get("description", "?"), mj.get("agentType", "?"), last[0], last[2], os.path.basename(jl)[6:-6]))
    return sorted(out, key=lambda x: x[2])


def my_pids():
    """この道具を呼んだセッション（親プロセスをたどって最初に当たる CLI）を見分けるための祖先 pid"""
    out, p = set(), os.getpid()
    for _ in range(12):
        try:
            p = int(os.popen(f"ps -o ppid= -p {p}").read().strip() or 0)
        except ValueError:
            break
        if p <= 1:
            break
        out.add(p)
    return out


def sessions():
    out, me = [], my_pids()
    for f in glob.glob(os.path.join(SESS, "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if not isinstance(d.get("pid"), int) or not alive(d["pid"]):
            continue
        d["_tp"] = transcript(d.get("sessionId", ""))
        d["_me"] = d["pid"] in me
        out.append(d)
    return sorted(out, key=lambda d: d.get("updatedAt") or 0, reverse=True)


def grep_hit(tp, pat):
    b = pat.encode()
    paths = [tp] + glob.glob(os.path.join(tp[:-6], "subagents", "*.jsonl"))
    for p in paths:
        try:
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(8 << 20), b""):
                    if b in chunk:
                        return True
        except OSError:
            pass
    return False


def last_human(tp):
    """末尾に user の発話が無いとき（係の返事・別セッションの連絡が続いた長いセッション）に全体から最後の 1 つを探す"""
    last = None
    with open(tp, "rb") as f:
        for l in f:
            if b'"type":"user"' not in l[:200] and b'"type": "user"' not in l[:200]:
                continue
            try:
                r = json.loads(l)
            except Exception:
                continue
            t = text_of((r.get("message") or {}).get("content"))
            if r.get("type") == "user" and is_human(t):
                last = (r.get("timestamp", ""), "user", t)
    return [last] if last else []


def summary(d):
    tp = d["_tp"]
    head = f"{d.get('name', '?'):8} {d.get('status', '?'):5} pid {d.get('pid')} sid {d.get('sessionId', '?')[:8]}" + ("  ← このセッション" if d.get("_me") else "")
    if not tp:
        return head + "  （transcript 不明）"
    ev = events(tail_rows(tp))
    u = [e for e in ev if e[1] == "user"] or last_human(tp)
    t = [e for e in ev if e[1] == "text"]
    L = [head + (f"  最後の動き {ago(ev[-1][0])}" if ev else "")]
    L.append(f"    user: {short(u[-1][2]) if u else '（末尾 3MB に無し）'}" + (f"（{ago(u[-1][0])}）" if u else ""))
    if t:
        L.append(f"    発言: {short(t[-1][2])}")
    for desc, typ, ts, last, aid in running_agents(tp):
        L.append(f"    係 動作中: {desc}（{typ}・{aid[:8]}）最後 {ago(ts)}: {short(last, 70)}")
    return "\n".join(L)


def detail(d, n=15):
    tp = d["_tp"]
    print(summary(d)); print()
    if not tp:
        return
    for ts, k, s in events(tail_rows(tp))[-n:]:
        print(f"  {ts[11:19]} {k:4} {short(s, 140)}")
    for desc, typ, ts, last, aid in running_agents(tp):
        print(f"\n  係 {desc}（{typ}・{aid}）")
        jl = os.path.join(tp[:-6], "subagents", f"agent-{aid}.jsonl")
        for ts2, k, s in events(tail_rows(jl, 512 * 1024))[-8:]:
            print(f"    {ts2[11:19]} {k:4} {short(s, 120)}")


def main():
    a = sys.argv[1:]
    ss = sessions()
    if "--grep" in a:
        pat = a[a.index("--grep") + 1]
        ss = [d for d in ss if d["_tp"] and grep_hit(d["_tp"], pat)]
        if not ss:
            print(f"「{pat}」が出るセッションは無い（生きているものの中）"); return
    names = [x for x in a if not x.startswith("--") and (("--grep" not in a) or x != a[a.index("--grep") + 1])]
    if names:
        for d in ss:
            if d.get("name") in names or d.get("sessionId", "").startswith(tuple(names)):
                detail(d)
        return
    print(f"生きている Claude セッション {len(ss)} 個（新しく動いた順）。送るときは SendMessage(to=<名前>)\n")
    for d in ss:
        if d.get("status") == "busy" or "--all" in a or "--grep" in a or len(ss) <= 4:
            print(summary(d)); print()
        else:
            print(f"{d.get('name', '?'):8} {d.get('status', '?'):5} sid {d.get('sessionId', '?')[:8]}")


if __name__ == "__main__":
    main()
