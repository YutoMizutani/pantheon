#!/usr/bin/env python3
"""hint_unread_project_claudemd — Bash/Grep/Glob が projects/<X>/ に初めて触れたとき、そのセッションで
projects/<X>/CLAUDE.md がまだ読み込まれていなければ「先に Read する」と知らせる PreToolUse フック
(止めない・知らせるだけ・1 セッション 1 案件 1 回)。

Failure it prevents
-------------------
ルート CLAUDE.md は「ルーティング先決定後は、必ず該当 projects/<name>/CLAUDE.md を読む」と定めるが、
harness が案件の CLAUDE.md を読み込むのは Read/Edit がその配下に触れたとき (nested_memory 添付) だけで、
Bash/rg/Grep/Glob だけで調べるセッションには案件ルール (人格・禁止事項・取得のこつ) が一度も入らない。
直近 30 日のあるプロジェクトのセッションで読み込みあり 62 / なし 60 (root-cause-auditor 2026-10-12)。
実害: 2026-10-12 sid a6346e39 で projects/<name>/CLAUDE.md の取得の手順を読まず、取得に 3 回失敗して
古い保存版で仕様を答えた。

Observable signal
-----------------
tool_input に projects/<X> が現れ、projects/<X>/CLAUDE.md が実在し、transcript に当該パスの
nested_memory 添付も Read も無いとき、additionalContext を注入する。exit 0 のみ (block しない)。
通知済み・読み込み済みの案件はセッション別の状態ファイルに記録し、以後は transcript を読まない。
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

from _paths import PROJECT_DIR as REPO  # noqa: E402  (nested_memory の path は非解決の PROJECT_DIR 形)
# 触ったパスの祖先にある CLAUDE.md をすべて対象にする（入れ子の案件 projects/<a>/<b>/ 等。
# 2026-10-16 sid 83b26e19: 入れ子の案件の CLAUDE.md の境界規則が一度も読まれず、責務境界を変える案を書いて差し戻された）
PROJ_RE = re.compile(r"projects/((?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]+)")
SKIP = {"example-infra"}  # テンプレート
SKIP_PARTS = {"example-infra", "node_modules", ".venv", "venv", ".next", "dist", "tmp"}


def state_path(session_id: str) -> Path:
    d = Path(tempfile.gettempdir()) / "claude-hint-unread-claudemd"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{re.sub(r'[^A-Za-z0-9_-]', '_', session_id)}.json"


def loaded_in_transcript(transcript: str, claudemd: str) -> bool:
    try:
        with open(transcript, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if claudemd not in line:
                    continue
                if '"nested_memory"' in line or '"file_path"' in line:
                    return True
    except OSError:
        return False
    return False


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    blob = json.dumps(data.get("tool_input") or {}, ensure_ascii=False)
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
    try:
        seen = set(json.loads(sp.read_text()))
    except Exception:
        seen = set()
    todo = [n for n in names if n not in seen]
    if not todo:
        return 0
    transcript = data.get("transcript_path") or ""
    unread = []
    for n in todo:
        seen.add(n)
        claudemd = str(REPO / "projects" / n / "CLAUDE.md")
        if not loaded_in_transcript(transcript, claudemd):
            unread.append(n)
    try:
        sp.write_text(json.dumps(sorted(seen)))
    except OSError:
        pass
    if not unread:
        return 0
    try:
        record_fire("hint_unread_project_claudemd")
    except Exception:
        pass
    paths = "、".join(f"projects/{n}/CLAUDE.md" for n in unread)
    msg = (f"{paths} がこのセッションでまだ読み込まれていない。案件ルール（人格・禁止事項・取得のこつ）が"
           f"文脈に無いまま進めている。このツール呼び出しの後、先に Read で通読してから続ける"
           f"（ルート CLAUDE.md「ルーティング先決定後は、必ず該当 projects/<name>/CLAUDE.md を読み」）。")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "additionalContext": msg}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
