#!/usr/bin/env python3
"""Deterministic test for proposal_ledger.py — the re-proposal churn guard.

Asserts: (1) same fingerprint crossing the threshold flips PROCEED→SUPPRESS;
(2) a different fingerprint is independent; (3) TTL expiry resets the count
(an old proposal no longer suppresses); (4) entries past the prune horizon are
dropped on write so the file stays bounded; (5) source ordering / hyphen-vs-
underscore aliasing do not change the fingerprint.

Run: python3 heaven/tools/test_proposal_ledger.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(name)
        print(f"  [{'ok' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")

    with tempfile.TemporaryDirectory() as td:
        os.environ["FRAME_TELEMETRY_DIR"] = td
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        pl = importlib.import_module("proposal_ledger")
        pl = importlib.reload(pl)  # pick up FRAME_TELEMETRY_DIR

        T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        src, tgt = "feedback_b,feedback_a", "CLAUDE.local.md"

        # (1) threshold default 2: 1st/2nd PROCEED, 3rd SUPPRESS
        d1, n1 = pl.check_and_record(src, tgt, 14, 2, now=T0)
        d2, n2 = pl.check_and_record(src, tgt, 14, 2, now=T0 + timedelta(hours=1))
        d3, n3 = pl.check_and_record(src, tgt, 14, 2, now=T0 + timedelta(hours=2))
        check("first_proceeds", d1 == "PROCEED" and n1 == 0, f"{d1} {n1}")
        check("second_proceeds", d2 == "PROCEED" and n2 == 1, f"{d2} {n2}")
        check("third_suppresses", d3 == "SUPPRESS" and n3 == 2, f"{d3} {n3}")

        # (5) reordered source + hyphen alias → SAME fingerprint → still suppressed
        d_alias, n_alias = pl.check_and_record(
            "feedback-a,feedback-b", tgt, 14, 2, now=T0 + timedelta(hours=3))
        check("alias_and_order_same_fp", d_alias == "SUPPRESS" and n_alias == 3,
              f"{d_alias} {n_alias}")

        # (2) different fingerprint is independent
        d_other, n_other = pl.check_and_record(
            "feedback_c", tgt, 14, 2, now=T0 + timedelta(hours=4))
        check("different_fp_independent", d_other == "PROCEED" and n_other == 0,
              f"{d_other} {n_other}")

        # (3) TTL expiry: 20 days later, the prior same-fp records are outside the
        #     14-day window → count resets → PROCEED again.
        d_exp, n_exp = pl.check_and_record(src, tgt, 14, 2, now=T0 + timedelta(days=20))
        check("ttl_expiry_resets_count", d_exp == "PROCEED" and n_exp == 0,
              f"{d_exp} {n_exp}")

        # (4) prune horizon: a record 100 days later drops everything >90d old.
        before = sum(1 for _ in open(pl.LEDGER, encoding="utf-8"))
        pl.check_and_record("feedback_z", tgt, 14, 2, now=T0 + timedelta(days=100))
        lines = [l for l in open(pl.LEDGER, encoding="utf-8") if l.strip()]
        # everything stamped at T0 (now 100d old) must be pruned; only the 20d-mark
        # record, the 100d record remain young enough relative to 100d? prune cut =
        # (T0+100d) - 90d = T0+10d. So T0..T0+4h pruned; T0+20d & T0+100d kept.
        check("prune_drops_old_entries", len(lines) == 2,
              f"before={before} after={len(lines)}")

        del os.environ["FRAME_TELEMETRY_DIR"]

    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
