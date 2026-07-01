"""Hermetic telemetry for hook tests — importing this redirects all frame-hook
telemetry writes to a throwaway per-process tmp dir.

Why this exists (2026-06-29 monitoring of the self-improvement loop):
``_paths.TELEMETRY_DIR`` derives from ``CLAUDE_PROJECT_DIR`` which falls back to
``os.getcwd()`` when unset. A manual ``python3 tests/test_*.py`` run therefore
appended test records (``test-gate-*`` / ``sigv-*`` / ``test-cqf-*``) straight
into the operator's production logs
(``~/.claude/projects/<slug>/telemetry/reflection_gate.jsonl`` etc.). Those logs
are exactly what ``review:pantheon`` / ``rule-auditor`` read to judge whether the
loop is working, so the self-assessment was reading inflated counts
(reflection_gate.jsonl was 56% test rows; correction_dispatch.jsonl 100%).

The fix is the ``FRAME_TELEMETRY_DIR`` seam in ``_paths.py``. Importing this
module sets that env var to a fresh tmp dir BEFORE any hook subprocess is spawned
(children inherit ``os.environ``) and before any in-process ``import _paths``, so
every telemetry write during the test lands in the throwaway dir. The dir is
removed at interpreter exit. Idempotent — a caller that already pinned
``FRAME_TELEMETRY_DIR`` (e.g. a parent runner) is respected.

Usage: add ``import _hermetic  # noqa: F401`` near the top of a test module,
before it imports hook modules or spawns the hook. ``tests/`` is ``sys.path[0]``
when a test is run directly, so the bare import resolves.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile


def _install() -> str:
    existing = os.environ.get("FRAME_TELEMETRY_DIR")
    if existing:
        return existing
    d = tempfile.mkdtemp(prefix="frame_telemetry_test_")
    os.environ["FRAME_TELEMETRY_DIR"] = d
    atexit.register(lambda: shutil.rmtree(d, ignore_errors=True))
    return d


# Side effect on import — this is the whole point of the module.
TELEMETRY_DIR = _install()
