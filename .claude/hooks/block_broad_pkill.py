#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — 名前の一致で無関係なプロセスまで巻き込む pkill / killall を実行前に止める。

`pkill -f "python3 -$"` や `pkill python3` / `killall node` のように、パターンがインタプリタ名や
短い汎用語だけだと、同じマシンで動いている別の Claude セッションや user の常駐プロセスまで止める。
（2026-10-18 sid 49c14d72: サブエージェントが止まった自分のヒアドキュメント Python を止めるために
`pkill -f "python3 -$"` を 2 回打った。同時に別セッションが長い計算を走らせていた。
同型の初回は 2026-06-07 の `pkill -f llm_dashboard` で user 配信中の本番 dashboard を巻き添えにした —
memory feedback_no_pkill_shared_name_isolate_verify_instance。サブエージェントは memory を読まないため hook で止める。）

止める条件: コマンド中の pkill / killall の引数パターンから、インタプリタ名・オプション・正規表現記号を
除いた残りの英数字が 6 文字未満のとき（= 自分の物だと特定できる語が無い）。
通す: `kill <pid>`、パスや固有のスクリプト名を含むパターン（例: `pkill -f tools/upgrade.py`）。
例外の印: コマンドに `# PKILL-SCOPED-OK: <根拠>` を含める。

追加（2026-10-19）: `kill <pid>` でも、対象 pid の親を辿って最初に当たる Claude 本体（comm の basename が
`claude`）が自分の Claude 本体と別なら止める。2026-10-13 sid fd8208ba で「本セッションを優先させて」を
「止めてよい」と読み、別セッションの取引所検索 4 プロセスを `kill <pid>` で止めた（名前一致の判定では拾えない形）。
祖先に Claude が無い pid（user の常駐など）・ps が失敗した場合は通す。
例外の印: user が停止を明示したときだけ `# KILL-OTHER-SESSION-OK: <user の発言>`。
フレーム層（どの環境でも成立する汎用機構）。
"""
import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover - counter is best-effort
    def record_fire(*_a, **_k):
        return None

_GENERIC = {
    "python", "python3", "python2", "node", "bun", "deno", "ruby", "perl", "bash", "sh", "zsh",
    "java", "claude", "npm", "npx", "uv", "pip", "go", "php",
}
_SEG = re.compile(r"(?:^|[;&|\n]\s*|\$\(\s*|`\s*)(?:sudo\s+)?(pkill|killall)\b([^;&|\n`]*)")
_MARKER = "PKILL-SCOPED-OK:"
_KILL_SEG = re.compile(r"(?:^|[;&|\n]\s*|\$\(\s*|`\s*)(?:sudo\s+)?kill\b([^;&|\n`]*)")
_KILL_MARKER = "KILL-OTHER-SESSION-OK:"


def _ps(pid: int):
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        ppid, comm = out.split(None, 1)
        return int(ppid), comm
    except Exception:
        return None


def _claude_ancestor(pid: int):
    """pid 自身を含めて親を辿り、最初に当たる Claude 本体の pid を返す（無ければ None）。"""
    for _ in range(40):
        if pid <= 1:
            return None
        info = _ps(pid)
        if not info:
            return None
        ppid, comm = info
        if os.path.basename(comm) == "claude":
            return pid
        pid = ppid
    return None


def _other_session_kills(cmd: str):
    mine = _claude_ancestor(os.getppid())
    if mine is None:
        return []
    hits = []
    for m in _KILL_SEG.finditer(cmd):
        try:
            toks = shlex.split(m.group(1), comments=True)
        except ValueError:
            toks = m.group(1).split()
        for t in toks:
            if not t.isdigit():
                continue
            owner = _claude_ancestor(int(t))
            if owner is not None and owner != mine:
                hits.append((t, owner))
    return hits


def _residual(pattern: str) -> str:
    s = re.sub(r"[\^\$\\.*+?()\[\]{}|]", " ", pattern)
    words = [w for w in re.split(r"[\s/=,:-]+", s) if w]
    return "".join(w for w in words if w.lower() not in _GENERIC and not re.fullmatch(r"\d", w))


def _patterns(args: str):
    try:
        toks = shlex.split(args, comments=True)
    except ValueError:
        toks = args.split()
    out, skip = [], False
    for t in toks:
        if skip:
            skip = False
            continue
        if t in ("-u", "-U", "-g", "-G", "-P", "-t", "-s", "--signal", "-F"):
            skip = True
            continue
        if t.startswith("-"):
            continue
        out.append(t)
    return out


_PGREP_SUB = re.compile(r"\$\(\s*pgrep\b([^)|]*)")
_KILL_ANY = re.compile(r"(?:^|[;&|\n]\s*|\$\(\s*|`\s*|&&\s*|\|\|\s*)(?:sudo\s+)?kill\b")
_ASSIGN = re.compile(r"(?:^|[;&\n]\s*)([A-Za-z_][A-Za-z0-9_]*)=([^\s;&|]+)")


def _broad_pgrep_into_kill(text: str):
    """`kill $(pgrep ...)` や `D=$(pgrep ...)` + `kill $D` の pgrep の型が汎用語だけなら返す。
    pgrep -P <pid>（親で絞る）のように型の無い形は通す。"""
    if not _KILL_ANY.search(text):
        return []
    hits = []
    for m in _PGREP_SUB.finditer(text):
        pats = _patterns(m.group(1))
        if not pats:
            continue
        for p in pats:
            if len(re.sub(r"[^A-Za-z0-9]", "", _residual(p))) < 6:
                hits.append("pgrep" + m.group(1).rstrip())
                break
    return hits


def _expand(cmd: str, tok: str) -> str:
    env = {k: v.strip("'\"") for k, v in _ASSIGN.findall(cmd)}
    return re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: env.get(m.group(1), m.group(0)), tok)


def _executed_scripts(cmd: str):
    """コマンド中で実行されているローカルのスクリプトファイル（読める・小さい）のパスを返す。"""
    out = []
    for seg in re.split(r"[;&|\n]+", cmd):
        toks = seg.strip().split()
        if not toks:
            continue
        cand = toks[0]
        if cand in ("bash", "zsh", "sh") and len(toks) > 1:
            cand = toks[1]
        if "=" in cand and not cand.startswith(("/", ".", "$")):
            continue
        path = _expand(cmd, cand)
        if "/" not in path or not os.path.isfile(path):
            continue
        try:
            if os.path.getsize(path) > 65536:
                continue
            out.append(path)
        except OSError:
            continue
    return out


def _script_kill_hits(cmd: str):
    hits = []
    for path in _executed_scripts(cmd):
        try:
            body = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if not _KILL_ANY.search(body) and "pkill" not in body and "killall" not in body:
            continue
        if re.search(r"\bsed\s+-i\b", cmd) and os.path.basename(path) in cmd.split("sed", 1)[1]:
            hits.append(f"{path}（同じコマンドで sed -i で書き換えた直後に実行。書き換え後の中身は実行前に検査できない）")
            continue
        for h in _broad_pgrep_into_kill(body):
            hits.append(f"{path}: {h}")
        for m in _SEG.finditer(body):
            for p in _patterns(m.group(2)) or [""]:
                if len(re.sub(r"[^A-Za-z0-9]", "", _residual(p))) < 6:
                    hits.append(f"{path}: {m.group(1)} {m.group(2).strip()}")
                    break
    return hits


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(cmd, str):
        return
    if re.search(r"\bkill\b", cmd) and _KILL_MARKER not in cmd:
        others = _other_session_kills(cmd)
        if others:
            sys.stderr.write(
                "[other-session-kill] 別の Claude セッションの配下のプロセスを止めようとしています: "
                + ", ".join(f"pid {p}（claude pid {o}）" for p, o in others)
                + "\n止めずに `python3 heaven/tools/peers.py` で相手を特定し、SendMessage で譲ってもらう"
                "（取引所の制限などの取り合いなら、自分の要求を相手の待ちの隙間に入れる）。"
                "\nuser が停止を明示したときだけ `# KILL-OTHER-SESSION-OK: <user の発言>` を付けて再実行。\n"
            )
            record_fire("block_broad_pkill", "block", context=cmd[:120])
            sys.exit(2)
    if _MARKER not in cmd:
        indirect = _broad_pgrep_into_kill(cmd) + _script_kill_hits(cmd)
        if indirect:
            sys.stderr.write(
                "[broad-pkill] pgrep の結果やスクリプト内で、名前の一致で別セッションや user のプロセスまで止める形です: "
                + " / ".join(indirect) +
                "\n止める対象は起動時に控えた pid（`$!`・`pgrep -P <自分の親 pid>`）で取る。"
                "スクリプトを書き換えたら、実行は別の呼び出しに分けて中身を確かめてから。"
                "\n範囲を確かめた上でどうしても必要なら `# PKILL-SCOPED-OK: <根拠>` を付ける。\n"
            )
            record_fire("block_broad_pkill", "block", context=cmd[:120])
            sys.exit(2)
    if ("pkill" not in cmd and "killall" not in cmd) or _MARKER in cmd:
        return
    hits = []
    for m in _SEG.finditer(cmd):
        for p in _patterns(m.group(2)) or [""]:
            if len(re.sub(r"[^A-Za-z0-9]", "", _residual(p))) < 6:
                hits.append(f"{m.group(1)} {m.group(2).strip()}")
                break
    if not hits:
        return
    sys.stderr.write(
        "[broad-pkill] 名前の一致で別セッションや user のプロセスまで止める形です: " + " / ".join(hits) +
        "\n自分が起動したプロセスの pid（起動時に控えた値、または `pgrep -fl <固有の語>` で一覧を見て特定した値）"
        "を `kill <pid>` で止める。パターンを使うならスクリプトのパスなど自分の物と特定できる語を入れる。"
        "\n範囲を確かめた上でどうしても必要なら `# PKILL-SCOPED-OK: <根拠>` を付ける。\n"
    )
    record_fire("block_broad_pkill", "block", context=cmd[:120])
    sys.exit(2)


if __name__ == "__main__":
    main()
