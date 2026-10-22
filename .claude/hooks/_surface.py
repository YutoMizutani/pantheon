"""PreToolUse の注意型 hook の注意を Claude に届く形で出す共通部品。

背景 (2026-10-20): 「止めずに stderr へ注意を書いて exit 0」で作った PreToolUse hook の注意は、
Claude Code では Claude に渡らない。block_timeout_binary_on_macos は 2026-10-19 12:46:42Z に発火して
いたが、同じ呼び出しの結果は `zsh: command not found: timeout` だけだった（発火 223 回・届いた回数 0 の
可能性が高い）。同じ作りの hook が 6 本あった。届くのは stdout の JSON
`hookSpecificOutput.additionalContext`（inject_project_claudemd.py で到達を確認済み）か、exit 2 の拒否。

使い方: hook の import の直後で `surface_stderr()` を呼ぶ。以後 stderr に書かれた注意は、
従来どおり stderr にも出しつつ、終了時に additionalContext として stdout に 1 回だけ出す。
hook 本体の判定・文面は変えない。
"""
from __future__ import annotations

import atexit
import io
import json
import sys


class _Tee(io.TextIOBase):
    def __init__(self, orig):
        self._orig = orig
        self.buf: list[str] = []

    def write(self, s):  # type: ignore[override]
        self.buf.append(s)
        try:
            return self._orig.write(s)
        except Exception:
            return len(s)

    def flush(self):  # type: ignore[override]
        try:
            self._orig.flush()
        except Exception:
            pass


def surface_stderr(event: str = "PreToolUse") -> None:
    tee = _Tee(sys.stderr)
    sys.stderr = tee

    def _emit() -> None:
        msg = "".join(tee.buf).strip()
        if not msg:
            return
        try:
            sys.__stdout__.write(json.dumps(
                {"hookSpecificOutput": {"hookEventName": event, "additionalContext": msg}},
                ensure_ascii=False))
            sys.__stdout__.flush()
        except Exception:
            pass

    atexit.register(_emit)
