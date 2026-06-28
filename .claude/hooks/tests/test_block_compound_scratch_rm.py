#!/usr/bin/env python3
"""Contract test for block_compound_scratch_rm.py.

The hook DENIES a COMPOUND command whose recursive `rm` targets a scratch tmp path
(steering it to the standalone-absolute form allow_tmp_rm auto-approves). It must:
  - fire on the real failing idiom (bundled rm -rf of a tmp path, incl. via $VAR);
  - NEVER fire on a standalone rm (allow_tmp_rm's job), a non-tmp compound rm
    (block_recursive_rm_unrecoverable's job / normal flow), a non-recursive rm, or
    a command that merely mentions the text;
  - respect the `# RM-COMPOUND-OK:` escape hatch.
Includes the guard-conflict check: a command echoing a top-level safety guard's
legitimate stop-output must NOT fire (verb-based; the hook only reads Bash commands).
Run: python3 .claude/hooks/tests/test_block_compound_scratch_rm.py
"""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "block_compound_scratch_rm.py"
LLM = str(Path(__file__).resolve().parents[3])  # repo root (env-derived, no hardcoded home)


def emits_deny(command: str) -> bool:
    out = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True, text=True,
    ).stdout.strip()
    if not out:
        return False
    try:
        obj = json.loads(out)
    except json.JSONDecodeError:
        return False
    return obj.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# (description, command, expect_deny)
CASES = [
    # ---- DENY: the recurrent failing idiom ----
    ("the actual stall: $VAR tmp rm bundled with mkdir",
     f'cd {LLM}\nROOT="projects/llm/tmp/h2h-duration"\nrm -rf "$ROOT"; mkdir -p "$ROOT"/{{A1,A2}}', True),
    ("literal absolute tmp rm bundled",
     f"cd {LLM}; rm -rf {LLM}/tmp/foo; mkdir -p {LLM}/tmp/foo", True),
    ("relative tmp rm in compound",
     f"cd {LLM} && rm -rf tmp/dump && mkdir tmp/dump", True),
    ("system /tmp via $VAR bundled",
     'RT=/tmp/v2_runtime\nrm -rf "$RT"; mkdir -p "$RT"', True),
    ("heredoc reset idiom",
     f'rm -rf {LLM}/projects/x/tmp/run && cat > {LLM}/projects/x/tmp/run/spec <<EOF\\nhi\\nEOF', True),

    # ---- NO FIRE: not our concern (other hooks / normal flow own these) ----
    ("standalone absolute tmp rm (allow_tmp_rm owns it)", f"rm -rf {LLM}/tmp/foo", False),
    ("standalone relative tmp rm", "rm -rf tmp/foo", False),
    ("non-tmp compound rm (projects/ → block_recursive_rm owns)",
     f"cd {LLM} && rm -rf {LLM}/projects/example/build && echo done", False),
    ("non-tmp $VAR compound rm",
     f'D={LLM}/projects/example/build\nrm -rf "$D" && mkdir "$D"', False),
    ("non-recursive single-file rm of tmp in compound",
     f"cd {LLM}; rm {LLM}/tmp/foo.log", False),
    ("grep merely mentioning the text (verb-based)",
     f'grep -rn "rm -rf tmp" {LLM} && echo found', False),
    ("echo only, no rm", 'echo "hi"; ls', False),

    # ---- escape hatch ----
    ("RM-COMPOUND-OK marker lifts the deny",
     f"rm -rf {LLM}/tmp/foo; mkdir x  # RM-COMPOUND-OK: intentional atomic reset", False),

    # ---- guard-conflict: a safety-guard's legitimate stop-output as echoed text ----
    ("echoing 苛立ち停止 text does not fire (verb=echo)",
     'echo "止まります。指示ください"; echo done', False),
]


def main() -> int:
    failed = []
    for desc, cmd, expect in CASES:
        # the test embeds literal \n; turn them into real newlines for the heredoc case
        got = emits_deny(cmd.replace("\\n", "\n"))
        ok = got == expect
        print(f"[{'PASS' if ok else 'FAIL'}] {desc} "
              f"(got={'DENY' if got else 'pass'} want={'DENY' if expect else 'pass'})")
        if not ok:
            failed.append(desc)
    print(f"\n{len(CASES) - len(failed)}/{len(CASES)} passed")
    if failed:
        for d in failed:
            print(f"  FAILED: {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
