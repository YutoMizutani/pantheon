#!/usr/bin/env python3
"""Contract test for audit_fetch_vs_sources.py (observe-mode Stop hook).

The hook warns (stderr, exit 0) when the latest turn's Sources section names a URL
that was never actually fetched (WebFetch / curl) in the session. It must:
  - count fetches made by subagents / workflow agents
    (``<transcript dir>/<session_id>/**/*.jsonl``) — delegated web-research output
    may be cited;
  - treat x.com alias hosts (fxtwitter / nitter / twitter …) as the same post;
  - still warn when the URL appears only in a WebSearch result, or nowhere.
Exit code is always 0 (observe mode).
Run: python3 .claude/hooks/tests/test_audit_fetch_vs_sources.py
"""
import _hermetic  # noqa: F401 — hermetic telemetry: writes go to a tmp dir, not the real log
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(os.environ.get("HOOK_UNDER_TEST") or
            Path(__file__).resolve().parent.parent / "audit_fetch_vs_sources.py")
SID = "test-session-fetch-sources"


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant(*content: dict) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": list(content)}}


def _tool_use(name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": "toolu_x", "name": name, "input": inp}


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _write(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                    encoding="utf-8")


def run(parent: list, subagent: list | None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d) / f"{SID}.jsonl"
        _write(tp, parent)
        if subagent is not None:
            _write(Path(d) / SID / "subagents" / "agent-a1.jsonl", subagent)
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"session_id": SID, "transcript_path": str(tp),
                              "hook_event_name": "Stop"}),
            capture_output=True, text=True,
        )


def _answer(url: str) -> dict:
    return _assistant(_text(f"結論はこうです。\n\n出典:\n- {url}\n"))


URL = "https://example.com/spec/page"


def case_subagent_fetched() -> tuple[bool, str]:
    """親に Sources 節・subagent に WebFetch → 発火しない."""
    r = run([_user("調べて"), _answer(URL)],
            [_user("subtask"), _assistant(_tool_use("WebFetch", {"url": URL, "prompt": "p"}))])
    return r.returncode == 0 and r.stderr == "", f"rc={r.returncode} stderr={r.stderr[:120]!r}"


def case_subagent_not_fetched() -> tuple[bool, str]:
    """subagent はあるが、そこにも取得が無い → 発火する."""
    r = run([_user("調べて"), _answer(URL)],
            [_user("subtask"),
             _assistant(_tool_use("WebFetch", {"url": "https://other.example.org/x", "prompt": "p"}))])
    return r.returncode == 0 and "[audit_fetch_vs_sources]" in r.stderr, \
        f"rc={r.returncode} stderr={r.stderr[:120]!r}"


def case_xcom_via_fxtwitter() -> tuple[bool, str]:
    """x.com の投稿を fxtwitter で取得 → x.com を記名しても発火しない."""
    r = run([_user("この投稿は?"),
             _assistant(_tool_use("WebFetch", {"url": "https://fxtwitter.com/someone/status/1234567890",
                                               "prompt": "p"})),
             _answer("https://x.com/someone/status/1234567890")], None)
    return r.returncode == 0 and r.stderr == "", f"rc={r.returncode} stderr={r.stderr[:120]!r}"


def case_websearch_only() -> tuple[bool, str]:
    """WebSearch の結果に出ただけの URL → 発火する (subagent の WebSearch も取得ではない)."""
    r = run([_user("調べて"),
             _assistant(_tool_use("WebSearch", {"query": "spec page"})),
             {"type": "user", "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "toolu_x", "content": f"Links: {URL}"}]}},
             _answer(URL)],
            [_user("subtask"), _assistant(_tool_use("WebSearch", {"query": URL}))])
    return r.returncode == 0 and "[audit_fetch_vs_sources]" in r.stderr, \
        f"rc={r.returncode} stderr={r.stderr[:120]!r}"


CASES = [case_subagent_fetched, case_subagent_not_fetched, case_xcom_via_fxtwitter, case_websearch_only]


def test_contract() -> None:
    failed = [(c.__name__, why) for c in CASES for ok, why in [c()] if not ok]
    assert not failed, failed


if __name__ == "__main__":
    fails = 0
    for c in CASES:
        ok, why = c()
        print(("PASS " if ok else "FAIL ") + c.__name__ + ("" if ok else f"  ({why})"))
        fails += 0 if ok else 1
    print(f"{len(CASES) - fails}/{len(CASES)} passed")
    sys.exit(1 if fails else 0)
