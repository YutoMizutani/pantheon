#!/usr/bin/env python3
"""block_ship_css_without_visual_verify — audit shipping a visual CSS change
without a Playwright MCP visual-verify call in the same turn.

Memory rule:
  - ``feedback_visual_verify_before_shipping_css.md`` (2026-05-28)

Stop event hook. Detects the failure pattern where the assistant:
  (A) edits CSS layout/positioning primitives that are prone to invisible
      failure (``::before`` / ``::after``, ``position: absolute|fixed``,
      ``overflow: hidden|clip``, ``z-index``, negative-offset ``-Npx`` in
      top/left/right/bottom), AND
  (B) emits a completion phrase ("完了" / "実装" / "改修" / "ship" /
      "完成" / "## 改修" / "## 実装") in the same turn, AND
  (C) does NOT call any ``mcp__playwright__`` tool in the same turn, AND
  (D) does NOT include a ``# VISUAL-VERIFY-OK: <reason>`` override marker.

When all four conditions hit, the hook writes to the audit log so the
regression is observable, and emits a stderr remind so the next turn can
self-correct (e.g. open the page in Playwright, take a screenshot or
inspect computed style, then re-declare completion).

Exit 0 always — audit + remind only, no abort.

Why this is gated to layout/positioning primitives (not all CSS): pure
color/font/text-content changes rarely have invisible-failure modes, so
firing on every CSS touch would be noise. The listed primitives are the
ones that empirically clip / stack-context / z-fight, where string
presence in the HTML response (curl | grep) tells you nothing about
whether the pixel actually rendered.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _fire_counter import record_fire  # noqa: E402

# Category A: CSS primitives whose rendering can silently fail
# (clipped by ancestor overflow, stuck behind sibling z-index, escaped
# stacking context, off-viewport due to wrong offsetParent, etc).
_CSS_RISK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"::before\b"),
    re.compile(r"::after\b"),
    re.compile(r"position\s*:\s*(?:absolute|fixed|sticky)"),
    re.compile(r"overflow\s*:\s*(?:hidden|clip)"),
    re.compile(r"overflow-(?:x|y)\s*:\s*(?:hidden|clip)"),
    re.compile(r"z-index\s*:\s*-?\d+"),
    # Negative offsets in top/left/right/bottom inside a CSS rule.
    # Matches both `top: -10px` and shorthand-ish patterns.
    re.compile(r"\b(?:top|left|right|bottom)\s*:\s*-\d+(?:\.\d+)?(?:px|rem|em|%)"),
    re.compile(r"clip-path\s*:"),
    # Drag-and-drop visual primitives commonly involved in insertion indicators.
    re.compile(r"\.drop-before\b"),
    re.compile(r"\.drop-after\b"),
    re.compile(r"drag-?over"),
    # State-conditioned selectors: persisted classList / data-attr / theme
    # token rules whose cascade depends on user's runtime state.
    # Default-state verify cannot exercise them (2026-05-29 dashboard theme
    # toggle / calm-mode-hard header.kpi-bar override case).
    re.compile(r"body\s*\.\s*[a-zA-Z_][\w-]*\s*[\.\s>+~{,]"),     # body.calm-mode-hard { ... } / body.x .y
    re.compile(r"body\s*\[\s*class\s*[*~|^$]?=\s*['\"]"),          # body[class*="..."]
    re.compile(r"\[\s*data-theme\s*[*~|^$]?=\s*['\"]"),
    re.compile(r"\[\s*data-density\s*[*~|^$]?=\s*['\"]"),
    re.compile(r"\[\s*data-mode\s*[*~|^$]?=\s*['\"]"),
    re.compile(r"\[\s*data-[a-z][\w-]*\s*[*~|^$]?=\s*['\"]"),      # generic [data-*=...] (any persisted attr)
    re.compile(r":root\s*\{[^}]*--[a-zA-Z_][\w-]*\s*:"),           # :root { --token: ... }
    re.compile(r"html\s*\[\s*data-[a-z][\w-]*\s*[*~|^$]?=\s*['\"]"),
)

# Category B: completion / ship phrases.
_COMPLETION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"##\s*改修\b"),
    re.compile(r"##\s*実装\b"),
    re.compile(r"##\s*完成\b"),
    re.compile(r"実装完了"),
    re.compile(r"改修完了"),
    re.compile(r"完成しました"),
    re.compile(r"shipped?\b", re.IGNORECASE),
    re.compile(r"completed?\b", re.IGNORECASE),
    # Bare "完了" is too broad (e.g. "テスト完了"). Require it to be
    # near a verb-ending or sentence boundary to bias toward declaration.
    re.compile(r"完了(?:です|しました|。|$|\s*$)"),
)

# Category D: override marker — explicit human/agent-acknowledged escape.
_OVERRIDE_MARKER_RE = re.compile(r"#\s*VISUAL-VERIFY-OK:\s*(\S[^\n]*)")

# Category C tool-name prefix.
_PLAYWRIGHT_PREFIX = "mcp__playwright__"

# Category C-strict: state-injecting Playwright calls. A bare browser_navigate
# + browser_take_screenshot pair only verifies default state, missing bugs
# conditioned on persisted localStorage / classList / data-attr / cookie.
# When the edited CSS contains state-conditioned selectors (body[class*=] /
# [data-theme=] / etc.) we require evidence of state mutation inside a
# browser_evaluate or browser_run_code_unsafe call in the same turn.
_STATE_MUTATION_RE = re.compile(
    r"localStorage\s*\.\s*setItem"
    r"|sessionStorage\s*\.\s*setItem"
    r"|classList\s*\.\s*(?:add|toggle|remove)"
    r"|setAttribute\s*\(\s*['\"]data-"
    r"|document\.cookie\s*="
)
_STATE_CONDITIONED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"body\s*\.\s*[a-zA-Z_][\w-]*\s*[\.\s>+~{,]"),
    re.compile(r"body\s*\[\s*class\s*[*~|^$]?=\s*['\"]"),
    re.compile(r"\[\s*data-theme\s*[*~|^$]?=\s*['\"]"),
    re.compile(r"\[\s*data-density\s*[*~|^$]?=\s*['\"]"),
    re.compile(r"\[\s*data-mode\s*[*~|^$]?=\s*['\"]"),
    re.compile(r"\[\s*data-[a-z][\w-]*\s*[*~|^$]?=\s*['\"]"),
    re.compile(r":root\s*\{[^}]*--[a-zA-Z_][\w-]*\s*:"),
    re.compile(r"html\s*\[\s*data-[a-z][\w-]*\s*[*~|^$]?=\s*['\"]"),
)

# Additional override markers covering state-aware verification escapes.
_OVERRIDE_COLORONLY_RE = re.compile(r"#\s*VISUAL-VERIFY-COLORONLY-OK:\s*(\S[^\n]*)")
_OVERRIDE_STATE_UNKNOWN_RE = re.compile(r"#\s*VISUAL-VERIFY-STATE-UNKNOWN-OK:\s*(\S[^\n]*)")

from _paths import RUNTIME_DIR  # noqa: E402
_AUDIT_LOG = RUNTIME_DIR / "block_ship_css_without_visual_verify_audit.log"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _iter_current_turn_blocks(transcript_path: str):
    """Yield assistant message content blocks for the current turn only.

    A turn = sequence of assistant messages since the most recent user
    message. We reset state on every user role and accumulate until
    end-of-file.
    """
    if not transcript_path:
        return
    p = Path(transcript_path)
    if not p.exists():
        return
    # Two-pass-ish: collect indices of assistant blocks since last user.
    assistant_blocks: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "user":
                assistant_blocks = []
                continue
            if role != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                assistant_blocks.extend(c for c in content if isinstance(c, dict))
            elif isinstance(content, str):
                assistant_blocks.append({"type": "text", "text": content})
    except OSError:
        return
    for block in assistant_blocks:
        yield block


def _extract_turn_signals(blocks: list[dict]) -> tuple[str, list[str], list[str], list[str]]:
    """Return (assistant_text, edited_css_payloads, tool_names, playwright_eval_code) for the turn.

    edited_css_payloads collects the new_string / content of Edit / Write
    tool_use blocks where the file looks like CSS or HTML/template that
    can embed <style>. tool_names lists every tool_use name in the turn
    (used to check for Playwright invocation). playwright_eval_code
    collects the JS payload of browser_evaluate / browser_run_code_unsafe
    calls so we can detect state mutation (localStorage.setItem /
    classList.add / setAttribute('data-...) / document.cookie = ...).
    """
    text_parts: list[str] = []
    css_payloads: list[str] = []
    tool_names: list[str] = []
    playwright_eval_code: list[str] = []
    for c in blocks:
        t = c.get("type")
        if t == "text":
            text_parts.append(str(c.get("text", "")))
        elif t == "tool_use":
            name = str(c.get("name", ""))
            tool_names.append(name)
            inp = c.get("input") or {}
            if not isinstance(inp, dict):
                continue
            if name == "Edit":
                fp = str(inp.get("file_path", ""))
                new_str = str(inp.get("new_string", ""))
                if _looks_like_visual_file(fp):
                    css_payloads.append(new_str)
            elif name == "Write":
                fp = str(inp.get("file_path", ""))
                content = str(inp.get("content", ""))
                if _looks_like_visual_file(fp):
                    css_payloads.append(content)
            elif name in ("mcp__playwright__browser_evaluate", "mcp__playwright__browser_run_code_unsafe"):
                code = str(inp.get("function") or inp.get("code") or inp.get("expression") or "")
                if code:
                    playwright_eval_code.append(code)
    return "\n".join(text_parts), css_payloads, tool_names, playwright_eval_code


def _looks_like_visual_file(file_path: str) -> bool:
    """Heuristic: file is CSS, HTML, JSX/TSX/Vue/Svelte, or a template
    that can embed <style>. Backend-only files are excluded."""
    if not file_path:
        return False
    lower = file_path.lower()
    visual_exts = (
        ".css", ".scss", ".sass", ".less",
        ".html", ".htm", ".jinja", ".jinja2", ".j2", ".tmpl", ".template",
        ".jsx", ".tsx", ".vue", ".svelte",
    )
    if lower.endswith(visual_exts):
        return True
    # Catch dashboard/templates style files even without extension match.
    return False


def _find_matches(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    hits: list[str] = []
    for pat in patterns:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    return hits


def _has_playwright_call(tool_names: list[str]) -> bool:
    return any(n.startswith(_PLAYWRIGHT_PREFIX) for n in tool_names)


def _has_override_marker(text: str, css_payloads: list[str]) -> bool:
    for rx in (_OVERRIDE_MARKER_RE, _OVERRIDE_COLORONLY_RE, _OVERRIDE_STATE_UNKNOWN_RE):
        if rx.search(text):
            return True
        if any(rx.search(p) for p in css_payloads):
            return True
    return False


def _css_has_state_conditioned(payloads: list[str]) -> bool:
    for p in payloads:
        for rx in _STATE_CONDITIONED_PATTERNS:
            if rx.search(p):
                return True
    return False


def _playwright_mutated_state(eval_codes: list[str]) -> bool:
    return any(_STATE_MUTATION_RE.search(code) for code in eval_codes)


def _log_hit(
    sid: str,
    css_hits: list[str],
    completion_hits: list[str],
    tool_names: list[str],
) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "ts": ts,
            "session_id": sid,
            "css_primitives": sorted(set(css_hits)),
            "completion_phrases": sorted(set(completion_hits)),
            "tool_names_in_turn": tool_names,
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    data = _read_payload()
    sid = data.get("session_id") or ""
    transcript = data.get("transcript_path") or ""
    if not sid or not transcript:
        return 0

    blocks = list(_iter_current_turn_blocks(transcript))
    if not blocks:
        return 0

    assistant_text, css_payloads, tool_names, playwright_eval_code = _extract_turn_signals(blocks)

    # Co-occurrence check.
    css_hits: list[str] = []
    for payload in css_payloads:
        css_hits.extend(_find_matches(payload, _CSS_RISK_PATTERNS))
    if not css_hits:
        return 0

    completion_hits = _find_matches(assistant_text, _COMPLETION_PATTERNS)
    if not completion_hits:
        return 0

    if _has_override_marker(assistant_text, css_payloads):
        return 0

    state_conditioned = _css_has_state_conditioned(css_payloads)
    has_pw = _has_playwright_call(tool_names)
    state_mutated = _playwright_mutated_state(playwright_eval_code)

    # Branch 1: edited CSS is state-conditioned (body[class*=] / [data-theme=]
    # / :root token / etc.). A bare navigate + screenshot is insufficient;
    # we require evidence of state injection inside browser_evaluate.
    if state_conditioned and not state_mutated:
        _log_hit(sid, css_hits, completion_hits, tool_names)
        record_fire(
            "feedback_visual_verify_before_shipping_css",
            "warn",
            count=1,
            context=f"state_conditioned css:{','.join(sorted(set(css_hits))[:3])} | done:{','.join(sorted(set(completion_hits))[:2])} | pw_state_mutation=False",
        )
        sys.stderr.write(
            "[block_ship_css_without_visual_verify] shipped state-conditioned CSS "
            f"({', '.join(sorted(set(css_hits))[:5])}) with completion phrase "
            f"({', '.join(sorted(set(completion_hits))[:3])}) but no state-injecting "
            "Playwright browser_evaluate in same turn. Default-state verify cannot "
            "exercise body[class*=] / [data-theme=] / :root token rules. "
            "Inject the user's persisted state via browser_evaluate "
            "(localStorage.setItem / classList.add / setAttribute('data-...)) "
            "before screenshot. Override with `# VISUAL-VERIFY-STATE-UNKNOWN-OK: "
            "<user-confirmed reason>` if state cannot be obtained.\n"
        )
        return 0

    # Branch 2: non-state-conditioned but risky CSS, no Playwright at all.
    if not has_pw:
        _log_hit(sid, css_hits, completion_hits, tool_names)
        record_fire(
            "feedback_visual_verify_before_shipping_css",
            "warn",
            count=1,
            context=f"css:{','.join(sorted(set(css_hits))[:3])} | done:{','.join(sorted(set(completion_hits))[:2])}",
        )
        sys.stderr.write(
            "[block_ship_css_without_visual_verify] shipped visual CSS change "
            f"({', '.join(sorted(set(css_hits))[:5])}) with completion phrase "
            f"({', '.join(sorted(set(completion_hits))[:3])}) but no Playwright MCP call in same turn. "
            "Per feedback_visual_verify_before_shipping_css: invoke mcp__playwright__browser_navigate + "
            "browser_take_screenshot / browser_evaluate to confirm pixel render before declaring done. "
            "Override with `# VISUAL-VERIFY-OK: <reason>` if no server is available.\n"
        )
        return 0

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[block_ship_css_without_visual_verify] error: {exc}\n")
        sys.exit(0)
