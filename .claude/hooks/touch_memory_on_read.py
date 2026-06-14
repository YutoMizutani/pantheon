#!/usr/bin/env python3
"""PreToolUse hook: stamp `last_reinforced` on memory files Claude reads.

Wired into PreToolUse on Read. When Claude opens a memory file (via
`[[name]]` resolution, manual lookup, or any other path), this hook
records the reinforcement timestamp on the file. Together with hook
fire telemetry, the aggregator can later flag rules with zero reads +
zero fires in N months as archival candidates.

Stamping BEFORE the Read (rather than after) is deliberate: a PostToolUse
stamp mutated the file immediately after Claude's Read snapshot, so the
next same-day Edit failed with "File has been modified since read". Doing
it in PreToolUse means the snapshot already contains today's stamp; same-day
re-stamps are no-ops, so the archival telemetry is preserved unchanged. Reads
only `tool_name` + `tool_input.file_path`, both present at PreToolUse time.

Best-effort: this hook never blocks the tool. Exceptions are swallowed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _memory_touch import is_memory_path, touch_memory_file  # noqa: E402
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def main() -> int:
    data = _read_payload()
    if (data.get("tool_name") or data.get("tool")) != "Read":
        return 0
    tool_input = data.get("tool_input") or data.get("toolInput") or {}
    file_path = tool_input.get("file_path") or tool_input.get("filePath")
    if not file_path:
        return 0
    if not is_memory_path(file_path):
        return 0
    touch_memory_file(file_path)
    record_fire("touch_memory_on_read", "audit")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[touch_memory_on_read] error: {exc}\n")
        sys.exit(0)
