#!/usr/bin/env python3
"""Deterministic test for promotion_layer_report.py — the promotion-layer (常時原則) GC.

The real CLAUDE.local.md currently has zero cold sections, so the cold-detection
path can only be exercised with synthetic input. This feeds a tiny CLAUDE.local.md
with one WARM section (its source memory has adopted records) and one COLD section
(no signal anywhere) plus a hermetic telemetry dir, and asserts:
  - the warm section is NOT flagged,
  - the cold section IS flagged as a demotion candidate,
  - hyphen/underscore wikilink aliases match the underscore slug in the log.

Run: python3 heaven/tools/test_promotion_layer_report.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "promotion_layer_report.py"

CLAUDE_LOCAL = """\
## 既存プロジェクト一覧

これは index 節でリンクも常時マーカーも無い → 監査対象外であるべき。

## 温かい原則（常時）

source は [[feedback_warm_norm]]。直近に adopted 記録がある。

## 冷えた原則（常時）

source は [[feedback-cold-norm]]（ハイフン別名 → underscore slug に正規化されて一致すべき）。
直近に adopted/touch/fire いずれの記録も無い。
"""


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(name)
        print(f"  [{'ok' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        local = tdp / "CLAUDE.local.md"
        local.write_text(CLAUDE_LOCAL, encoding="utf-8")

        telem = tdp / "telemetry"
        telem.mkdir()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        # warm: 2 adopted records for the warm norm's source memory
        (telem / "memory_adoption.jsonl").write_text(
            "\n".join(json.dumps({"ts": now, "memory": "feedback_warm_norm",
                                  "verdict": "adopted", "session": "s", "evidence": "x"})
                      for _ in range(2)) + "\n", encoding="utf-8")
        (telem / "memory_touches.jsonl").write_text("", encoding="utf-8")
        (telem / "hook_fires.jsonl").write_text("", encoding="utf-8")

        env = dict(os.environ, FRAME_TELEMETRY_DIR=str(telem))
        out = subprocess.run(
            [sys.executable, str(TOOL), "--days", "30", "--file", str(local)],
            capture_output=True, text=True, env=env, timeout=20,
        )
        report = out.stdout
        print(report)

        check("tool_exit_0", out.returncode == 0, out.stderr.strip()[:120])
        check("index_section_excluded", "既存プロジェクト一覧" not in report
              or "src=0" not in report.split("既存プロジェクト一覧")[0][-40:],
              "index 節は norm でないので候補表に出さない")
        # cold section flagged
        check("cold_section_flagged",
              "降格候補" in report and "冷えた原則" in report.split("降格候補")[-1])
        # warm section NOT in the candidate block
        cand_block = report.split("降格候補")[-1] if "降格候補" in report else ""
        check("warm_section_not_flagged", "温かい原則" not in cand_block)
        # hyphen alias matched underscore slug → warm got its 2 adopted
        check("alias_normalized_warm_has_signal", "[2/0/0/0]" in report)
        check("summary_zero_signal_is_1", "zero_signal=1" in report)

    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
