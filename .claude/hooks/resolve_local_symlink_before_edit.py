#!/usr/bin/env python3
"""resolve_local_symlink_before_edit — redirect Edit/Write/MultiEdit on a symlink
to its real target path before the harness refuses the write.

Source: meta-review of the ok-triggered self-improvement loop (2026-06-29, sid
7d808e1b). The harness refuses to write through a symlink ("Refusing to write
through symlink: ... Resolve the symlink and pass the real target path
explicitly."), but its message does NOT include the resolved target, so Claude
burns a readlink → realpath → re-Read → Edit round-trip rediscovering it. This
failure signature recurred across 5+ sessions (06-20 x2 / 06-21 / 06-22 / 06-29)
despite a covering memory, because the memory is on-demand and is not recalled at
the edit decision moment. This hook pre-empts deterministically: it resolves the
target and hands it back, recall-independent.

Frame layer: detection is ``os.path.islink(file_path)`` only — no user-specific
path is hardcoded, so it holds in any environment (the harness's symlink-write
refusal is environment-independent). ``islink`` checks only the FINAL path
component, so it matches the harness's actual refusal condition (the file written
IS a symlink) and does NOT fire when only a parent dir is a symlink — e.g. macOS
``/tmp`` → ``/private/tmp``, so ordinary scratchpad writes are untouched.

Verified (same session, scratchpad experiment): the harness tracks Read-state
PER LITERAL PATH, not per resolved realpath — reading the symlink path does NOT
satisfy the Read gate for the realpath. So the redirect message tells Claude to
Read the real target first. (The reflection's "no re-Read needed" claim was tested
false and deliberately omitted from the message.)

Deny is emitted via ``hookSpecificOutput.permissionDecision`` (matches the sibling
block_* hooks); the edit through the symlink is a no-op when denied. Editing the
realpath is behaviorally identical (same inode), so the redirect never changes the
write's effect and never blocks a write the harness would have allowed. This hook
reads only ``file_path`` (never command/content text), so it structurally cannot
block a safety guard's legitimate stop-output (no guard-conflict).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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


def _emit_deny(reason: str) -> None:
    obj = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    data = _read_payload()
    tool = data.get("tool_name") or ""
    if tool not in ("Edit", "Write", "MultiEdit"):
        return 0
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    file_path = str(tool_input.get("file_path") or "")
    if not file_path:
        return 0
    try:
        if not os.path.islink(file_path):
            return 0
        real = os.path.realpath(file_path)
    except OSError:
        return 0
    # islink is True, so a real symlink always resolves to a different path; the
    # guard only suppresses the degenerate self-loop case (can't write anyway).
    if not real or real == os.path.abspath(file_path):
        return 0

    record_fire(
        "feedback_edit_local_layer_via_real_path_not_symlink",
        "block",
        context="symlink_write_redirect",
    )
    _emit_deny(
        f"`{file_path}` は symlink です（実体 → `{real}`）。\n"
        "harness は symlink 経由の書き込みを拒否します。file_path を実体パス "
        f"`{real}` に差し替えて再実行してください。\n"
        "重要: harness の Read 状態はパス単位です（同一セッションで実測済み）— symlink パスを "
        "Read 済みでも、実体パスをまだ Read していなければ Edit は "
        "`File has not been read yet` で弾かれます。実体パスを先に Read してから Edit すること。\n"
        "（この deny で対象ファイルは一切変わっていません = no-op。）"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[resolve_local_symlink_before_edit] error: {exc}\n")
        sys.exit(0)
