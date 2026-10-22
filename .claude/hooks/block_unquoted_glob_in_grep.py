#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — warn when a command passes an UNQUOTED glob
(`*`, `?`, `[...]`) to grep/rg/find in a position the program itself should
interpret, e.g. `--include=*.py`, `--exclude=*.log`, `grep -rn pat dir/*.py`,
`find . -name *.py`.

Background (see feedback_quote_glob_in_grep_under_zsh.md):
  zsh enables the `nomatch` option by default. An unquoted glob on the command
  line is expanded by zsh against the CURRENT directory BEFORE the program runs.
  If nothing matches there, zsh aborts the whole command pre-exec with
  `(eval):N: no matches found: <pattern>` — grep/find never run. This is
  INDEPENDENT of cwd: even an absolute path with the right target dir fails,
  because `--include=*.py` is expanded against the shell's cwd, not the search
  dir. Quoting (`--include='*.py'`) is the only fix. bash leaves unmatched globs
  literal, so a bash-habit command silently costs a round-trip here.

  Observed 2026-06-08 session 68724983: three consecutive grep calls died on
  `no matches found` — `dir/*.py`, then `--include=*.py` (cwd was correct via cd,
  still aborted), until `--include='*.py'` (quoted) finally ran.

Contract (NON-BLOCKING, fail-quiet):
  - NEVER denies or blocks. Emits a stderr reminder (exit 0) only.
  - Fires only when the command invokes grep/egrep/fgrep/rg/find AND contains an
    UNQUOTED glob in a risky position:
      * `--include=<glob>` / `--exclude=<glob>` / `--include-dir=` / `--exclude-dir=`
        where <glob> is not wrapped in ' or " and contains a glob metachar.
      * a bare argument token containing a glob metachar that is not quoted
        (e.g. `dir/*.py`, `*.py`, `find . -name *.py`).
  - Skips tokens already quoted (single or double) — those are safe.
  - Only inspects arguments of the simple command (split on | ; && || & newline)
    whose verb is grep/rg/find; heredoc bodies are excluded. `ls *.jsonl`,
    `case` patterns and Python subscripts in a heredoc are NOT grep args
    (2026-10-16: 29 of 78 fires were these false positives).
  - Skips when stdin is not a Bash tool_use, or grep/find/rg not present.
"""
import json
import re
import shlex
import sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _surface import surface_stderr  # noqa: E402  stderr の注意を Claude に届く形でも出す
surface_stderr()

_GLOB_META = re.compile(r"[*?]|\[[^\]]*\]")
_SEARCH_VERBS = {"grep", "egrep", "fgrep", "rg", "find"}
_INCLUDE_FLAG = re.compile(r"^--(?:include|exclude)(?:-dir)?=(.+)$")


def _has_search_verb(cmd: str) -> bool:
    # cheap pre-check before tokenizing
    return any(re.search(rf"(?<![\w/]){v}\b", cmd) for v in _SEARCH_VERBS)


# heredoc bodies (python3 - <<'EOF' ... EOF) are not shell arguments
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1[ \t]*(?=\n|$)", re.S)
# prefixes that may precede the search verb (sudo grep / xargs -0 grep / env X=1 rg)
_PREFIX = {"sudo", "xargs", "command", "time", "nohup", "exec", "env", "builtin", "nice"}
_SEP = {"|", "||", "&&", ";", ";;", "&", "(", ")", "<", ">", ">>", "<<", "|&", ">&", "<&", "&>"}


def _segments(cmd: str):
    """Split the command into simple-command token lists. shlex(posix=False)
    keeps | ; inside quotes intact. `\\(` `\\)` are find grouping, not shell
    separators, so they are masked before tokenizing."""
    cmd = _HEREDOC.sub("", cmd).replace("\\(", "__LP__").replace("\\)", "__RP__")
    buf = ""
    for line in cmd.split("\n"):
        buf = (buf + "\n" + line) if buf else line
        try:
            lex = shlex.shlex(buf, posix=False, punctuation_chars=True)
            lex.whitespace_split = True
            toks = list(lex)
        except ValueError:
            continue  # unclosed quote: join with next line and retry
        buf = ""
        seg = []
        for t in toks:
            if t in _SEP or all(c in "|&;()<>" for c in t):
                if seg:
                    yield seg
                seg = []
            else:
                seg.append(t)
        if seg:
            yield seg


def _risky_unquoted_globs(cmd: str):
    """Yield offending raw tokens, looking only at arguments of a simple command
    whose verb is grep/rg/find. A token that still carries a leading ' or "
    (shlex posix=False keeps quote chars) was quoted by the author and is safe."""
    for seg in _segments(cmd):
        i = 0
        while i < len(seg) and (
            re.match(r"^\w+=", seg[i])
            or seg[i] in _PREFIX
            or (i > 0 and seg[i - 1] in _PREFIX and seg[i].startswith("-"))
        ):
            i += 1
        if i >= len(seg) or seg[i].rsplit("/", 1)[-1] not in _SEARCH_VERBS:
            continue
        for tok in seg[i + 1:]:
            if (tok.startswith("'") and tok.endswith("'")) or (
                tok.startswith('"') and tok.endswith('"')
            ):
                continue
            m = _INCLUDE_FLAG.match(tok)
            if m:
                val = m.group(1)
                if not (val.startswith("'") or val.startswith('"')) and _GLOB_META.search(val):
                    yield tok
                continue
            if not tok.startswith("-") and _GLOB_META.search(tok):
                yield tok


# --- telemetry instrumentation (best-effort; never breaks the hook) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
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
    if not isinstance(cmd, str) or not cmd:
        return
    if not _has_search_verb(cmd):
        return

    offenders = list(_risky_unquoted_globs(cmd))
    if not offenders:
        return

    shown = ", ".join(offenders[:3])
    record_fire("feedback_quote_glob_in_grep_under_zsh", "warn", context=shown[:200])
    sys.stderr.write(
        "[zsh-glob hint] Unquoted glob passed to grep/find: "
        f"{shown}. zsh has `nomatch` on by default and will expand this against "
        "the CURRENT dir BEFORE the program runs; if nothing matches there it "
        "aborts the whole command with `no matches found` and the search never "
        "runs (independent of cwd — an absolute path still fails). Quote the "
        "glob: --include='*.py' / -name '*.py' / pass a dir + --include='<glob>' "
        "instead of dir/*.py. See feedback_quote_glob_in_grep_under_zsh.md.\n"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
