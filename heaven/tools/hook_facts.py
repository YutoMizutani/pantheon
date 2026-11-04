#!/usr/bin/env python3
"""hook 1 本の判定に要る事実（hook-review の手順 3・4 の材料）を、1 回で集めて出す。

hook_denial_report.py が出すのは拒否と空回りだけで、撤去・手直しの判定に要る残りの数字
（入れた日・自分の誤作動で直した回数・狙った失敗の前後）と、撤去の PROPOSAL に並べる「消す物」
（登録・本体・試験・状態ファイル・参照）は、毎回手で集めていた。この道具はそれを集める。判定はしない。

- 本体: `.claude/hooks/<名>.py`・`.local/hooks/<名>.py`。層は git の tracked か（`git ls-files`）で書く。
- 登録: `.claude/settings.json`・`.local/settings.local.json` の hooks を JSON として読み、event・matcher・行番号を出す。
- 入れた日: git の初出（`--diff-filter=A --follow`）。git に無ければファイルの作成時刻（無い OS では mtime）。
- 直しの記録: git の commit のうち件名に `fix` を含む物と、本体の docstring の日付つきの行。
  どれが「自分の誤作動の直し」かは読む人が決める（範囲を広げた直しも混ざる）。
- 試験: hook のフォルダの `tests/` のうち、名前を含む物。
- 状態ファイル: 本体の中の `RUNTIME_DIR / "<名>"`・`gettempdir()) / "<名>"`・`Path(__file__)…with_suffix(...)` 等の
  置き場の候補と、その実在と件数。
- 規則と効き目: hook_fires.jsonl でこの hook が記録した rule_id と件数。`rule_efficacy_report.py --rule` の判定。
- 参照: `rg -uu -l` を、規範と道具の置き場（`.claude`・`.local`・`heaven`・`docs`・ルートの CLAUDE 系・各プロジェクトの
  CLAUDE.md と TODO.md）に限って引く。実行記録（`*.log`・`*.jsonl`・`.bak`・`.archive`・`todo-loop` の runs/reports/staging）は外す。

読むだけ。何も書き換えない。

`--cost` だけは hook を実際に起動して、手元で最大の会話記録を読ませたときの最大メモリと秒を測る。応答のたびに
同時に走る物なので、拒否の数字に出ない hook が 1 本 1GB を使っていても、読むだけの検査では気づけない。
本体を読んだだけでは見えない読み込みも、実測には出る。
1 本ずつ順に動かし、作業用の HOME・起動フォルダ・記録の置き場に向け、`claude` などの外へ作用する命令は空の代役にする。
時間と使用量には上限があり、超えたら止めて「上限で停止」と出す。判定はしない。

使い方:
    python3 heaven/tools/hook_facts.py <hook の名前（.py は省略可）> [...] [--json]
    python3 heaven/tools/hook_facts.py --cost [hook の名前 ...] [--transcript <記録>] [--json]
    python3 heaven/tools/hook_facts.py --cost <本体のパス> --event PreToolUse --matcher Edit   # 提案中の複製を測る
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK_DIRS = (".claude/hooks", ".local/hooks")
SETTINGS = (".claude/settings.json", ".local/settings.local.json")
REF_PLACES = (".claude", ".local", "heaven", "docs", "CLAUDE.md", "CLAUDE.local.md", "projects/*/CLAUDE.md", "projects/*/TODO.md")
REF_EXCLUDE = ("*.log", "*.jsonl", "*.bak*", "**/.archive/**", "**/__pycache__/**",
               "**/todo-loop/runs/**", "**/todo-loop/reports/**", "**/todo-loop/staging/**")
_STATE_RES = (
    re.compile(r'RUNTIME_DIR\s*/\s*"([^"]+)"'),
    re.compile(r'gettempdir\(\)\)?\s*/\s*"([^"]+)"'),
    re.compile(r'with_suffix\(\s*"([^"]+)"'),
)
_DATED_RE = re.compile(r"(?<!\d)(20\d\d-\d\d-\d\d|\d\d-\d\d)(?!\d)")


def _run(cmd: list, timeout: int = 60) -> str:
    try:
        return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def find_body(name: str, repo: str = "") -> str:
    for d in HOOK_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(os.path.join(repo or REPO, p)):
            return p
    return ""


def registrations(name: str, repo: str = REPO) -> list:
    out = []
    for rel in SETTINGS:
        p = os.path.join(repo, rel)
        try:
            text = open(p, encoding="utf-8").read()
            data = json.loads(text)
        except (OSError, ValueError):
            continue
        lines = text.splitlines()
        for event, groups in (data.get("hooks") or {}).items():
            for g in groups or []:
                for h in g.get("hooks") or []:
                    cmd = str(h.get("command", ""))
                    if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}", cmd):
                        continue
                    lineno = next((i + 1 for i, l in enumerate(lines) if name in l and '"command"' in l
                                   and not any(r["file"] == rel and r["line"] == i + 1 for r in out)), 0)
                    out.append({"file": rel, "line": lineno, "event": event, "matcher": g.get("matcher", "")})
    return out


def introduced(body: str) -> str:
    log = _run(["git", "log", "--diff-filter=A", "--follow", "--format=%ad", "--date=short", "--", body]).split()
    if log:
        return log[-1] + "（git）"
    st = os.stat(os.path.join(REPO, body))
    t = getattr(st, "st_birthtime", st.st_mtime)
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d") + "（作成時刻）"


def fixes(body: str, text: str) -> dict:
    commits = [l for l in _run(["git", "log", "--follow", "--format=%h %ad %s", "--date=short", "--", body]).splitlines()
               if re.search(r"\bfix", l.split(" ", 2)[-1] if l.count(" ") >= 2 else l)]
    m = re.match(r'\s*(?:#![^\n]*\n)?\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', text, re.S)
    doc = m.group(1) if m else ""
    dated = [l.strip()[:120] for l in doc.splitlines() if _DATED_RE.search(l)]
    return {"commits": commits, "docstring_dated": dated}


def tests_for(name: str, body: str) -> list:
    stem = name[:-3]
    d = os.path.join(REPO, os.path.dirname(body), "tests")
    out = []
    for p in sorted(glob.glob(os.path.join(d, "*.py"))):
        try:
            if stem in open(p, encoding="utf-8", errors="replace").read():
                out.append(os.path.relpath(p, REPO))
        except OSError:
            pass
    return out


def state_files(text: str, body: str) -> list:
    import tempfile
    stem = os.path.basename(body)[:-3]
    out = []
    for rx in _STATE_RES:
        for leaf in rx.findall(text):
            if rx is _STATE_RES[0]:
                cands = [os.path.join(REPO, ".claude", "runtime", leaf)]
            elif rx is _STATE_RES[1]:
                cands = [os.path.join(tempfile.gettempdir(), leaf)]
            else:
                cands = [os.path.join(REPO, os.path.dirname(body), stem + leaf)]
            for c in cands:
                n = len(os.listdir(c)) if os.path.isdir(c) else (1 if os.path.exists(c) else 0)
                shown = os.path.relpath(c, REPO) if c.startswith(REPO + os.sep) else c
                out.append({"path": shown, "exists": os.path.exists(c), "entries": n})
    return out


def fire_rules(stem: str) -> dict:
    sys.path.insert(0, os.path.join(REPO, ".claude", "hooks"))
    try:
        from _fire_counter import fires_log_path  # noqa: E402
        p = fires_log_path()
    except Exception:
        return {}
    cnt: dict = {}
    first = ""
    try:
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if str(d.get("hook", "")).split(":", 1)[0] == stem:
                cnt[d.get("rule_id", "")] = cnt.get(d.get("rule_id", ""), 0) + 1
                ts = str(d.get("ts", ""))[:10]
                first = min(first or ts, ts) if ts else first
    except OSError:
        return {}
    return {"rules": cnt, "first": first}


def efficacy(rules: list) -> list:
    if not rules:
        return []
    args = [sys.executable, os.path.join(REPO, "heaven", "tools", "rule_efficacy_report.py"), "--json"]
    for r in rules:
        args += ["--rule", r]
    try:
        data = json.loads(_run(args, timeout=180) or "null")
    except ValueError:
        return []
    rows = data.get("rows", data) if isinstance(data, dict) else (data or [])
    keep = ("rule_id", "verdict", "ratio", "birth", "early_rate", "recent_rate", "reason")
    return [{k: r[k] for k in keep if k in r} or r for r in rows if isinstance(r, dict)]


def references(stem: str) -> list:
    places = [p for pat in REF_PLACES for p in glob.glob(os.path.join(REPO, pat))]
    cmd = ["rg", "-uu", "-l", "--fixed-strings", stem]
    for g in REF_EXCLUDE:
        cmd += ["--glob", "!" + g]
    out = _run(cmd + [os.path.relpath(p, REPO) for p in places], timeout=90)
    return sorted(set(out.split()))


def facts(arg: str) -> dict:
    name = arg if arg.endswith(".py") else arg + ".py"
    stem = name[:-3]
    body = find_body(name)
    res = {"hook": name, "body": body, "registrations": registrations(name)}
    if not body:
        res["missing"] = True
        res["references"] = references(stem)
        return res
    text = open(os.path.join(REPO, body), encoding="utf-8", errors="replace").read()
    tracked = bool(_run(["git", "ls-files", "--", body]).strip())
    fr = fire_rules(stem)
    rules = fr.get("rules", {})
    res.update({
        "layer": "tracked" if tracked else "gitignored か未登録",
        "introduced": introduced(body),
        "first_fire": fr.get("first", ""),
        "fixes": fixes(body, text),
        "tests": tests_for(name, body),
        "state": state_files(text, body),
        "fire_rules": rules,
        "efficacy": efficacy(sorted(r for r in rules if r)),
        "references": references(stem),
    })
    return res


def render(f: dict) -> str:
    L = [f"## {f['hook']}"]
    if f.get("missing"):
        L.append("- 本体: 見つからない（撤去済みか名前の誤り）")
    else:
        L.append(f"- 本体: {f['body']}（{f['layer']}）")
        L.append(f"- 入れた日: {f['introduced']}・最初の発火の記録 {f['first_fire'] or '無し'}"
                 "（作成時刻は書き直しで新しくなるので、git に無い hook は早い方を使う）")
    regs = f["registrations"]
    L.append(f"- 登録: {len(regs)} 件" + "".join(f"\n  - {r['file']}:{r['line']} {r['event']} matcher={r['matcher'] or '（無し）'}" for r in regs))
    if f.get("missing"):
        L.append(f"- 参照: {len(f['references'])} 件" + "".join(f"\n  - {p}" for p in f["references"]))
        return "\n".join(L)
    fx = f["fixes"]
    L.append(f"- 件名に fix を含む commit: {len(fx['commits'])} 件" + "".join(f"\n  - {c}" for c in fx["commits"]))
    L.append(f"- docstring の日付つきの行: {len(fx['docstring_dated'])} 行" + "".join(f"\n  - {d}" for d in fx["docstring_dated"]))
    L.append(f"- 試験: {len(f['tests'])} 件" + "".join(f"\n  - {t}" for t in f["tests"]))
    L.append(f"- 状態ファイル: {len(f['state'])} 件" + "".join(
        f"\n  - {s['path']}（{'有り・' + str(s['entries']) + ' 件' if s['exists'] else '無し'}）" for s in f["state"]))
    L.append("- 発火の記録: " + (", ".join(f"{k or '（rule_id 無し）'} {v} 件" for k, v in sorted(f["fire_rules"].items())) or "無し"))
    L.append("- 効き目（rule_efficacy_report）: " + (
        "; ".join(json.dumps(e, ensure_ascii=False) for e in f["efficacy"]) or "発火の記録が無いので測れない"))
    L.append(f"- 参照: {len(f['references'])} 件" + "".join(f"\n  - {p}" for p in f["references"]))
    return "\n".join(L)


COST_SKIP_EVENTS = ("SessionStart",)  # 起動時に 1 回だけ走り、応答ごとには重ならない
_HOOK_PATH_RE = re.compile(r"hooks/(?:local/)?([\w.-]+\.py)")
_WHOLE_READ_RE = re.compile(r"read_text\([^)]*\)\s*\.splitlines\(\)|\.read\(\)\s*\.splitlines\(\)|\.readlines\(\)")
_READ_CODE_RE = re.compile(r"transcript_path|iter_lines|tail_lines")
# hook の本体を動かす前に、記録を開いたら印を残す監査を入れる（本体の読み込みの経路によらず分かる）
_WRAPPER = """
import os, runpy, sys
_t, _mark, _body = sys.argv[1:4]
def _audit(ev, args):
    if ev == "open" and args and str(args[0]) == _t:
        os.close(os.open(_mark, os.O_CREAT | os.O_WRONLY))
sys.addaudithook(_audit)
sys.argv = [_body]
sys.path[0] = os.path.dirname(_body)
runpy.run_path(_body, run_name="__main__")
"""
_STUBS = ("claude", "curl", "wget", "osascript", "gh", "open", "afplay", "say", "terminal-notifier",
          "launchctl", "pkill", "killall", "ssh", "scp", "code")


def registered_hooks(repo: str = REPO) -> tuple:
    """settings の登録から、測る hook と測れない登録を分けて返す。同じ hook は event ごとに 1 回。"""
    rows, unmeasured, seen = [], [], set()
    for rel in SETTINGS:
        try:
            data = json.load(open(os.path.join(repo, rel), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for event, groups in (data.get("hooks") or {}).items():
            if event in COST_SKIP_EVENTS:
                continue
            for g in groups or []:
                for h in g.get("hooks") or []:
                    cmd = str(h.get("command", ""))
                    m = _HOOK_PATH_RE.search(cmd)
                    body = find_body(m.group(1), repo) if m else ""
                    if not body:
                        unmeasured.append({"event": event, "command": cmd[:100]})
                    elif (body, event) not in seen:
                        seen.add((body, event))
                        rows.append({"hook": m.group(1), "body": os.path.join(repo, body), "event": event,
                                     "matcher": g.get("matcher", "") or ""})
    # plugin の hook は起動しない: 常駐の子を残す物があり（limit-resume の待機役）、測定が外へ作用してしまう
    for pat in ("plugins/*/hooks/hooks.json", "projects/*/.claude/hooks/hooks.json"):
        for p in sorted(glob.glob(os.path.join(repo, pat))):
            try:
                data = json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for event, groups in (data.get("hooks") or {}).items():
                for g in groups or []:
                    for h in g.get("hooks") or []:
                        unmeasured.append({"event": event, "command": f"{os.path.relpath(p, repo)}: {str(h.get('command', ''))[:80]}"})
    return rows, unmeasured


def default_transcript() -> str:
    paths = glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", "*", "*.jsonl"))
    return max(paths, key=os.path.getsize) if paths else ""


def make_sandbox(root: str) -> dict:
    """hook の書き込みと外への作用を作業用の場所へ向ける環境変数を作る。"""
    dirs = {k: os.path.join(root, k) for k in ("home", "project", "telemetry", "runtime", "bin")}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    for name in _STUBS:
        p = os.path.join(dirs["bin"], name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(p, 0o755)
    target = os.path.join(dirs["project"], "target.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("a\n")
    env = dict(os.environ, HOME=dirs["home"], CLAUDE_PROJECT_DIR=dirs["project"], FRAME_TELEMETRY_DIR=dirs["telemetry"],
               COURT_RUNTIME_DIR=dirs["runtime"], PYTHONPATH=os.path.join(REPO, ".claude", "hooks"), PATH=dirs["bin"] + os.pathsep + os.environ.get("PATH", ""))
    return {"env": env, "target": target, "project": dirs["project"]}


def _payload(event: str, matcher: str, transcript: str, box: dict) -> str:
    m = re.match(r"[A-Za-z_]+", matcher)
    return json.dumps({
        "session_id": "hook-cost-" + str(os.getpid()), "transcript_path": transcript, "hook_event_name": event,
        "cwd": REPO, "prompt": "ok", "stop_hook_active": False, "tool_name": m.group(0) if m else "Edit",
        "tool_input": {"command": "echo ok", "file_path": box["target"], "old_string": "a", "new_string": "b"},
        "tool_response": {},
    })


def _rss_mb(pid: int) -> float:
    try:
        return int(subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip() or 0) / 1024
    except (OSError, ValueError):
        return 0.0


def measure(body: str, event: str, matcher: str, transcript: str, box: dict, limit: float = 60, mem_limit_mb: float = 3000) -> dict:
    """1 本を起動して、最大メモリ（MB）と経過秒を返す。上限を超えたら止める。"""
    fd, mark = tempfile.mkstemp(prefix="opened-", dir=box["project"])
    os.close(fd)
    os.unlink(mark)
    t0 = time.monotonic()
    p = subprocess.Popen([sys.executable, "-c", _WRAPPER, transcript, mark, body], cwd=REPO, env=box["env"], stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        p.stdin.write(_payload(event, matcher, transcript, box).encode("utf-8"))
        p.stdin.close()
    except OSError:
        pass  # 読まずに帰った hook
    stopped, next_check = "", t0
    while True:
        pid, status, ru = os.wait4(p.pid, os.WNOHANG)
        if pid:
            break
        now = time.monotonic()
        if not stopped and now - t0 > limit:
            stopped = f"上限で停止（{limit:g} 秒）"
        if not stopped and now >= next_check:
            next_check = now + 0.5
            if _rss_mb(p.pid) > mem_limit_mb:
                stopped = f"上限で停止（{mem_limit_mb:g}MB）"
        if stopped:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            pid, status, ru = os.wait4(p.pid, 0)
            break
        time.sleep(0.01)
    p.returncode = os.waitstatus_to_exitcode(status)  # Popen の後始末に、回収済みと伝える
    unit = 1 if sys.platform == "darwin" else 1024  # ru_maxrss は macOS が byte、Linux が KiB
    return {"mb": round(ru.ru_maxrss * unit / 2**20, 1), "sec": round(time.monotonic() - t0, 2),
            "rc": p.returncode, "stopped": stopped, "opened": os.path.exists(mark)}


def whole_read_lines(text: str) -> list:
    return [i + 1 for i, line in enumerate(text.splitlines()) if _WHOLE_READ_RE.search(line)]


def other_readers(repo: str = REPO) -> list:
    """hook 以外で会話記録を丸ごと読む候補（道具・plugin。測らず、行だけ出す）。"""
    me = os.path.basename(__file__)
    out = []
    for pat in ("plugins/**/*.py", "heaven/tools/*.py", "projects/*/.claude/hooks/*.py"):
        for p in sorted(glob.glob(os.path.join(repo, pat), recursive=True)):
            if os.path.basename(p) == me or os.path.basename(p).startswith("test_"):
                continue
            try:
                lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            out += [f"{os.path.relpath(p, repo)}:{i + 1}" for i, l in enumerate(lines)
                    if _WHOLE_READ_RE.search(l) and re.search(r"transcript|jsonl", l)]
    return out


def cost_report(names: list, transcript: str, limit: float = 60, event: str = "Stop", matcher: str = "") -> dict:
    rows, unmeasured = registered_hooks()
    if names:
        # パスで渡した物（提案中の、scratchpad の複製）は登録に無いので、event を指定して測る
        files = [n for n in names if os.sep in n]
        names = [n for n in names if os.sep not in n]
        want = {n if n.endswith(".py") else n + ".py" for n in names}
        rows = [r for r in rows if r["hook"] in want]
        rows += [{"hook": n, "body": os.path.join(REPO, find_body(n)), "event": "Stop", "matcher": ""}
                 for n in sorted(want - {r["hook"] for r in rows}) if find_body(n)]
        rows += [{"hook": os.path.basename(n), "body": os.path.abspath(n), "event": event, "matcher": matcher}
                 for n in files if os.path.isfile(n)]
        unmeasured = []
    size_mb = os.path.getsize(transcript) / 2**20
    with tempfile.TemporaryDirectory(prefix="hook-cost-") as td:
        box = make_sandbox(td)
        for r in rows:
            text = open(r["body"], encoding="utf-8", errors="replace").read()
            r.update(measure(r["body"], r["event"], r["matcher"], transcript, box, limit=limit))
            r["whole_read"] = whole_read_lines(text)
            notes = [r["stopped"]] if r["stopped"] else []
            if r["whole_read"]:
                notes.append("丸読みの候補 " + ",".join(f"L{n}" for n in r["whole_read"]))
            if r["rc"] not in (0, 2) and not r["stopped"]:
                notes.append(f"異常終了 rc={r['rc']}（実測にならない）")
            if _READ_CODE_RE.search(text) and not r["opened"]:
                notes.append("記録を読むコードがあるが、この入力では読まなかった（実測にならない）")
            r["note"] = "・".join(notes)
            del r["body"]
    rows.sort(key=lambda r: -r["mb"])
    totals = {}
    for r in rows:
        t = totals.setdefault(r["event"], {"count": 0, "mb": 0.0})
        t["count"] += 1
        t["mb"] = round(t["mb"] + r["mb"], 1)
    return {"transcript": transcript, "size_mb": round(size_mb, 1), "rows": rows, "totals": totals,
            "unmeasured": unmeasured, "other_readers": other_readers()}


def render_cost(c: dict) -> str:
    L = [f"測定に使った記録: {c['transcript']}（{c['size_mb']}MB）", "",
         "| hook | event | 最大MB | 秒 | 記録 | 注記 |", "|---|---|---:|---:|---|---|"]
    L += [f"| {r['hook']} | {r['event']} | {r['mb']} | {r['sec']} | {'読んだ' if r['opened'] else '-'} | {r['note']} |" for r in c["rows"]]
    L += ["", "event ごとの合計（全部同時に走った場合。matcher で絞られる物があるので、これは上限）:"]
    L += [f"- {e}: {t['count']} 本・{t['mb']}MB" for e, t in c["totals"].items()]
    if c["unmeasured"]:
        L += ["", "未測定（起動していない登録・plugin の hook）:"]
        counts: dict = {}
        for u in c["unmeasured"]:  # plugin は 1 ファイルに何十本もあるので、ファイルごとの本数で出す
            key = u["command"].split(": ", 1)[0] if ".json: " in u["command"] else f"{u['event']}: {u['command']}"
            counts[key] = counts.get(key, 0) + 1
        L += [f"- {k}" + (f"（{n} 本）" if k.endswith(".json") else "") for k, n in counts.items()]
    if c["other_readers"]:
        L += ["", "hook 以外で記録を丸ごと読む候補（未測定）:"] + [f"- {p}" for p in c["other_readers"]]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("hooks", nargs="*", help="hook の名前（.py は省略可）。--cost なら省略すると登録済みの全部")
    ap.add_argument("--json", action="store_true", help="JSON で出す")
    ap.add_argument("--cost", action="store_true", help="起動して最大メモリと秒を測る")
    ap.add_argument("--transcript", help="--cost で読ませる記録（既定は手元で最大の物）")
    ap.add_argument("--limit", type=float, default=60, help="--cost の 1 本あたりの上限秒")
    ap.add_argument("--event", default="Stop", help="--cost でパスを渡した hook の event")
    ap.add_argument("--matcher", default="", help="--cost でパスを渡した hook の matcher（先頭の語を tool_name にする）")
    a = ap.parse_args(argv)
    if a.cost:
        transcript = a.transcript or default_transcript()
        if not transcript or not os.path.isfile(transcript):
            print("測定に使う会話記録が見つからない: " + (transcript or "~/.claude/projects"), file=sys.stderr)
            return 1
        c = cost_report(a.hooks, transcript, a.limit, a.event, a.matcher)
        print(json.dumps(c, ensure_ascii=False, indent=2) if a.json else render_cost(c))
        return 0
    if not a.hooks:
        ap.error("hook の名前か --cost が要る")
    out = [facts(h) for h in a.hooks]
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else "\n\n".join(render(f) for f in out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
