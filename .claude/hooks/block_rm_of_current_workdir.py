#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — 単独の `rm` の対象が、今の作業ディレクトリ（harness が渡す cwd）か
その祖先なら実行前に止め、「先に単独で cd してから同じ rm を打つ」と返す。

背景: 複合コマンドの中の `cd X && ...` では harness の cwd が X に残ったままになり、後の単独・絶対パスの
`rm -rf X` が組み込みの安全確認（作業ディレクトリの削除）で拒否される。片付けが終わらず user に
「消すかはお任せします」と返す形が 8 セッションで起きた（2026-10-14 sid 2628adc1 ほか）。
memory feedback_tmp_cleanup_single_absolute_rm の How「単独の cd を先に打つ」は読まれないまま 2 件目になった。

判定は harness が渡す cwd だけを使う（シェルの cd を解析しない）。組み込みの確認はそのまま残るので、
安全の仕組みを緩めない。判定できなければ素通り（fail-open）。
"""
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:
    def record_fire(*_a, **_k):  # type: ignore
        return


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    cmd = (data.get("tool_input") or {}).get("command", "")
    cwd = data.get("cwd") or ""
    if not cmd or not cwd or any(op in cmd for op in ("&&", "||", ";", "|", "\n")):
        return 0
    try:
        toks = shlex.split(cmd, comments=True)
    except ValueError:
        return 0
    if not toks or os.path.basename(toks[0]) != "rm":
        return 0
    here = os.path.realpath(cwd)
    hits = []
    for t in toks[1:]:
        if t.startswith("-") or not os.path.isabs(t):
            continue
        target = os.path.realpath(t)
        if here == target or here.startswith(target.rstrip("/") + "/"):
            hits.append(t)
    if not hits:
        return 0
    root = os.environ.get("CLAUDE_PROJECT_DIR") or "<リポジトリのルート>"
    reason = (
        f"rm の対象 {', '.join(hits)} が、今の作業ディレクトリ（{cwd}）かその親です。"
        "このままでは組み込みの安全確認で拒否されます。"
        f"先に単独で `cd {root}` を打ち、同じ rm をもう一度打ってください（cd を rm に連結しない）。"
    )
    record_fire("feedback_tmp_cleanup_single_absolute_rm", "deny", hook_name="block_rm_of_current_workdir")
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
