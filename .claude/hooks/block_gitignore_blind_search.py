#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — warn when a grep-family command will be
SILENTLY blind to this repo's gitignored local layer, or uses a ripgrep-only
ignore flag that fails on the ugrep wrapper.

Background (see feedback_grep_residual_refs_blind_to_gitignored_config.md):
  Bash `grep` here is a Claude Code shell function wrapping ugrep with
  `--ignore-files` => it RESPECTS .gitignore and has NO off-switch. This repo is
  two-layer: the entire local layer (projects/, *.local.*, heaven/memory/ entities,
  settings.local.json, local hooks) is gitignored. A recursive `grep -r` over the
  tree therefore returns 0 hits for the BULK of the repo and looks "clean".
  Worse, `grep --no-ignore` (a ripgrep-only flag) is INVALID on ugrep => exit 2,
  empty stdout; under `2>/dev/null` the error vanishes and "0 hits = clean" is
  believed. Observed 2026-06-15 / 06-16 (3rd recurrence): residual-ref audits and
  a "the instruction does not exist" claim were all false-negatives from this.

  The only reliable search here is ripgrep: `rg --no-ignore-vcs <pat>` (sees the
  local layer, keeps .ignore churn-suppression). Run a POSITIVE CONTROL (a term you
  KNOW exists) before trusting any 0.

Contract (NON-BLOCKING, fail-quiet):
  - NEVER denies. Emits a stderr reminder (exit 0) only; instruments via record_fire.
  - Fires ONLY on a grep/egrep/fgrep verb (NOT rg, NOT `git grep`) that either
      (a) carries a ripgrep-only ignore flag (--no-ignore[-vcs/-parent/-dot/-global],
          --unrestricted, -uu) => silent exit 2 on ugrep; or
      (b) does a recursive tree walk (-r / -R / --recursive) => gitignore-blind.
  - A single-file `grep pat file` and a stdin filter `... | grep pat` do NOT walk
    the tree and are NOT flagged. --no-ignore-case / --no-ignore-files are VALID on
    ugrep and are NOT flagged.
  - Skips when stdin is not a Bash tool_use, or no grep verb present.
"""
import json
import re
import sys

_SEP = re.compile(r"(?:\|\||&&|;|\||\n|\(|\{)")
_GREP_VERBS = {"grep", "egrep", "fgrep"}
_BROKEN_FLAG = re.compile(
    r"(?<![\w-])--no-ignore(?:-(?:vcs|parent|dot|global))?(?![-\w])"
    r"|(?<![\w-])--unrestricted(?![\w])"
    r"|(?<![\w-])-u{2,}(?![\w])"
)
_RECURSIVE = re.compile(
    r"(?<![\w-])-[A-Za-z]*[rR][A-Za-z]*(?![\w])"
    r"|(?<![\w-])--recursive(?![\w])"
)

# --- telemetry (best-effort; never breaks the hook) ---
import os as _os

sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry import must never break a hook
    def record_fire(*_a, **_k):  # type: ignore
        return None


def _blind_hits(cmd: str):
    """Yield (verb, kind) for grep invocations that are gitignore-blind here."""
    for seg in _SEP.split(cmd):
        seg = seg.strip()
        if not seg:
            continue
        words = seg.split()
        i = 0
        while i < len(words):
            w = words[i]
            # skip leading `VAR=val` env assignments to reach the verb
            if "=" in w and not w.startswith("-") and "/" not in w.split("=", 1)[0]:
                i += 1
                continue
            break
        if i >= len(words):
            continue
        base = words[i].rsplit("/", 1)[-1]
        if base not in _GREP_VERBS:
            continue
        # only inspect flag tokens (start with '-'); patterns/paths can't be flags
        flags = " ".join(t for t in words[i + 1:] if t.startswith("-"))
        if _BROKEN_FLAG.search(flags):
            yield (base, "broken")
        elif _RECURSIVE.search(flags):
            yield (base, "recursive")


_MSG = {
    "broken": (
        "[grep-blind] `grep --no-ignore` (and -uu / --unrestricted) is a RIPGREP-only "
        "flag; this repo's `grep` is a ugrep wrapper => it exits 2 with EMPTY stdout, "
        "and `2>/dev/null` hides that as a false '0 hits = clean'. Use "
        "`rg --no-ignore-vcs <pat>` instead, and run a positive control (a term you "
        "KNOW exists) before trusting a 0. "
        "See feedback_grep_residual_refs_blind_to_gitignored_config.md."
    ),
    "recursive": (
        "[grep-blind] Recursive `grep -r` here wraps ugrep with --ignore-files => it "
        "RESPECTS .gitignore with NO off-switch. This repo's whole LOCAL layer "
        "(projects/, *.local.*, heaven/memory/ entities, settings.local.json) is "
        "gitignored, so this returns 0 for the bulk of the repo and looks 'clean' "
        "(false-negative). For repo-wide search use `rg --no-ignore-vcs <pat>`; run a "
        "positive control before trusting a 0. "
        "See feedback_grep_residual_refs_blind_to_gitignored_config.md."
    ),
}


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(cmd, str) or "grep" not in cmd:
        return
    hits = list(_blind_hits(cmd))
    if not hits:
        return
    # broken flag fails outright -> prioritise that message over recursive
    kind = "broken" if any(k == "broken" for _, k in hits) else "recursive"
    record_fire(
        "feedback_grep_residual_refs_blind_to_gitignored_config",
        "warn",
        context=f"{kind}:{hits[0][0]}",
    )
    sys.stderr.write(_MSG[kind] + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
