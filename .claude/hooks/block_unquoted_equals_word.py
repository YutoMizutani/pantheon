#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — warn when a command contains an UNQUOTED
word that begins with `=`, e.g. the habitual section separator `echo ===` or
`echo ===LABEL===`, or `[ "$a" == "$b" ]` outside `[[ ]]`.

Background (see feedback_no_unquoted_equals_word_under_zsh.md):
  zsh enables the EQUALS option by default. Any unquoted command-line word
  beginning with `=` undergoes equals-expansion (`=cmd` -> full path of `cmd`).
  `echo ===` makes zsh look up a command literally named `==`; the lookup fails
  and zsh aborts the WHOLE command line pre-exec with
  `(eval):1: == not found` — sibling segments after (and sometimes around) the
  separator never run. In a long compound verification chain this throws away
  the partial output and forces a full re-run round-trip. This is the same
  abort-the-whole-line failure family as nomatch globs
  (block_unquoted_glob_in_grep.py).

  Observed 2026-06-11 session df1e3bef: two compound verification commands
  (entries 64, 260) used `echo ===` separators; both aborted with
  `(eval):1: == not found`, one required a verbatim re-run with `echo "---"`.
  Cross-session: 8 transcripts in this project dir contain `(eval):1: =...
  not found`. The META reflection session mining this fault hit it live too
  (`echo ===RECUR===`).

Contract (BLOCKING since 2026-10-19, fail-quiet):
  - Blocks (exit 2, stderr reaches the model). The old warn-only form (stderr + exit 0) never
    reached the model: 387 warns were logged while `(eval):1: = not found` still appeared in
    69 transcripts in 2026-10 alone (backfill reflection sid b490bc48). A matching command is
    aborted by zsh anyway, so blocking it costs no extra round-trip. Rewriting the command via
    updatedInput was rejected: it requires returning permissionDecision=allow, which would
    bypass the auto-mode classifier for every such command.
  - Words naming a real command (`=ls` -> /bin/ls) are legitimate equals-expansion and pass.
  - Fires only when the Bash command contains a word starting with `=` in an
    expansion-eligible position: start of a simple command word, i.e. preceded
    by start-of-string / whitespace / one of `;|&(` — after masking:
      * single- and double-quoted spans (quoted words are safe),
      * heredoc bodies (`<<EOF ... EOF` content is not command words),
      * `[[ ... ]]` spans (`==` there is a reserved operator, no expansion),
      * `(( ... ))` arithmetic spans,
      * `#` comments to end of line.
  - Skips `=(...)` process substitution (legitimate zsh).
  - Skips when stdin is not a Bash tool_use or no `=` present at all.

Suggested fixes surfaced in the hint:
  - separator: use `echo "==="` (quoted) or a non-`=` separator like `echo ---`
  - test:      use `[[ $a == $b ]]` or `[ "$a" = "$b" ]` (single `=`)
"""
import json
import os.path
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover - counter is best-effort
    def record_fire(*_a, **_k):
        return None

_HEREDOC_OPEN = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
_EQ_WORD = re.compile(r"(?:^|[\s;|&(])(=[^\s;|&)]*)")


def _mask_span(s: str, start: int, end: int) -> str:
    return s[:start] + (" " * (end - start)) + s[end:]


def _mask_quotes_and_comments(cmd: str) -> str:
    out = []
    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n:
            out.append("  ")
            i += 2
            continue
        if c in ("'", '"'):
            j = i + 1
            while j < n:
                if cmd[j] == "\\" and c == '"' and j + 1 < n:
                    j += 2
                    continue
                if cmd[j] == c:
                    break
                j += 1
            out.append(" " * (min(j, n - 1) - i + 1))
            i = min(j, n - 1) + 1
            continue
        if c == "#":
            j = cmd.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _mask_heredocs(cmd: str) -> str:
    # Mask heredoc bodies: from the newline after the opener to the line that
    # equals the delimiter (optionally tab-indented for <<-).
    for m in list(_HEREDOC_OPEN.finditer(cmd)):
        delim = m.group(2)
        nl = cmd.find("\n", m.end())
        if nl == -1:
            continue
        close = re.search(rf"^\t*{re.escape(delim)}\s*$", cmd[nl + 1:], re.M)
        end = (nl + 1 + close.start()) if close else len(cmd)
        cmd = _mask_span(cmd, nl + 1, end)
    return cmd


def _mask_bracket_spans(cmd: str) -> str:
    # [[ ... ]] and (( ... )) — `==` inside is an operator, not a word.
    for opener, closer in (("[[", "]]"), ("((", "))")):
        start = 0
        while True:
            i = cmd.find(opener, start)
            if i == -1:
                break
            j = cmd.find(closer, i + 2)
            if j == -1:
                break
            cmd = _mask_span(cmd, i, j + 2)
            start = j + 2
    return cmd


def _offending_words(cmd: str):
    # Heredocs first: their opener may carry a quoted delimiter (<<'EOF') that
    # quote-masking would erase, hiding the body from _mask_heredocs.
    masked = _mask_bracket_spans(_mask_quotes_and_comments(_mask_heredocs(cmd)))
    for m in _EQ_WORD.finditer(masked):
        word = m.group(1)
        if word.startswith("=("):  # process substitution
            continue
        if word == "=":  # `=` 1 文字は展開されない（zsh -c 'echo =' は = を出す。2026-10-19 実測）
            continue
        if len(word) > 1 and shutil.which(word[1:]):  # `=ls` は実在コマンドのパスに展開される正当な用法
            continue
        yield word


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(cmd, str) or "=" not in cmd:
        return

    offenders = list(_offending_words(cmd))
    if not offenders:
        return
    sys.stderr.write(
        "[zsh-equals] 引用符なしで `=` から始まる語があります"
        f"（{', '.join(offenders[:3])}）。zsh はこれをコマンド名として展開しようとして失敗し、"
        "`(eval):1: == not found` で行全体が 1 つも実行されません。"
        "区切りは `echo \"===\"`（引用符付き）か `echo ---` に、比較は `[[ a == b ]]` か "
        "`[ \"$a\" = \"$b\" ]` に書き換えて、同じコマンドを打ち直してください。\n"
    )
    record_fire(
        "feedback_no_unquoted_equals_word_under_zsh", "block",
        context=offenders[0],
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
