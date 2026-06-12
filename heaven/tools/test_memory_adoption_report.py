#!/usr/bin/env python3
"""memory_adoption_report.is_load_bearing の回帰テスト.

hook-enforced / hook-fired / reference 型の memory を deprecation 候補から
除外することを固定する。prevention 型 (応答に現れないので adopted=0 になる) を
「cold だから降格」と誤判定した実バグの回帰ガード:
  実在する hook 名 / ルール名 で guard されている memory が NEVER-READ 降格候補に
  載っていた問題を防ぐ (hook-enforced / hook-fired / reference の各ケースをカバー)。

実行:
    python3 -m pytest tools/test_memory_adoption_report.py -q
    python3 tools/test_memory_adoption_report.py
"""
from __future__ import annotations

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "memory_adoption_report", os.path.join(_HERE, "memory_adoption_report.py")
)
mar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mar)

lb = mar.is_load_bearing


def test_hook_enforced_is_exempt():
    # frontmatter が enforcement: hook* を宣言 → hook がルールを担うので
    # 応答 adoption=0 でも降格候補にしない
    assert lb("feedback", "hook", 0) is True
    assert lb("feedback", "hook+memory", 0) is True


def test_fired_is_exempt():
    # in-window で hook が実発火 (rule_id==slug) → demonstrably live
    assert lb("feedback", None, 3) is True


def test_reference_type_still_exempt():
    # 既存の reference 除外を壊さない (lookup table は never-read で正常)
    assert lb("reference", None, 0) is True


def test_plain_cold_feedback_is_candidate():
    # hook なし・fire なし・非 reference の cold feedback は降格候補のまま
    assert lb("feedback", None, 0) is False
    # enforcement が memory のみ (hook を含まない) は除外しない
    assert lb("feedback", "memory", 0) is False


if __name__ == "__main__":
    import types
    import traceback

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
