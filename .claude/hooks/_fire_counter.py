"""Shared logging layer for hook fire telemetry.

Purpose: provide a single `record_fire(rule_id, outcome, ...)` call that any
hook can use to log when it actually fires. Aggregated JSONL is then mineable
for "rule X had 0 fires in the last N days" — the trigger for deprecation
review proposed in MEMORY.md section on rule-removal asymmetry.

Design:
  * Append-only JSONL at the path returned by `fires_log_path()`.
  * Best-effort: never raises into the caller's hook (hook failures must
    not break the harness). All exceptions are swallowed.
  * Minimal call surface: `record_fire(rule_id, outcome)` is enough.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

Outcome = Literal["block", "warn", "audit", "transform", "allow"]

# hooks は常に hooks dir が sys.path 上にある状態で実行/import される
from _paths import TELEMETRY_DIR as _TELEMETRY_DIR  # noqa: E402

_FIRES_LOG = _TELEMETRY_DIR / "hook_fires.jsonl"


def fires_log_path() -> Path:
    return _FIRES_LOG


def record_fire(
    rule_id: str,
    outcome: Outcome,
    *,
    hook_name: str | None = None,
    count: int = 1,
    context: str | None = None,
    extra: dict | None = None,
) -> None:
    """Append one fire record to the telemetry JSONL.

    rule_id   : memory file slug the hook is enforcing (e.g.
                "feedback_verify_before_negating"). The aggregator joins on
                this to map fires → memory entries.
    outcome   : "block"     = exit non-zero, prompt aborted
                "warn"      = stderr feedback, exit 0
                "audit"     = silent log only, exit 0
                "transform" = hook rewrote payload (mask/strip variety)
                "allow"     = hook upgraded a tool call to auto-approved
                              (permissionDecision=allow), exit 0
    hook_name : optional override; defaults to caller script basename without
                the .py extension.
    count     : number of distinct hits in this single fire (a hedge scan
                may find 3 hedge phrases in one turn — that's count=3).
    context   : optional short token / snippet for forensics. Keep it small;
                heavy context belongs in the per-hook audit log.
    extra     : optional dict merged into the record under an "extra" key.
                Reserved for metadata the reporter keys on — notably
                ``extra={"reason": "test fixture"}`` so synthetic verification
                fires are excluded by telemetry_report.is_test_fixture().
    """
    try:
        if hook_name is None:
            hook_name = _infer_hook_name()
        record = {
            "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rule_id": rule_id,
            "outcome": outcome,
            "hook": hook_name,
            "count": count,
        }
        if context:
            record["context"] = context[:200]
        if extra:
            record["extra"] = extra
        _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        with open(_FIRES_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return


def _infer_hook_name() -> str:
    try:
        argv0 = sys.argv[0] if sys.argv else ""
        if argv0:
            return os.path.splitext(os.path.basename(argv0))[0]
    except Exception:
        pass
    return "unknown"
