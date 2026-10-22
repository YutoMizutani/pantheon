#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — DENY a recursive `rm` of a `tmp/` subtree
that is still referenced by non-tmp files (reports / scripts / manifests) of the
owning project, unless the command carries the marker `# TMP-REFS-OK: <reason>`.

Root cause (2026-10-08 sid 626f9ddf): tmp cleanup deleted
`projects/<name>/apps/<app>/tmp/<eval>` (4.3GB, gitignored, no
backup). ~15 cycle reports / implementation-notes / eval_attribution.py point at
`tmp/round3_eval/<phase>/score.json` etc. The pre-delete evidence check used
`find -size -1M`, which on BSD/macOS rounds up to whole MiB and therefore only
matches 0-byte files -> "0 small evidence files" -> rm. allow_tmp_rm.py treats
every tmp/ path as regenerable and auto-approves, so nothing stopped it.

Contract:
  - Fires only when a statement's verb is `rm` with a recursive flag and an
    absolute operand under PANTHEON_ROOT containing a `tmp` path component.
  - Owner dir = the directory that contains that `tmp` component. Searches the
    owner dir (excluding any tmp/ subtree) for the literal `tmp/<rest>` string
    with `rg --no-ignore-vcs -l -F` (gitignored local layer must be visible).
  - Hits -> deny, listing up to 8 referencing files, and tell the agent to move
    the referenced small files out of tmp (e.g. outputs/) first, or add the
    marker if the references are known-stale scratch descriptions.
  - Fail-open on any internal error / rg missing / timeout (cleanup must stay
    possible; this hook only adds a check, it never widens permissions).
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:
    def record_fire(*_a, **_k):  # type: ignore
        return
try:
    from _paths import PANTHEON_ROOT  # noqa: E402
except Exception:
    PANTHEON_ROOT = Path(os.environ.get("PANTHEON_ROOT", str(Path.home() / "Developer/llm"))).resolve()

MARKER = "# TMP-REFS-OK:"
TIMEOUT = "__TIMEOUT__"


def _statements(cmd):
    return [p.strip() for p in re.split(r"(?:\n|;|&&|\|\||\|)", cmd) if p.strip()]


def _rm_operands(stmt):
    try:
        toks = shlex.split(stmt, comments=True)
    except ValueError:
        return []
    if not toks or os.path.basename(toks[0]) != "rm":
        return []
    flags = [t for t in toks[1:] if t.startswith("-")]
    recursive = any(t == "--recursive" or (not t.startswith("--") and ("r" in t or "R" in t)) for t in flags)
    if not recursive:
        return []
    return [t for t in toks[1:] if not t.startswith("-")]


def _refs_for(operand):
    p = Path(operand)
    if not p.is_absolute():
        return None, []
    try:
        p.relative_to(PANTHEON_ROOT)
    except ValueError:
        return None, []
    parts = p.parts
    if "tmp" not in parts:
        return None, []
    i = parts.index("tmp")
    rest = parts[i + 1:]
    if not rest:
        return None, []  # whole tmp/ dir: too broad to key on; leave to other gates
    owner = Path(*parts[:i])
    needle = "tmp/" + "/".join(rest)
    try:
        out = subprocess.run(
            ["rg", "--no-ignore-vcs", "-l", "-F", needle, "-g", "!**/tmp/**", "-g", "!**/node_modules/**",
             "-g", "!**/.git/**", "-g", "!**/.venv/**", "-g", "!**/venv/**", str(owner)],
            capture_output=True, text=True, timeout=25,
        )
    except subprocess.TimeoutExpired:
        # repo 直下 tmp は owner = repo 全体で 8 秒を超え、黙って素通りしていた（2026-10-19 実測 15 秒）。
        # 時間切れは「参照なし」ではないので、通しはするが確認未実施を文脈に出す。
        return needle, [TIMEOUT]
    except Exception:
        return needle, []
    files = [l for l in out.stdout.splitlines() if l.strip()]
    return needle, files


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    cmd = (data.get("tool_input") or {}).get("command", "")
    if "rm" not in cmd or MARKER in cmd:
        return 0
    hits = []
    for stmt in _statements(cmd):
        for op in _rm_operands(stmt):
            needle, files = _refs_for(op)
            if files:
                hits.append((needle, files))
    timeouts = [n for n, fs in hits if fs == [TIMEOUT]]
    hits = [(n, fs) for n, fs in hits if fs != [TIMEOUT]]
    if timeouts and not hits:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "tmp の参照確認が時間切れで未実施: " + ", ".join(timeouts)
                + " — 消す前に自分で `rg --no-ignore-vcs -l -F '<tmp/名前>' -g '!**/tmp/**' <所有ディレクトリ>` を打ち、"
                "参照 0 件を確かめてから消すこと（gitignored の tmp は消すと復旧できない）。",
        }}, ensure_ascii=False) + "\n")
        return 0
    if not hits:
        return 0
    lines = []
    for needle, files in hits:
        lines.append(f"- `{needle}` は tmp 外の {len(files)} ファイルから参照されています:")
        for f in files[:8]:
            lines.append(f"    {f}")
    reason = (
        "tmp の削除対象がまだレポート/スクリプトから参照されています（gitignored の tmp は消すと復旧できません）。\n"
        + "\n".join(lines)
        + "\n参照されている小さい証跡（score.json 等）を tmp 外（outputs/ 等）へ移してから消すか、"
        "参照が単なる scratch の説明だと確認できたら `# TMP-REFS-OK: <理由>` を付けて再実行してください。"
        "小さいファイルの抽出に `find -size -1M` を使わないこと（BSD find は MiB 単位に切り上げるため 0 バイトしか当たらない。`-size -1024k` を使う）。"
    )
    try:
        record_fire("feedback_check_references_before_tmp_rm", "deny",
                    hook_name="block_tmp_rm_with_live_references", count=len(hits))
    except Exception:
        pass
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
