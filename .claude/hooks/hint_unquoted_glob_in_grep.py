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
  - Rewriting is opt-in via `--transform`, and only flag-value globs are rewritten
    (`--include=*.py` → `--include='*.py'`, `-name *.py` → `-name '*.py'`); positional
    globs like `dir/*.py` are never touched. The rewrite is returned as a bare
    `updatedInput` (no permissionDecision); on any doubt the hook falls back to the hint.
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


def _verb_args(seg):
    """Arguments after the search verb of a simple command, or None when the
    verb (after env assignments and prefixes like sudo/xargs) is not a search verb."""
    i = 0
    while i < len(seg) and (
        re.match(r"^\w+=", seg[i])
        or seg[i] in _PREFIX
        or (i > 0 and seg[i - 1] in _PREFIX and seg[i].startswith("-"))
    ):
        i += 1
    if i >= len(seg) or seg[i].rsplit("/", 1)[-1] not in _SEARCH_VERBS:
        return None
    return seg[i + 1:]


def _is_quoted(tok: str) -> bool:
    return (tok.startswith("'") and tok.endswith("'")) or (
        tok.startswith('"') and tok.endswith('"'))


def _risky_unquoted_globs(cmd: str):
    """Yield offending raw tokens, looking only at arguments of a simple command
    whose verb is grep/rg/find. A token that still carries a leading ' or "
    (shlex posix=False keeps quote chars) was quoted by the author and is safe."""
    for seg in _segments(cmd):
        args = _verb_args(seg)
        if args is None:
            continue
        for tok in args:
            if _is_quoted(tok):
                continue
            m = _INCLUDE_FLAG.match(tok)
            if m:
                val = m.group(1)
                if not (val.startswith("'") or val.startswith('"')) and _GLOB_META.search(val):
                    yield tok
                continue
            if not tok.startswith("-") and _GLOB_META.search(tok):
                yield tok


# --- transform (--transform opt-in): quote flag-value globs in place ---
# Measured: in 30 days of
# transcripts flag-value globs died 177 times vs 6 successes, positional globs ran
# 1692 times vs 100 deaths — positional ones are intended shell expansion, left alone.
_VALUE_FLAGS = {"-name", "-iname", "-path", "-ipath", "-wholename", "-g", "--glob", "--iglob"}


def _flag_glob_hits(cmd: str):
    """Yield (flag, token) per unquoted flag-value glob of a search verb. flag is
    None for `--include=<glob>` tokens, else the preceding `_VALUE_FLAGS` token."""
    for seg in _segments(cmd):
        args = _verb_args(seg)
        if args is None:
            continue
        for k, tok in enumerate(args):
            if _is_quoted(tok):
                continue
            m = _INCLUDE_FLAG.match(tok)
            if m:
                val = m.group(1)
                if not (val.startswith("'") or val.startswith('"')) and _GLOB_META.search(val):
                    yield None, tok
                continue
            if (k > 0 and args[k - 1] in _VALUE_FLAGS and not tok.startswith("-")
                    and _GLOB_META.search(tok)):
                yield args[k - 1], tok


def _quote_value(val: str):
    """Quote a flag value so zsh stops globbing it but expands nothing less.
    `$`/backtick keep expanding inside "..."; a value whose meaning quoting would
    change (backslash escapes, embedded ", leading ~, {a,b} braces) gets None."""
    if any(c in val for c in '\\"{') or val.startswith("~"):
        return None
    if "$" in val or "`" in val:
        return None if "'" in val else f'"{val}"'
    return f'"{val}"' if "'" in val else f"'{val}'"


def _quoted_token(flag, tok: str):
    if flag is None:
        name, val = tok.split("=", 1)
        q = _quote_value(val)
        return None if q is None else f"{name}={q}"
    return _quote_value(tok)


def _quotable_flag_globs(cmd: str):
    """[(original token, quoted token)] for the flag-value globs that can be quoted.
    Positional globs are never returned."""
    out = []
    for flag, tok in _flag_glob_hits(cmd):
        new = _quoted_token(flag, tok)
        if new is not None:
            out.append((tok, new))
    return out


def transform_command(cmd: str):
    """Return (new_cmd, [(before, after), ...]). Positions are searched on a copy
    whose heredoc bodies, quoted spans and comments are blanked out, so text inside
    them is never touched. On any doubt — a token found a different number of times
    than the parser saw it, an unquotable value, a flag-value glob left over, or an
    unexpected length — return (cmd, []) and let the hint run."""
    try:
        from block_unquoted_equals_word import _mask_heredocs, _mask_quotes_and_comments
    except Exception:
        return cmd, []
    hits = list(_flag_glob_hits(cmd))
    if not hits:
        return cmd, []
    masked = _mask_quotes_and_comments(_mask_heredocs(cmd))
    spans = []  # (start, end, replacement, (before, after))
    for key in dict.fromkeys(hits):
        flag, tok = key
        new = _quoted_token(flag, tok)
        if new is None:
            return cmd, []
        lead = "" if flag is None else re.escape(flag) + r"\s+"
        pat = re.compile(rf"(?<![^\s;|&(]){lead}({re.escape(tok)})(?=[\s;|&)]|$)")
        found = list(pat.finditer(masked))
        if len(found) != hits.count(key):
            return cmd, []
        label = (tok, new) if flag is None else (f"{flag} {tok}", f"{flag} {new}")
        spans += [(m.start(1), m.end(1), new, label) for m in found]
    new_cmd = cmd
    for start, end, rep, _ in sorted(spans, reverse=True):
        new_cmd = new_cmd[:start] + rep + new_cmd[end:]
    if list(_flag_glob_hits(new_cmd)) or len(new_cmd) != len(cmd) + 2 * len(spans):
        return cmd, []
    return new_cmd, [s[3] for s in sorted(spans)]


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

    if "--transform" in sys.argv[1:]:
        new_cmd, changes = transform_command(cmd)
        if changes:
            done = ", ".join(f"{a} → {b}" for a, b in changes[:3])
            note = (f"[zsh-glob transform] 引用符を補った: {done}"
                    "（flag 値の glob は zsh の nomatch で命令ごと止まるため。検索の意味は同じ）")
            rest = list(_risky_unquoted_globs(new_cmd))
            if rest:
                note += (f" 位置引数の裸 glob は書き換えていない: {', '.join(rest[:3])}。"
                         "cwd に当たる物が無いと zsh が `no matches found` で命令を止める。"
                         "引用するか、dir と --include='<glob>' に分ける。")
            record_fire("feedback_quote_glob_in_grep_under_zsh", "transform",
                        context=", ".join(f"{a}→{b}" for a, b in changes[:3])[:200],
                        session_id=data.get("session_id"))
            sys.stdout.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": {**data["tool_input"], "command": new_cmd},
                "additionalContext": note,
            }}, ensure_ascii=False))
            sys.stdout.flush()
            sys.exit(0)

    shown = ", ".join(offenders[:3])
    record_fire("feedback_quote_glob_in_grep_under_zsh", "warn", context=shown[:200],
                session_id=data.get("session_id"))
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
