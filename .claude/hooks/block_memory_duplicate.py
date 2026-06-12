#!/usr/bin/env python3
"""block_memory_duplicate — redirect near-duplicate memory creation to extend (Recommendation #2).

Source: meta-review of the ok-triggered self-improvement loop. The reflection
sub-agent's prompt says "extend existing OR create new", but the extend-vs-create
boundary is left to the author with no independent check. Observed consequence: a
semantically related sub-cluster grew to multiple separate files that an independent
reviewer would flag for consolidation (原環境で実際に重複が観測された類義 slug 群)。

This PreToolUse Write hook compares a NEW memory file's frontmatter ``description``
against every existing memory description using a language-agnostic character-bigram
Jaccard. If similarity >= THRESHOLD it denies the new-file creation and names the
closest existing memory so the sub-agent extends that entry instead.

Threshold calibration: initial value calibrated on the origin-environment corpus
(123 entries; max pairwise similarity 0.268 / p99 0.135 / mean 0.041).
THRESHOLD = 0.45 sits well above that ceiling, so the gate produces ZERO
false-positives against the calibration corpus and only fires when a new memory is
more similar to an existing one than any two existing memories are to each other.

Re-calibration recipe: once your own memory exceeds ~50 entries, measure the full
pairwise similarity distribution and reset THRESHOLD between the observed max and
p99. Alternatively, override via the FRAME_DUP_THRESHOLD environment variable.

Scope: only Write (file creation) of a memory/<slug>.md that does NOT already exist.
Edit (the extend path), overwrites of existing files, and MEMORY.md all pass.
Registration is queued to pending_hook_registrations.json for batch user review.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path

THRESHOLD = float(os.environ.get("FRAME_DUP_THRESHOLD", "0.45"))
_MEMORY_DIR_MARKER = "/memory/"
_DESC_RE = re.compile(r"^description:\s*(.+)$", re.M)


sys.path.insert(0, str(Path(__file__).parent))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # never let telemetry import break a hook
    def record_fire(*_a, **_k) -> None:  # type: ignore
        return None


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _emit_decision(decision: str, reason: str) -> None:
    obj = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _is_memory_file(file_path: str) -> bool:
    if not file_path:
        return False
    norm = file_path.replace("\\", "/")
    name = os.path.basename(norm)
    return _MEMORY_DIR_MARKER in norm.lower() and name.endswith(".md") and name != "MEMORY.md"


def _description_of(text: str) -> str:
    m = _DESC_RE.search(text or "")
    return m.group(1).strip() if m else ""


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", (s or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _closest_existing(description: str, exclude_path: str) -> tuple[str, float, str]:
    """Return (closest_slug, similarity, closest_description)."""
    target = _bigrams(description)
    if not target:
        return "", 0.0, ""
    mem_dir = os.path.dirname(exclude_path)
    best_slug, best_sim, best_desc = "", 0.0, ""
    for p in glob.glob(os.path.join(mem_dir, "*.md")):
        if os.path.basename(p) == "MEMORY.md" or os.path.abspath(p) == os.path.abspath(exclude_path):
            continue
        try:
            d = _description_of(Path(p).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not d:
            continue
        sim = _jaccard(target, _bigrams(d))
        if sim > best_sim:
            best_slug, best_sim, best_desc = os.path.basename(p)[:-3], sim, d
    return best_slug, best_sim, best_desc


def main() -> int:
    data = _read_payload()
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    if tool != "Write":
        return 0
    if not isinstance(tool_input, dict):
        return 0
    file_path = str(tool_input.get("file_path") or "")
    if not _is_memory_file(file_path):
        return 0
    # Only police NEW-file creation; extending an existing file is the desired path.
    try:
        if Path(file_path).exists():
            return 0
    except OSError:
        return 0

    description = _description_of(str(tool_input.get("content") or ""))
    if not description:
        return 0

    slug, sim, existing_desc = _closest_existing(description, file_path)
    if sim < THRESHOLD or not slug:
        return 0

    record_fire(
        "feedback_memory_duplicate",
        "block",
        context=f"{sim:.2f}|{slug}"[:200],
    )
    _emit_decision(
        "deny",
        (
            f"既存 memory と重複の疑い (類似度 {sim:.2f} >= {THRESHOLD}).\n"
            f"  - 既存: {slug}\n"
            f"    desc: {existing_desc[:160]}\n\n"
            "新規作成ではなく既存を extend してください:\n"
            f"  1) `{slug}.md` を Read し、今回の learning を Why/How に追記 (Edit)\n"
            "  2) 本当に独立した別 rule なら、description を差別化して再 Write\n"
            "     (なぜ別 entry なのかを 1 行で明示)\n"
            "由来: 自己改善ループのメタレビュー (原環境で再発を実測)"
        ),
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[block_memory_duplicate] error: {exc}\n")
        sys.exit(0)
