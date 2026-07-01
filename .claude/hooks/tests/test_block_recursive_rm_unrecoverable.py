#!/usr/bin/env python3
"""Tests for block_recursive_rm_unrecoverable.py.

Run: python3 .claude/hooks/tests/test_block_recursive_rm_unrecoverable.py
The dangerous command strings live here as data (not on a shell command line),
so running this file does not trip the harness destructive-rm permission gate.
"""
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "block_recursive_rm_unrecoverable.py")
RM = "rm"  # avoid the literal verb+flags sequence appearing pre-assembled
LLM = str(Path(__file__).resolve().parents[3])  # repo root (env-derived, no hardcoded user path)

# (label, command, expect_deny)
CASES = [
    ("incident-multiline",
     f"cd {LLM}\nset -e\n"
     "git mv projects/example-old projects/example-new\n"
     f"{RM} -rf projects/example-old", True),
    ("grep-arg-not-rm",
     'grep -rn "' + RM + ' -rf projects/example-old" *.jsonl', False),
    ("echo-arg-not-rm",
     'echo "would ' + RM + ' -rf projects/foo"', False),
    ("tmp-rm-allowed",
     f"{RM} -rf projects/example/tmp/data_cache", False),
    ("pycache-allowed",
     f"{RM} -rf projects/example/__pycache__", False),
    ("ack-marker-lifts",
     f"{RM} -rf projects/example-old  # RM-PROJECTS-OK: verified empty", False),
    ("mv-and-rm-still-blocked",
     f"git mv projects/old projects/new && {RM} -rf projects/old", True),
    ("projects-root-abs",
     f"{RM} -rf {LLM}/projects", True),
    ("single-file-nonrecursive",
     f"{RM} projects/example/notes.md", False),
    ("heaven-not-projects",
     f"{RM} -rf heaven/tmp/x", False),
    ("abs-fr-order",
     f"{RM} -fr {LLM}/projects/foo", True),
    ("long-recursive-flag",
     f"{RM} --recursive projects/foo", True),
    ("glob-in-projects",
     f"{RM} -rf projects/example/build/*", True),
    ("force-only-no-recursive",
     f"{RM} -f projects/example/notes.md", False),
    ("binpath-rm",
     f"/bin/{RM} -rf projects/foo", True),
    ("sudo-wrapper",
     f"sudo {RM} -rf projects/foo", True),
    # [2] cwd-aware resolution: a leading `cd` into projects/ makes a *relative*
    # recursive rm hit the unrecoverable tree (the 2026-06-14 incident vector).
    ("cd-abs-into-projects-then-rel-rm",
     f"cd {LLM}/projects/example\n"
     f"/bin/{RM} -rf app.bak.20260614", True),
    ("cd-rel-into-projects-then-rel-rm",
     f"cd projects/example && {RM} -rf app.bak.20260614", True),
    ("cd-abs-outside-then-rm-safe",     # cd outside repo → relative rm not in tree
     f"cd /tmp && {RM} -rf foo", False),
    # [4] gitignored (git-unrecoverable) heaven/ subtrees: memory 実体 +
    # creative archive 等。tracked な heaven/tools は復旧可で許可。
    ("heaven-projects-archive",
     f"{RM} -rf heaven/projects/sample-archive/data", True),
    ("heaven-memory",
     f"{RM} -rf heaven/memory", True),
    ("heaven-root-contains-memory",
     f"{RM} -rf heaven", True),
    ("heaven-tools-tracked-safe",
     f"{RM} -rf heaven/tools", False),
    # git clean vectors (same unrecoverable class)
    ("git-clean-fdx-whole-repo",
     "git clean -fdx", True),
    ("git-clean-fdX-whole-repo",
     "git clean -fdX", True),
    ("git-clean-fd-no-x-safe",
     "git clean -fd", False),            # no -x: ignored projects/* untouched
    ("git-clean-dry-run-safe",
     "git clean -fdxn", False),          # dry-run never blocks
    ("git-clean-pathspec-projects",
     "git clean -fdx projects/example", True),
    ("git-clean-pathspec-heaven-safe",
     "git clean -fdx heaven", False),    # pathspec outside projects/
    ("git-clean-long-force",
     "git clean --force -x -d", True),
    ("git-status-not-clean",
     "git status projects/foo", False),
]


def run(cmd):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    # Pin the hook's repo root to LLM so cases stay deterministic regardless of the
    # ambient CLAUDE_PROJECT_DIR / cwd (the hook derives PANTHEON_ROOT from this env var).
    env = {**os.environ, "CLAUDE_PROJECT_DIR": LLM}
    p = subprocess.run([sys.executable, HOOK], input=payload,
                       capture_output=True, text=True, env=env)
    return '"deny"' in p.stdout


def main():
    fails = 0
    for label, cmd, expect_deny in CASES:
        got = run(cmd)
        ok = got == expect_deny
        if not ok:
            fails += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: "
              f"expect_deny={expect_deny} got_deny={got}")
    print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
