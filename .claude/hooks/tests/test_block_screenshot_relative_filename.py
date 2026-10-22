#!/usr/bin/env python3
"""Regression test for block_screenshot_relative_filename.py.

Encodes the BLOCKING contract (2026-06-29, user-approved): the relative-path
hazard now BLOCKS (exit 2, reason fed to the model) instead of a non-blocking
exit-0 stderr hint — because PreToolUse exit-0 stderr is not surfaced to the
model, so the old hint reached no one and the waste-chain recurred repeatedly.

Each case asserts two things:
  - want_fire: did the hook emit a "screenshot-path" message at all?
  - want_exit: 2 = BLOCK (hazard; message reaches the model and forces a retry),
               0 = non-blocking (safe silence, or the soft absolute-outside note).

Contract encoded:
  - BLOCK (exit 2): a bare relative `filename`; a relative path in run_code's
    page.screenshot — on ANY mcp__playwright*__ server variant.
  - SAFE (exit 0, silent): omitted/empty filename (MCP auto-writes to --output-dir),
    absolute path under the canonical tmp/playwright-mcp/ dir, non-screenshot tool.
  - SOFT note (exit 0, fires): absolute path outside the canonical dir (still
    deterministic, no waste-chain → must not block).

Run bare (non-zero exit on any failure):  python3 test_block_screenshot_relative_filename.py
"""
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "block_screenshot_relative_filename.py")
# Marker shared by both the blocking ("[screenshot-path BLOCKED]") and the soft
# ("[screenshot-path hint]") messages — presence = the hook fired.
MARK = "screenshot-path"


def _run(payload: dict):
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True)
    return p.stderr, p.returncode


def _shot(tool_input: dict, server: str = "playwright"):
    return _run({"tool_name": f"mcp__{server}__browser_take_screenshot",
                 "tool_input": tool_input})


def _runcode(code: str, server: str = "playwright"):
    return _run({"tool_name": f"mcp__{server}__browser_run_code_unsafe",
                 "tool_input": {"code": code}})


CANON = os.path.join(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(), "tmp", "playwright-mcp") + "/"

# (label, (stderr, returncode), want_fire, want_exit)
CASES = [
    # SAFE — omission auto-lands in --output-dir; silent + non-blocking.
    ("omit filename (no key)", _shot({}), False, 0),
    ("empty filename", _shot({"filename": ""}), False, 0),
    # HAZARD — bare relative resolves to repo root → BLOCK (exit 2).
    ("bare relative filename", _shot({"filename": "foo.png"}), True, 2),
    ("bare relative with ./", _shot({"filename": "./foo.png"}), True, 2),
    # SAFE — absolute under canonical dir; silent + non-blocking.
    ("absolute canonical", _shot({"filename": CANON + "x.png"}), False, 0),
    # SOFT NOTE — absolute outside canonical: fires but does NOT block (exit 0).
    ("absolute outside canonical", _shot({"filename": "/tmp/x.png"}), True, 0),
    # login-profile server variant must also be covered → BLOCK.
    ("bare relative on playwright-login", _shot({"filename": "foo.png"}, "playwright-login"), True, 2),
    # run_code: relative page.screenshot path → BLOCK.
    ("run_code relative path", _runcode("await page.screenshot({path:'./x.png'})"), True, 2),
    # run_code absolute → safe + silent.
    ("run_code absolute path", _runcode("await page.screenshot({path:'" + CANON + "x.png'})"), False, 0),
    # non-screenshot tool is silent + non-blocking.
    ("non-screenshot tool", _run({"tool_name": "mcp__playwright__browser_navigate",
                                  "tool_input": {"url": "about:blank"}}), False, 0),
]


def main() -> int:
    failures = []
    for label, (err, code), want_fire, want_exit in CASES:
        got_fire = MARK in err
        if got_fire != want_fire or code != want_exit:
            failures.append(
                f"  [{label}] want(fire={want_fire},exit={want_exit}) "
                f"got(fire={got_fire},exit={code})\n"
                f"      stderr: {err.strip()[:160]!r}")
    total = len(CASES)
    if failures:
        print(f"RED {total - len(failures)}/{total} — failing cases:")
        print("\n".join(failures))
        return 1
    print(f"GREEN {total}/{total} cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
