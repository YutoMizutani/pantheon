#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — block `lsof` that runs without `-b`.

Background:
  `lsof` stats every mount point while building its device table. On this Mac
  `/System/Volumes/Data/home` is an autofs mount, which macOS treats as a
  network volume, so a plain `lsof` from Claude Code makes macOS show the
  "“2.1.310” would like to access files on a network volume" permission dialog
  to the user. That turns a read-only lookup into an action the user has to
  take.

  `-b` makes lsof avoid the kernel calls (stat/lstat/readlink) that touch
  mount points. Whether `-b` alone suppresses the dialog is not yet observed;
  the first allowed `lsof -b` is the test.

Contract:
  - Blocks (exit 2) only when the command invokes `lsof` as a command verb
    and no `-b` flag (alone or combined, e.g. `-bw`) is present.
  - For "which processes run binary X", prefer `ps -axo pid,args` and resolve
    the symlink of argv[0] instead of lsof.
"""
import json
import re
import sys

_LSOF_VERB = re.compile(r"(?:^|[|&;\n(){}`]|\$\(|\bxargs\s+|\bsudo\s+)\s*(?:[A-Za-z_]\w*=\S+\s+)*(?:/\S*/)?lsof\b([^|&;\n]*)")
_B_FLAG = re.compile(r"(?:^|\s)-[a-zA-Z]*b")


# --- telemetry instrumentation (best-effort; never breaks the hook) ---
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry import must never break a hook
    def record_fire(*_a, **_k):  # type: ignore
        return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(cmd, str) or "lsof" not in cmd:
        return
    for m in _LSOF_VERB.finditer(cmd):
        if not _B_FLAG.search(m.group(1)):
            record_fire("block_unscoped_lsof", "block")
            sys.stderr.write(
                "[lsof] `lsof` without `-b` stats every mount point; the autofs "
                "mount /System/Volumes/Data/home counts as a network volume, so "
                "macOS shows the user a network-volume permission dialog. Add "
                "`-b` (e.g. `lsof -b -p <pid>`), or for 'which process runs "
                "binary X' use `ps -axo pid,args` and resolve argv[0].\n"
            )
            sys.exit(2)


if __name__ == "__main__":
    main()
