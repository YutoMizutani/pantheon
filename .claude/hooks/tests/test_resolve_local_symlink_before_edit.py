#!/usr/bin/env python3
"""Contract test for resolve_local_symlink_before_edit.py.

The hook DENIES (and redirects) an Edit/Write/MultiEdit whose file_path is itself a
symlink, handing back the resolved real target. It must:
  - fire on Edit / Write / MultiEdit when file_path is a symlink, and the reason
    text must contain the resolved realpath (so the round-trip is skipped);
  - NEVER fire on a regular file, a non-existent (new) file, a path whose only a
    PARENT dir is a symlink (the file component is not — matches the harness's
    actual refusal condition; protects macOS /tmp → /private/tmp), or a non-edit
    tool;
  - emit nothing (allow) so the deny is a clean no-op.
No guard-conflict case is needed: the hook reads only file_path, never command or
content text, so it structurally cannot block a safety guard's stop-output.
Run: python3 .claude/hooks/tests/test_resolve_local_symlink_before_edit.py
"""
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "resolve_local_symlink_before_edit.py"


def run(tool: str, file_path: str) -> dict | None:
    out = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": tool, "tool_input": {"file_path": file_path}}),
        capture_output=True, text=True,
    ).stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def denies(obj: dict | None) -> bool:
    return bool(obj) and obj.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def main() -> int:
    failed = []
    with tempfile.TemporaryDirectory() as d:
        real = os.path.join(d, "real.txt")
        link = os.path.join(d, "link.txt")
        plain = os.path.join(d, "plain.txt")
        new = os.path.join(d, "does_not_exist.txt")
        Path(real).write_text("hi\n", encoding="utf-8")
        Path(plain).write_text("hi\n", encoding="utf-8")
        os.symlink("real.txt", link)
        # a directory symlink: writing to a file UNDER it (file component not a link)
        realdir = os.path.join(d, "realdir")
        linkdir = os.path.join(d, "linkdir")
        os.mkdir(realdir)
        os.symlink("realdir", linkdir)
        under_linkdir = os.path.join(linkdir, "child.txt")  # parent is a symlink, child is not

        resolved_real = os.path.realpath(link)

        # (description, tool, file_path, expect_deny, must_contain_realpath)
        cases = [
            ("Edit on symlink file → deny + realpath in reason", "Edit", link, True, True),
            ("Write on symlink file → deny", "Write", link, True, True),
            ("MultiEdit on symlink file → deny", "MultiEdit", link, True, True),
            ("Edit on regular file → no fire", "Edit", plain, False, False),
            ("Edit on the real target itself → no fire", "Edit", real, False, False),
            ("Edit on non-existent new file → no fire", "Write", new, False, False),
            ("Edit on file under a symlinked PARENT dir → no fire", "Edit", under_linkdir, False, False),
            ("non-edit tool (Bash) → no fire", "Bash", link, False, False),
        ]

        for desc, tool, fp, expect_deny, want_real in cases:
            obj = run(tool, fp)
            got = denies(obj)
            ok = got == expect_deny
            if ok and want_real:
                reason = obj.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
                if resolved_real not in reason:
                    ok = False
                    desc += " [realpath MISSING from reason]"
            print(f"[{'PASS' if ok else 'FAIL'}] {desc} "
                  f"(got={'DENY' if got else 'pass'} want={'DENY' if expect_deny else 'pass'})")
            if not ok:
                failed.append(desc)

    print(f"\n{8 - len(failed)}/8 passed")
    if failed:
        for f in failed:
            print(f"  FAILED: {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
