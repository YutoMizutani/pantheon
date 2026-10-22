#!/usr/bin/env python3
"""Contract test for inject_skill_addons.py (PostToolUse(Skill) で skill addon を差し込む hook).

仮の repo を tmp に作り、CLAUDE_PROJECT_DIR をそこへ向けて hook を走らせる。Run:
  python3 .claude/hooks/tests/test_inject_skill_addons.py
"""
import _hermetic  # noqa: F401 — hermetic telemetry
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "inject_skill_addons.py"


def run(root: Path, skill: str, args: str = "", cwd: str | None = None) -> str:
    payload = {"tool_name": "Skill", "tool_input": {"skill": skill, "args": args}, "cwd": cwd or str(root)}
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                         capture_output=True, text=True, env=env).stdout.strip()
    if not out:
        return ""
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def put(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    fails = 0

    def check(desc, cond):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + desc)
        fails += 0 if cond else 1

    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        (root / "projects" / "foo" / "projects" / "bar").mkdir(parents=True)
        (root / "projects" / "foo" / "baz").mkdir(parents=True)
        (root / "projects" / "other").mkdir(parents=True)

        check("addon が 1 つも無ければ何も出さない", run(root, "lens-next", "projects/foo") == "")

        put(root / ".local/skill-addons/lens-next.md", "ROOT-ADDON")
        ctx = run(root, "lens-next", "projects/other 観点.md")
        check("root の addon を差し込む", "ROOT-ADDON" in ctx)
        check("対象パスが判定できたら判定不能の注記を出さない", "判定できなかった" not in ctx)

        put(root / "projects/foo/.claude/skill-addons/lens-next.md", "FOO-ADDON")
        put(root / "projects/foo/projects/bar/.claude/skill-addons/lens-next.md", "BAR-ADDON")
        ctx = run(root, "lens-next", "projects/foo/projects/bar URL")
        check("入れ子 projects/foo/projects/bar で root→foo→bar の順",
              0 <= ctx.find("ROOT-ADDON") < ctx.find("FOO-ADDON") < ctx.find("BAR-ADDON"))

        put(root / "projects/foo/baz/.claude/skill-addons/lens-next.md", "BAZ-ADDON")
        ctx = run(root, "lens-next", f"{root}/projects/foo/baz")
        check("入れ子 projects/foo/baz（絶対パス）で foo→baz、bar は入らない",
              ctx.find("FOO-ADDON") < ctx.find("BAZ-ADDON") and "BAR-ADDON" not in ctx)

        ctx = run(root, "lens-next", "", cwd=str(root / "projects/foo"))
        check("args に無くても cwd の階層を拾う", "FOO-ADDON" in ctx and "BAR-ADDON" not in ctx)

        ctx = run(root, "lens-next", "観点だけ")
        check("対象パス不明なら root だけ＋注記", "ROOT-ADDON" in ctx and "FOO-ADDON" not in ctx and "判定できなかった" in ctx)

        put(root / ".local/skill-addons/decision-tool-selection.md", "DTS-ADDON")
        check("plugin の skill 名（plugin:skill）は : の後ろで引く",
              "DTS-ADDON" in run(root, "decision-tools:decision-tool-selection"))

        check("別の skill には効かない", "ROOT-ADDON" not in run(root, "decision-tools:decision-tool-selection"))
        check("repo 外のパスは無視", "FOO-ADDON" not in run(root, "lens-next", "/etc/projects/foo"))
        check("名前に / を含む不正な skill 名は何もしない", run(root, "../x") == "")

    print(f"{'OK' if not fails else 'NG'}: fails={fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
