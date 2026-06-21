#!/usr/bin/env python3
"""Guard: no single-operator home path leaks into *tracked* (public) files.

Failure it prevents
-------------------
A frame-layer artifact (committed → pushed to the public repo) that hardcodes
this operator's home path — e.g. ``/Users/<you>/Developer/llm`` or the derived
state slug ``-Users-<you>-Developer-llm``. Observed 2026-06-21: a new slash
command was committed with ``cd /Users/you/Developer/llm`` (real home) and pushed before the
hardcode was caught, so the path landed in public git history (history rewrite
is the only removal). See ``.claude/rules/common/no-hardcoded-repo-path.md``.

Why a test and NOT a Write-blocking hook (guard-conflict)
--------------------------------------------------------
The real home path *legitimately* appears in **gitignored local-layer** files
(``allow_tmp_rm.py``, ``mask_home_in_text.py``, ``strip_user_names.py`` need the
real home to do their job) and in local config (``.local/``, ``settings.local``).
A PreToolUse Write/Edit gate on the home string would false-positive on every
one of those legitimate edits. The correct boundary is **tracked vs gitignored**:
only committed content must be clean. ``git grep`` searches tracked files only,
so this test enforces exactly that boundary without touching the local layer.

Run: python3 .claude/hooks/tests/test_no_hardcoded_home_in_tracked.py
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HOME = str(Path.home())                                  # e.g. /Users/you
HOME_SLUG = re.sub(r"[^A-Za-z0-9]", "-", HOME)           # e.g. -Users-you  (state-dir slug form)

# Each needle is matched as a fixed string against tracked files only.
NEEDLES = [HOME, HOME_SLUG]


def _tracked_hits(needle: str) -> list[str]:
    """Lines in *tracked* files containing `needle` (git grep = tracked-only)."""
    p = subprocess.run(
        ["git", "grep", "-nF", "--", needle],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    # exit 0 = matches found, 1 = no match, >1 = real error.
    if p.returncode not in (0, 1):
        print(f"[FAIL] git grep errored for {needle!r}: {p.stderr.strip()}")
        return ["<git-grep-error>"]
    return [ln for ln in p.stdout.splitlines() if ln.strip()]


def main() -> int:
    failures = []
    for needle in NEEDLES:
        hits = _tracked_hits(needle)
        ok = not hits
        print(f"[{'PASS' if ok else 'FAIL'}] no tracked file contains {needle!r} "
              f"({len(hits)} hit(s))")
        if not ok:
            for h in hits[:20]:
                print(f"    {h}")
            failures.append(needle)

    total = len(NEEDLES)
    print(f"\n{total - len(failures)}/{total} passed")
    if failures:
        print("→ move the value behind _paths.py (PROJECT_DIR / STATE_DIR …) or, for a "
              "doc example, use the /Users/you placeholder. tracked files must be env-independent.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
