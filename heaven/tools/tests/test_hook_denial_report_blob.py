#!/usr/bin/env python3
"""hook_denial_report.scan() が API 要求の写しの行を拒否として数えないことの回帰テスト。

transcript には `type: "api-request-blob"` の行があり、それまでの tool_use・tool_result を
同じ id のまま含む。これを数えると 1 回の拒否が 2 回になり、同じ入力の再拒否（空回り）にも見える。

実行:
    python3 heaven/tools/tests/test_hook_denial_report_blob.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS)

import hook_denial_report as r  # noqa: E402

HOOK = "inject_rule_on_tool.py"
DENIAL = (
    'PreToolUse:Bash hook error: [f="$CLAUDE_PROJECT_DIR/.claude/hooks/' + HOOK
    + '"; [ -f "$f" ] || exit 0; python3 "$f"]: [規則の読み込み] この操作に関わる規則です'
)
USE = {"type": "tool_use", "id": "X", "name": "Bash", "input": {"command": "git commit"}}
RESULT = {"type": "tool_result", "tool_use_id": "X", "content": DENIAL, "is_error": True}


def _lines() -> list:
    return [
        {"type": "assistant", "timestamp": "2026-11-19T12:00:00Z",
         "message": {"role": "assistant", "content": [USE]}},
        {"type": "user", "timestamp": "2026-11-19T12:00:01Z",
         "message": {"role": "user", "content": [RESULT]}},
        {"type": "api-request-blob", "sessionId": "s", "hash": "h",
         "message": {"role": "user", "content": [USE, RESULT]}},
    ]


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for line in _lines():
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        stat = r.scan([path])
    s = stat.get(HOOK)
    got = (s["denials"], s["spins"], s["max_run"]) if s else None
    want = (1, 0, 1)
    if got != want:
        print(f"FAIL: {HOOK} (denials, spins, max_run) = {got}, want {want}")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
