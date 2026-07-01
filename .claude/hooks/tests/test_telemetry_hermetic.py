#!/usr/bin/env python3
"""Regression: running a hook test must NOT append to the operator's real
telemetry (~/.claude/projects/<slug>/telemetry/).

Backstory (2026-06-29): _paths.TELEMETRY_DIR derived only from CLAUDE_PROJECT_DIR
(= cwd when unset), so manual `python3 tests/test_*.py` runs wrote test records
(test-gate-* / sigv-* / test-cqf-*) straight into the production logs that
review:pantheon / rule-auditor read as real activity (reflection_gate.jsonl was
56% test rows). The fix: the FRAME_TELEMETRY_DIR seam in _paths.py + tests/_hermetic.py
(imported here) which redirects every telemetry write to a throwaway tmp dir.

This test fails loudly if either half regresses:
  - the seam is removed from _paths.py (the spawned hook writes to the real dir), or
  - _hermetic stops setting FRAME_TELEMETRY_DIR (same effect).

Run: python3 .claude/hooks/tests/test_telemetry_hermetic.py
"""
from __future__ import annotations

import _hermetic  # noqa: F401 — sets FRAME_TELEMETRY_DIR before anything else
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
ACCEPTANCE_HOOK = HOOKS_DIR / "detect_acceptance_signal.py"
REPO_ROOT = HOOKS_DIR.parent.parent  # .../<repo>/.claude/hooks -> <repo>
EXAMPLE_PACK = HOOKS_DIR / "local" / "signals.json.example"


def _real_gate_log() -> Path:
    """Canonical production reflection_gate.jsonl path, computed the way _paths
    does WITHOUT the hermetic override (FRAME_TELEMETRY_DIR removed)."""
    env = dict(os.environ)
    env.pop("FRAME_TELEMETRY_DIR", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["PYTHONPATH"] = str(HOOKS_DIR)  # so `import _paths` resolves
    out = subprocess.run(
        [sys.executable, "-c",
         "import _paths,sys; sys.stdout.write(str(_paths.TELEMETRY_DIR/'reflection_gate.jsonl'))"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=20,
    )
    resolved = out.stdout.strip()
    if not resolved:
        raise RuntimeError(f"could not resolve real telemetry path: {out.stderr.strip()}")
    return Path(resolved)


def _count(p: Path) -> int:
    return sum(1 for _ in p.open(encoding="utf-8")) if p.exists() else 0


def _fire_transcript() -> str:
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="herm_reg_")
    msgs = [
        json.dumps({"message": {"role": "user", "content": [{"type": "text", "text": "実装して"}]}}),
        json.dumps({"message": {"role": "assistant", "content": (
            [{"type": "tool_use", "name": "Bash", "input": {}} for _ in range(6)]
            + [{"type": "text", "text": "done"}]
        )}}),
    ]
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(msgs) + "\n")
    return path


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        status = "ok" if cond else "FAIL"
        if not cond:
            failures.append(name)
        print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")

    herm_dir = os.environ.get("FRAME_TELEMETRY_DIR", "")
    check("hermetic_env_set", bool(herm_dir) and Path(herm_dir).is_dir(), herm_dir)

    real_gate = _real_gate_log()
    check("real_path_resolves_outside_tmp",
          herm_dir not in str(real_gate), str(real_gate))
    before = _count(real_gate)

    # Spawn the acceptance hook on a fire-eligible window. It inherits os.environ
    # (FRAME_TELEMETRY_DIR set by _hermetic), so its gate write must land in tmp.
    tx = _fire_transcript()
    env = dict(os.environ)
    env["FRAME_SIGNALS_FILE"] = str(EXAMPLE_PACK)
    env["CLAUDE_CORRECTION_QUEUE"] = f"/tmp/herm_reg_empty_{os.getpid()}.json"
    # Unique per run: the hook's per-session debounce file lives in /tmp (NOT the
    # hermetic dir), so a fixed id would make this test flaky across runs.
    sid = f"herm-reg-fire-{os.getpid()}"
    proc = subprocess.run(
        [sys.executable, str(ACCEPTANCE_HOOK)],
        input=json.dumps({"prompt": "ok", "transcript_path": tx,
                          "session_id": sid}),
        capture_output=True, text=True, timeout=20, env=env,
    )
    fired = "AUTO-LEARN-META" in proc.stdout
    check("hook_fired_on_eligible_window", fired)

    herm_gate = Path(herm_dir) / "reflection_gate.jsonl"
    check("gate_write_landed_in_tmp", herm_gate.exists() and _count(herm_gate) >= 1,
          str(herm_gate))

    after = _count(real_gate)
    check("real_telemetry_unchanged", before == after, f"{before} -> {after}")

    for cleanup in (tx, f"/tmp/claude_acceptance_signal_last_{sid}.txt",
                    env["CLAUDE_CORRECTION_QUEUE"]):
        try:
            os.unlink(cleanup)
        except OSError:
            pass

    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
