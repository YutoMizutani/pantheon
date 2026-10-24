#!/usr/bin/env python3
"""PreToolUse: ルーティング先 projects/<name>/CLAUDE.md を、セッションで最初に触れた瞬間に全文注入する。

背景 (2026-10-10 sid 6415c72a): harness が入れ子の CLAUDE.md を自動で読み込むのは Read ツールで
そのディレクトリのファイルを開いたときだけ。auto mode の Bash-first 運用では Claude が `sed -n 1,80p` や
`rg -A12` の窓でしかプロジェクト規約を見ず、窓の 1 行外にあった規則 (poe2 即答モード 4. の
「まだ行ったことのない場所は名前と方角だけで送らない」) が一度も context に入らないまま 4 回目の再発をした。
直近 20 の poe2 セッションのうち 14 で、プロジェクト CLAUDE.md の全文が一度も読み込まれていなかった。

挙動: Bash / Read / Grep / Glob / Edit / Write の tool_input か cwd に `projects/<name>` が現れ、
`projects/<name>/CLAUDE.md` が実在し、このセッションでまだ注入していなければ、全文を additionalContext で 1 回だけ渡す。
決して block しない (exit 0)。例外時も黙って通す。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _paths import PROJECT_DIR, TELEMETRY_DIR  # noqa: E402
except Exception:  # pragma: no cover
    sys.exit(0)

try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover
    record_fire = None

MAX_CHARS = 40000
PROJ_RE = re.compile(r"projects/([A-Za-z0-9_][A-Za-z0-9_.-]*)")
SKIP_NAMES = {"example-infra"}


def _candidates(payload: dict) -> list[str]:
    tin = payload.get("tool_input") or {}
    hay = " ".join(str(v) for v in tin.values() if isinstance(v, (str, int)))
    hay += " " + str(payload.get("cwd") or "")
    root = PROJECT_DIR.resolve()
    out: list[str] = []
    for m in PROJ_RE.finditer(hay):
        name = m.group(1).rstrip(".")
        if name in SKIP_NAMES or name in out:
            continue
        if (root / "projects" / name / "CLAUDE.md").is_file():
            out.append(name)
    return out


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    sid = str(payload.get("session_id") or "nosession")
    names = _candidates(payload)
    if not names:
        return 0
    state_dir = TELEMETRY_DIR / "project_claudemd_injected"
    state_file = state_dir / f"{sid}.json"
    try:
        done = set(json.loads(state_file.read_text())) if state_file.is_file() else set()
    except Exception:
        done = set()
    todo = [n for n in names if n not in done]
    if not todo:
        return 0
    root = PROJECT_DIR.resolve()
    parts = []
    for name in todo:
        text = (root / "projects" / name / "CLAUDE.md").read_text(encoding="utf-8", errors="replace")
        note = ""
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
            note = f"\n[以下省略: 先頭 {MAX_CHARS} 字まで。続きは Read で開くこと]"
        parts.append(
            f"[project-claudemd] projects/{name}/CLAUDE.md の全文（このセッション初回のみ注入。"
            f"sed/rg の部分窓で済ませず、以下の規約に従って応答すること）:\n\n{text}{note}"
        )
        done.add(name)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(sorted(done)))
    except Exception:
        pass
    if record_fire is not None:
        try:
            record_fire("feedback_project_claudemd_reachability", "transform",
                        hook_name="inject_project_claudemd", context=",".join(todo))
        except Exception:
            pass
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n\n".join(parts),
        }
    }, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
