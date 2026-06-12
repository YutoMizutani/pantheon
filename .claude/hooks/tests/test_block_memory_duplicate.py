#!/usr/bin/env python3
"""RED-first test for block_memory_duplicate.

Contract: PreToolUse Write of a NEW memory/<slug>.md must DENY when its frontmatter
description is near-duplicate (bigram-Jaccard >= 0.45) of an existing memory's
description — redirecting the reflection sub-agent to EXTEND the existing entry
instead of creating a parallel one. Novel descriptions, MEMORY.md, overwrites of
existing files, and Edit calls all PASS.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import pytest
    _PYTEST_AVAILABLE = True
except ImportError:
    _PYTEST_AVAILABLE = False

HOOK = Path(__file__).resolve().parent.parent / "block_memory_duplicate.py"
_SLUG = re.sub(r"[^A-Za-z0-9]", "-", os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
MEM_DIR = str(Path.home() / ".claude/projects" / _SLUG / "memory")


def run(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
    )
    out = proc.stdout.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_raw": out}


def decision(payload: dict) -> str:
    return (run(payload).get("hookSpecificOutput") or {}).get("permissionDecision", "allow")


def _existing_description() -> str:
    for p in glob.glob(f"{MEM_DIR}/feedback_*.md"):
        t = Path(p).read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^description:\s*(.+)$", t, re.M)
        if m and len(m.group(1).strip()) > 40:
            return m.group(1).strip()
    return ""


def memory_write(path: str, description: str) -> dict:
    content = (
        "---\nname: probe-slug\n"
        f"description: {description}\n"
        "metadata:\n  type: feedback\n---\n\nbody\n"
    )
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


def _get_dup_desc_or_skip() -> str:
    """Return an existing memory description, or skip the test if no corpus exists."""
    dup_desc = _existing_description()
    if not dup_desc:
        if _PYTEST_AVAILABLE:
            pytest.skip("no existing memory corpus — fresh environment")
        else:
            print("setup: no existing memory corpus — skipping duplicate tests")
    return dup_desc


def test_near_duplicate_new_file() -> None:
    """New file with a description copied from existing memory must be denied."""
    dup_desc = _get_dup_desc_or_skip()
    assert decision(memory_write(f"{MEM_DIR}/feedback_brand_new_dup.md", dup_desc)) == "deny"


def test_novel_description() -> None:
    """New file with a genuinely novel description must be allowed."""
    _get_dup_desc_or_skip()  # skip on fresh env so all cases skip together
    assert decision(memory_write(f"{MEM_DIR}/feedback_brand_new_novel.md",
        "quux frobnicate the zorptastic widget via plover xyzzy gadgets")) == "allow"


def test_memory_md_passthrough() -> None:
    """MEMORY.md itself is not policed by this hook."""
    dup_desc = _get_dup_desc_or_skip()
    assert decision(memory_write(f"{MEM_DIR}/MEMORY.md", dup_desc)) == "allow"


def test_edit_not_write() -> None:
    """Edit (extend path) of a duplicate description must be allowed."""
    dup_desc = _get_dup_desc_or_skip()
    assert decision({"tool_name": "Edit", "tool_input": {
        "file_path": f"{MEM_DIR}/feedback_brand_new_dup.md",
        "old_string": "a", "new_string": dup_desc}}) == "allow"


def main() -> int:
    if not HOOK.exists():
        print(f"RED: hook not found at {HOOK}")
        return 1
    dup_desc = _existing_description()
    if not dup_desc:
        print("setup: no existing memory corpus — fresh environment, skipping duplicate tests")
        return 0

    cases = [
        # 1. New file, description copied from an existing memory → DENY
        ("near_duplicate_new_file", memory_write(f"{MEM_DIR}/feedback_brand_new_dup.md", dup_desc), "deny"),
        # 2. New file, novel description → ALLOW
        ("novel_description", memory_write(f"{MEM_DIR}/feedback_brand_new_novel.md",
            "quux frobnicate the zorptastic widget via plover xyzzy gadgets"), "allow"),
        # 3. MEMORY.md is not this hook's concern → ALLOW
        ("memory_md_passthrough", memory_write(f"{MEM_DIR}/MEMORY.md", dup_desc), "allow"),
        # 4. Edit (not Write) of a dup description → ALLOW (extend path)
        ("edit_not_write", {"tool_name": "Edit", "tool_input": {
            "file_path": f"{MEM_DIR}/feedback_brand_new_dup.md", "old_string": "a", "new_string": dup_desc}}, "allow"),
    ]

    failures = []
    for name, payload, expected in cases:
        got = decision(payload)
        status = "ok" if got == expected else "FAIL"
        if got != expected:
            failures.append(name)
        print(f"  [{status}] {name}: expected={expected} got={got}")
    if failures:
        print(f"\n{len(failures)} FAILED")
        return 1
    print(f"\nall {len(cases)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
