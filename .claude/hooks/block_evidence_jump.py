#!/usr/bin/env python3
"""block_evidence_jump — linguistic scanner for entity-disappearance assertions
paired with destructive remediation switches.

Memory rule:
  - ``feedback_cross_check_evidence_before_destructive_plan.md`` (2026-05-23)

Stop event hook. Detects the failure pattern where a single signal (e.g.
"only window1 in latest log directory") is used to assert entity
disappearance ("残り 2 は消失") and immediately followed by a destructive
remediation switch (logout/login provider change, wipe, kill chain), often
ending in an unverified user-action request.

The hook audits the latest assistant turn for co-occurrence of:
  (A) entity-disappearance assertion phrases — "残り N は消失", "N だけ起動",
      "window N が無い", "N が消えた", "壊れた", "死んだ", "飛んだ"
  (B) destructive remediation switch phrases — "provider 切替", "logout +
      login", "方針で〜に切り替え", "wipe して", "再起動して"

When both categories hit in the same turn, the hook writes to the audit
log so the regression is observable, and emits a stderr remind line so
the next turn can self-correct (e.g. cross-check pid_count / TCP / status
before remediation, or downgrade the assertion to a hedge form).

Exit 0 always — audit + remind only.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _fire_counter import record_fire  # noqa: E402

# Category A: entity-disappearance assertions.
_DISAPPEARANCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"残り\s*\d+\s*[はが]?\s*消失"),
    re.compile(r"\d+\s*だけ起動"),
    re.compile(r"window\s*\d+\s*が[無な]い"),
    re.compile(r"\d+\s*が消えた"),
    re.compile(r"壊れた"),
    re.compile(r"死んだ"),
    re.compile(r"飛んだ"),
    re.compile(r"消失した"),
    re.compile(r"window が消えた"),
    re.compile(r"全部消えた"),
)

# Category B: destructive remediation switches.
_DESTRUCTIVE_REMEDIATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"provider\s*切替"),
    re.compile(r"provider\s*を?切り替え"),
    re.compile(r"logout[^\n]{0,40}login"),
    re.compile(r"切り替える方針"),
    re.compile(r"方針で[^\n]{0,30}切り替え"),
    re.compile(r"切替えて様子"),
    re.compile(r"wipe\s*して"),
    re.compile(r"完全消去"),
    re.compile(r"再起動して様子"),
    re.compile(r"kill\s*して.*再起動"),
)

from _paths import RUNTIME_DIR  # noqa: E402
_AUDIT_LOG = RUNTIME_DIR / "block_evidence_jump_audit.log"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _scan_current_turn_assistant_text(transcript_path: str) -> str | None:
    """Return concatenated assistant text since the last user message."""
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        return None
    turn_parts: list[str] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "user":
                turn_parts = []
                continue
            if role != "assistant":
                continue
            content = msg.get("content")
            parts: list[str] = []
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(str(c.get("text", "")))
            text = "\n".join(parts).strip()
            if text:
                turn_parts.append(text)
    except OSError:
        return None
    if not turn_parts:
        return None
    return "\n".join(turn_parts)


def _find_hits(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
) -> list[tuple[str, int, int]]:
    hits: list[tuple[str, int, int]] = []
    for pat in patterns:
        for m in pat.finditer(text):
            hits.append((m.group(0), m.start(), m.end()))
    return hits


def _log_hits(
    sid: str,
    disappearance_hits: list[tuple[str, int, int]],
    remediation_hits: list[tuple[str, int, int]],
    text: str,
) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "ts": ts,
            "session_id": sid,
            "category": "evidence_jump",
            "disappearance_tokens": sorted({t for t, _, _ in disappearance_hits}),
            "remediation_tokens": sorted({t for t, _, _ in remediation_hits}),
            "context_excerpt": text[:600],
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    data = _read_payload()
    sid = data.get("session_id") or ""
    transcript = data.get("transcript_path") or ""
    if not sid or not transcript:
        return 0
    text = _scan_current_turn_assistant_text(transcript)
    if not text:
        return 0

    disappearance_hits = _find_hits(text, _DISAPPEARANCE_PATTERNS)
    remediation_hits = _find_hits(text, _DESTRUCTIVE_REMEDIATION_PATTERNS)

    # Only fire when both categories hit in the same turn — that is the
    # specific failure pattern (assertion + destructive switch). Either
    # alone is benign or already covered by other rules.
    if disappearance_hits and remediation_hits:
        _log_hits(sid, disappearance_hits, remediation_hits, text)
        dtokens = ", ".join(sorted({t for t, _, _ in disappearance_hits}))
        rtokens = ", ".join(sorted({t for t, _, _ in remediation_hits}))
        record_fire(
            "feedback_cross_check_evidence_before_destructive_plan",
            "warn",
            count=len(disappearance_hits) + len(remediation_hits),
            context=f"d:{dtokens}|r:{rtokens}",
        )
        sys.stderr.write(
            f"[block_evidence_jump] evidence-jump pattern detected: "
            f"disappearance={dtokens} + remediation={rtokens}. "
            "Per feedback_cross_check_evidence_before_destructive_plan: "
            "cross-check parallel signals (pid_count / TCP / status) before "
            "executing destructive remediation, or downgrade the assertion "
            "to a hedge form with the missing signal noted.\n"
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[block_evidence_jump] error: {exc}\n")
        sys.exit(0)
