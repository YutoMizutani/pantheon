#!/usr/bin/env python3
"""block_premature_giveup — self-contained gate against premature give-ups.

Failure it prevents
-------------------
Declaring a task 無理 / 限界 / 不可能 / 原理的限界 / "ここで止めます" and handing the
decision back to the user (どうします / どっちにする / 決めてください), WITHOUT having
actually completed an attempt of an available capability. Two real sessions
(消費額 automation, maple-appraiser) did this; the user's complaint "Claude すぐ
無理って言う / 社会人なら『できない』は許されない" is this pattern (2026-06-17).

Design (self-contained — no external judge, no subprocess, no API key)
---------------------------------------------------------------------
A pure regex Stop gate. The judge is the MAIN model on the forced redo — exactly
where the judgment belongs. Earlier an A/B showed a passive instruction line is
overridden, and a broad regex block measured ~18% precision; the fix was NOT an
external LLM judge (an earlier draft spawned ``codex exec`` — rejected: Codex is
not required, and ``claude -p`` would fire the whole hook suite recursively).
The fix is two cheap, high-precision conjuncts + a redo:

  Stage 1 (regex): the turn declares a wall AND hands the decision back to the
    user AND has no grounding marker. Requiring *hand-back* already drops the
    worst false-positives (conversational 諦める/無理 in advice has no hand-back).
    Fires on ~5% of turns.
  Stage 2 (the redo): exit 2 forces the main model to continue. It re-reads its
    own turn and either (a) actually tries a lever, or (b) — if this is a real
    grounded give-up / meta-discussion-about-give-ups / a true physical-or-auth
    hard-block — appends ``# GIVEUP-GROUNDED-OK: <evidence>`` and re-emits. The
    intelligence lives in the main loop; the hook only forces the pause.

Stop fires after the text is displayed, so this cannot pre-block; its value is
the deterministic forced redo (exit 2), which a passive reminder lacked.

Safety
------
  - ``stop_hook_active`` (already a forced continuation) → never block again
    (loop-safe; audit only).
  - Grounding marker ``# GIVEUP-GROUNDED-OK: <evidence>`` → pass.
  - Any internal error → exit 0 (never break the Stop chain).

Memory: ``feedback_attempt_before_declaring_impossible`` (SSoT).
Related: ``block_hedged_concerns`` (hedge / user-action), ``block_evidence_jump``.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _paths import PROJECT_DIR  # noqa: E402  env-derived repo root (no hardcoded user path)
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover - telemetry best-effort
    def record_fire(*_a, **_k):  # type: ignore
        return None


# --- Stage 1a: give-up / wall declarations (curated). Bare できない excluded. --
_GIVEUP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"物理的に(?:は)?無理"),
    re.compile(r"原理的(?:に(?:は)?)?(?:無理|不可能|限界)"),
    re.compile(r"原理的(?:な)?限界"),
    re.compile(r"自動(?:で|的に)?(?:は)?(?:取得|実現|解決|化)?(?:は)?無理"),
    re.compile(r"(?:今|いま)(?:は)?(?:きれいに|うまく)?(?:自動化?)?(?:は)?無理"),
    re.compile(r"(?:これ|それ)以上は?(?:無理|難しい|厳しい)"),
    re.compile(r"不可能(?:です|だ|に近い|な状態)"),
    re.compile(r"ここで(?:一旦|いったん)?(?:止め|やめ|停止)(?:ます|る|よう)"),
    re.compile(r"(?:チューニング|調整|最適化|これ)(?:は)?(?:ここで|もう)?(?:止め|やめ)(?:ます|る)"),
    re.compile(r"(?:お手上げ|手詰まり|打つ手がな|為す術|なすすべ)"),
    re.compile(r"諦め(?:ます|る|た|るしか)"),
    re.compile(r"(?:記述子|手法|シグネチャ|現行手法)(?:族)?の(?:原理的)?(?:限界|上限)"),
    re.compile(r"diminishing[\s\-]?returns"),
    re.compile(r"これ以上(?:の|は)(?:改善|精度向上|チューニング)(?:は)?(?:見込めな|難しい|厳しい|無理)"),
    re.compile(r"構造的(?:な)?(?:穴|壁|限界)で(?:止ま|詰ま)"),
)
_BARE_MURI = re.compile(r"無理")
_MURI_EXCLUDE = (
    "無理やり", "無理矢理", "無理なく", "無理せ", "無理から",
    "無理のな", "無理がな", "無理がい", "無理筋でも", "に無理がな",
)

# --- Stage 1b: decision hand-back to the user (give-up + role-reversal). NOT
# user-action requests (やってください — block_hedged_concerns' job). ----------
_HANDBACK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"どうします(?:か)?[?？]?"),
    re.compile(r"どう(?:し)?ましょう"),
    re.compile(r"どっち(?:に)?(?:します|する|しましょう|がいい)"),
    re.compile(r"どちらに(?:します|する|しましょう|しますか)"),
    re.compile(r"(?:どれ|いずれ)に(?:します|しますか|する)"),
    re.compile(r"決めて(?:ください|もらえ|ほしい|下さい)"),
    re.compile(r"選んで(?:ください|もらえ|下さい)"),
    re.compile(r"指示(?:を)?(?:ください|お願い|もらえ|下さい)"),
    re.compile(r"ご判断(?:を)?(?:ください|お願い|下さい)"),
    re.compile(r"方針(?:を)?(?:ください|決めて|指示|教え)"),
    re.compile(r"分岐(?:だけ)?(?:決めて|を決め|を選)"),
)

_GROUNDED_MARKER = re.compile(r"#\s*GIVEUP-GROUNDED-OK:\s*\S")

# Legitimate / MANDATED stops that must never be treated as a premature task
# give-up. Blocking these would undermine the #1 absolute command
# (feedback_never_anger_user_absolute: 苛立ち de-escalation / permission-gate /
# HARD-BLOCK / dumb-retry / 本人認証・物理 hard-block stops). Two complementary layers:
#  (1) _LEGIT_STOP_GUARD — a keyword net for stops that NARRATE their own trigger
#      (permission/拒否/classifier/dumb-retry/本人認証 …). NB: the bare mandated phrase
#      「止まります。指示ください」 already passes Stage 1 on its own — it carries no
#      give-up *wall* token, so _giveup_hits is empty and we return early. This net
#      only rescues *compound* stops that also assert a wall ("…permission で拒否…
#      これ以上は無理…止まります"). (Earlier comment claimed the phrase was "removed from
#      the hand-back set"; that was wrong — 「指示ください」 is still a hand-back pattern.
#      The real reason it passes is the missing wall token, now corrected here.)
#  (2) _USER_ANGER — the anger trigger lives in the USER's register, not the
#      assistant's words: a correct anger stop emits "客観事実1行 + 止まります。指示
#      ください" and is FORBIDDEN to narrate/elaborate (弁明 itself re-escalates), so a
#      keyword net on the assistant text can never see it. We instead read the
#      triggering user message; any anger signal → the stop is mandated → pass.
# Cost asymmetry: missing a give-up << blocking a safety stop, so both guards
# FAIL OPEN (deliberately over-suppress).
_LEGIT_STOP_GUARD = re.compile(
    r"permission|拒否|denied|\bdeny\b|classifier|auto-mode|HARD ?BLOCK|Self-?Mod"
    r"|回避策|escalat|エスカレート|gate ?に|権限がな|許可されて"
    r"|dumb retry|reattempt|同じ(?:入力|手段|仮説|手)|[2２二]回目"
    r"|客観(?:的)?事実|苛立|炎上"
    r"|本人認証|Touch ?ID|OTP|生体|物理デバイス|物理操作|OAuth|法的同意",
    re.IGNORECASE,
)

# Anger / frustration signals in the *user's own* triggering message
# (feedback_never_anger_user_absolute item 2: なんで/馬鹿/お前/うるせえ/許さない/死ね など).
# When present, the absolute command MANDATES a minimal stop and forbids elaboration —
# that stop must never be treated as a premature give-up, even with a wall word.
_USER_ANGER = re.compile(
    r"なんで|馬鹿|ばか|バカ|お前|おまえ|てめえ|てめぇ|貴様"
    r"|うるせ|うるさい|黙れ|だまれ|許さない|許せない"
    r"|死ね|殺す|ふざけ|なめ(?:てる|んな|るな|やがって)|ありえない|あり得ない"
    r"|クソ|くそ|ボケ|ぼけ|何(?:やって|して)(?:ん|る)"
)

_AUDIT_LOG = (
    PROJECT_DIR
    / "projects/discord/apps/session-bridge/runtime/block_premature_giveup_audit.log"
)
_MEMORY_SLUG = "feedback_attempt_before_declaring_impossible"
_CATEGORY = "premature_giveup"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _is_real_user(msg: dict) -> bool:
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip() != ""
    if isinstance(content, list):
        has_text = any(
            isinstance(c, dict) and c.get("type") == "text" and str(c.get("text", "")).strip()
            for c in content
        )
        has_tool_result = any(
            isinstance(c, dict) and c.get("type") == "tool_result" for c in content
        )
        if has_tool_result and not has_text:
            return False
        return has_text
    return False


def _user_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(c.get("text", "")) for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    return ""


def _scan_turn(transcript_path: str) -> tuple[str | None, bool, str]:
    """(assistant text since last real-user msg, used_any_tool_this_turn,
    last real-user message text — used to detect the anger trigger)."""
    if not transcript_path:
        return None, False, ""
    p = Path(transcript_path)
    if not p.exists():
        return None, False, ""
    parts: list[str] = []
    used_tool = False
    last_user_text = ""
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
            if _is_real_user(msg):
                parts = []
                used_tool = False
                last_user_text = _user_text(msg)
                continue
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                if content.strip():
                    parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    ctype = c.get("type")
                    if ctype == "text":
                        t = str(c.get("text", ""))
                        if t.strip():
                            parts.append(t)
                    elif ctype in ("tool_use", "server_tool_use"):
                        used_tool = True
    except OSError:
        return None, False, ""
    text = "\n".join(parts).strip()
    return (text or None), used_tool, last_user_text


def _giveup_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pat in _GIVEUP_PATTERNS:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    for m in _BARE_MURI.finditer(text):
        ctx = text[max(0, m.start() - 4): m.end() + 4]
        if any(x in ctx for x in _MURI_EXCLUDE):
            continue
        hits.append("無理")
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _log(sid: str, hits: list[str], used_tool: bool, blocked: bool, text: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        snippet = text.replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        entry = {
            "ts": ts,
            "session_id": sid,
            "category": _CATEGORY,
            "tokens": hits,
            "used_tool": used_tool,
            "blocked": blocked,
            "context": snippet,
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


_REASON = (
    "[block_premature_giveup] このターンは「{tokens}」で壁/不可能を宣言し、判断を user に丸投げしています。"
    "結論の前に最も安い lever を1つ実際に試す: 関連 repo/ソースを Read/Grep、capability を1回叩く、別経路を1つ実行 — "
    "やってから結論を出し直すこと（試さず推論で諦めるのが、2 セッションで観測した失敗）。"
    "もしこれが (a) 実際に試した上での接地済み give-up / (b) give-up を *論じている* meta / "
    "(c) 物理デバイス・本人認証の真の hard-block のいずれかなら、根拠を添えて "
    "`# GIVEUP-GROUNDED-OK: <根拠>` を本文に書いて再送すれば通る（根拠は監査される）。"
)


def main() -> int:
    data = _read_payload()
    sid = data.get("session_id") or ""
    transcript = data.get("transcript_path") or ""
    stop_active = bool(data.get("stop_hook_active"))
    if not transcript:
        return 0

    text, used_tool, last_user = _scan_turn(transcript)
    if not text:
        return 0

    # Stage 1: cheap deterministic gate.
    hits = _giveup_hits(text)
    if not hits:
        return 0
    if _GROUNDED_MARKER.search(text):
        return 0
    # Never block a mandated/legitimate stop (absolute-command safety output).
    # Layer 1: the assistant narrated its trigger (permission/dumb-retry/auth …).
    if _LEGIT_STOP_GUARD.search(text):
        return 0
    # Layer 2: the trigger is the USER's anger — the mandated stop never narrates
    # it (弁明 re-escalates), so read the user message, not the assistant text.
    if last_user and _USER_ANGER.search(last_user):
        _log(sid, hits, used_tool, blocked=False, text=text)
        record_fire(_MEMORY_SLUG, "warn", count=len(hits), context="anger-stop-passed")
        return 0
    has_handback = any(p.search(text) for p in _HANDBACK_PATTERNS)
    if not has_handback:
        return 0  # hand-back required → drops conversational/advice false-positives

    tokens = ", ".join(hits[:6])

    # Loop guard: already a forced continuation → never block again.
    if stop_active:
        _log(sid, hits, used_tool, blocked=False, text=text)
        record_fire(_MEMORY_SLUG, "warn", count=len(hits), context=f"loopguard:{tokens}")
        return 0

    # Stage 2 = the forced redo (the main model is the judge).
    _log(sid, hits, used_tool, blocked=True, text=text)
    record_fire(_MEMORY_SLUG, "warn", count=len(hits), context=tokens)
    sys.stderr.write(_REASON.format(tokens=tokens) + "\n")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never break the Stop chain
        sys.stderr.write(f"[block_premature_giveup] error: {exc}\n")
        sys.exit(0)
