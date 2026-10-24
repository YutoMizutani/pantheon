#!/usr/bin/env python3
"""block_broad_pkill: 汎用語だけの pkill を止めて発火を記録し、固有の語を含む pkill は通して記録しない。

Run: python3 .claude/hooks/tests/test_block_broad_pkill.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401 — 発火の記録を使い捨ての場所へ向ける
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "block_broad_pkill.py"
LOG = Path(_hermetic.TELEMETRY_DIR) / "hook_fires.jsonl"


def run(cmd: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30).returncode


def fires() -> int:
    if not LOG.exists():
        return 0
    return sum(1 for l in LOG.read_text(encoding="utf-8").splitlines()
               if json.loads(l).get("rule_id") == "block_broad_pkill")


cases = [
    # (名前, コマンド, 期待 exit, 期待する発火の増分)
    ("インタプリタ名だけの pkill → 止めて記録", "pkill python3", 2, 1),
    ("スクリプトのパスで絞った pkill → 通して記録しない", "pkill -f tools/upgrade_worker.py", 0, 0),
]
bad = 0
for name, cmd, want_rc, want_fire in cases:
    before = fires()
    rc = run(cmd)
    got_fire = fires() - before
    ok = rc == want_rc and got_fire == want_fire
    bad += not ok
    print(("OK  " if ok else "NG  ") + f"{name}: exit={rc} want={want_rc} 発火+{got_fire} want=+{want_fire}")
sys.exit(1 if bad else 0)
