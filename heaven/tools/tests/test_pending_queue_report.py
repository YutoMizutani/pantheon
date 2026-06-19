#!/usr/bin/env python3
"""Regression tests for pending_queue_report._obsolete_reason.

Focus: the 2026-06-20 fix for the `.md`-suffix mismatch. Memory files on disk are
``<slug>.md`` but the promotion queues store source-memory *slugs* without the
extension. The old check ``(MEMORY_DIR / s).exists()`` therefore reported every
real memory as missing and flagged agent-def items as bogus DROP candidates. The
fix (`_mem_exists`) accepts both the bare slug and the `.md` form.

Hermetic: a temp MEMORY_DIR is monkeypatched in, so the suite does not depend on
the operator's real memory corpus. Run: python3 test_pending_queue_report.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
TOOLS = HERE.parent.parent  # heaven/tools
sys.path.insert(0, str(TOOLS))

import pending_queue_report as r  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="test_pqr_"))
    (tmp / "real_mem.md").write_text("body", encoding="utf-8")
    (tmp / "MEMORY.md").write_text("index", encoding="utf-8")

    # Monkeypatch the module-level MEMORY_DIR to the hermetic temp dir.
    orig_dir = r.MEMORY_DIR
    orig_excluded = set(r.EXCLUDED_TARGETS)
    r.MEMORY_DIR = tmp
    r.EXCLUDED_TARGETS = {"/some/frame/CLAUDE.md"}

    # name, item, predicate(result) -> bool, human description of expectation
    cases = [
        (
            "real_slug_without_md_not_dropped",  # the core regression
            {"source_memories": ["real_mem"], "target_file": "x"},
            lambda res: res is None,
            "実在 memory (slug, .md 無し) は DROP しない",
        ),
        (
            "real_slug_with_md_not_dropped",
            {"source_memories": ["real_mem.md"], "target_file": "x"},
            lambda res: res is None,
            ".md を含む slug でも在席扱い",
        ),
        (
            "all_missing_still_flagged",
            {"source_memories": ["nope_xyz_999"], "target_file": "x"},
            lambda res: isinstance(res, str) and "不在" in res,
            "真に不在の memory は引き続き DROP 候補に",
        ),
        (
            "mixed_present_and_missing_not_dropped",
            {"source_memories": ["real_mem", "nope_xyz_999"], "target_file": "x"},
            lambda res: res is None,
            "1 つでも実在すれば全不在でないので None",
        ),
        (
            "excluded_target_flagged",
            {"source_memories": ["real_mem"], "target_file": "/some/frame/CLAUDE.md"},
            lambda res: isinstance(res, str) and "昇格対象外" in res,
            "frame 専用 target は昇格対象外",
        ),
        (
            "empty_sources_not_dropped",
            {"source_memories": [], "target_file": "x"},
            lambda res: res is None,
            "source_memories 空は判定対象外",
        ),
        (
            "non_dict_item_none",
            ["not", "a", "dict"],
            lambda res: res is None,
            "dict でない item は None",
        ),
    ]

    failed = []
    try:
        for name, item, pred, desc in cases:
            res = r._obsolete_reason(item)
            ok = bool(pred(res))
            tag = "ok" if ok else "FAIL"
            print(f"  [{tag}] {name}: {desc} -> {res!r}")
            if not ok:
                failed.append(name)
    finally:
        r.MEMORY_DIR = orig_dir
        r.EXCLUDED_TARGETS = orig_excluded

    print()
    if failed:
        print(f"{len(failed)} FAILED: {failed}")
        return 1
    print(f"all {len(cases)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
