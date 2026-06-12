#!/usr/bin/env python3
"""block_memory_index_bloat — keep MEMORY.md index entries short (Recommendation #1).

Source: meta-review of the ok-triggered self-improvement loop. The reflection
sub-agents (detect_correction_signal_v2 / detect_acceptance_signal) write memory
files AND append index lines to MEMORY.md with NO gate. The cumulative result
observed: MEMORY.md grew well past the harness auto-memory load limit (~24.4KB), so
the auto-memory injection only partially loads it ("index entries are too long; only
part was loaded") — the self-improvement loop's own reference base degrades.

This PreToolUse Edit|Write hook enforces the documented rule "keep index entries to
one line under ~200 chars; move detail into topic files." It is deliberately
non-disruptive to pre-existing over-long entries: it denies ONLY when an edit
*increases* the count of over-long index lines (i.e. adds/lengthens a bloated
line). Shortening an over-long line, adding a short line, consolidation passes, and
any non-MEMORY.md file all PASS — so this hook never fights a consolidation effort.

Rule A (auto-fix): count_overlong(new) > count_overlong(old) -> truncate the
  newly over-long index line(s) at a word boundary (append "…") and ALLOW the
  edit through via hookSpecificOutput.updatedInput, emitting a transparent
  additionalContext notice. Deny is now only a FALLBACK, used when a line's
  structural prefix (title/path) alone exceeds the budget and cannot be trimmed.
  Rationale: the prior hard-deny forced a manual guess-and-retry loop (原環境実測:
  ~98 blocks / 145 fires, 4-retry bursts on a single line); truncate+allow
  removes the round-trips while still guaranteeing <=200-char index lines.
Rule B (non-blocking warn): MEMORY.md byte size > SIZE_LIMIT -> stderr reminder +
  telemetry, so the standing bloat stays observable until consolidated.

Classified saying-fault (deterministic line-length check). Wired in this frame's
settings.json under PreToolUse Edit|Write|MultiEdit (new hooks of this kind are
queued to ~/.claude/runtime/pending_hook_registrations.json for batch user review).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Per-index-line character ceiling (the documented "~200 chars" rule).
LIMIT_CHARS = 200
# Whole-file byte ceiling (the harness auto-memory limit; warn-only).
SIZE_LIMIT_BYTES = 24_400


sys.path.insert(0, str(Path(__file__).parent))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # never let telemetry import break a hook
    def record_fire(*_a, **_k) -> None:  # type: ignore
        return None


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _emit_decision(decision: str, reason: str) -> None:
    obj = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _is_memory_index(file_path: str) -> bool:
    if not file_path:
        return False
    norm = file_path.replace("\\", "/")
    return norm.endswith("/MEMORY.md") and "/memory/" in norm.lower()


def _index_entry_lines(text: str) -> list[str]:
    """Index entries are markdown link bullets: ``- [Title](file.md) — ...``."""
    out: list[str] = []
    for line in (text or "").splitlines():
        if line.lstrip().startswith("- ["):
            out.append(line.rstrip())
    return out


def _count_overlong(text: str) -> int:
    return sum(1 for line in _index_entry_lines(text) if len(line) > LIMIT_CHARS)


def _longest_overlong(text: str) -> str | None:
    over = [line for line in _index_entry_lines(text) if len(line) > LIMIT_CHARS]
    if not over:
        return None
    return max(over, key=len)


ELLIPSIS = "…"


def _truncate_index_line(line: str) -> str | None:
    """Trim one over-long index bullet to <= LIMIT_CHARS, cutting at a word
    boundary and keeping the ``- [Title](file.md) — `` prefix intact. Returns
    the trimmed line, or None if even the prefix alone exceeds the budget
    (title/path too long to auto-fix → caller should fall back to deny)."""
    line = line.rstrip()
    if len(line) <= LIMIT_CHARS:
        return line
    budget = LIMIT_CHARS - len(ELLIPSIS)  # reserve room for the ellipsis → result <= LIMIT
    sep = line.find("— ")                 # em-dash separating the link from the hook text
    min_keep = (sep + 2) if sep != -1 else 0
    if min_keep > budget:
        return None                       # structural prefix alone is over budget
    head = line[:budget]
    cut = head.rfind(" ")                  # prefer a word boundary in the tail
    if cut <= min_keep:
        cut = budget                       # no boundary past the prefix → hard cut
    return line[:cut].rstrip() + ELLIPSIS


def _truncate_overlong(text: str) -> tuple[str, int, bool]:
    """Rewrite every over-long index bullet in ``text`` to fit. Returns
    (new_text, num_truncated, ok); ok is False if any over-long line could not
    be fixed. Non-index lines and already-short lines pass through verbatim."""
    out: list[str] = []
    n = 0
    ok = True
    for raw in (text or "").splitlines():
        if raw.lstrip().startswith("- [") and len(raw.rstrip()) > LIMIT_CHARS:
            fixed = _truncate_index_line(raw)
            if fixed is None:
                ok = False
                out.append(raw)
            else:
                out.append(fixed)
                n += 1
        else:
            out.append(raw)
    joined = "\n".join(out)
    if text.endswith("\n"):
        joined += "\n"
    return joined, n, ok


def _rebuild_input(tool: str, tool_input: dict) -> tuple[dict | None, int, bool]:
    """Return (updatedInput, num_truncated, ok): a copy of tool_input with
    over-long index lines truncated in-place. updatedInput is None when nothing
    needed trimming. Covers Write (content), Edit (new_string) and MultiEdit
    (edits[].new_string)."""
    ui = dict(tool_input)
    total = 0
    ok = True
    if tool == "Write":
        fixed, n, k = _truncate_overlong(str(tool_input.get("content") or ""))
        ui["content"] = fixed
        total += n
        ok = ok and k
    elif isinstance(tool_input.get("edits"), list):
        new_edits = []
        for e in tool_input["edits"]:
            if isinstance(e, dict):
                e2 = dict(e)
                fixed, n, k = _truncate_overlong(str(e.get("new_string") or ""))
                e2["new_string"] = fixed
                total += n
                ok = ok and k
                new_edits.append(e2)
            else:
                new_edits.append(e)
        ui["edits"] = new_edits
    else:  # single Edit
        fixed, n, k = _truncate_overlong(str(tool_input.get("new_string") or ""))
        ui["new_string"] = fixed
        total += n
        ok = ok and k
    return (ui if total else None), total, ok


def _emit_allow_with_input(updated_input: dict, notice: str) -> None:
    obj = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
            "additionalContext": notice,
        }
    }
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _new_and_old_text(tool: str, tool_input: dict, file_path: str) -> tuple[str, str]:
    """Return (new_text, old_text) for the diff comparison."""
    if tool == "Write":
        new_text = str(tool_input.get("content") or "")
        old_text = ""
        try:
            p = Path(file_path)
            if p.exists():
                old_text = p.read_text(encoding="utf-8")
        except OSError:
            old_text = ""
        return new_text, old_text
    # Edit (and MultiEdit-style payloads with an edits list)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        new_text = "\n".join(str(e.get("new_string") or "") for e in edits if isinstance(e, dict))
        old_text = "\n".join(str(e.get("old_string") or "") for e in edits if isinstance(e, dict))
        return new_text, old_text
    return str(tool_input.get("new_string") or ""), str(tool_input.get("old_string") or "")


def main() -> int:
    data = _read_payload()
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    if tool not in ("Edit", "Write", "MultiEdit"):
        return 0
    if not isinstance(tool_input, dict):
        return 0
    file_path = str(tool_input.get("file_path") or "")
    if not _is_memory_index(file_path):
        return 0

    new_text, old_text = _new_and_old_text(tool, tool_input, file_path)
    new_over = _count_overlong(new_text)
    old_over = _count_overlong(old_text)

    if new_over > old_over:
        # Prefer auto-truncating the over-long index line(s) and letting the edit
        # through (updatedInput), rather than denying and forcing a manual retry
        # loop. Fall back to deny only when a line cannot be auto-fixed.
        updated, n_fixed, ok = _rebuild_input(tool, tool_input)
        if updated is not None and ok:
            new_text2, _ = _new_and_old_text(tool, updated, file_path)
            if _count_overlong(new_text2) <= old_over:
                record_fire(
                    "feedback_memory_index_bloat",
                    "transform",
                    context=f"truncated_{n_fixed}_lines"[:200],
                )
                _emit_allow_with_input(
                    updated,
                    (
                        f"MEMORY.md の index 行を {n_fixed} 本、{LIMIT_CHARS} 字以内へ自動短縮して"
                        "通しました (末尾を語境界で切り「…」付与)。切詰めで意味が欠けた行が"
                        "あれば 200 字以内で言い換えて再 Edit してください (任意・ブロックしません)。"
                        "詳細は対応する memory 本文が SSoT。"
                    ),
                )
                return 0

        # Fallback: a line's structural prefix (title/path) alone exceeds the
        # budget, so it can't be auto-truncated — deny with manual guidance.
        offending = _longest_overlong(new_text)
        sample = (offending or "")[:120]
        over_len = len(offending or "")
        record_fire(
            "feedback_memory_index_bloat",
            "block",
            context=f"+{new_over - old_over}_overlong|{over_len}chars"[:200],
        )
        _emit_decision(
            "deny",
            (
                f"MEMORY.md の index 行が長すぎます ({over_len} 字 > 上限 {LIMIT_CHARS} 字).\n"
                f"  - 該当(先頭): {sample}…\n\n"
                "index は 1 行 1 hook、~200 字以内に収め、詳細は topic file 側へ。\n"
                "重要: この deny で MEMORY.md は一切変わっていません (no-op)。"
                "長すぎる行はファイルに書かれていないので、それを old_string にした短縮 Edit は "
                "`String to replace not found` になります — やらないこと。\n"
                "対処 (1 発で打ち直す):\n"
                "  1) 新行を `- [Title](file.md) — <~200字の hook>` に縮めて作り直す\n"
                "  2) 既存の安定した行 (直前の index 行など) を old_string にし、"
                "new_string にその行 + `\\n` + 縮めた新行を入れて append する\n"
                "  3) 削った詳細は対応する memory 本文 (`<slug>.md`) へ移す\n"
                "由来: 自己改善ループのメタレビュー (原環境実測) / 推奨 #1 "
                "+ (deny された Edit は no-op。続きの編集の前に対象ファイルを Read し直すこと) (原環境で再発を実測)"
            ),
        )
        return 0

    # Rule B — non-blocking standing-bloat reminder.
    try:
        if os.path.getsize(file_path) > SIZE_LIMIT_BYTES:
            record_fire("feedback_memory_index_bloat", "warn", context="over_size_limit")
            sys.stderr.write(
                f"[block_memory_index_bloat] MEMORY.md is over {SIZE_LIMIT_BYTES}B "
                "— consolidation needed (index is partially loaded by the harness).\n"
            )
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[block_memory_index_bloat] error: {exc}\n")
        sys.exit(0)
