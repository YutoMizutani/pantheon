#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — 作業ツリー全体を退避する `git stash`（push / save / 引数なし）を実行前に止める。

このリポジトリは同じ作業ツリーを複数の Claude セッションと user が同時に使う。`git stash` は自分の変更だけでなく
他のセッション・user の未コミットの変更まで作業ツリーから抜く。pop までの間に相手が同じファイルを書けば
pop が衝突し、相手は消えた変更の上に書き直すことになる（実例: 検査コマンドの末尾に
意図しない `git stash -q 2>/dev/null; true` を付けて実行し、別作業の 4 ファイルの変更を退避。すぐ pop で戻した）。

通す: `git stash list / show / pop / apply / branch`（作業ツリーの変更を抜かない形）。
止める: `git stash`・`git stash push|save|-q|-u|-k|--keep-index …`。
代わり: 基準の状態で試すなら `git worktree add <scratchpad>/base HEAD`、自分の変更だけ外すなら
`git diff -- <自分のファイル> > <scratchpad>/mine.patch` → `git checkout -- <自分のファイル>`。
例外の印: `# STASH-OK: <他に変更が無いことを git status で確かめた根拠>`。
フレーム層（どの環境でも成立する汎用機構）。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover
    def record_fire(*_a, **_k):
        return None

_MARKER = "STASH-OK:"
_SAFE = {"list", "show", "pop", "apply", "branch"}
_SEG = re.compile(
    r"(?:^|[;&|\n(]\s*|\$\(\s*|`\s*)git(?:\s+-C\s+\S+|\s+-c\s+\S+)*\s+stash\b([^;&|\n)`]*)"
)


def hits(cmd: str):
    out = []
    for m in _SEG.finditer(cmd):
        args = m.group(1).split()
        sub = next((a for a in args if not a.startswith("-") and not a.startswith(">") and not a[:1].isdigit()), "")
        if sub in _SAFE:
            continue
        out.append(("git stash " + m.group(1).strip()).strip())
    return out


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(cmd, str) or "stash" not in cmd or _MARKER in cmd:
        return
    found = hits(cmd)
    if not found:
        return
    sys.stderr.write(
        "[git-stash] 作業ツリー全体の変更を退避する形です: " + " / ".join(found) +
        "\nこの作業ツリーは別のセッションと user も使っていて、相手の未コミットの変更まで抜けます。"
        "\n基準の状態で試すなら `git worktree add <scratchpad>/base HEAD`、自分の変更だけ外すなら"
        " `git diff -- <自分のファイル> > <scratchpad>/mine.patch` を使う。"
        "\n`git status` で他人の変更が無いことを確かめた上でだけ `# STASH-OK: <根拠>` を付ける。\n"
    )
    record_fire("block_git_stash_shared_tree", "block", context=cmd[:120])
    sys.exit(2)


if __name__ == "__main__":
    main()
