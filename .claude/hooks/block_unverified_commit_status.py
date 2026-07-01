#!/usr/bin/env python3
"""block_unverified_commit_status — Stop gate against volunteering an unverified
commit / tracking status.

Failure it prevents
-------------------
The assistant VOLUNTEERS a commit / tracking STATUS claim — 未コミット / 'commit 不要' /
gitignored / 'commit するなら言って' / and especially the guess 'gitignored の可能性が高い'
— in a completion report or footnote, WITHOUT having run a verification command, when the
user never raised git/commit. 2026-06-30: across several completion reports the assistant
wrote 「未コミットで置いています」「commit するなら言って」 with no `git check-ignore`, then
finally floated 「gitignored の可能性が高い」 — all for `/projects/*` paths that are in fact
ignored. This is the (i) command-未実行 type breach in
.claude/rules/common/git-workflow.md (instruction-only failed ~9×).

Design (self-contained — pure regex Stop gate; the main model is the judge on redo)
-----------------------------------------------------------------------------------
Block ONLY when ALL hold (conjunctive → high precision, fails open):
  1. assistant text this turn asserts a commit/tracking STATUS (curated patterns,
     NOT the bare word "commit"), AND
  2. no `git check-ignore` / `git ls-files` / `git status` ran this turn
     (a verified fact is fine — '確認してから事実を言う' satisfied), AND
  3. the user did NOT raise git/commit this turn (on-topic git discussion is not
     policed — drops the huge false-positive surface of git conversations), AND
  4. no override marker, not a forced-continuation loop, not an anger-stop.

Stop fires after the text is shown, so this can't pre-block; its value is the
deterministic forced redo (exit 2): the model re-reads its turn and either removes
the unsolicited status footnote, or runs the check and states the fact, or — if this
is a legitimate verified/meta case — appends `# COMMIT-STATUS-OK: <根拠>` and re-emits.

Safety / guard-conflict (must never block a mandated stop —
feedback_never_anger_user_absolute): anger in the user's message → pass; an explicit
forced continuation (stop_hook_active) → pass; the plain anger-stop
「止まります。指示ください」 carries no status token so it passes on conjunct 1 anyway.

Memory: feedback_no_assumed_commit_for_projects_metadir (SSoT).
Rule: .claude/rules/common/git-workflow.md.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _paths import RUNTIME_DIR  # noqa: E402  env-derived runtime dir (no hardcoded user path)
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover - telemetry best-effort
    def record_fire(*_a, **_k):  # type: ignore
        return None


# --- conjunct 1: commit / tracking STATUS assertions or unsolicited offers. ----
# Curated so the bare word "commit" (e.g. `git commit -m`, "commit message format")
# does NOT match — only claims ABOUT commit/tracking state.
_STATUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"未コミット"),
    re.compile(r"コミット(?:は|が|を|も)?(?:不要|必要|要らない|いらない|済み?|していな|してな|されていな|されてな|対象|待ち)"),
    re.compile(r"commit\s*(?:するなら|したいなら|して(?:いい|ほしい|おく)|が必要|は?不要|対象|済)", re.IGNORECASE),
    re.compile(r"gitignored|git\s*管理外|版管理外|バージョン管理外|管理対象外"),
    re.compile(r"(?:^|[^A-Za-z])(?:un)?tracked(?:[^A-Za-z]|$)", re.IGNORECASE),
    re.compile(r"追跡(?:対象|されて)"),
    re.compile(r"コミット対象|「commit\s*対象」"),
)

# --- conjunct 3: did the USER raise git/commit this turn? If so, pass (on-topic). -
_USER_GIT_RAISED = re.compile(
    r"commit|コミット|push|プッシュ|(?:^|[^A-Za-z])git(?:[^A-Za-z]|$)|ギット"
    r"|版管理|バージョン管理|gitignore|tracked|追跡|リポジトリ|レポジトリ|\brepo\b",
    re.IGNORECASE,
)

# --- conjunct 2: a verification command actually ran this turn → pass. ----------
_GIT_CHECK_RE = re.compile(r"git\s+(?:check-ignore|ls-files|status)", re.IGNORECASE)

# --- override + loop/anger guards. ---------------------------------------------
_OK_MARKER = re.compile(r"#\s*COMMIT-STATUS-OK:\s*\S")
# Narrowing to high precision (measured on 632 real transcripts): an ASSERTIVE
# commit/tracking mention is usually legitimate (grounded "heaven/ は gitignored",
# memory filenames, rule-editing, descriptive use) → too false-positive-prone to
# block. The honest deterministic target is the SPECULATIVE form — guessing a
# commit/tracking status you could have verified with one command ('可能性が高い' /
# 'かも' / 'はず'). A guess about tracking state is almost never legitimate. Block
# only when a speculation token sits next to the status token.
_SPECULATION = re.compile(
    r"可能性|かも(?:しれ)?|たぶん|多分|おそらく|恐らく|はず(?:です|だ|;|。|、|$)|と思われ|気がする|でしょう|だろう"
)
_SPEC_WINDOW = 40  # chars on each side of a status hit to look for speculation
# Anger / frustration in the user's OWN message (feedback_never_anger_user_absolute):
# a mandated minimal stop must never be blocked, even if it states a fact like 未コミット.
_USER_ANGER = re.compile(
    r"なんで|馬鹿|ばか|バカ|お前|おまえ|てめえ|てめぇ|貴様"
    r"|うるせ|うるさい|黙れ|だまれ|許さない|許せない"
    r"|死ね|殺す|ふざけ|なめ(?:てる|んな|るな|やがって)|ありえない|あり得ない"
    r"|クソ|くそ|ボケ|ぼけ|何(?:やって|して)(?:ん|る)"
)

_AUDIT_LOG = RUNTIME_DIR / "block_unverified_commit_status_audit.log"
_MEMORY_SLUG = "feedback_no_assumed_commit_for_projects_metadir"
_CATEGORY = "unverified_commit_status"


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
    """(assistant text since last real-user msg, git-check-ran-this-turn, last
    real-user message text). Resets on each real-user message so only the current
    turn is considered."""
    if not transcript_path:
        return None, False, ""
    p = Path(transcript_path)
    if not p.exists():
        return None, False, ""
    parts: list[str] = []
    git_check_ran = False
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
                git_check_ran = False
                last_user_text = _user_text(msg)
                continue
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            # Only the FINAL assistant message's text is judged (the answer being
            # stopped), NOT everything since the last real-user msg — otherwise a
            # phrase quoted mid-task (e.g. while discussing this very rule) lingers
            # across later tool-driven turns and self-trips. git_check_ran still
            # accumulates across the whole turn (a check earlier in the turn counts).
            msg_parts: list[str] = []
            if isinstance(content, str):
                if content.strip():
                    msg_parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    ctype = c.get("type")
                    if ctype == "text":
                        t = str(c.get("text", ""))
                        if t.strip():
                            msg_parts.append(t)
                    elif ctype == "tool_use" and c.get("name") == "Bash":
                        inp = c.get("input") or {}
                        if isinstance(inp, dict) and _GIT_CHECK_RE.search(str(inp.get("command") or "")):
                            git_check_ran = True
            if msg_parts:  # replace → keep only the latest text-bearing assistant msg
                parts = msg_parts
    except OSError:
        return None, False, ""
    text = "\n".join(parts).strip()
    return (text or None), git_check_ran, last_user_text


def _status_hits(text: str) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for pat in _STATUS_PATTERNS:
        m = pat.search(text)
        if m and m.group(0) not in seen:
            seen.add(m.group(0))
            hits.append(m.group(0).strip())
    return hits


def _speculative_hits(text: str) -> list[str]:
    """status tokens that have a speculation token within _SPEC_WINDOW chars
    (= a guess about commit/tracking state, the high-precision target)."""
    out: list[str] = []
    seen: set[str] = set()
    for pat in _STATUS_PATTERNS:
        for m in pat.finditer(text):
            window = text[max(0, m.start() - _SPEC_WINDOW): m.end() + _SPEC_WINDOW]
            if _SPECULATION.search(window) and m.group(0) not in seen:
                seen.add(m.group(0))
                out.append(m.group(0).strip())
    return out


def _log(sid: str, hits: list[str], blocked: bool, text: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        snippet = text.replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        entry = {
            "ts": ts, "session_id": sid, "category": _CATEGORY,
            "tokens": hits, "blocked": blocked, "context": snippet,
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


_REASON = (
    "[block_unverified_commit_status] このターンは「{tokens}」と commit/追跡の状態を、"
    "user が git/commit を訊いてもいないのに、`git check-ignore` も踏まず断定/言及しています。"
    "git-workflow.md の最優先条項違反: (1) 頼まれてもいない commit/git は自分から出さない "
    "(完了報告の『未コミット』脚注を消す)。(2) どうしても状態に触れるなら、推測せず "
    "`git check-ignore -v <path>`(ignored か) / `git ls-files --error-unmatch <path>`(tracked か) を"
    "踏んで事実だけ言う。『可能性が高い』のような確認で潰せる推測は禁止。"
    "(`/projects/*` は全て gitignored が事実。)\n"
    "正当な場合 — user が直前に commit/git を訊いた・確認コマンドを既に踏んだ・git ルール自体を"
    "編集/議論している — は本文に `# COMMIT-STATUS-OK: <根拠>` を添えて再送すれば通る(根拠は監査)。"
)


def main() -> int:
    data = _read_payload()
    sid = data.get("session_id") or ""
    transcript = data.get("transcript_path") or ""
    stop_active = bool(data.get("stop_hook_active"))
    if not transcript:
        return 0

    text, git_check_ran, last_user = _scan_turn(transcript)
    if not text:
        return 0

    # conjunct 1: a SPECULATIVE commit/tracking status claim (guess + status token).
    # Assertive/grounded mentions are left to instruction (too FP-prone to block).
    hits = _speculative_hits(text)
    if not hits:
        return 0
    # override marker (legit verified / meta).
    if _OK_MARKER.search(text):
        return 0
    # conjunct 2: a verification command actually ran → verified fact → pass.
    if git_check_ran:
        return 0
    # conjunct 3: user raised git/commit this turn → on-topic discussion → pass.
    if last_user and _USER_GIT_RAISED.search(last_user):
        return 0
    # guard: anger in the user's message → mandated minimal stop → never block.
    if last_user and _USER_ANGER.search(last_user):
        _log(sid, hits, blocked=False, text=text)
        record_fire(_MEMORY_SLUG, "warn", count=len(hits), context="anger-stop-passed")
        return 0

    tokens = ", ".join(hits[:6])

    # loop guard: already a forced continuation → audit only.
    if stop_active:
        _log(sid, hits, blocked=False, text=text)
        record_fire(_MEMORY_SLUG, "warn", count=len(hits), context=f"loopguard:{tokens}")
        return 0

    # forced redo (the main model is the judge on the rewrite).
    _log(sid, hits, blocked=True, text=text)
    record_fire(_MEMORY_SLUG, "warn", count=len(hits), context=tokens)
    sys.stderr.write(_REASON.format(tokens=tokens) + "\n")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never break the Stop chain
        sys.stderr.write(f"[block_unverified_commit_status] error: {exc}\n")
        sys.exit(0)
