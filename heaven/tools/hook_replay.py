#!/usr/bin/env python3
"""過去の Bash 入力に、今の PreToolUse hook を当て直して判定を数える（replay）。

hook_fires.jsonl は「hook が発火した」行しか残さないので、hook が黙って通した命令は数えられず、
偽陽性率も見逃し率も出せない。この道具は transcript の Bash tool_use の input を抜き、
hook が受けるのと同じ形の payload を作って今の hook を子プロセスで起動し、判定を区分ごとに数える。

- 対象の hook: `--hook` が無ければ `.claude/settings.json` と `.local/settings.local.json` の
  PreToolUse のうち、matcher が `Bash` に一致する物（`Bash` を含む正規表現。全一致の `""`・`*` は
  transcript や会話の状態を読む hook なので既定では除き、`--include-wildcard` で入れる）。
  command の `f="<path>"` から hook のパスを、`python3 "$f"` の後ろから引数（`--transform` 等）を取る。
  `$CLAUDE_PROJECT_DIR` は repo ルートに置き換える。本文が transcript_path を読む hook には表で ※ を付ける
  （transcript を渡さないので、会話の中で済んだ前提が見えず warn / block が実際より多く出る）。
- payload: `{"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": <元の input>,
  "session_id": <元の sessionId>, "cwd": <元の cwd>}`。transcript_path は渡さない。子の作業フォルダは
  元の cwd（無ければ repo ルート）。
- 区分: block = exit 2 か stdout の `permissionDecision` が deny / transform = exit 0 で stdout に
  `updatedInput` / warn = exit 0 で stderr か `additionalContext` がある / pass = それ以外の exit 0 /
  error = それ以外の exit code・時間切れ・起動失敗。
- 走査: `~/.claude/projects/<slug>/` 配下の *.jsonl を再帰で（subagent のサブフォルダを含む）。
  entry の timestamp が `--days` 以内の Bash tool_use を新しい順に `--limit` 件（tool_use の id で重複を除く）。

テレメトリを汚さない: 子プロセスの環境で `FRAME_TELEMETRY_DIR`（`.claude/hooks/_paths.py` の
逸らし口。`record_fire` の書き先がここから決まる）と `TMPDIR`（hook が tempfile 配下に置く
会話ごとの状態）を使い捨てのフォルダに向け、終了時に消す。hook 本体・settings・本番の
hook_fires.jsonl には書かない。

Usage:
    python3 heaven/tools/hook_replay.py --days 7 --limit 300
    python3 heaven/tools/hook_replay.py --hook block_timeout_binary_on_macos.py --days 30 --limit 5000 --out <md>
    python3 heaven/tools/hook_replay.py --days 30 --json     # 1 命令 × 1 hook ごとの結果を JSON で
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS_FILES = (".claude/settings.json", ".local/settings.local.json")
CATEGORIES = ("pass", "warn", "block", "transform", "error")
_F_RE = re.compile(r'f="([^"]+)"')
_ARGS_RE = re.compile(r'python3\s+"\$f"((?:\s+[^\s;&|]+)*)')


def transcripts_dir(repo: str) -> str:
    return os.path.expanduser("~/.claude/projects/" + re.sub(r"[^a-zA-Z0-9]", "-", repo))


def _matches_bash(matcher: str, include_wildcard: bool) -> bool:
    if matcher in ("", "*"):
        return include_wildcard
    try:
        return re.fullmatch(matcher, "Bash") is not None
    except re.error:
        return False


def _reads_transcript(path: str) -> bool:
    """hook の本文が transcript_path を読むか（読むなら replay の判定は実際と違いうる）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "transcript_path" in f.read()
    except OSError:
        return False


def load_hooks(repo: str, include_wildcard: bool = False) -> list[dict]:
    """settings の PreToolUse から Bash に掛かる hook を [{name, path, args, matcher, source}] で返す。"""
    hooks, seen = [], set()
    for rel in SETTINGS_FILES:
        try:
            with open(os.path.join(repo, rel), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for ent in (data.get("hooks") or {}).get("PreToolUse") or []:
            matcher = ent.get("matcher") or ""
            if not _matches_bash(matcher, include_wildcard):
                continue
            for h in ent.get("hooks") or []:
                cmd = h.get("command") or ""
                m = _F_RE.search(cmd)
                if not m:
                    continue
                path = m.group(1).replace("$CLAUDE_PROJECT_DIR", repo).replace("${CLAUDE_PROJECT_DIR}", repo)
                if path in seen:
                    continue
                seen.add(path)
                am = _ARGS_RE.search(cmd)
                args = shlex.split(am.group(1)) if am and am.group(1).strip() else []
                hooks.append({"name": os.path.basename(path), "path": path, "args": args,
                              "matcher": matcher, "source": rel, "stateful": _reads_transcript(path)})
    return hooks


def select_hooks(repo: str, names: list[str] | None, include_wildcard: bool) -> list[dict]:
    hooks = load_hooks(repo, include_wildcard=True if names else include_wildcard)
    if not names:
        return hooks
    out = []
    for n in names:
        base = os.path.basename(n)
        if not base.endswith(".py"):
            base += ".py"
        hit = [h for h in hooks if h["name"] == base]
        if hit:
            out.extend(hit)
            continue
        for cand in (n, os.path.join(repo, ".claude/hooks", base), os.path.join(repo, ".claude/hooks/local", base)):
            if os.path.isfile(cand):
                out.append({"name": base, "path": os.path.abspath(cand), "args": [], "matcher": "(未配線)",
                            "source": "--hook", "stateful": _reads_transcript(cand)})
                break
        else:
            raise SystemExit(f"hook が見つからない: {n}")
    return out


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def collect_commands(tdir: str, days: int, limit: int, now: datetime | None = None) -> list[dict]:
    """transcript から Bash tool_use を新しい順に limit 件。"""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    cutoff_epoch = cutoff.timestamp()
    found: dict[str, dict] = {}
    for fp in glob.glob(os.path.join(tdir, "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(fp) < cutoff_epoch:
                continue
            fh = open(fp, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"Bash"' not in line or '"tool_use"' not in line:
                    continue
                try:
                    ent = json.loads(line)
                except ValueError:
                    continue
                ts = _parse_ts(ent.get("timestamp") or "")
                if ts is None or ts < cutoff:
                    continue
                content = (ent.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not (isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Bash"):
                        continue
                    inp = c.get("input")
                    if not isinstance(inp, dict) or not isinstance(inp.get("command"), str):
                        continue
                    key = c.get("id") or f"{fp}:{len(found)}"
                    found.setdefault(key, {"ts": ts, "session_id": ent.get("sessionId") or "",
                                           "cwd": ent.get("cwd") or "", "tool_input": inp})
    rows = sorted(found.values(), key=lambda r: r["ts"], reverse=True)
    return rows[:limit]


def classify(code: int | None, stdout: str, stderr: str) -> str:
    if code is None:
        return "error"
    spec = {}
    try:
        out = json.loads(stdout) if stdout.strip() else {}
        spec = out.get("hookSpecificOutput") or {} if isinstance(out, dict) else {}
    except ValueError:
        pass
    if code == 2 or spec.get("permissionDecision") == "deny":
        return "block"
    if code != 0:
        return "error"
    if "updatedInput" in spec:
        return "transform"
    if stderr.strip() or spec.get("additionalContext"):
        return "warn"
    return "pass"


def run_one(hook: dict, row: dict, env: dict, repo: str, timeout: float) -> dict:
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": row["tool_input"],
               "session_id": row["session_id"], "cwd": row["cwd"]}
    cwd = row["cwd"] if row["cwd"] and os.path.isdir(row["cwd"]) else repo
    try:
        p = subprocess.run([sys.executable, hook["path"], *hook["args"]], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        code, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        code, out, err = None, "", f"(時間切れ {timeout}s)"
    except OSError as e:
        code, out, err = None, "", f"(起動失敗 {e})"
    return {"hook": hook["name"], "category": classify(code, out, err), "exit": code,
            "updated_input": '"updatedInput"' in out,
            "stderr": " ".join(err.split())[:120],
            "command": row["tool_input"]["command"], "ts": row["ts"].strftime("%Y-%m-%dT%H:%M:%SZ")}


def replay(hooks: list[dict], rows: list[dict], repo: str, jobs: int = 8, timeout: float = 30.0) -> list[dict]:
    scratch = tempfile.mkdtemp(prefix="hook_replay_")
    try:
        env = dict(os.environ)
        env["FRAME_TELEMETRY_DIR"] = os.path.join(scratch, "telemetry")
        env["TMPDIR"] = os.path.join(scratch, "tmp")
        env["CLAUDE_PROJECT_DIR"] = repo
        os.makedirs(env["TMPDIR"], exist_ok=True)
        tasks = [(h, r) for r in rows for h in hooks]
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            return list(ex.map(lambda t: run_one(t[0], t[1], env, repo, timeout), tasks))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _cell(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    s = s if len(s) <= n else s[: n - 1] + "…"
    return s.replace("|", "\\|").replace("`", "'")


def render(results: list[dict], hooks: list[dict], n_rows: int, args, k: int, seed: int) -> str:
    rng = random.Random(seed)
    by_hook: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_hook[r["hook"]].append(r)
    lines = [f"# hook replay（Bash {n_rows} 件 × hook {len(hooks)} 本、--days {args.days} --limit {args.limit}）", "",
             "| hook | 引数 | " + " | ".join(CATEGORIES) + " |",
             "|---|---|" + "---|" * len(CATEGORIES)]
    for h in hooks:
        c = Counter(r["category"] for r in by_hook[h["name"]])
        name = h["name"] + (" ※" if h.get("stateful") else "")
        lines.append(f"| {name} | {' '.join(h['args']) or '-'} | " + " | ".join(str(c[x]) for x in CATEGORIES) + " |")
    if any(h.get("stateful") for h in hooks):
        lines += ["", "※ transcript_path を読む hook。replay は transcript を渡さないので、会話の中で済んだ前提"
                  "（読んだ・答えた等）が見えず、warn / block は実際より多く出る。偽陽性率の材料にしない。"]
    lines.append("")
    for h in hooks:
        rs = by_hook[h["name"]]
        cats = [x for x in CATEGORIES if x != "pass" and any(r["category"] == x for r in rs)]
        if not cats:
            continue
        lines.append(f"## {h['name']}")
        for cat in cats:
            pool = [r for r in rs if r["category"] == cat]
            sample = rng.sample(pool, min(k, len(pool)))
            lines += ["", f"### {cat}（{len(pool)} 件中 {len(sample)} 件）", "", "| 時刻 | 命令 | stderr 先頭 |", "|---|---|---|"]
            for r in sorted(sample, key=lambda x: x["ts"]):
                lines.append(f"| {r['ts']} | `{_cell(r['command'])}` | {_cell(r['stderr'], 120) or '-'} |")
        lines.append("")
    total = Counter(r["category"] for r in results)
    lines.append("HOOK_REPLAY_SUMMARY commands=%d hooks=%d " % (n_rows, len(hooks))
                 + " ".join(f"{x}={total[x]}" for x in CATEGORIES))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=2000, help="新しい順に何件の命令を当てるか")
    ap.add_argument("--hook", action="append", help="hook のファイル名（複数可）。省略時は Bash の PreToolUse 全部")
    ap.add_argument("--include-wildcard", action="store_true", help="matcher が空・* の hook も入れる")
    ap.add_argument("-k", "--samples", type=int, default=10, help="区分ごとの標本数")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--repo", default=REPO, help="settings と hook を読む repo ルート")
    ap.add_argument("--transcripts", help="transcript のフォルダ（既定 ~/.claude/projects/<repo の slug>）")
    ap.add_argument("--out", help="md の書き先（省略時は stdout）")
    ap.add_argument("--json", action="store_true", help="1 命令 × 1 hook ごとの結果を JSON で出す")
    args = ap.parse_args(argv)

    repo = os.path.abspath(args.repo)
    hooks = select_hooks(repo, args.hook, args.include_wildcard)
    if not hooks:
        print("Bash に掛かる PreToolUse hook が settings に無い", file=sys.stderr)
        return 1
    rows = collect_commands(args.transcripts or transcripts_dir(repo), args.days, args.limit)
    results = replay(hooks, rows, repo, jobs=args.jobs, timeout=args.timeout)
    text = json.dumps(results, ensure_ascii=False, indent=1) if args.json else render(results, hooks, len(rows), args, args.samples, args.seed)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.out}")
        if not args.json:
            print(text.strip().splitlines()[-1])
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
