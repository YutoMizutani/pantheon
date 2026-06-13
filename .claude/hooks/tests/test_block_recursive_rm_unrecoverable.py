#!/usr/bin/env python3
"""Tests for block_recursive_rm_unrecoverable.py.

Run: python3 .claude/hooks/tests/test_block_recursive_rm_unrecoverable.py
The dangerous command strings live here as data (not on a shell command line),
so running this file does not trip the harness destructive-rm permission gate.
"""
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "block_recursive_rm_unrecoverable.py")
RM = "rm"  # avoid the literal verb+flags sequence appearing pre-assembled

# (label, command, expect_deny)
CASES = [
    ("incident-multiline",
     "cd /Users/ym/Developer/llm\nset -e\n"
     "git mv projects/mac-remote-desktop projects/mac-access/vnc\n"
     f"{RM} -rf projects/mac-remote-desktop", True),
    ("grep-arg-not-rm",
     'grep -rn "' + RM + ' -rf projects/mac-remote-desktop" *.jsonl', False),
    ("echo-arg-not-rm",
     'echo "would ' + RM + ' -rf projects/foo"', False),
    ("tmp-rm-allowed",
     f"{RM} -rf projects/maple/tmp/wz_stats", False),
    ("pycache-allowed",
     f"{RM} -rf projects/maple/__pycache__", False),
    ("ack-marker-lifts",
     f"{RM} -rf projects/mac-remote-desktop  # RM-PROJECTS-OK: verified empty", False),
    ("mv-and-rm-still-blocked",
     f"git mv projects/old projects/new && {RM} -rf projects/old", True),
    ("projects-root-abs",
     f"{RM} -rf /Users/ym/Developer/llm/projects", True),
    ("single-file-nonrecursive",
     f"{RM} projects/maple/notes.md", False),
    ("heaven-not-projects",
     f"{RM} -rf heaven/tmp/x", False),
    ("abs-fr-order",
     f"{RM} -fr /Users/ym/Developer/llm/projects/foo", True),
    ("long-recursive-flag",
     f"{RM} --recursive projects/foo", True),
    ("glob-in-projects",
     f"{RM} -rf projects/maple/build/*", True),
    ("force-only-no-recursive",
     f"{RM} -f projects/maple/notes.md", False),
    ("binpath-rm",
     f"/bin/{RM} -rf projects/foo", True),
    ("sudo-wrapper",
     f"sudo {RM} -rf projects/foo", True),
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
     "git clean -fdx projects/maple", True),
    ("git-clean-pathspec-heaven-safe",
     "git clean -fdx heaven", False),    # pathspec outside projects/
    ("git-clean-long-force",
     "git clean --force -x -d", True),
    ("git-status-not-clean",
     "git status projects/foo", False),
]


def run(cmd):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    p = subprocess.run([sys.executable, HOOK], input=payload,
                       capture_output=True, text=True)
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
