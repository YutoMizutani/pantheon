#!/usr/bin/env python3
"""detect_acceptance_signal — auto-trigger META self-improvement on user acceptance.

This hook fires on a positive *closure* signal (user acceptance) and asks the
sub-agent to extract a META improvement opportunity — what Claude could have
done more efficiently, more directly, or with less back-and-forth, even though
the user did not explicitly complain.

(The former negative-signal counterpart ``detect_correction_signal_v2.py`` and
its global correction queue were RETIRED 2026-06-17: the queue drained
cross-subject corrections into unrelated reflections and the detector self-fed
on meta-discussion. See docs/self-improvement-loop.md. Acceptance-triggered
META reflection is now the sole self-improvement intake.)

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

# Distinctive opening of the genuine injected reminder (_REMINDER, defined below).
# The window-boundary detector keys on this FULL phrase — not the bare
# "AUTO-LEARN-META" word — so that lines merely *quoting* the marker (tool_result
# from Reading/Editing this hook's source, prose discussion, pasted diffs) do not
# get mistaken for a real reflection fire. A module-level `assert` after _REMINDER
# guarantees the two never drift (a silent drift would resurrect the double-fire
# bug). Keep this string a verbatim prefix of _REMINDER's first directive line.
_REMINDER_ANCHOR = (
    "[AUTO-LEARN-META] User acceptance signal detected. "
    "Run a background meta-improvement reflection"
)


def _is_wakeup_or_system(text: str) -> bool:
    return text.lstrip().startswith(_SYSTEM_USER_PREFIXES)


def _line_is_prior_reflection_fire(line: str) -> bool:
    """True if this transcript line marks a *prior* reflection fire that should
    close the mining window. Two carriers:

      (a) the auto-injected reminder this hook emits — detected by the raw
          ``AUTO-LEARN-META`` marker (where it always lands);
      (b) a *manual* spawn of the self-reflection sub-agent — an assistant
          ``Agent``/``Task`` tool_use whose input carries
          ``subagent_type == "self-reflection"``. A hand-launched reflection
          leaves NO marker, so without (b) the window never advanced past it and
          the next acceptance signal re-mined the same work in a second, ~costly
          sub-agent (observed ~96748 wasted subagent_tokens — the double-fire bug
          this guard closes).

    Direction of the guard is fail-CLOSED: any match here only advances the
    window start LATER, shrinking the counted window — i.e. it can only make the
    gate MORE likely to skip, never to double-fire and never to loosen firing.
    A false match (e.g. the agent-listing reminder mentioning self-reflection in
    prose) is parsed away by the role/tool_use check below, and even if it
    slipped through it would merely skip a reflection — the cheap, safe miss."""
    # (a) the genuine auto-injected reminder. Match the FULL directive opening,
    # NOT the bare marker word — and exclude anything carried inside a
    # tool_use/tool_result block. Working ON this hook (Reading/Editing its
    # source, which literally contains the _REMINDER text, or pasting its diff)
    # otherwise floods the transcript with the marker string; the old bare
    # ``"AUTO-LEARN-META" in line`` then matched those quotes and advanced the
    # window start to the end, collapsing the counted window to ~0 tool_use so
    # every later acceptance signal got skipped by the cost gate. Self-referential
    # false-positive observed 2026-06-20 (gate log: 3× "low-activity tool_use=0<4"
    # right after a session spent editing this very file). Still fail-CLOSED: a
    # rare miss only over-skips a reflection, never double-fires.
    if _REMINDER_ANCHOR in line:
        # A Read of this hook's own source (tool_result) contains the anchor
        # verbatim too — reject anything inside a tool_use/tool_result block so
        # only the actually-injected reminder counts as a window boundary.
        try:
            _obj = json.loads(line.strip())
        except (json.JSONDecodeError, ValueError):
            return True  # not JSON but carries the anchor → treat as the reminder
        _msg = _obj.get("message") if isinstance(_obj, dict) else None
        _content = _msg.get("content") if isinstance(_msg, dict) else None
        if isinstance(_content, list):
            for _b in _content:
                if isinstance(_b, dict) and _b.get("type") in ("tool_use", "tool_result"):
                    return False
        return True
    # Cheap reject before JSON-parsing: the spawn line must name the sub-agent.
    if "self-reflection" not in line:
        return False
    try:
        obj = json.loads(line.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(obj, dict):
        return False
    msg = obj.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for b in content:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        if b.get("name") not in ("Agent", "Task"):
            continue
        inp = b.get("input")
        if isinstance(inp, dict) and inp.get("subagent_type") == "self-reflection":
            return True
    return False


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
    # Window start = just after the last line that marks a prior reflection
    # fire — either the auto-injected AUTO-LEARN-META reminder OR a manual
    # self-reflection sub-agent spawn (see _line_is_prior_reflection_fire).
    # Counting the manual spawn closes the double-fire bug: a hand-launched
    # reflection leaves no marker, so the window otherwise never advanced and the
    # next acceptance signal re-mined the same work. (Shrinking the window here
    # only ever makes us MORE likely to skip work we already reflected on — the
    # safe direction.)
    start = 0
    for i, line in enumerate(lines):
        if _line_is_prior_reflection_fire(line):
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
- ↑ この transcript_path / session_id が META mining の **唯一の分析対象 (subject session)**。起動直後に subject session の先頭 user 発話を 1 度 Read し、テーマを自分に固定してから mining に入る。
---

Spawn the background sub-agent in the same turn as your user-facing response.

可視性 (silent 運用は廃止済み): 完全サイレントにはしない。(1) 起動した同じ応答の末尾に日本語で1行だけ通知する — 例「🔍 自己改善リフレクションをバックグラウンド起動（完了時に結果を1行で出す）」。(2) この background エージェントの完了通知 (task-notification) を受け取ったターンで、結果を日本語1行に圧縮して出す — 例「✅ リフレクション完了: <memory N件更新 / no-action: 理由>」。reflection 内部の思考や全文はチャットに出さず、必ず1行ダイジェストに留める。

(3) inline drain (旧 pending_structural_reviews.json キューの後継 — 2026-06-19 廃止): reflection の完了結果に `ESCALATION: recurrence-despite-memory` ブロックが含まれていたら、**その同じ完了ターンで** root-cause-auditor を inline 起動する (Agent tool, subagent_type: root-cause-auditor, run_in_background: true)。ブロックの target_hint / signal / origin_session をそのまま auditor の prompt に渡す。キューファイルへは一切書かない・読まない。auditor 完了時はその verdict を user に提示し human-gate (採否は user、採った提案だけ pending_hook/claudemd/agent-def 承認キューへ)。ESCALATION ブロックが無ければ (3) は何もしない。
</system-reminder>"""

# Drift guard: the window-boundary detector (_line_is_prior_reflection_fire)
# recognizes a prior fire by _REMINDER_ANCHOR. If a future edit changes the
# reminder's opening so it no longer contains the anchor, boundary detection
# silently breaks and the double-fire bug returns. Fail loudly at import instead.
assert _REMINDER_ANCHOR in _REMINDER, (
    "_REMINDER_ANCHOR must stay a verbatim prefix of _REMINDER's directive line "
    "(see _line_is_prior_reflection_fire)."
)


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
    feat = _gate_features(transcript)
    if feat is not None:
        should_fire, reason = _gate_decision(feat)
        if not should_fire:
            _log_gate(sid, "skip", reason, feat)
            return 0
    else:
        reason = "fire-failopen-no-features"

    if not _check_and_set_debounce(sid):
        _log_gate(sid, "debounce", reason, feat)
        return 0

    _log_gate(sid, "fire", reason, feat)
    record_fire("feedback_classify_failure_saying_vs_judgement", "audit",
                context="acceptance-signal reflection")
    sys.stdout.write(
        _REMINDER.format(
            transcript=transcript,
            sid=sid,
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
