#!/usr/bin/env python3
"""PostToolUse hook (matcher: Bash) — a gitignore-blind search that returned
NOTHING is the only harmful case; flag it at the moment of interpretation.

Companion to ``block_gitignore_blind_search.py`` (PreToolUse), whose warning text
was never delivered: it wrote to stderr and exited 0, and this harness does not
show stdout/stderr of an exit-0 hook to the model (measured 2026-10-12: all 615
``[grep-blind]`` attachments carry ``"content": ""``). The reminder that DID
reach context was only the always-loaded MEMORY.md index line. 2026-10-12
(sid e86632bf): a blind ``grep -rl`` returned 0 and the 0 was believed without
the prescribed positive control -- 6th recurrence.

This hook therefore speaks on the delivered path (stdout
``hookSpecificOutput.additionalContext``), not stderr.

Design (measure-first): a blind search that returns HITS costs nothing, so this
hook stays silent there. It speaks only when the flagged command produced NO
output — the silent, costly case (false "clean" / false "not present") that
``feedback_grep_residual_refs_blind_to_gitignored_config.md`` documents 6 times.

Contract (NON-BLOCKING, fail-quiet):
  - Never blocks (the tool already ran). Emits hookSpecificOutput.additionalContext
    on stdout (+ a stderr copy) and exits 0.
  - Fires only when BOTH hold: (a) the command is a grep-family invocation that
    ``block_gitignore_blind_search._blind_hits`` flags, (b) the tool response has
    empty stdout (or the "no output" marker).
  - Any unexpected payload shape => silent exit 0.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from block_gitignore_blind_search import _blind_hits  # noqa: E402
except Exception:  # detector must stay single-source; no local copy
    def _blind_hits(_cmd):  # type: ignore
        return iter(())

try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return None

_NO_OUTPUT_MARKERS = (
    "(Bash completed with no output)",
    "(no content)",
)

_MSG = (
    "[blind-search-zero] 直前の grep は **0 件** を返した。"
    "この 0 は『無い』の証拠にならない — この環境の再帰 grep は .gitignore を尊重する ugrep ラッパで、"
    "この repo のローカル層 (projects/ ・*.local.* ・heaven/memory 実体 ・settings.local.json) が "
    "丸ごと見えていなかった**可能性がある** "
    "(盲目かどうかは -r の有無では決まらず、ignore ファイルと走査根の相対位置で決まる。"
    "この hook はその区別ができないので、原因は候補として提示する)。"
    "『見つからない / 手元に資料なし / 残存ゼロ』と解釈・断定する前に "
    "`rg --no-ignore-vcs <pat>` で引き直し、確実に在る語で positive control を 1 本取る。"
    "ただし repo 直下の .ignore が `build/`・`logs/`・`tmp/`・`dist/` の下を rg からも隠すので、"
    "そこが中身のフォルダ（例: 成果物の build/）なら `rg -u <pat>` か対象フォルダを直接パスで渡す。"
    "自分の過去回答・過去セッションを探すなら拡張子集合に `*.jsonl` を入れる "
    "(transcript / 送信記録の outbox が実体で、--include=\"*.json\" は .jsonl に当たらない)。"
    "根拠: feedback_grep_residual_refs_blind_to_gitignored_config.md"
)


def _response_is_empty(resp) -> bool:
    if resp is None:
        return False
    if isinstance(resp, dict):
        if resp.get("interrupted"):
            return False
        for key in ("stdout", "output", "content"):
            if key in resp:
                val = resp.get(key)
                text = "" if val is None else str(val)
                return not text.strip() or any(m in text for m in _NO_OUTPUT_MARKERS)
        return False
    text = str(resp)
    return not text.strip() or any(m in text for m in _NO_OUTPUT_MARKERS)


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
    if not _response_is_empty(data.get("tool_response")):
        return
    record_fire(
        "feedback_grep_residual_refs_blind_to_gitignored_config",
        "warn",
        context=f"zero-result:{hits[0][1]}",
    )
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": _MSG,
                }
            },
            ensure_ascii=False,
        )
    )
    sys.stderr.write(_MSG + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
