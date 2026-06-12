#!/usr/bin/env python3
# RECIPE (非配線): 原環境の自己改善ループが育てた規範の実体。
# 採用条件 = 根拠のない hedge 懸念の量産、または reflection が saying-fault hook を起案する場面を自環境で観測したこと。
# 本ファイルは reflection が saying-fault hook を起案する際の雛形としても使える。
# 採用手順と再校正の注意は ../README.md (docs/recipes/README.md)。
"""block_hedged_concerns — linguistic red-flag scanner for unverified hedges,
user-action requests, and semantic-trigger / required-marker contracts.

Memory rules:
  - ``feedback_verify_before_negating.md`` (hedge section, 原環境実測)
  - ``feedback_minimize_user_actions_absolute.md`` (絶対命令: 役割逆転禁止)
  - ``feedback_positive_emission_markers.md`` (原環境実測)

Stop event hook. Scans the latest assistant turn text from the transcript
and detects four failure patterns:

  1. Hedged-risk claims (negative blacklist) — phrases like 念のため /
     確認のため / 影響不明 / リスク受け入れ.
  2. User-action requests (negative blacklist) — phrases like
     やってください / お願いします / 試してください.
  3. Uncertainty triggers without marker (positive emission) — vocabulary
     like かもしれない / 可能性が / 懸念 / 恐れがある / 不確実 in a
     paragraph that lacks [確度: ...] / [根拠: ...] / [未検証: ...] /
     [影響範囲: ...]. Forces structured confidence emission instead of
     relying on exhaustive hedge-word enumeration.
  4. Exploratory triggers without marker (positive emission) —
     とりあえず / ひとまず / 一旦 / 暫定 in a paragraph that lacks
     [探索範囲: ...] + [中断条件: ...]. Forces bounded-scope declaration
     on exploratory actions.

On hit:
  * Logs to ``runtime/block_hedged_concerns_audit.log`` for visible audit
    of regressions (regardless of exit code).
  * Emits a stderr feedback line so the next turn can self-correct.

Exit 0 always — this hook does not abort, it audits + reminds. The Stop
event fires after the assistant text is already displayed, so true
pre-display blocking is not available; the value here is closing the
self-recall gap by inserting a deterministic post-turn signal.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _fire_counter import record_fire  # noqa: E402

# Pattern set 1: hedged-risk claims (verify-before-claiming).
# These are phrases used to introduce a concern/risk without backing it.
_HEDGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"念のため"),
    re.compile(r"確認のため"),
    re.compile(r"影響不明"),
    re.compile(r"リスク受け入れ"),
    re.compile(r"念のため検証保留"),
    re.compile(r"影響を受けないと思うが"),
    re.compile(r"念のため確認"),
    re.compile(r"影響あるかもしれない"),
    re.compile(r"念のため検証"),
)

# Pattern set 2: user-action requests (絶対命令: 役割逆転禁止).
# These are phrases that delegate operation to the human.
# Excluded contexts: when the action is genuinely human-only (auth, physical),
# so this is audit-only — we log + warn, do not block.
_USER_ACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"やってみてください"),
    re.compile(r"試してみてください"),
    re.compile(r"試してください"),
    re.compile(r"確認お願いします"),
    re.compile(r"確認してください"),
    re.compile(r"お願いします"),
    re.compile(r"〜したら教えてください"),
    re.compile(r"あとで結果を教えてください"),
    re.compile(r"タイミングを見計らって"),
    re.compile(r"動けば OK"),
    re.compile(r"ダメなら次の指示"),
)

# Pattern set 3: uncertainty / concern triggers (positive emission rule).
# Per feedback_positive_emission_markers: semantic concepts that grammar-level
# blacklists cannot exhaustively enumerate. Strategy — detect the trigger
# vocabulary (broad net) and require a structured marker (precise grammar)
# in the same paragraph. If trigger present but no marker → flag.
_UNCERTAIN_TRIGGER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"かもしれな[いく]"),
    re.compile(r"かも知れな[いく]"),
    re.compile(r"可能性が(?:あ[るり]|高い|残[るり])"),
    re.compile(r"恐れがあ[るり]"),
    re.compile(r"おそれがあ[るり]"),
    re.compile(r"不確実"),
    re.compile(r"不確か"),
    re.compile(r"懸念(?:点|事項|材料|され|があ[るり])"),
    re.compile(r"リスクが(?:ある|高い|残[るり])"),
    re.compile(r"壊れる(?:可能性|恐れ|かも)"),
    re.compile(r"失敗する(?:可能性|かも)"),
    re.compile(r"影響(?:範囲)?が(?:不明|未確認|分から)"),
)

# Pattern set 4: exploratory triggers (positive emission rule).
# 「とりあえず実装」「ひとまずやってみる」型の探索的アクション宣言。
_EXPLORATORY_TRIGGER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"とりあえず"),
    re.compile(r"ひとまず"),
    re.compile(r"一旦(?:実装|試[しす]|やって|入れ|書い)"),
    re.compile(r"いったん(?:実装|試[しす]|やって|入れ|書い)"),
    re.compile(r"仮に(?:実装|やって|試[しす]|入れ)"),
    re.compile(r"試しに(?:実装|やって|入れ)"),
    re.compile(r"暫定(?:的|実装|対応|措置)"),
    re.compile(r"仮実装"),
    re.compile(r"まず(?:試[しす]|実装してみ)"),
)

# Required markers (positive emission contract).
_MARKERS_FOR_UNCERTAIN: tuple[re.Pattern[str], ...] = (
    re.compile(r"\[確度[:：]\s*[^\]]+\]"),
    re.compile(r"\[根拠[:：]\s*[^\]]+\]"),
    re.compile(r"\[未検証[:：]\s*[^\]]+\]"),
    re.compile(r"\[影響範囲[:：]\s*[^\]]+\]"),
)
_MARKERS_FOR_EXPLORATORY: tuple[re.Pattern[str], ...] = (
    re.compile(r"\[探索範囲[:：]\s*[^\]]+\]"),
    re.compile(r"\[中断条件[:：]\s*[^\]]+\]"),
)

from _paths import RUNTIME_DIR as _RUNTIME_DIR  # noqa: E402

_AUDIT_LOG = _RUNTIME_DIR / "block_hedged_concerns_audit.log"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _scan_current_turn_assistant_text(transcript_path: str) -> str | None:
    """Return concatenated assistant text since the last user message.

    Stop fires once per turn. A turn may contain multiple assistant text
    blocks interleaved with tool calls. Returning only the latest block
    would miss a hedge phrase emitted earlier in the same turn, so we
    accumulate every assistant text block since the most recent user
    message and return the concatenation.
    """
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


def _scan_unmarked_triggers(
    text: str,
    triggers: tuple[re.Pattern[str], ...],
    markers: tuple[re.Pattern[str], ...],
) -> list[tuple[str, str]]:
    """For each paragraph containing a trigger keyword, return (token, snippet)
    if no required marker is present in the same paragraph. Paragraph =
    blank-line-separated chunk. False positives are accepted as the cost
    of broader semantic coverage; this is audit-only.
    """
    hits: list[tuple[str, str]] = []
    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        for tpat in triggers:
            m = tpat.search(para)
            if not m:
                continue
            if any(mp.search(para) for mp in markers):
                continue
            snippet = para.strip().replace("\n", " ")
            if len(snippet) > 120:
                snippet = snippet[:120] + "..."
            hits.append((m.group(0), snippet))
            break
    return hits


def _log_hits(sid: str, category: str, hits: list[tuple[str, int, int]], text: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for token, s, e in hits:
            start = max(0, s - 30)
            end = min(len(text), e + 30)
            entry = {
                "ts": ts,
                "session_id": sid,
                "category": category,
                "token": token,
                "context": text[start:end],
            }
            with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _log_unmarked(sid: str, category: str, hits: list[tuple[str, str]]) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for token, snippet in hits:
            entry = {
                "ts": ts,
                "session_id": sid,
                "category": category,
                "token": token,
                "context": snippet,
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

    hedge_hits = _find_hits(text, _HEDGE_PATTERNS)
    action_hits = _find_hits(text, _USER_ACTION_PATTERNS)
    uncertain_unmarked = _scan_unmarked_triggers(
        text, _UNCERTAIN_TRIGGER_PATTERNS, _MARKERS_FOR_UNCERTAIN
    )
    exploratory_unmarked = _scan_unmarked_triggers(
        text, _EXPLORATORY_TRIGGER_PATTERNS, _MARKERS_FOR_EXPLORATORY
    )

    if hedge_hits:
        _log_hits(sid, "hedge", hedge_hits, text)
        tokens = ", ".join(sorted({t for t, _, _ in hedge_hits}))
        record_fire(
            "feedback_verify_before_negating",
            "warn",
            count=len(hedge_hits),
            context=tokens,
        )
        sys.stderr.write(
            f"[block_hedged_concerns] hedge phrases detected: {tokens}. "
            "Per feedback_verify_before_negating: replace with concrete S/E/T/R trajectory or delete the concern.\n"
        )

    if action_hits:
        _log_hits(sid, "user_action", action_hits, text)
        tokens = ", ".join(sorted({t for t, _, _ in action_hits}))
        record_fire(
            "feedback_minimize_user_actions_absolute",
            "warn",
            count=len(action_hits),
            context=tokens,
        )
        sys.stderr.write(
            f"[block_hedged_concerns] user-action request detected: {tokens}. "
            "Per feedback_minimize_user_actions_absolute: verify side-effects / Plan B / 1-shot completion before asking, or do it yourself.\n"
        )

    if uncertain_unmarked:
        _log_unmarked(sid, "uncertain_unmarked", uncertain_unmarked)
        tokens = ", ".join(sorted({t for t, _ in uncertain_unmarked}))
        record_fire(
            "feedback_positive_emission_markers",
            "warn",
            count=len(uncertain_unmarked),
            context=f"uncertain:{tokens}",
        )
        sys.stderr.write(
            f"[block_hedged_concerns] uncertainty trigger without marker: {tokens}. "
            "Per feedback_positive_emission_markers: attach [確度: ...] / [根拠: ...] / [未検証: ...] / [影響範囲: ...] in same paragraph, or drop the claim.\n"
        )

    if exploratory_unmarked:
        _log_unmarked(sid, "exploratory_unmarked", exploratory_unmarked)
        tokens = ", ".join(sorted({t for t, _ in exploratory_unmarked}))
        record_fire(
            "feedback_positive_emission_markers",
            "warn",
            count=len(exploratory_unmarked),
            context=f"exploratory:{tokens}",
        )
        sys.stderr.write(
            f"[block_hedged_concerns] exploratory trigger without marker: {tokens}. "
            "Per feedback_positive_emission_markers: attach [探索範囲: ...] + [中断条件: ...] in same paragraph, or commit to the action.\n"
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[block_hedged_concerns] error: {exc}\n")
        sys.exit(0)
