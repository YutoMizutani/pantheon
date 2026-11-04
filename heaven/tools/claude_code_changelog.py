#!/usr/bin/env python3
"""claude_code_changelog — Claude Code の CHANGELOG を版の範囲で取り、hook・settings・plugin に効く行を抜く。

Failure it prevents
-------------------
hook の入出力・settings のキー・plugin の契約は Claude Code の版ごとに変わるが、どの版で何が変わったかを
追う仕組みが無かった（版の追跡は 0 件）。harness 側の変更で hook が黙って効かなくなっても、
発火ログには何も出ない。

Observable signal
-----------------
stdout の 1 行目:
  claude_code_changelog: OK range=<since>..<until> versions=<範囲内の版数> matched=<該当行数>
  claude_code_changelog: UNAVAILABLE <理由>
続けて md（`## <版>` の下に該当行を `- ` で）を新しい版から出す。取得に失敗して保存を使ったときは
2 行目に `WARN fetch failed, using saved copy (<取得日時>)` を出す。`--json` は 1 行目の後に JSON。
exit 0 = 抜き出せた（該当 0 行を含む）/ 2 = 判定できない（取得失敗かつ保存なし・版の書式違い）。

範囲は since を含まず until を含む（`--since 2.1.300` → 2.1.301〜until）。
  --since 省略: `~/.claude/runtime/claude_code_changelog/last_checked.json` の版、無ければ `claude --version` の版
  --until 省略: `claude --version` の版
語の照合は大文字小文字を無視し、語の頭だけを揃える（`hook` は `hooks` に当たり `webhooks` には当たらない。
設定キー名のキャメルケース `disableAllHooks`・`allowedHttpHookUrls` の大文字始まりは語の頭として当たる）。

取得物は `~/.claude/runtime/claude_code_changelog/CHANGELOG.md` と `CHANGELOG.md.json`
（url / etag / sha256 / size / fetched_at）。保存の ETag を If-None-Match で送り、304 なら保存を使う
（raw.githubusercontent.com は ETag を返し 304 になることを確認）。
`--mark-checked` で `last_checked.json` を until の版にする（次の既定の since になる）。

usage:
  python3 claude_code_changelog.py [--since <版>] [--until <版>] [--keywords a,b,…]
                                   [--offline <CHANGELOG.md>] [--url <URL>] [--json] [--mark-checked]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
DEFAULT_KEYWORDS = ("hook", "hooks", "settings", "plugin", "skill", "subagent", "memory",
                    "permission", "CLAUDE.md")
VERSION_HEAD = re.compile(r"^## (\d+(?:\.\d+)+)\s*$")
VERSION_ARG = re.compile(r"^\d+(?:\.\d+)+$")


class Unavailable(Exception):
    """判定できない（exit 2）。"""


def _runtime_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "runtime" / "claude_code_changelog"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _vkey(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def installed_version() -> str | None:
    """`claude --version` の版（例: `2.1.310 (Claude Code)` → `2.1.310`）。取れなければ None。"""
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\d+(?:\.\d+)+", out)
    return m.group(0) if m else None


# ---------------------------------------------------------------- 取得

def load_changelog(url: str, notes: list[str]) -> str:
    """取得して保存する。304 か失敗なら保存を使う（失敗は WARN を notes に足す）。"""
    rt = _runtime_dir()
    saved, meta_path = rt / "CHANGELOG.md", rt / "CHANGELOG.md.json"
    meta = _read_json(meta_path)
    headers = {"User-Agent": "Mozilla/5.0 (claude_code_changelog)"}
    if meta.get("etag") and meta.get("url") == url and saved.exists():
        headers["If-None-Match"] = meta["etag"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
            body = r.read()
            etag = r.headers.get("ETag") if hasattr(r, "headers") and r.headers else None
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return saved.read_text(encoding="utf-8")
        return _fallback(saved, meta, f"HTTP {e.code}", notes)
    except (OSError, ValueError) as e:
        return _fallback(saved, meta, str(e), notes)
    text = body.decode("utf-8", "replace")
    rt.mkdir(parents=True, exist_ok=True)
    saved.write_text(text, encoding="utf-8")
    meta_path.write_text(json.dumps({
        "url": url, "etag": etag, "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body), "fetched_at": _now()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return text


def _fallback(saved: Path, meta: dict, reason: str, notes: list[str]) -> str:
    if not saved.exists():
        raise Unavailable(f"fetch failed: {reason}")
    notes.append(f"WARN fetch failed, using saved copy ({meta.get('fetched_at', '?')}): {reason}")
    return saved.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 抜き出し

def split_versions(text: str) -> list[tuple[str, list[str]]]:
    """`## x.y.z` 見出しごとに、その下の `- ` 行（先頭の `- ` を外した文）を返す。文書の順のまま。"""
    out: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        m = VERSION_HEAD.match(line)
        if m:
            out.append((m.group(1), []))
        elif line.startswith("## ") or line.startswith("# "):
            continue
        elif out and line.lstrip().startswith("- "):
            out[-1][1].append(line.lstrip()[2:].strip())
    return out


def keyword_re(keywords) -> re.Pattern:
    """語の頭で当てる。英数字の直後でない位置か、キャメルケースの大文字始まり（`disableAllHooks`）。"""
    ks = sorted(set(keywords), key=len, reverse=True)
    alts = "|".join(re.escape(k) for k in ks)
    camel = "|".join(re.escape(k[0].upper()) + "(?i:" + re.escape(k[1:]) + ")" for k in ks)
    return re.compile(rf"(?<![A-Za-z0-9])(?i:{alts})|(?<=[a-z0-9])(?:{camel})")


def select(versions, since: str, until: str, pat: re.Pattern):
    lo, hi = _vkey(since), _vkey(until)
    in_range = [(v, lines) for v, lines in versions if lo < _vkey(v) <= hi]
    in_range.sort(key=lambda x: _vkey(x[0]), reverse=True)
    return [(v, [ln for ln in lines if pat.search(ln)]) for v, lines in in_range]


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", help="この版より後（含まない）")
    ap.add_argument("--until", help="この版まで（含む）")
    ap.add_argument("--keywords", help="カンマ区切りの語（既定: " + ",".join(DEFAULT_KEYWORDS) + "）")
    ap.add_argument("--offline", help="取得せずこの CHANGELOG.md を読む（何も保存しない）")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--mark-checked", action="store_true", help="last_checked.json を until の版にする")
    args = ap.parse_args(argv)

    notes: list[str] = []
    try:
        installed = None
        until = args.until
        if not until:
            installed = installed_version()
            if not installed:
                raise Unavailable("claude --version gave no version; pass --until")
            until = installed
        since = args.since or _read_json(_runtime_dir() / "last_checked.json").get("version")
        if not since:
            installed = installed or installed_version()
            if not installed:
                raise Unavailable("no last_checked.json and claude --version gave no version; pass --since")
            since = installed
        for v in (since, until):
            if not VERSION_ARG.match(str(v)):
                raise Unavailable(f"bad version: {v!r}")
        if args.offline:
            try:
                text = Path(args.offline).read_text(encoding="utf-8")
            except OSError as e:
                raise Unavailable(f"offline read failed: {e}")
        else:
            text = load_changelog(args.url, notes)
        versions = split_versions(text)
        if not versions:
            raise Unavailable("no '## x.y.z' headings found")
    except Unavailable as e:
        print(f"claude_code_changelog: UNAVAILABLE {e}")
        return 2

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else DEFAULT_KEYWORDS
    picked = select(versions, since, until, keyword_re(keywords))
    matched = sum(len(lines) for _, lines in picked)
    print(f"claude_code_changelog: OK range={since}..{until} versions={len(picked)} matched={matched}")
    for n in notes:
        print(n)
    if args.json:
        print(json.dumps({"range": [since, until], "keywords": list(keywords),
                          "versions": [{"version": v, "lines": lines} for v, lines in picked if lines]},
                         ensure_ascii=False, indent=2))
    else:
        for v, lines in picked:
            if lines:
                print(f"\n## {v}\n")
                for ln in lines:
                    print(f"- {ln}")

    if args.mark_checked:
        rt = _runtime_dir()
        rt.mkdir(parents=True, exist_ok=True)
        (rt / "last_checked.json").write_text(json.dumps(
            {"version": until, "checked_at": _now()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # `| head` で読み手が先に閉じた
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
