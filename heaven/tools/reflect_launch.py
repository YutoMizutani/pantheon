#!/usr/bin/env python3
"""振り返りの係（self-reflection）を、土台の小さい別プロセスの `claude -p` で起こす。

## なぜ存在するか
係を親の Agent ツールで起こすと、repo の CLAUDE.md 系・規則・memory の索引（≒ 44K）が係の土台に乗り、
往復ごとに送り直される（1 往復の土台 ≒ 61K）。係の費用はほぼ「土台 × 往復の回数」で決まるので、
土台を削るのが一番効く。repo 内の dot フォルダ（heaven/.self-improvement/reflection/）を起動フォルダにし、
係の定義だけを CLAUDE.md として読ませ、上の階層の CLAUDE.md 系・memory・hook を設定で切ると ≒ 18K になる
（実測）。

## 起動フォルダ
- 起動のたびに作り直す（gitignore 済み）。中身は CLAUDE.md（係の定義 .claude/agents/self-reflection.md への
  相対 symlink）と settings.json（除外するパスは repo の置き場から求めた絶対パス）と runs/（各回の出力 JSON）。
- hook を切るのは必須: 切らないと係の中で受諾検知や通知の hook が動き、係が係を起こす・投稿する。

## hook の見直し（hook-review）を先に渡す
起動の前に、直近 30 日の hook の拒否の表（hook_denial_report.py）を作り、Skill hook-review の手順 2 の対象の行
（今ある hook の行のうち、拒否の上位 5 件と空回りの率が 10% 以上の物。持ち主が「見つからない」の行は外す）だけを
prompt に載せる。係はそれに hook-review の手順 2〜4 を当て、自分の提案より先に撤去・手直しの PROPOSAL を出す。
表を作るのに 30 秒ほどかかる（transcript 全部を読む。手元の計算だけで、係の読み込みは増えない）。
係の費用を毎回増やさないよう、載せるのは対象の hook の顔ぶれが前回載せたときと変わったときか、前回から 7 日
過ぎたときだけ（前回の顔ぶれは起動フォルダの hook_review_seen.json）。`--no-hook-review` で載せない。

## 使い方（親が Bash の run_in_background で起こす。hook の注入文がこの行を渡す）
    python3 heaven/tools/reflect_launch.py <transcript> <session_id> [--start N] [--model M] [--no-hook-review] [--dry-run]
標準出力には係の最終メッセージと、費用の 1 行（往復・読み込み）だけを出す。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import importlib.util
import re
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT_DEF = REPO / ".claude" / "agents" / "self-reflection.md"
DIGEST = REPO / "heaven" / "tools" / "transcript_digest.py"
DENIAL_REPORT = REPO / "heaven" / "tools" / "hook_denial_report.py"
HOOK_REVIEW_DAYS = 30
HOOK_REVIEW_EVERY = dt.timedelta(days=7)
ALLOWED = ("Read,Grep,Glob,Write,Edit,Bash(python3:*),Bash(git status:*),Bash(git log:*),"
           "Bash(git diff:*),Bash(git ls-files:*),Bash(ls:*),Bash(wc:*)")
# 既定の 120 秒を超えた Bash は裏へ回され、その完了の知らせで係が 2 回目の応答を始める。結果は最後の応答
# だけなので本来の報告が消え、往復も増える（実測）。上限を延ばして表で終わらせる（待ちは費用ゼロ）。
BASH_TIMEOUT_ENV = {"BASH_DEFAULT_TIMEOUT_MS": "600000", "BASH_MAX_TIMEOUT_MS": "600000"}


def default_home() -> Path:
    return REPO / "heaven" / ".self-improvement" / "reflection"


def settings() -> dict:
    return {
        "disableAllHooks": True,
        "autoMemoryEnabled": False,
        "claudeMdExcludes": [
            str(REPO / "CLAUDE.md"),
            str(REPO / "CLAUDE.local.md"),
            str(REPO / ".claude" / "rules") + "/**",
            str(REPO / ".local") + "/**",
        ],
    }


def prepare_home(home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    md = home / "CLAUDE.md"
    target = os.path.relpath(AGENT_DEF, home)
    if md.is_symlink() or md.exists():
        if not (md.is_symlink() and os.readlink(md) == target):
            md.unlink()
    if not md.is_symlink():
        md.symlink_to(target)
    sp = home / "settings.json"
    sp.write_text(json.dumps(settings(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sp


def _hdr():
    spec = importlib.util.spec_from_file_location("hook_denial_report", DENIAL_REPORT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def review_rows(table: list, top: int = 5, spin_rate: int = 10) -> list:
    """hook-review の手順 2 の対象: 今ある hook の行のうち、拒否の上位 top 件と空回りの率が spin_rate% 以上の物。"""
    hdr = _hdr()
    live = [r for r in table if r.get("owner") and r["owner"] != hdr.MISSING]
    return [r for i, r in enumerate(live) if i < top or r["spin_rate"] >= spin_rate]


def due(rows: list, seen_path: Path, now: dt.datetime) -> bool:
    """対象の顔ぶれが前回載せたときと違うか、前回から HOOK_REVIEW_EVERY 過ぎたら True。"""
    try:
        seen = json.loads(seen_path.read_text(encoding="utf-8"))
        same = seen["hooks"] == sorted(r["owner"] for r in rows)
        return not same or now - dt.datetime.fromisoformat(seen["at"]) >= HOOK_REVIEW_EVERY
    except (OSError, ValueError, KeyError, TypeError):
        return True


def hook_review_table(seen_path: Path | None = None, days: int = HOOK_REVIEW_DAYS) -> str:
    hdr = _hdr()
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(REPO))
    files = sorted(glob.glob(str(Path.home() / ".claude" / "projects" / slug / "*.jsonl")))
    stat = hdr.scan(files, since)
    rows = review_rows(hdr.rows(stat, 0, hdr.load_hooks(str(REPO))))
    now = dt.datetime.now()
    if not rows or (seen_path is not None and not due(rows, seen_path, now)):
        return ""
    if seen_path is not None:
        seen_path.write_text(json.dumps({"hooks": sorted(r["owner"] for r in rows), "at": now.isoformat()},
                                        ensure_ascii=False) + "\n", encoding="utf-8")
    return hdr.render(rows, since, sum(s["denials"] for s in stat.values()))


def prompt(transcript: str, sid: str, start: int | None, hook_table: str = "") -> str:
    digest = f"python3 {DIGEST} {transcript}" + (f" --start {start}" if start is not None else "")
    return f"""Inputs:
- transcript_path: {transcript}
- session_id: {sid}
- ↑ この transcript_path / session_id が META mining の **唯一の分析対象 (subject session)**。
- 最初の 1 手: `{digest}`（① 先頭 user 発話でテーマを固定する。② 窓の user 発話の全逐語と道具の呼び出し。③ 集計と開いた memory）。深く読む所は `--lines a-b,c-d` で 1 回にまとめて引く。
- repo のルート: {REPO}。あなたの作業フォルダは repo の中の起動フォルダで、repo のルートではない。定義・手順に出てくる相対パス（heaven/…・.claude/…・docs/…・projects/…）はこのルートからのパスとして扱い、絶対パスで開く・実行する。
- repo の CLAUDE.md・CLAUDE.local.md・.claude/rules/ は土台に読み込んでいない。照らし合わせが要る規則は、その節だけを Grep か Read で引く。
""" + (f"""
hook の拒否の表（hook-review の手順 1 の出力のうち、手順 2 の対象の行だけ。定義の「hook の見直し」節に従う）:
{hook_table}
""" if hook_table else "")


def build(transcript: str, sid: str, start: int | None, model: str | None, home: Path, hook_table: str = "") -> dict:
    sp = home / "settings.json"
    argv = ["claude", "-p", "--output-format", "json",
            "--tools", "Read,Grep,Glob,Write,Edit,Bash", "--allowedTools", ALLOWED,
            "--strict-mcp-config", "--settings", str(sp),
            "--add-dir", str(REPO), "--add-dir", str(Path(transcript).parent)]
    if model:
        argv += ["--model", model]
    return {"cwd": str(home), "settings_path": str(sp), "argv": argv, "prompt": prompt(transcript, sid, start, hook_table)}


def _cost_line(d: dict) -> str:
    # usage は最後の応答の分だけ。全往復の合計は modelUsage にある（裏回しで応答が 2 回になると 6 倍ずれた）
    mu = d.get("modelUsage") or {}
    if mu:
        read = sum((v.get(k, 0) or 0) for v in mu.values()
                   for k in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))
    else:
        u = d.get("usage") or {}
        read = sum(u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return f"（係の費用: {d.get('num_turns')} 往復・読み込み {read / 1000:.0f}K）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("transcript")
    ap.add_argument("session_id")
    ap.add_argument("--start", type=int)
    ap.add_argument("--model")
    ap.add_argument("--no-hook-review", action="store_true", help="hook の拒否の表を prompt に載せない")
    ap.add_argument("--dry-run", action="store_true", help="起動フォルダを用意し、起動の中身を JSON で出すだけ")
    a = ap.parse_args(argv)

    home = Path(os.environ.get("REFLECT_HOME") or default_home())
    prepare_home(home)
    table = "" if a.no_hook_review else hook_review_table(None if a.dry_run else home / "hook_review_seen.json")
    spec = build(a.transcript, a.session_id, a.start, a.model, home, table)
    if a.dry_run:
        print(json.dumps(spec, ensure_ascii=False, indent=2))
        return 0

    proc = subprocess.run(spec["argv"], cwd=spec["cwd"], input=spec["prompt"], capture_output=True, text=True,
                          env={**os.environ, **BASH_TIMEOUT_ENV})
    runs = home / "runs"
    runs.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = runs / f"{stamp}-{a.session_id[:8]}.json"
    out.write_text(proc.stdout or "", encoding="utf-8")
    try:
        d = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        print(f"係の起動に失敗しました（終了コード {proc.returncode}）: {(proc.stderr or proc.stdout).strip()[:500]}")
        return 1
    print(d.get("result", ""))
    print(_cost_line(d))
    return 1 if d.get("is_error") or proc.returncode else 0


if __name__ == "__main__":
    sys.exit(main())
