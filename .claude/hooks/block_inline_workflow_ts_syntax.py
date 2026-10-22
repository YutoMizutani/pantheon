#!/usr/bin/env python3
"""PreToolUse hook (matcher: Workflow) — warn BEFORE launch when an inline
`script` violates the harness plain-JS contract, so the launch→fail→Read→Edit→
relaunch round-trip is avoided.

Background (see feedback_inline_workflow_script_plain_js_contract.md):
  The Workflow tool's inline `script` is parsed by a plain-JavaScript parser.
  Two failure modes are DETERMINISTIC and statically detectable in the script
  text, and each one costs a full launch round-trip when not caught up front:

    (1) TypeScript syntax leaks in (type annotations `: string[]`, `interface`,
        generics `<T>`, `as Type`). Harness returns:
        "Invalid workflow script: Script parse error: Unexpected token.
         Workflow scripts must be plain JavaScript — TypeScript syntax
         (type annotations like ': string[]', interfaces, generics) fails to parse."

    (2) `args` consumed as if already parsed (e.g. `const key = args` then
        array methods on it). The harness passes `args` as a STRING; the script
        must normalize: `const key = Array.isArray(args) ? args : JSON.parse(args)`.

Recurrence that motivates this hook:
  2026-06-06/07 session e926ff7d (obs-clipper) — LOO/hybrid eval workflows launched
  inline. THREE consecutive launch failures: `const key = args` (stringified args),
  then a TS type annotation, then unbalanced parens — 5 wasted tool round-trips
  before the third attempt succeeded.

Contract (NON-BLOCKING, fail-quiet):
  - NEVER denies or blocks. Emits a stderr reminder (exit 0) only.
  - Fires only when tool is Workflow AND an inline `script` string is present.
  - Silent when launching via `scriptPath` (a pre-written .js file), or when the
    inline script already normalizes args and carries no TS markers.
  - Paren-balance (failure mode 3) is intentionally NOT checked here — a naive
    bracket count is fragile inside template literals and would false-positive;
    that one stays on the authoring self-check.
"""
import json
import os.path
import re
import sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _surface import surface_stderr  # noqa: E402  stderr の注意を Claude に届く形でも出す
surface_stderr()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # telemetry is best-effort; never break the hook
    def record_fire(*_a, **_k):  # type: ignore
        return

_TOOL = "Workflow"

# Type-annotation / interface / generic / `as` markers. Kept conservative to
# avoid matching legitimate JS (object literals use `key: value`, so we only
# flag `: <TypeName>` immediately followed by `[]`, `|`, `,`, `)`, `=`, or EOL
# in a *parameter/declaration* shape, plus the unambiguous keywords).
_TS_PATTERNS = [
    (r":\s*(?:string|number|boolean|any|void|unknown|object|null)\s*(?:\[\])?\s*(?:[,)=;|&]|$)",
     "type annotation (e.g. `: string[]`)"),
    (r"\binterface\s+[A-Za-z_]", "`interface` declaration"),
    (r"\bas\s+(?:string|number|boolean|any|unknown|const)\b", "`as Type` cast"),
    (r"\benum\s+[A-Za-z_]", "`enum` declaration"),
    (r"function\s+[A-Za-z_]\w*\s*<[A-Za-z_]", "generic function `<T>`"),
]

# args consumed without Array.isArray/JSON.parse normalization in the same script.
_ARGS_BARE = re.compile(r"=\s*args\b")
_ARGS_NORMALIZED = re.compile(r"Array\.isArray\(\s*args\s*\)|JSON\.parse\(\s*args\s*\)")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != _TOOL:
        return
    ti = data.get("tool_input") or {}
    script = ti.get("script")
    # scriptPath form (pre-written .js) — out of scope, let it through silently.
    if not isinstance(script, str) or script.strip() == "":
        return

    hits = []

    for pat, label in _TS_PATTERNS:
        if re.search(pat, script):
            hits.append("TypeScript " + label + " — harness rejects non-plain-JS")
            break  # one TS hint is enough

    if _ARGS_BARE.search(script) and not _ARGS_NORMALIZED.search(script):
        hits.append(
            "`args` is consumed without normalization — the harness passes `args` "
            "as a STRING. Add `const key = Array.isArray(args) ? args : JSON.parse(args)`"
        )

    if not hits:
        return

    sys.stderr.write(
        "[inline-workflow hint] before launch, this inline `script` likely breaks "
        "the plain-JS contract:\n  - " + "\n  - ".join(hits) + "\n"
        "Fix in the script text now to avoid a launch→fail→edit→relaunch round-trip. "
        "If you will launch >1 time, write a persistent .js and pass `scriptPath` "
        "instead. See feedback_inline_workflow_script_plain_js_contract.md.\n"
    )
    record_fire("feedback_inline_workflow_script_plain_js_contract", "warn",
                count=len(hits))
    sys.exit(0)


if __name__ == "__main__":
    main()
