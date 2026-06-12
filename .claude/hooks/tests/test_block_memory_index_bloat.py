#!/usr/bin/env python3
"""RED-first test for block_memory_index_bloat.

Contract: PreToolUse Edit|Write on MEMORY.md must DENY only when the operation
*increases* the count of over-long (>200 char) index entries — i.e. adds a new
bloated index line. Shortening an existing over-long line, adding a short line,
or any edit to a non-MEMORY.md file must PASS.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "block_memory_index_bloat.py"
import tempfile  # noqa: E402

# Hermetic memory index: the hook's Write rule reads the EXISTING file at
# file_path to decide whether the over-long count *increases*. Pointing at the
# real per-user MEMORY.md made the expectation depend on live state (a real
# index that already contains N>=1 over-long lines turns case 5 into a
# no-increase pass-through with no truncation). _is_memory_index() matches by
# path shape (".../memory/MEMORY.md"), so a temp dir satisfies it.
_TMP_MEMORY_DIR = Path(tempfile.mkdtemp(prefix="frame_bloat_test_")) / "memory"
_TMP_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_MD = str(_TMP_MEMORY_DIR / "MEMORY.md")
Path(MEMORY_MD).write_text("# Memory Index\n", encoding="utf-8")
LONG = "x" * 240  # a 240-char body → an index line well over the 200 limit


def run(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    out = proc.stdout.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_raw": out}


def decision(payload: dict) -> str:
    obj = run(payload)
    return (obj.get("hookSpecificOutput") or {}).get("permissionDecision", "allow")


def updated_input(payload: dict) -> dict:
    obj = run(payload)
    return (obj.get("hookSpecificOutput") or {}).get("updatedInput") or {}


def _index_lines(text: str) -> list[str]:
    return [ln for ln in (text or "").splitlines() if ln.lstrip().startswith("- [")]


def edit(file_path: str, old: str, new: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": file_path, "old_string": old, "new_string": new}}


def write(file_path: str, content: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": file_path, "content": content}}


# A title/path whose prefix alone exceeds the budget → cannot be auto-truncated.
OVERLONG_PREFIX = "- [" + ("T" * 200) + "](x.md) — short"


def _check_truncated_edit(payload: dict) -> str | None:
    """Verify the updatedInput's index lines all fit and the trimmed one ends '…'."""
    ui = updated_input(payload)
    ns = ui.get("new_string")
    if ns is None:
        return "no updatedInput.new_string"
    lines = _index_lines(ns)
    over = [ln for ln in lines if len(ln) > 200]
    if over:
        return f"line still >200: {len(over[0])} chars"
    if not any(ln.endswith("…") for ln in lines):
        return "no line shows the '…' truncation marker"
    return None


def _check_truncated_write(payload: dict) -> str | None:
    ui = updated_input(payload)
    content = ui.get("content")
    if content is None:
        return "no updatedInput.content"
    over = [ln for ln in _index_lines(content) if len(ln) > 200]
    if over:
        return f"content line still >200: {len(over[0])} chars"
    return None


CASES = []

# 1. Append a NEW over-long index line → ALLOW + auto-truncate (was DENY before
#    the deny→truncate change). Verify the trimmed line fits and is marked.
CASES.append((
    "append_new_overlong_entry_truncated",
    edit(MEMORY_MD, "## 7. Tooling", "## 7. Tooling\n- [Bloat](x.md) — " + LONG),
    "allow",
    _check_truncated_edit,
))

# 2. Append a SHORT index line → ALLOW (no mutation)
CASES.append((
    "append_short_entry",
    edit(MEMORY_MD, "## 7. Tooling", "## 7. Tooling\n- [Fine](x.md) — short hook"),
    "allow",
    None,
))

# 3. SHORTEN an existing over-long line (not an INCREASE) → ALLOW, untouched
CASES.append((
    "shorten_existing_overlong",
    edit(MEMORY_MD, "- [A](a.md) — " + ("y" * 300), "- [A](a.md) — " + ("y" * 220)),
    "allow",
    None,
))

# 4. Edit to a NON-MEMORY.md file with an over-long line → ALLOW (topic files may be long)
CASES.append((
    "non_memory_file",
    edit(str(Path(MEMORY_MD).parent / "feedback_x.md"), "a", "a\n- [B](b.md) — " + LONG),
    "allow",
    None,
))

# 5. Write whose content adds an over-long index line → ALLOW + content truncated
CASES.append((
    "write_overlong_content_truncated",
    write(MEMORY_MD, "# Memory Index\n- [Bloat](x.md) — " + LONG + "\n"),
    "allow",
    _check_truncated_write,
))

# 6. Fallback: structural prefix (title/path) alone over budget → DENY (can't auto-fix)
CASES.append((
    "overlong_prefix_falls_back_to_deny",
    edit(MEMORY_MD, "## 7. Tooling", "## 7. Tooling\n" + OVERLONG_PREFIX),
    "deny",
    None,
))


def _run_case(name: str, payload: dict, expected: str, verify) -> None:
    """Shared assertion used by both pytest functions and main()."""
    got = decision(payload)
    assert got == expected, f"{name}: expected={expected} got={got}"
    if verify is not None:
        err = verify(payload)
        assert err is None, f"{name}: {err}"


def test_append_new_overlong_entry_truncated() -> None:
    name, payload, expected, verify = CASES[0]
    _run_case(name, payload, expected, verify)


def test_append_short_entry() -> None:
    name, payload, expected, verify = CASES[1]
    _run_case(name, payload, expected, verify)


def test_shorten_existing_overlong() -> None:
    name, payload, expected, verify = CASES[2]
    _run_case(name, payload, expected, verify)


def test_non_memory_file() -> None:
    name, payload, expected, verify = CASES[3]
    _run_case(name, payload, expected, verify)


def test_write_overlong_content_truncated() -> None:
    name, payload, expected, verify = CASES[4]
    _run_case(name, payload, expected, verify)


def test_overlong_prefix_falls_back_to_deny() -> None:
    name, payload, expected, verify = CASES[5]
    _run_case(name, payload, expected, verify)


def main() -> int:
    if not HOOK.exists():
        print(f"RED: hook not found at {HOOK}")
        return 1
    failures = []
    for name, payload, expected, verify in CASES:
        got = decision(payload)
        detail = ""
        ok = got == expected
        if ok and verify is not None:
            err = verify(payload)
            if err:
                ok = False
                detail = f" — {err}"
        status = "ok" if ok else "FAIL"
        if not ok:
            failures.append((name, expected, got, detail))
        print(f"  [{status}] {name}: expected={expected} got={got}{detail}")
    if failures:
        print(f"\n{len(failures)} FAILED")
        return 1
    print(f"\nall {len(CASES)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
