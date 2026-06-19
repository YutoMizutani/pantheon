#!/usr/bin/env python3
"""inject_correction_nudge — stateless lightweight push-back handler.

On a UserPromptSubmit whose prompt matches a correction signal (the user pushing
back on the prior Claude turn), inject ONE <system-reminder> block nudging the
MAIN Claude to FIRST CLASSIFY the push-back before reacting:
  (a) empirical correction — the user holds a decisive observation/measurement
      (real-machine observation, primary-source value) → verify it and defer
      (the user's ground truth outranks Claude's proxy reasoning); or
  (b) design/judgment counter-proposal — neither side holds a decisive
      measurement ("which approach is better") → DON'T cave by default and
      DON'T stubbornly defend; write the shared judgment criteria and score
      both options on them before concluding.
The classification (which the regex layer cannot do — same surface vocabulary
for both) lives in the main model, which has the in-context content the hook
lacks. This generalizes feedback_no_high_confidence_when_decisive_measurement_absent
and feedback_user_observation_outranks_proxy_diagnosis to the symmetric case:
absent a decisive measurement, grant authority to NEITHER side.

STATELESS by design: no queue, no memory write, no sub-agent spawn. Durable
learning waits for an explicit acceptance signal — detect_acceptance_signal.py
is the sole memory-write gate.

Replaces the retired global correction queue (INC-2026-06-17-01 /
docs/design-self-improvement-two-tier-intake.md): that queue drained
cross-subject corrections into unrelated reflections and self-fed on
meta-discussion. A stateless nudge can do neither — it only affects the current
turn of the current session, and a false positive costs one stray reminder line.

Vocabulary is user calibration from local/signals.json via _signals.py; topic
words (自己改善 / 次から / ...) are deliberately NOT triggers (the self-feed root).

Guards: correction signal present; not entirely third-party-negation; previous
turn was assistant (something to correct); per-session debounce; skip
system/automated prompts. Fail-open: any error → exit 0 (never block the turn).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _signals import correction_pattern_sets  # noqa: E402
    _SETS = correction_pattern_sets()
except Exception as exc:  # config layer must never kill the hook
    sys.stderr.write(f"[inject_correction_nudge] _signals fallback: {exc}\n")
    _SETS = {"patterns": (), "third_party_negation": ()}
_PATTERNS = _SETS.get("patterns", ())
_TP_PATTERNS = _SETS.get("third_party_negation", ())

try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return

# Per-session debounce so a multi-turn back-and-forth doesn't nudge every turn.
_DEBOUNCE_SECONDS = 30
_DEBOUNCE_DIR = Path("/tmp")
_DEBOUNCE_PREFIX = "claude_correction_nudge_last_"
_STALE_TTL_SECONDS = 86400

_SYSTEM_USER_PREFIXES = (
    "<task-notification>", "<system-reminder>",
    "<command-message>", "<command-name>",
)

_NUDGE = """<system-reminder>
[correction-signal] 直前のあなた(Claude)の応答への push-back の可能性があります。応答の前に、まず何への指摘かを分類してください:
(a) **事実の訂正** — ユーザーが決定的な観測/測定（実機観測・一次ソースの値）を握っている場合。→ それを検証し、ユーザー観測を上位として自説を改める。
(b) **設計/判断の対案** — 「どちらの案が良いか」等、どちらも決定的測定を握っていない場合。→ 迎合して自動的にユーザー案を採らない・自案にも固執しない。両案の判定基準を 1–2 行で書き出し、自案とユーザー案を各基準で中立に採点してから結論を出す。
そのうえで誤りが見つかればその場で修正してください。**reflection の spawn・memory 書き込みはしないこと** — durable な学習は user の明示 acceptance（「完了」「ok」等）を待ちます。push-back でなければ（第三者の状態説明等）この行を無視して通常どおり応答してください。
</system-reminder>"""


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _matches_only_third_party(prompt: str) -> bool:
    """True iff every correction match lies inside a third-party-negation span
    (e.g. 「Xがないとだめ」 = structural-state description, not a Claude critique)."""
    spans: list[tuple[int, int]] = []
    for tp in _TP_PATTERNS:
        for m in tp.finditer(prompt):
            spans.append(m.span())

    def _inside(span: tuple[int, int]) -> bool:
        s, e = span
        return any(ts <= s and e <= te for ts, te in spans)

    any_corr = False
    for cp in _PATTERNS:
        for m in cp.finditer(prompt):
            any_corr = True
            if not _inside(m.span()):
                return False
    return any_corr


def _has_correction(prompt: str) -> bool:
    if _matches_only_third_party(prompt):
        return False
    return any(p.search(prompt) for p in _PATTERNS)


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
    safe = "".join(c for c in (sid or "") if c.isalnum() or c in "-_") or "nosession"
    return _DEBOUNCE_DIR / f"{_DEBOUNCE_PREFIX}{safe}.txt"


def _sweep_stale(now: float) -> None:
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


def main() -> int:
    data = _read_payload()
    prompt = data.get("prompt") or ""
    transcript = data.get("transcript_path") or ""
    sid = data.get("session_id") or ""

    if not prompt:
        return 0
    if prompt.lstrip().startswith(_SYSTEM_USER_PREFIXES):
        return 0
    if not _has_correction(prompt):
        return 0
    if not _previous_turn_was_assistant(transcript):
        return 0
    if not _check_and_set_debounce(sid):
        return 0

    # Inject the nudge as UserPromptSubmit context (mirrors detect_acceptance_signal's
    # stdout <system-reminder> emission). STATELESS — no queue, no memory, no spawn.
    sys.stdout.write(_NUDGE + "\n")
    record_fire("inject_correction_nudge", "audit", context="nudge emitted")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail-open: never block the user's turn
        sys.stderr.write(f"[inject_correction_nudge] error: {exc}\n")
        sys.exit(0)
