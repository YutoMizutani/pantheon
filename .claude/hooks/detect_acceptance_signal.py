#!/usr/bin/env python3
"""detect_acceptance_signal — auto-trigger META self-improvement on user acceptance.

Counterpart to ``detect_correction_signal_v2.py``. That hook fires on negative
signals (「違う」「だめ」「本当に？」) and extracts a *failure* to learn from.
This hook fires on a positive *closure* signal and asks the sub-agent to
extract a META improvement opportunity — what Claude could have done more
efficiently, more directly, or with less back-and-forth, even though the
user did not explicitly complain.

The asymmetry is the point: corrections cover only the failures the user
bothers to point out. Many sessions wrap up with a polite "完了" hiding
two or three avoidable detours that the user does not flag because the
end-state arrived. Mining those moments is the only way to catch the
``judgement-fault`` patterns that don't surface as corrections.

Trigger = EXACT full-string match (NOT substring). The entire user prompt,
after trimming whitespace and stripping an optional leading routing prefix
(FRAME_ROUTING_PREFIX env var), must equal one of the acceptance words. This
is the property that lets the hook run enforcing without an observe-mode
burn-in: a full-string match cannot be a status question ("完了？" ≠ "完了"),
cannot be casual embedded usage ("ok、次やって"), and cannot be an incidental
quote — the three false-positive classes that, in the origin environment, got
an earlier substring-matching blocker hook disabled within a day of being added.

The MECHANISM (exact match) is frame-layer and hardcoded here. The VOCABULARY
(which words count as acceptance) is a per-user calibration constant and comes
from ``_signals.py``: conservative built-in defaults ({ok, done, thanks},
case-insensitive), overridden by the local-layer config
``.claude/hooks/local/signals.json`` (gitignored; see signals.json.example).

Memory rules referenced:
  - ``feedback_classify_failure_saying_vs_judgement.md``
  - ``feedback_no_user_pick_from_self_options.md`` (sibling failure mode)

Remaining guards (beyond exact match):
  - Skip system-generated prompts (task-notification / system-reminder /
    command markers).
  - Require a prior assistant turn (something to reflect on).
  - 5 min debounce so repeated "ok" in one flow fires reflection once.

Batched-queue redesign (原環境 user feedback「やりとり中の自己発火は過剰実装」):
``detect_correction_signal_v2.py`` no longer spawns a reflection
mid-conversation — it queues correction events into
``~/.claude/runtime/pending_correction_reflections.json``. This hook drains
that queue when it fires and hands ALL pending corrections to the single
batched reflection sub-agent. A non-empty queue bypasses the cost gate
(queued corrections are guaranteed mineable) but not the debounce; the queue
is global, so corrections from sessions that never said "ok" are recovered by
the next acceptance fire in any session.
"""

from __future__ import annotations

import json
import os.path
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return

# Exact-match acceptance whitelist. The ENTIRE prompt (after normalization)
# must equal one of these — substring matches are deliberately NOT accepted.
# Restricting to a small set + full-string match is what eliminates the
# false-positive classes (status questions, casual embedded usage, incidental
# quotes) so the hook can run enforcing. The word lists are a calibration
# constant loaded via _signals.py (local/signals.json overriding conservative
# defaults); only the matching mechanism is fixed here.
try:
    from _signals import acceptance_sets  # noqa: E402
    _ACCEPTANCE_EXACT, _ACCEPTANCE_EXACT_CI = acceptance_sets()
except Exception as _sig_exc:  # config layer must never kill the hook
    sys.stderr.write(f"[detect_acceptance_signal] _signals fallback: {_sig_exc}\n")
    # Empty = opt-in: with no vocabulary the hook simply never fires (the safe
    # direction — a missed reflection is cheap; a false background spawn is not).
    _ACCEPTANCE_EXACT = frozenset()
    _ACCEPTANCE_EXACT_CI = frozenset()
# 中継 transport (例: chat bridge) が付与する routing prefix。
# FRAME_ROUTING_PREFIX 環境変数で指定 (例: "[From Discord]")。
# 未設定時は空文字 = strip 処理が no-op になる。
_ROUTING_PREFIX = os.environ.get("FRAME_ROUTING_PREFIX", "")

# Anti-spam: don't trigger meta-reflection too often. 5 min cooldown —
# acceptance signals are common in casual confirmation flow ("OK, then…")
# so we want this much less chatty than the correction reflection.
_DEBOUNCE_SECONDS = 300
# Per-SESSION debounce — see _debounce_file() / _check_and_set_debounce() below.
# A single shared file used to live here (/tmp/claude_acceptance_signal_last.txt)
# and caused a cross-session collision: one session's "ok" silently ate another
# live session's 5-minute window, dropping its acceptance reflection.
_DEBOUNCE_DIR = Path("/tmp")
_DEBOUNCE_PREFIX = "claude_acceptance_signal_last_"
# GC abandoned per-session debounce files after this long. Far larger than the
# debounce window, so an active session's file is never swept.
_STALE_TTL_SECONDS = 86400

# --- cost gate -------------------------------------------------------------
# The reflection spawns a full background sub-agent that re-reads the whole
# transcript. That cost is wasted on sessions that structurally cannot yield a
# generalizable process lesson. Every improvement category the reflection mines
# (redundant steps / avoidable back-and-forth / late diagnostic / tool-choice
# mismatch / premature implementation / order-of-operations / missed parallelism
# / repeated tool-call loops) REQUIRES tool activity. So we gate on a cheap,
# LLM-free measure of mineable work done in the CURRENT window — messages since
# the last prior reflection fire (else session start) — and skip when it is too
# small. Grounded in the observed no-yield classes (原環境 corpus): trivial
# "clean conversational / design-question / single-Write" tasks and the explicit
# "no-action: only-cron-wakeups since last reflection" stops. Real-yield
# sessions in that corpus had >=9 tool calls, so the floor below is safely under.
#
# This gate targets the LOW-ACTIVITY no-yield class only. The other no-yield
# class ("every candidate already covered in memory") is NOT cheaply detectable
# without the LLM and is intentionally left to the sub-agent's own no-action exit.
_MIN_TOOLUSE_TO_REFLECT = 4  # skip when window tool_use < this
# A cron/system-wakeup-only window (no human task) skips too, but only when also
# under this ceiling — a heavy automated pipeline run may still be worth mining.
_CRON_ONLY_TOOLUSE_CEIL = 12
# Decision telemetry — observable signal for the kill-switch. If skipped windows
# turn out to have been learnable (e.g. a skipped session later draws a user
# correction), raise the floor or disable. Report: tally decision/reason here.
# hooks は常に hooks dir が sys.path 上にある状態で実行/import される
from _paths import TELEMETRY_DIR as _TELEMETRY_DIR  # noqa: E402

_GATE_LOG = _TELEMETRY_DIR / "reflection_gate.jsonl"
# User-turn prefixes that mark an automated / system turn rather than a real
# human task. Mirrors the system-prompt skips in main().
_SYSTEM_USER_PREFIXES = (
    "<task-notification>",
    "<system-reminder>",
    "<command-message>",
    "<command-name>",
)

# --- correction queue (fed by detect_correction_signal_v2) ------------------
# Same env override as the producer so tests stay hermetic.
_CORRECTION_QUEUE = Path(
    os.environ.get(
        "CLAUDE_CORRECTION_QUEUE",
        str(Path.home() / ".claude/runtime/pending_correction_reflections.json"),
    )
)
# Drained items are journaled here so a failed/killed reflection never loses
# them silently (the queue file itself is emptied at dispatch time).
_DISPATCH_LOG = _TELEMETRY_DIR / "correction_dispatch.jsonl"


def _peek_corrections() -> list[dict]:
    if not _CORRECTION_QUEUE.exists():
        return []
    try:
        data = json.loads(_CORRECTION_QUEUE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _drain_corrections(sid: str) -> list[dict]:
    """Empty the queue and journal what was dispatched. Called only after all
    fire guards (including debounce) have passed, so a gated turn never eats
    the queue."""
    items = _peek_corrections()
    if not items:
        return []
    try:
        _CORRECTION_QUEUE.write_text(
            json.dumps({"items": []}, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        # If we cannot empty the queue, do not dispatch duplicates next time
        # silently — still proceed; the reflection is idempotent enough
        # (memory dedup happens in the sub-agent) and the journal records it.
        pass
    try:
        _DISPATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DISPATCH_LOG.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "dispatched_by_session": sid,
                        "count": len(items),
                        "items": items,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    return items


def _corrections_block(items: list[dict]) -> str:
    """Render queued corrections as a reminder section, or '' when empty.

    Dynamic data only (event list). The processing policy lives in the agent
    definition (.claude/agents/self-reflection.md「correction 処理ワークフロー」節)
    — single owner per path; do not inline policy text here."""
    if not items:
        return ""
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(
            f"  {i}. ts={it.get('ts', '?')} session={it.get('session_id', '?')} "
            f"transcript={it.get('transcript_path', '?')}\n"
            f"     訂正発話 (抜粋):「{it.get('prompt_excerpt', '')}」"
        )
    joined = "\n".join(lines)
    return f"""
**処理待ち correction イベント ({len(items)} 件 — やりとり中はキューに積むだけにし、この acceptance 時点で一括処理する):**
{joined}

処理方針は agent 定義 (.claude/agents/self-reflection.md) の「correction 処理ワークフロー」節に従う — META mining より先に各イベントへ適用する。
"""


def _is_wakeup_or_system(text: str) -> bool:
    return text.lstrip().startswith(_SYSTEM_USER_PREFIXES)


def _gate_features(transcript_path: str) -> dict | None:
    """Single-pass scan of the current window (messages since the last prior
    reflection fire, else session start). Returns counts of mineable work, or
    ``None`` on any read/parse-level failure so the caller can FAIL OPEN (fire)
    rather than lose a learnable session to a gate bug.

    The current acceptance prompt is not yet in the transcript at
    UserPromptSubmit time, so the window correctly ends at the prior assistant
    turn — the work this acceptance is closing."""
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    # Window start = just after the last line that mentions a prior fire. We scan
    # raw text so it matches regardless of where the injected reminder landed in
    # the transcript structure. (Shrinking the window here only ever makes us
    # MORE likely to skip work we already reflected on — the safe direction.)
    start = 0
    for i, line in enumerate(lines):
        if "AUTO-LEARN-META" in line:
            start = i + 1
    tool_use = 0
    asst_turns = 0
    real_user = 0
    for line in lines[start:]:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "assistant":
            asst_turns += 1
            if isinstance(content, list):
                tool_use += sum(
                    1
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                )
        elif role == "user":
            if isinstance(content, str):
                txt = content
            elif isinstance(content, list):
                txt = "".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            else:
                txt = ""
            if txt and not _is_wakeup_or_system(txt):
                real_user += 1
    return {"tool_use": tool_use, "asst_turns": asst_turns, "real_user": real_user}


def _gate_decision(feat: dict) -> tuple[bool, str]:
    """Return ``(should_fire, reason)`` from cheap window features."""
    tu = feat["tool_use"]
    if tu < _MIN_TOOLUSE_TO_REFLECT:
        return False, f"low-activity tool_use={tu}<{_MIN_TOOLUSE_TO_REFLECT}"
    if feat["real_user"] == 0 and tu < _CRON_ONLY_TOOLUSE_CEIL:
        return False, f"cron-only-light tool_use={tu} no-real-user-task"
    return True, f"fire tool_use={tu} turns={feat['asst_turns']}"


def _log_gate(sid: str, decision: str, reason: str, feat: dict | None) -> None:
    """Append the decision for kill-switch auditing. Best-effort; never raises."""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session": sid,
            "decision": decision,
            "reason": reason,
            "tool_use": (feat or {}).get("tool_use"),
            "turns": (feat or {}).get("asst_turns"),
            "real_user": (feat or {}).get("real_user"),
        }
        _GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _GATE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# Reminder injected on hit. The sub-agent workflow is META-focused:
# look for efficiency / process / decision improvements, not failure
# extraction. May exit with no-action if nothing genuinely learnable.
_REMINDER = """<system-reminder>
[AUTO-LEARN-META] User acceptance signal detected. Run a background meta-improvement reflection (起動と完了を日本語1行で可視化する; 完全サイレントにはしない).

After responding to the user's actual message in this turn, spawn a background sub-agent with the Agent tool:
- subagent_type: self-reflection
- run_in_background: true
- prompt: 下の Inputs ブロックをそのまま渡す。振り返りの方針・ワークフロー・category は self-reflection エージェント定義 (.claude/agents/self-reflection.md) 側に持たせてあるので、ここでは動的入力だけ渡せばよい。

---
Inputs:
- transcript_path: {transcript}
- session_id: {sid}
{corrections_block}
---

Spawn the background sub-agent in the same turn as your user-facing response.

可視性 (silent 運用は廃止済み): 完全サイレントにはしない。(1) 起動した同じ応答の末尾に日本語で1行だけ通知する — 例「🔍 自己改善リフレクションをバックグラウンド起動（完了時に結果を1行で出す）」。(2) この background エージェントの完了通知 (task-notification) を受け取ったターンで、結果を日本語1行に圧縮して出す — 例「✅ リフレクション完了: <memory N件更新 / no-action: 理由>」。reflection 内部の思考や全文はチャットに出さず、必ず1行ダイジェストに留める。
</system-reminder>"""


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _normalize_prompt(prompt: str) -> str:
    """Trim whitespace and strip an optional leading routing prefix so the
    exact-match check sees the real user text regardless of transport.
    The prefix is configured via FRAME_ROUTING_PREFIX env var;
    when unset the strip is a no-op."""
    body = prompt.strip()
    if _ROUTING_PREFIX and body.startswith(_ROUTING_PREFIX):
        body = body[len(_ROUTING_PREFIX):].strip()
    return body


def _has_acceptance_signal(prompt: str) -> bool:
    body = _normalize_prompt(prompt)
    if not body:
        return False
    return body in _ACCEPTANCE_EXACT or body.lower() in _ACCEPTANCE_EXACT_CI


def _previous_turn_was_assistant(transcript_path: str) -> bool:
    """Confirm there's an assistant turn to reflect on."""
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
    acceptance signal from suppressing a concurrent session's within the
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
    """Return True if cooldown elapsed (OK to fire); update timestamp.

    Debounce is per-session (keyed by ``sid``) so concurrent Claude sessions do
    not suppress one another."""
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
    stripped = prompt.lstrip()
    if stripped.startswith("<task-notification>"):
        return 0
    if stripped.startswith("<system-reminder>"):
        return 0
    if stripped.startswith("<command-message>"):
        return 0
    if stripped.startswith("<command-name>"):
        return 0
    if not _has_acceptance_signal(prompt):
        return 0
    if not _previous_turn_was_assistant(transcript):
        return 0

    # Cost gate: skip the expensive reflection when too little mineable work was
    # done in the current window. Fails OPEN (fires) if features can't be read.
    # A non-empty correction queue bypasses the gate — queued corrections are
    # guaranteed mineable material regardless of this window's activity.
    corrections = _peek_corrections()
    feat = _gate_features(transcript)
    if corrections:
        reason = f"corrections-queued n={len(corrections)}"
    elif feat is not None:
        should_fire, reason = _gate_decision(feat)
        if not should_fire:
            _log_gate(sid, "skip", reason, feat)
            return 0
    else:
        reason = "fire-failopen-no-features"

    if not _check_and_set_debounce(sid):
        _log_gate(sid, "debounce", reason, feat)
        return 0

    drained = _drain_corrections(sid) if corrections else []
    _log_gate(sid, "fire", reason, feat)
    record_fire("feedback_classify_failure_saying_vs_judgement", "audit",
                context="acceptance-signal reflection")
    sys.stdout.write(
        _REMINDER.format(
            transcript=transcript,
            sid=sid,
            corrections_block=_corrections_block(drained),
        )
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[detect_acceptance_signal] error: {exc}\n")
        sys.exit(0)
