#!/usr/bin/env python3
"""PreToolUse hook (matcher: mcp__playwright*__browser_take_screenshot) — warn when
the screenshot `filename` is PRESENT but a bare relative name (not an absolute path).

Background (see runbook_playwright_mcp_output_dir.md):
  A bare relative `filename` is resolved against the CLIENT (Claude Code) cwd — the
  repo root (リポジトリのルート) — NOT the MCP server's --output-dir. (Source: playwright-core
  resolveClientFilename -> workspaceFile(filename, clientWorkspace), where
  clientWorkspace = clientInfo.cwd. Verified 2026-06-24: lsof shows the server cwd IS
  tmp/playwright-mcp, yet a bare-relative `filename` still lands in the repo root —
  proving client-cwd, not server-cwd, resolution.) The stranded PNG then forces a
  `find` to locate it, and the cleanup `rm` of that repo-root path is itself denied by
  block_repo_root_artifacts.py (read/write non-discriminating).
  Two deterministic safe paths, either is fine:
    - OMIT `filename` entirely — the MCP then auto-writes under its --output-dir
      (tmp/playwright-mcp). Verified safe 2026-06-24 (omission landed in output-dir,
      repo root untouched). This branch must NOT warn.
    - Pass an ABSOLUTE path:  <リポジトリ>/tmp/playwright-mcp/<name>.png
  NOTE: run_code_unsafe's page.screenshot({path:'./...'}) is DIFFERENT — that path is
  server-cwd relative (tmp/playwright-mcp), evidenced by the './tmp/playwright-mcp/'
  double-nest. The run_code branch below keeps its server-cwd wording.

Recurrence that motivates this hook:
  2026-06-07 session 6577136e — dashboard collect-button move, Playwright visual
  verify. browser_take_screenshot was called with filename="launchpad-collect-verify.png"
  (bare relative). PNG landed in repo root; Claude then ran 2 `find` calls to locate
  it (one failed), the cleanup `rm -f <リポジトリ>/launchpad-collect-verify.png`
  was blocked by block_repo_root_artifacts.py, and a basename-only rm retry was needed.
  This recurred ONE DAY after the memory's last_reinforced (2026-06-06) with no
  enforcing hook in place.

Recurrence 2026-06-12/13 session 7e31b3e9 (motivates the GENERALIZATION):
  The whole predicted waste-chain replayed despite this hook existing, because the
  matcher was over-narrow in two ways:
  (a) server-name fixed: the session used mcp__playwright-login__browser_take_screenshot
      (the login-profile MCP server) — the literal matcher mcp__playwright__... never fired.
  (b) tool fixed: rounds 2-3 wrote screenshots via browser_run_code_unsafe with
      page.screenshot({path:'./sheet-ab-zoom.png'}) — same relative-path trap, not
      covered at all. Round 3 even prefixed './tmp/playwright-mcp/' which DOUBLE-NESTED
      (server cwd already IS tmp/playwright-mcp) → landed at
      tmp/playwright-mcp/tmp/playwright-mcp/sheet-cards.png.
  Cost: 3x (failed Read + find) round trips + 2 denied cleanup rm's.
  Fix here: tool_name matched by regex over any mcp__playwright*__ server, and
  run_code_unsafe code is scanned for relative screenshot/path writes.
  NOTE: settings.local.json matcher must be the regex
  "mcp__playwright.*__browser_(take_screenshot|run_code_unsafe)" for this to fire.

Contract (BLOCKING for the relative-path hazard — 2026-06-29, user-approved):
  - BLOCKS (exit 2, reason fed to the model) when the screenshot would strand: a bare
    relative `filename`, or a relative path inside run_code_unsafe's page.screenshot.
    This was non-blocking (stderr + exit 0) before — but PreToolUse exit-0 stderr is NOT
    surfaced to the model, so the hint reached no one and the full waste-chain recurred
    despite the hook existing (2026-06-07 / 06-12/13 / 06-28 + the screenshot session here).
    Blocking makes the precise, already-safe detection actually reach the agent so it
    retries with an absolute path / omitted filename before anything strands.
  - SOFT note only (exit 0, non-blocking) when `filename` is ABSOLUTE but outside the
    canonical tmp/playwright-mcp/ dir — that still lands deterministically (no waste-chain),
    so it must not block.
  - Silent (SAFE, no fire) on: a MISSING/empty `filename` (the MCP auto-writes under
    its --output-dir = tmp/playwright-mcp), an absolute path already under the
    canonical tmp/playwright-mcp/ dir, and any non-screenshot tool.
"""
import json
import os.path
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return

from _paths import PROJECT_DIR  # noqa: E402
_CANONICAL = str(PROJECT_DIR / "tmp" / "playwright-mcp") + "/"
# Any playwright MCP server variant (playwright, playwright-login, ...).
_SCREENSHOT_RE = re.compile(r"^mcp__playwright[\w-]*__browser_take_screenshot$")
_RUN_CODE_RE = re.compile(r"^mcp__playwright[\w-]*__browser_run_code_unsafe$")
# Inside run_code: page.screenshot({ ... path: '<relative>' ... }) — a path value
# not starting with '/' resolves against the SERVER cwd (tmp/playwright-mcp), which
# both strands the file and double-nests './tmp/playwright-mcp/...' prefixes.
_REL_PATH_IN_CODE_RE = re.compile(
    r"""screenshot\s*\(\s*\{[^)]*?path\s*:\s*['"](?!/)""", re.S
)


def _check_run_code(code: str) -> None:
    if not _REL_PATH_IN_CODE_RE.search(code):
        return
    record_fire("runbook_playwright_mcp_output_dir", "block")
    sys.stderr.write(
        "[screenshot-path BLOCKED] run_code_unsafe writes a screenshot with a RELATIVE "
        "path. It resolves against the MCP SERVER cwd (" + _CANONICAL + "), so "
        "'./name.png' lands there invisibly and './tmp/playwright-mcp/name.png' "
        "DOUBLE-NESTS. Re-run with an ABSOLUTE path: path: \"" + _CANONICAL + "<name>.png\" "
        "and Read that exact path afterward. See runbook_playwright_mcp_output_dir.md.\n"
    )
    sys.exit(2)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    if _RUN_CODE_RE.match(tool):
        code = ti.get("code")
        if isinstance(code, str):
            _check_run_code(code)
        return
    if not _SCREENSHOT_RE.match(tool):
        return
    fn = ti.get("filename")

    # Already correct: absolute path under the canonical dir.
    if isinstance(fn, str) and fn.startswith(_CANONICAL):
        return

    # SAFE — no fire: a missing/empty `filename` makes the MCP auto-write under its
    # --output-dir (tmp/playwright-mcp). Verified 2026-06-24: omission landed there,
    # repo root untouched. Warning on this was a false-positive (root-cause-auditor
    # 2026-06-24, user-approved B); do not treat the safe default as a hazard.
    if not isinstance(fn, str) or fn == "":
        return

    # Bare relative filename = the waste-chain hazard → BLOCK so the model retries.
    if not fn.startswith("/"):
        record_fire("runbook_playwright_mcp_output_dir", "block")
        sys.stderr.write(
            "[screenshot-path BLOCKED] filename=\"" + fn + "\" is RELATIVE. A bare relative "
            "`filename` resolves against the CLIENT (Claude Code) cwd = the repo root "
            "(リポジトリのルート), NOT the MCP --output-dir — so the PNG strands in the repo "
            "root, then `find` to locate it and the cleanup `rm` is denied by "
            "block_repo_root_artifacts.py. Re-run with one of: OMIT `filename` (it then lands "
            "in the --output-dir automatically), or an ABSOLUTE path: filename=\""
            + _CANONICAL + fn.lstrip('./') + "\". See runbook_playwright_mcp_output_dir.md.\n"
        )
        sys.exit(2)

    # Absolute but outside the canonical dir — still deterministic, no waste-chain; soft
    # note only (exit 0, non-blocking). This branch is invisible to the model by design.
    record_fire("runbook_playwright_mcp_output_dir", "warn")
    sys.stderr.write(
        "[screenshot-path hint] filename is absolute but outside the canonical "
        + _CANONICAL + " dir. That's fine for landing, but the canonical home keeps "
        "all MCP screenshots together and gitignored. See runbook_playwright_mcp_output_dir.md.\n"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
