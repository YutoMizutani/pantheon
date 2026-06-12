#!/usr/bin/env python3
"""propose_claudemd_updates — queue project-scoped CLAUDE.md update proposals at session/turn end.

Source reference:
  - Anthropic blog "How Claude Code works in large codebases" → "Use stop hooks
    to reflect on sessions and propose CLAUDE.md updates" (2026)

Stop event hook. Differentiates from ``detect_correction_signal_v2.py``:

  * detect_correction_signal: UserPromptSubmit, on user correction → queues the
    event for the batched reflection that detect_acceptance_signal dispatches
    on the next exact acceptance signal (writes user-global memory under
    ``~/.claude/projects/.../memory/``).
  * propose_claudemd_updates (this hook): Stop, when user's prompt this turn
    contains a project-scoped or codify-explicit signal → queues a proposal to
    update the project's ``CLAUDE.md`` or ``.claude/rules/`` (NOT user memory).

These are complementary: a single turn can fire both. Memory captures behavior
patterns that follow Claude across projects; CLAUDE.md captures rules that
apply only inside one ``projects/<name>/`` tree.

Signal categories (all evaluated against the user's prompt this turn):

  A. Project-scoped marker — phrases that explicitly bound the rule to a
     project / repo / directory: 「このプロジェクトでは」「このリポジトリでは」
     「ここでは」.
  B. Codify-explicit — user asks for codification: 「ルール化」「CLAUDE.md に」
     「ドキュメント化」「rules に追加」.
  C. Repeated-guidance — user signals they've said this before: 「毎回」「何度も」
     「またこれ」「前にも(言った|伝えた|指摘した)」.

A or B alone => proposal queued.
C alone => weak; proposal queued only if combined with A or with a forward
directive in the assistant turn.

The proposal target file is resolved from ``cwd``, following the two-layer
repo structure (frame = pantheon-tracked mechanism / local = user-specific,
gitignored). Root ``CLAUDE.md`` is frame-layer (routing + mechanism only) and
is never a target:
  * cwd under ``projects/<name>/`` => target ``projects/<name>/CLAUDE.md``
    (local layer — projects/ is the user's possession)
  * else => target ``CLAUDE.local.md`` (local layer — user-specific
    cross-project guidance). If review finds the rule environment-generic,
    the reviewer generalizes it into ``.claude/rules/common/`` (frame layer).
Each queued item carries a ``layer`` field derived from the target so the
batch review can see at a glance what would become a commit candidate.

Queue file: ``~/.claude/runtime/pending_claudemd_updates.json`` (same flavor as
``pending_hook_registrations.json``). The user reviews + applies in batch;
this hook never edits CLAUDE.md directly.

Audit log: ``.claude/runtime/propose_claudemd_audit.log``
to keep all queue events observable.

Exit 0 always (audit-only). Stop fires post-display so blocking is moot;
the value is closing the codification gap with a deterministic post-turn signal.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- Pattern sets ---

_PROJECT_SCOPED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"このプロジェクトでは"),
    re.compile(r"このプロジェクトで"),
    re.compile(r"このリポジトリでは"),
    re.compile(r"このリポジトリで"),
    re.compile(r"このディレクトリでは"),
    re.compile(r"この repo では"),
    re.compile(r"この repo で"),
    re.compile(r"projects/[a-zA-Z0-9_-]+ でのみ"),
    re.compile(r"projects/[a-zA-Z0-9_-]+ では"),
)

_CODIFY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ルール化(?:して|しろ|したい)"),
    re.compile(r"CLAUDE\.md に(?:書|追加|入)"),
    re.compile(r"claude\.md に(?:書|追加|入)"),
    re.compile(r"rules?/?\s*に(?:書|追加|入)"),
    re.compile(r"ドキュメント化"),
    re.compile(r"明文化"),
    re.compile(r"恒久(?:化|ルール)"),
    re.compile(r"次のセッションでも"),
    re.compile(r"今後も覚え"),
)

_REPEATED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"毎回"),
    re.compile(r"何度も"),
    re.compile(r"何回も"),
    re.compile(r"またこれ"),
    re.compile(r"また同じ"),
    re.compile(r"前にも(?:言った|伝えた|指摘した|書いた)"),
    re.compile(r"前回も"),
    re.compile(r"以前にも"),
)

# Forward directives in assistant text — used only to upgrade weak (repeated-only)
# signals into queueable proposals (proves the turn produced a codifiable rule).
_FORWARD_DIRECTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"次から(?:は|)"),
    re.compile(r"今後は"),
    re.compile(r"以降(?:は|)"),
    re.compile(r"次回(?:は|)"),
)

from _paths import PROJECT_DIR as _REPO_ROOT  # noqa: E402
from _paths import RUNTIME_DIR as _RUNTIME_DIR  # noqa: E402

_PROJECTS_ROOT = _REPO_ROOT / "projects"
_QUEUE_FILE = Path.home() / ".claude/runtime/pending_claudemd_updates.json"
_AUDIT_LOG = _RUNTIME_DIR / "propose_claudemd_audit.log"

# Cap proposals per session per project to prevent runaway queue growth on a
# session that hits these patterns repeatedly.
_PER_SESSION_PER_TARGET_CAP = 3


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _is_system_generated(text: str) -> bool:
    """True for harness-injected pseudo-user messages that are NOT real prompts.

    Treating a <task-notification> (workflow completion) as a user prompt caused a
    false-positive codify when its <result> JSON happened to contain 明文化
    (pending_claudemd_updates item1). detect_acceptance_signal applies the same guard.
    """
    s = (text or "").lstrip()
    return s.startswith((
        "<task-notification>",
        "<system-reminder>",
        "<command-message>",
        "<command-name>",
    ))


def _collect_turn_text(transcript_path: str) -> tuple[str, str]:
    """Return (user_text, assistant_text) accumulated since the previous user message.

    Stop fires once per assistant turn. We accumulate every user message that
    belongs to the *current* turn (typically one, but tool-result re-injections
    can show as user role) and every assistant text block, so the pattern
    scanner sees the full conversational unit.
    """
    if not transcript_path:
        return "", ""
    p = Path(transcript_path)
    if not p.exists():
        return "", ""
    user_parts: list[str] = []
    assistant_parts: list[str] = []
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
                # Reset on each user message — keep only the most recent user turn
                # (this represents "since the last user message" for assistant
                # accumulation). For our purposes the latest user prompt is the
                # signal source. System-generated pseudo-user messages (task
                # notifications / reminders / command markers) are skipped
                # transparently so they never become the signal (FP guard).
                content = msg.get("content")
                cand: list[str] = []
                if isinstance(content, str):
                    cand.append(content)
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            cand.append(str(c.get("text", "")))
                if _is_system_generated("\n".join(cand).strip()):
                    continue
                user_parts = cand
                assistant_parts = []
                continue
            if role != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                assistant_parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        assistant_parts.append(str(c.get("text", "")))
    except OSError:
        return "", ""
    return "\n".join(user_parts).strip(), "\n".join(assistant_parts).strip()


def _detect_signals(user_text: str, assistant_text: str) -> dict:
    """Return a dict summarizing which signal categories fired."""

    def _hits(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
        out: list[str] = []
        for pat in patterns:
            for m in pat.finditer(text):
                tok = m.group(0)
                if tok not in out:
                    out.append(tok)
        return out

    project_scoped = _hits(user_text, _PROJECT_SCOPED_PATTERNS)
    codify = _hits(user_text, _CODIFY_PATTERNS)
    repeated = _hits(user_text, _REPEATED_PATTERNS)
    forward_in_assistant = _hits(assistant_text, _FORWARD_DIRECTIVE_PATTERNS)

    # Decision: A or B => fire. C alone is upgraded only if assistant produced
    # a forward directive (proves the turn yielded a codifiable rule) AND a
    # project-scoped marker exists. C without context is noise.
    strong = bool(project_scoped) or bool(codify)
    upgraded = bool(repeated) and bool(forward_in_assistant) and bool(project_scoped)
    fire = strong or upgraded
    return {
        "project_scoped": project_scoped,
        "codify": codify,
        "repeated": repeated,
        "forward_in_assistant": forward_in_assistant,
        "fire": fire,
    }


def _layer_for_target(target: Path) -> str:
    """二層構成での所属層。frame = pantheon git 同梱 (commit 候補) /
    local = ユーザー固有 (gitignore 済み — CLAUDE.local.md / projects/*)。"""
    try:
        rel = str(target.relative_to(_REPO_ROOT))
    except ValueError:
        return "local"
    if rel.startswith(".claude/rules/common/"):
        return "frame"
    return "local"


def _resolve_target(cwd: str) -> tuple[Path, str]:
    """Return (target file path, scope_label).

    Non-project cwd targets CLAUDE.local.md, NOT root CLAUDE.md — the root
    file is frame-layer (routing + mechanism only); user-specific guidance
    accumulated by this loop belongs to the local layer."""
    try:
        cwd_p = Path(cwd).resolve()
    except OSError:
        return _REPO_ROOT / "CLAUDE.local.md", "local-global"
    try:
        rel = cwd_p.relative_to(_PROJECTS_ROOT)
        # First component under projects/ is the project name. Nested
        # sub-projects target the closest CLAUDE.md upward.
        parts = rel.parts
        if parts:
            # Walk up from cwd looking for the nearest CLAUDE.md inside projects/<name>/.
            cur = cwd_p
            while _PROJECTS_ROOT in cur.parents or cur == _PROJECTS_ROOT:
                cand = cur / "CLAUDE.md"
                if cand.exists():
                    rel_name = cand.relative_to(_REPO_ROOT)
                    return cand, str(rel_name).replace("/CLAUDE.md", "")
                if cur == _PROJECTS_ROOT:
                    break
                cur = cur.parent
            # Fallback: projects/<first>/CLAUDE.md even if absent (proposal still
            # meaningful as a creation request).
            return _PROJECTS_ROOT / parts[0] / "CLAUDE.md", f"projects/{parts[0]}"
    except ValueError:
        pass
    return _REPO_ROOT / "CLAUDE.local.md", "local-global"


def _excerpt(text: str, limit: int = 400) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …"


def _load_queue() -> dict:
    if not _QUEUE_FILE.exists():
        return {"queued_at": None, "items": []}
    try:
        return json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"queued_at": None, "items": []}


def _count_session_target(queue: dict, session_id: str, target: str) -> int:
    if not isinstance(queue.get("items"), list):
        return 0
    return sum(
        1
        for item in queue["items"]
        if isinstance(item, dict)
        and item.get("session_id") == session_id
        and item.get("target_file") == target
    )


def _save_queue(queue: dict) -> None:
    try:
        _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _QUEUE_FILE.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _audit(entry: dict) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


sys.path.insert(0, str(Path(__file__).parent))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # never let telemetry import break a hook
    def record_fire(*_a, **_k) -> None:  # type: ignore
        return None


def main() -> int:
    data = _read_payload()
    sid = data.get("session_id") or ""
    transcript = data.get("transcript_path") or ""
    cwd = data.get("cwd") or os.getcwd()
    if not sid or not transcript:
        return 0

    user_text, assistant_text = _collect_turn_text(transcript)
    if not user_text:
        return 0

    signals = _detect_signals(user_text, assistant_text)
    if not signals["fire"]:
        return 0

    target_path, scope = _resolve_target(cwd)
    target_str = str(target_path)

    queue = _load_queue()
    if _count_session_target(queue, sid, target_str) >= _PER_SESSION_PER_TARGET_CAP:
        _audit(
            {
                "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "session_id": sid,
                "result": "capped",
                "target_file": target_str,
                "signals": {k: v for k, v in signals.items() if k != "fire"},
            }
        )
        return 0

    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    item = {
        "session_id": sid,
        "queued_at": now_iso,
        "target_file": target_str,
        "scope": scope,
        "layer": _layer_for_target(target_path),
        "cwd": cwd,
        "trigger_signals": {
            "project_scoped": signals["project_scoped"],
            "codify": signals["codify"],
            "repeated": signals["repeated"],
            "forward_in_assistant": signals["forward_in_assistant"],
        },
        "user_excerpt": _excerpt(user_text),
        "assistant_excerpt": _excerpt(assistant_text),
        "review_note": (
            "Two-layer review: the suggested target keeps this on the layer "
            "named in `layer` (local = user-specific, gitignored / frame = "
            "pantheon-tracked). Keep user-specific guidance in CLAUDE.local.md "
            "or the project CLAUDE.md; only environment-generic rules belong "
            "in .claude/rules/common/ (frame, commit candidate). Root CLAUDE.md "
            "is routing+mechanism only — never a target. Apply manually after "
            "review."
        ),
    }
    if not isinstance(queue.get("items"), list):
        queue["items"] = []
    queue["items"].append(item)
    queue["queued_at"] = now_iso
    _save_queue(queue)

    record_fire(
        "propose_claudemd_updates",
        "audit",
        context=f"{scope}|{target_str}"[:200],
    )
    _audit(
        {
            "ts": now_iso,
            "session_id": sid,
            "result": "queued",
            "target_file": target_str,
            "scope": scope,
            "signals": {k: v for k, v in signals.items() if k != "fire"},
        }
    )

    sys.stderr.write(
        f"[propose_claudemd_updates] queued proposal for {scope} "
        f"({len(queue['items'])} total pending). Review: {_QUEUE_FILE}\n"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — hook must never raise into runtime
        sys.stderr.write(f"[propose_claudemd_updates] error: {exc}\n")
        sys.exit(0)
