#!/usr/bin/env python3
"""PreToolUse: その道具を使う瞬間に、関わる規則の全文を 1 回だけ渡す（規則を常時読み込みから下ろすための受け口）。

`.claude/rules/` の規則は毎回の呼び出しで読み直されるので、特定の道具を使うときにしか効かない規則を
そこに置くと、使わないセッションでも全呼び出しで費用を払う。そうした規則は `rules-ondemand/` に置き、
この hook が引き金の道具を初めて使った瞬間に渡す。

引き金の一覧（広い方から順に読み、両方とも効く）:
  1. `<repo>/.claude/rules-ondemand/triggers.json`（フレーム層）
  2. `<repo>/.local/rules-ondemand/triggers.json`（ローカル層）
各要素: {"rule": "<一覧からの相対パス>", "tools": ["Bash", ...],
         "match": {"<tool_input の欄>": "<正規表現>", ...}（任意。どれか 1 つ当たれば引き金）,
         "mode": "block" | "context"}
  - block:   呼び出しを 1 回止め、規則の全文を理由として返す（取り消しにくい操作・引数を規則で決める呼び出し向け。
             PreToolUse の追加の文は実行結果の後に届くので、その前に読ませるにはこれしかない）
  - context: 止めずに追加の文として差し込む（その後の進め方に効く規則向け）
1 つの規則は、セッション（とサブエージェント）ごとに 1 回だけ渡す。一覧が無ければ何もしない（opt-in）。
例外時は黙って通す (exit 0)。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _paths import PROJECT_DIR  # noqa: E402
except Exception:  # pragma: no cover
    sys.exit(0)

try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover
    record_fire = None

TRIGGER_FILES = (".claude/rules-ondemand/triggers.json", ".local/rules-ondemand/triggers.json")
MAX_CHARS = 20000


def _triggers():
    for rel in TRIGGER_FILES:
        f = PROJECT_DIR / rel
        try:
            items = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for t in items if isinstance(items, list) else []:
            if isinstance(t, dict) and t.get("rule"):
                yield f.parent / t["rule"], t


def _hits(t: dict, tool: str, tool_input: dict) -> bool:
    if tool not in (t.get("tools") or []):
        return False
    match = t.get("match")
    if not match:
        return True
    for field, pat in match.items():
        v = tool_input.get(field)
        if isinstance(v, str) and re.search(pat, v):
            return True
    return False


def _state_path(sid: str, agent_id: str) -> Path:
    d = Path(tempfile.gettempdir()) / "claude-inject-rule-on-tool"
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^A-Za-z0-9_-]", "_", f"{sid}_{agent_id}") + ".json")


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    sid = str(data.get("session_id") or "")
    if not tool or not sid or not isinstance(tool_input, dict):
        return 0

    sp = _state_path(sid, str(data.get("agent_id") or ""))
    try:
        done = set(json.loads(sp.read_text()))
    except Exception:
        done = set()

    block, context = [], []
    for path, t in _triggers():
        key = str(path)
        if key in done or not _hits(t, tool, tool_input):
            continue
        try:
            text = path.read_text(encoding="utf-8")[:MAX_CHARS]
        except Exception:
            continue
        done.add(key)
        try:
            rel = path.relative_to(PROJECT_DIR)
        except ValueError:
            rel = path
        (block if t.get("mode") == "block" else context).append(f"--- {rel} ---\n{text}")
        if record_fire:
            try:
                record_fire("inject_rule_on_tool", "block" if t.get("mode") == "block" else "transform",
                            context=str(rel), session_id=sid)
            except Exception:
                pass

    if not block and not context:
        return 0
    try:
        sp.write_text(json.dumps(sorted(done)))
    except Exception:
        pass

    if block:
        print("[規則の読み込み] この操作に関わる規則です（このセッションで 1 回だけ渡します）。"
              "読んだうえで、必要なら呼び出しを直して、もう一度出してください。\n\n"
              + "\n\n".join(block + context), file=sys.stderr)
        return 2
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "additionalContext": "[規則の読み込み] この操作に関わる規則です。\n\n"
                                             + "\n\n".join(context)}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
