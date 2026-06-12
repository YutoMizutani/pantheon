#!/usr/bin/env python3
"""rule_adoption_report.classify の回帰テスト (機構2 adoption レンズ)。

実行:
    python3 -m pytest tools/test_rule_adoption_report.py -q
    python3 tools/test_rule_adoption_report.py
"""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "rule_adoption_report", os.path.join(_HERE, "rule_adoption_report.py")
)
rar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rar)

NOW = datetime(2026, 6, 7, tzinfo=timezone.utc)
# defaults: recent=7, repeat_days=3, quiet=14
ARGS = dict(recent=7, repeat_days=3, quiet=14)


def _c(distinct_days, days_since_last, enforcing=True):
    return rar.classify(
        {"distinct_days": distinct_days, "days_since_last": days_since_last, "enforcing": enforcing},
        NOW, ARGS["recent"], ARGS["repeat_days"], ARGS["quiet"],
    )[0]


def test_not_internalized_when_recurring_and_recent():
    # 5日にわたり再発・直近2日 = lesson 未定着 (feedback_positive_emission_markers の実形)
    assert _c(distinct_days=5, days_since_last=2) == "not-internalized"
    # ちょうど閾値: 3日・直近7日
    assert _c(distinct_days=3, days_since_last=7) == "not-internalized"


def test_decaying_when_recurred_then_quiet():
    # 再発したが直近は沈黙 → 内面化が進んだ可能性
    assert _c(distinct_days=4, days_since_last=20) == "decaying"


def test_single_incident():
    assert _c(distinct_days=1, days_since_last=2) == "single-incident"
    assert _c(distinct_days=1, days_since_last=30) == "single-incident"


def test_watch_when_recurring_but_mid_gap():
    # 再発閾値だが recent でも quiet でもない谷間 (例: 3日・10日前)
    assert _c(distinct_days=3, days_since_last=10) == "watch"


def test_audit_only_never_friction():
    # audit outcome は再発が激しくても friction でなく観測のみ
    assert _c(distinct_days=9, days_since_last=1, enforcing=False) == "audit-only"


def test_enforcing_flag_flips_verdict():
    # 同じ統計でも enforcing かどうかで判定が変わる
    assert _c(distinct_days=5, days_since_last=1, enforcing=True) == "not-internalized"
    assert _c(distinct_days=5, days_since_last=1, enforcing=False) == "audit-only"


if __name__ == "__main__":
    import types, traceback
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            try:
                fn(); passed += 1; print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
