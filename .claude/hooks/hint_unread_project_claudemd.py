#!/usr/bin/env python3
"""hint_unread_project_claudemd — projects/<X>/ に触れる呼び出しで、そのセッションで
projects/<X>/CLAUDE.md（祖先の案件すべて）がまだ読み込まれていなければ、
書く呼び出し（Write / Edit / NotebookEdit / 書き込みを伴う Bash）は止め、読むだけの呼び出し
（Bash / Grep / Glob）は「先に Read する」と知らせる PreToolUse フック。

Failure it prevents
-------------------
ルート CLAUDE.md は「ルーティング先決定後は、必ず該当 projects/<name>/CLAUDE.md を読む」と定めるが、
harness が案件の CLAUDE.md を読み込むのは Read/Edit がその配下に触れた**後** (nested_memory 添付) で、
書く前には案件の禁止事項（書き込み禁止の場所など）が文脈に無い。
直近 30 日のあるプロジェクトのセッションで読み込みあり 62 / なし 60 (root-cause-auditor 2026-10-12)。
実害: 取得の手順を読まずに古い保存版で仕様を答えた例と、知らせ（1 案件 1 回・Bash だけ）を読み流して、
案件の CLAUDE.md が書き込み禁止と定める場所に Write と `sed -i` で書いた例があった — 書く瞬間に効く層が
無かったので、書く呼び出しは止める形にした。

Observable signal
-----------------
tool_input に projects/<X> が現れ、projects/<X>/CLAUDE.md が実在し、transcript に当該パスの
nested_memory 添付も Read も無いとき: 書く呼び出しなら exit 2（stderr に読む先）、
読むだけなら additionalContext を 1 案件 1 回注入。読み込みを確かめた案件はセッション別の状態ファイルに記録する。
"""
from __future__ import annotations
import json, re, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _fire_counter import record_fire  # type: ignore
except Exception:  # 計器が無くても本体は動かす
    def record_fire(*a, **k):
        return None

# 起動フォルダ（PROJECT_DIR）は projects/<name> で起動したセッションでは案件のフォルダになり、
# projects/<X>/CLAUDE.md が見つからず黙って何もしなくなるので、hook の置き場から求める REPO_ROOT を使う。
from _paths import REPO_ROOT as REPO  # noqa: E402
# 触ったパスの祖先にある CLAUDE.md をすべて対象にする（入れ子の案件 projects/<a>/<b>/ 等。
# 入れ子の案件の CLAUDE.md の境界規則が一度も読まれないまま、責務境界を変える案を書いた例があった）
PROJ_RE = re.compile(r"projects/((?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]+)")
SKIP_PARTS = {"example-infra", "node_modules", ".venv", "venv", ".next", "dist", "tmp"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
# 書き込みを伴う Bash の目安（誤検出しても、案件の CLAUDE.md を読めば通るので費用は小さい）
BASH_WRITE_RE = re.compile(
    r"(sed\s+-i|(?<![0-9&>])>>?\|?\s*(?!/dev/null)[^&\s>]|\btee\b|\b(mv|cp|rm|mkdir|touch|ln|rsync|install)\s|"
    r"git\s+(checkout|restore|reset|apply|mv|rm)\b|\bopen\([^)]*['\"][wa]['\"]|write_text\(|"
    r"\bpatch\s|\bperl\s+-[a-z]*i)")


def state_path(session_id: str) -> Path:
    d = Path(tempfile.gettempdir()) / "claude-hint-unread-claudemd"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{re.sub(r'[^A-Za-z0-9_-]', '_', session_id)}.json"


def load_state(sp: Path) -> dict:
    try:
        raw = json.loads(sp.read_text())
    except Exception:
        return {"hinted": [], "read": []}
    if isinstance(raw, list):  # 旧形式（知らせ済みの一覧だけ）
        return {"hinted": raw, "read": []}
    return {"hinted": list(raw.get("hinted", [])), "read": list(raw.get("read", []))}


def loaded_in_transcript(transcript: str, name: str) -> bool:
    # nested_memory の path は起動フォルダ由来の表記になりうるので、絶対パスでなく末尾で照合する
    suffix = f"/projects/{name}/CLAUDE.md"
    try:
        with open(transcript, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if suffix not in line:
                    continue
                if '"nested_memory"' in line or '"file_path"' in line:
                    return True
    except OSError:
        return False
    return False


def is_write(tool: str, tool_input: dict) -> bool:
    if tool in WRITE_TOOLS:
        return True
    if tool == "Bash":
        return bool(BASH_WRITE_RE.search(str(tool_input.get("command") or "")))
    return False


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    # 書く道具は書き込み先だけを見る。本文に別案件のパスが書かれているだけで止めない
    # （別の案件のログ・試験の本文に出てくるパスで、その案件の未読を理由に止めていた）
    if tool in WRITE_TOOLS:
        blob = " ".join(str(tool_input.get(k) or "") for k in ("file_path", "notebook_path"))
    else:
        blob = json.dumps(tool_input, ensure_ascii=False)
    names = []  # 値は "<a>" / "<a>/<b>" のような projects/ からの相対ディレクトリ
    for m in PROJ_RE.finditer(blob):
        parts = m.group(1).rstrip(".").split("/")
        for k in range(1, len(parts) + 1):
            if SKIP_PARTS & set(parts[:k]):
                break
            n = "/".join(parts[:k])
            if n not in names and (REPO / "projects" / n / "CLAUDE.md").is_file():
                names.append(n)
    if not names:
        return 0
    sid = data.get("session_id") or "unknown"
    sp = state_path(sid)
    st = load_state(sp)
    writing = is_write(tool, tool_input)
    candidates = [n for n in names if n not in st["read"] and (writing or n not in st["hinted"])]
    if not candidates:
        return 0
    transcript = data.get("transcript_path") or ""
    unread = []
    for n in candidates:
        if loaded_in_transcript(transcript, n):
            st["read"].append(n)
        else:
            unread.append(n)
    if not writing:
        st["hinted"] = sorted(set(st["hinted"]) | set(unread))
    try:
        sp.write_text(json.dumps(st))
    except OSError:
        pass
    if not unread:
        return 0
    paths = "、".join(f"projects/{n}/CLAUDE.md" for n in unread)
    if writing:
        try:
            record_fire("hint_unread_project_claudemd", "block", context=paths)
        except Exception:
            pass
        print(f"{paths} をこのセッションでまだ読んでいないまま、その配下に書こうとしている。"
              f"案件の CLAUDE.md には書き込み禁止の場所・禁止事項がある。先に Read で最後まで読み、"
              f"この書き込みがそれに反しないか確かめてから、同じ呼び出しを出し直す。", file=sys.stderr)
        return 2
    try:
        record_fire("hint_unread_project_claudemd", "warn", context=paths)
    except Exception:
        pass
    msg = (f"{paths} がこのセッションでまだ読み込まれていない。案件ルール（人格・禁止事項・取得のこつ）が"
           f"文脈に無いまま進めている。このツール呼び出しの後、先に Read で通読してから続ける"
           f"（ルート CLAUDE.md「ルーティング先決定後は、必ず該当 projects/<name>/CLAUDE.md を読み」）。"
           f"未読のまま配下へ書く呼び出しは止まる。")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "additionalContext": msg}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
