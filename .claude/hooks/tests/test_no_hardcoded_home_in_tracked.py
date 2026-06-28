#!/usr/bin/env python3
"""Guard: no single-operator home path leaks into *tracked* (public) files.

Failure it prevents
-------------------
A frame-layer artifact (committed → pushed to the public repo) that hardcodes
this operator's home path — e.g. ``/Users/<you>/Developer/llm`` or the derived
state slug ``-Users-<you>-Developer-llm``. Observed 2026-06-21: a new slash
command was committed with ``cd /Users/you/Developer/llm`` (real home) and pushed before the
hardcode was caught, so the path landed in public git history (history rewrite
is the only removal). A second variant (2026-06-29): a FICTIONAL placeholder
``/Users/you/...`` swapped in for the real home — it passes the real-home needle
yet is still a hardcoded home no env resolves, so a second stage rejects ANY
``/Users|home/<x>/`` under the mechanism dirs (``.claude/hooks`` / ``heaven/tools``).
See ``.claude/rules/common/no-hardcoded-repo-path.md``.

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


# --- mechanism-dir scan: catch a FICTIONAL home too, not only THIS operator's ---
# A fake placeholder like /Users/you/Developer/llm passes the real-home needles
# above (it isn't this home) yet still violates the rule: a hardcoded absolute
# home that no env resolves. Observed 2026-06-29: a hook's docstring/message was
# "scrubbed" /Users/<real> -> /Users/you and shipped; the narrow needle check
# stayed green and the fictional path reached public history (history rewrite to
# remove). So for the mechanism dirs — which MUST env-derive the root via _paths —
# reject ANY /Users|home/<x>/. Allowlist the files that cite the home SHAPE as the
# forbidden example (the convention-defining doc + this test); everything else
# under these dirs must be env-independent.
_MECH_DIRS = [".claude/hooks", "heaven/tools"]
_MECH_HOME_RE = r"/(Users|home)/[A-Za-z0-9._-]+/"
_MECH_ALLOWLIST = {
    ".claude/hooks/_paths.py",
    ".claude/hooks/tests/test_no_hardcoded_home_in_tracked.py",
}


def _is_mech_home_line(line: str) -> bool:
    """Pure predicate (unit-testable): does a line hardcode a POSIX home path?"""
    return re.search(_MECH_HOME_RE, line) is not None


def _mechanism_home_hits() -> list[str]:
    """Tracked mechanism-dir lines hardcoding a home path (real OR fictional),
    excluding the allowlisted convention-defining files."""
    p = subprocess.run(
        ["git", "grep", "-nE", _MECH_HOME_RE, "--", *_MECH_DIRS],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    if p.returncode not in (0, 1):
        print(f"[FAIL] git grep errored: {p.stderr.strip()}")
        return ["<git-grep-error>"]
    hits = []
    for ln in p.stdout.splitlines():
        if not ln.strip():
            continue
        if ln.split(":", 1)[0] in _MECH_ALLOWLIST:
            continue
        hits.append(ln)
    return hits


def main() -> int:
    failures = []

    # (a) Narrow: THIS operator's real home + its state-dir slug, repo-wide.
    for needle in NEEDLES:
        hits = _tracked_hits(needle)
        ok = not hits
        print(f"[{'PASS' if ok else 'FAIL'}] no tracked file contains {needle!r} "
              f"({len(hits)} hit(s))")
        if not ok:
            for h in hits[:20]:
                print(f"    {h}")
            failures.append(needle)

    # (b) Broad: ANY hardcoded home path (real OR fictional) in mechanism dirs,
    # which must env-derive via _paths. Closes the gap where /Users/you passes (a).
    mech_hits = _mechanism_home_hits()
    ok = not mech_hits
    print(f"[{'PASS' if ok else 'FAIL'}] no mechanism file hardcodes a home path "
          f"(/Users|home/<x>/, real or fictional) ({len(mech_hits)} hit(s))")
    if not ok:
        for h in mech_hits[:20]:
            print(f"    {h}")
        failures.append("mechanism-home")

    # (c) Predicate self-test: RED->GREEN proof the regex tells a hardcoded home
    # from an env-derived reference, without needing a real bad file staged.
    POS = ['x = Path("/Users/foo/Developer/llm")', "rm -rf /Users/you/Developer/llm/tmp"]
    NEG = ["from _paths import PANTHEON_ROOT", 'cd "$CLAUDE_PROJECT_DIR"', "see /tmp/scratch"]
    pred_ok = all(_is_mech_home_line(s) for s in POS) and not any(_is_mech_home_line(s) for s in NEG)
    print(f"[{'PASS' if pred_ok else 'FAIL'}] home-path predicate flags fictional home, "
          f"not env-derived refs")
    if not pred_ok:
        failures.append("predicate")

    total = len(NEEDLES) + 2
    print(f"\n{total - len(failures)}/{total} passed")
    if failures:
        print("→ env 由来へ寄せる: 機能パスは _paths.py (PROJECT_DIR / PANTHEON_ROOT / STATE_DIR …)、"
              "例示は $CLAUDE_PROJECT_DIR か角括弧 <リポジトリ絶対パス>。"
              "架空 home (/Users/you/...) も直書き禁止 — tracked は env 非依存。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
