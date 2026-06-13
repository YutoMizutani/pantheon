#!/usr/bin/env python3
"""detect_correction_signal_v2 — tightened correction-signal detector with
context-aware false-positive exclusions. Drop-in replacement for
`detect_correction_signal.py`.

Memory rules:
  - ``feedback_classify_failure_saying_vs_judgement.md`` (classification framework)
  - ``feedback_verify_before_negating.md`` (hedge guard)
  - ``feedback_minimize_user_actions_absolute.md`` (role-reversal guard)
  - ``feedback_correction_signal_third_party_negation.md`` (this file's
    raison d'etre: bare ``だめ`` / ``違う`` / ``本当に？`` matched on
    third-party-state descriptions; tightened with exclusion patterns)

UserPromptSubmit event hook. Scans the user's current prompt for correction
signals (the user pointing out a Claude failure). On hit, appends one event to
the correction queue (``~/.claude/runtime/pending_correction_reflections.json``)
and stays SILENT — no stdout, no sub-agent spawn.

Batched-queue redesign (原環境 user feedback「やりとり中の自己発火は過剰実装」):
this hook used to inject a reminder that spawned a background reflection
sub-agent mid-conversation (14 fires/day observed). That interleaved
task-notifications with primary findings and wrote memory without review.
Now the reflection itself runs ONCE, batched, when the user closes the task
with an exact acceptance signal — ``detect_acceptance_signal.py`` drains this
queue and hands all pending corrections to a single reflection sub-agent.
Sessions that never say "ok" are recovered by the next acceptance fire in any
session (the queue is global) and can be surfaced by periodic checks when stale.

Differences from v1:
  - Third-party-negation patterns short-circuit detection (「X がないとだめ」
    is a structural-state description, not a Claude critique)
  - Acceptance-prefix patterns (なるほど / ありがとう / 了解 / わかった ...)
    suppress detection UNLESS an explicit improvement-request marker
    (次から / 今後は / 再発防止 / 自己改善 / 分析して...修正) co-occurs

Detection rules:
  * If acceptance-prefix WITHOUT explicit-improvement marker: skip
  * If only matched correction patterns are entirely covered by third-party
    negation matches: skip
  * Otherwise: match high-precision correction phrases in the user's prompt
  * Require the previous assistant turn to exist (something to reflect on)
  * Suppress if a recent reflection was already triggered (anti-spam)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Pattern vocabulary — calibration constants, NOT mechanism. The four groups
# (correction phrases / third-party-negation exclusions / acceptance prefixes /
# explicit improvement markers) encode how one specific user phrases
# corrections, with the third-party-negation set born from an observed origin-
# environment false positive ("claudeがないとだめなんだね" — structural-state
# description, not a Claude critique; see
# feedback_correction_signal_third_party_negation.md). They load via
# _signals.py: conservative English defaults, overridden per-key by the
# local-layer config .claude/hooks/local/signals.json (the origin-environment
# Japanese pack ships as signals.json.example). The detection RULES that
# combine these groups (_has_correction_signal below) stay hardcoded here.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _signals import correction_pattern_sets  # noqa: E402
    _PATTERN_SETS = correction_pattern_sets()
except Exception as _sig_exc:  # config layer must never kill the hook
    sys.stderr.write(f"[detect_correction_signal_v2] _signals fallback: {_sig_exc}\n")
    _PATTERN_SETS = {
        "patterns": (), "third_party_negation": (),
        "acceptance_prefix": (), "explicit_improvement": (),
    }
_CORRECTION_PATTERNS: tuple[re.Pattern[str], ...] = _PATTERN_SETS["patterns"]
_THIRD_PARTY_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = _PATTERN_SETS["third_party_negation"]
_ACCEPTANCE_PREFIX_PATTERNS: tuple[re.Pattern[str], ...] = _PATTERN_SETS["acceptance_prefix"]
_EXPLICIT_IMPROVEMENT_PATTERNS: tuple[re.Pattern[str], ...] = _PATTERN_SETS["explicit_improvement"]

# Anti-spam: don't trigger reflection twice within this many seconds.
# Per-SESSION debounce — see _debounce_file() / _check_and_set_debounce() below.
# A single shared file used to live here (/tmp/claude_correction_signal_last.txt)
# and caused a cross-session collision: one session's correction signal could
# suppress a concurrent session's within the cooldown window.
_DEBOUNCE_SECONDS = 30
_DEBOUNCE_DIR = Path("/tmp")
_DEBOUNCE_PREFIX = "claude_correction_signal_last_"
# GC abandoned per-session debounce files after this long. Far larger than the
# debounce window, so an active session's file is never swept.
_STALE_TTL_SECONDS = 86400

# Correction queue — drained by detect_acceptance_signal.py on the next exact
# acceptance signal ("ok"/"完了"/...). Env override exists so tests stay hermetic.
_QUEUE_FILE = Path(
    os.environ.get(
        "CLAUDE_CORRECTION_QUEUE",
        str(Path.home() / ".claude/runtime/pending_correction_reflections.json"),
    )
)
# Hard cap: if acceptance never fires, the queue must not grow unbounded.
# Oldest items are dropped first; the drop is recorded via record_fire context.
_QUEUE_MAX_ITEMS = 50
_EXCERPT_LIMIT = 400


def _load_queue() -> dict:
    if not _QUEUE_FILE.exists():
        return {"items": []}
    try:
        data = json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"items": []}
    return data


def _append_to_queue(sid: str, transcript: str, prompt: str) -> int:
    """Append one correction event; return resulting queue length."""
    queue = _load_queue()
    queue["items"].append(
        {
            "session_id": sid,
            "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "transcript_path": transcript,
            "prompt_excerpt": prompt.strip()[:_EXCERPT_LIMIT],
        }
    )
    if len(queue["items"]) > _QUEUE_MAX_ITEMS:
        queue["items"] = queue["items"][-_QUEUE_MAX_ITEMS:]
    _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QUEUE_FILE.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return len(queue["items"])


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _has_acceptance_prefix(prompt: str) -> bool:
    return any(p.search(prompt) for p in _ACCEPTANCE_PREFIX_PATTERNS)


def _has_explicit_improvement(prompt: str) -> bool:
    return any(p.search(prompt) for p in _EXPLICIT_IMPROVEMENT_PATTERNS)


def _correction_matches_only_third_party(prompt: str) -> bool:
    """True iff every だめ/ダメ correction match is inside a third-party-negation
    match span. Returns False if any correction pattern matches outside of
    third-party-negation context (genuine Claude critique).
    """
    tp_spans: list[tuple[int, int]] = []
    for tp in _THIRD_PARTY_NEGATION_PATTERNS:
        for m in tp.finditer(prompt):
            tp_spans.append(m.span())

    def _inside_tp(span: tuple[int, int]) -> bool:
        s, e = span
        for ts, te in tp_spans:
            if ts <= s and e <= te:
                return True
        return False

    any_correction = False
    for cp in _CORRECTION_PATTERNS:
        for m in cp.finditer(prompt):
            any_correction = True
            if not _inside_tp(m.span()):
                # Genuine correction outside third-party-negation context
                return False
    # All correction matches were inside third-party-negation spans
    return any_correction


def _has_correction_signal(prompt: str) -> bool:
    # Acceptance prefix without explicit-improvement marker → suppress
    if _has_acceptance_prefix(prompt) and not _has_explicit_improvement(prompt):
        return False
    # All matches inside third-party-negation → suppress
    if _correction_matches_only_third_party(prompt):
        return False
    return any(p.search(prompt) for p in _CORRECTION_PATTERNS)


def _previous_turn_was_assistant(transcript_path: str) -> bool:
    if not transcript_path:
        return False
    p = Path(transcript_path)
    if not p.exists():
        return False
    last_role: str | None = None
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
            if role in ("user", "assistant"):
                last_role = role
    except OSError:
        return False
    return last_role == "assistant"


def _debounce_file(sid: str) -> Path:
    """Per-session debounce path. Keying by session_id prevents one session's
    correction signal from suppressing a concurrent session's within the
    cooldown window (the bug the single shared file caused)."""
    safe = "".join(c for c in (sid or "") if c.isalnum() or c in "-_") or "nosession"
    return _DEBOUNCE_DIR / f"{_DEBOUNCE_PREFIX}{safe}.txt"


def _sweep_stale(now: float) -> None:
    """Best-effort GC of abandoned per-session debounce files plus the legacy
    shared file from before the per-session migration. TTL is far longer than
    the debounce window, so a concurrently-active session's file is never
    removed. Fully swallowed on error — cleanup must never break the hook."""
    try:
        legacy = _DEBOUNCE_DIR / f"{_DEBOUNCE_PREFIX[:-1]}.txt"
        if legacy.exists():
            legacy.unlink()
    except OSError:
        pass
    try:
        for old in _DEBOUNCE_DIR.glob(f"{_DEBOUNCE_PREFIX}*.txt"):
            try:
                if now - old.stat().st_mtime > _STALE_TTL_SECONDS:
                    old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _check_and_set_debounce(sid: str) -> bool:
    now = time.time()
    _sweep_stale(now)
    f = _debounce_file(sid)
    try:
        if f.exists():
            last = float(f.read_text().strip() or "0")
            if now - last < _DEBOUNCE_SECONDS:
                return False
    except (OSError, ValueError):
        pass
    try:
        f.write_text(str(now))
    except OSError:
        pass
    return True


sys.path.insert(0, str(Path(__file__).parent))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # never let telemetry import break a hook
    def record_fire(*_a, **_k) -> None:  # type: ignore
        return None


def main() -> int:
    data = _read_payload()
    prompt = data.get("prompt") or ""
    transcript = data.get("transcript_path") or ""
    sid = data.get("session_id") or ""

    if not prompt:
        return 0
    stripped = prompt.lstrip()
    if stripped.startswith("<task-notification>"):
        return 0
    if stripped.startswith("<system-reminder>"):
        return 0
    if stripped.startswith("<command-message>"):
        return 0
    if stripped.startswith("<command-name>"):
        return 0
    if not _has_correction_signal(prompt):
        return 0
    if not _previous_turn_was_assistant(transcript):
        return 0
    if not _check_and_set_debounce(sid):
        return 0

    try:
        qlen = _append_to_queue(sid, transcript, prompt)
    except OSError as exc:
        sys.stderr.write(f"[detect_correction_signal_v2] queue write failed: {exc}\n")
        return 0
    # Silent by design: no stdout. The reflection runs batched at the next
    # acceptance signal; record_fire keeps this observable in telemetry.
    record_fire("detect_correction_signal_v2", "audit", context=f"queued n={qlen}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[detect_correction_signal_v2] error: {exc}\n")
        sys.exit(0)
