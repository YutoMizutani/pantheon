#!/usr/bin/env python3
"""Tests for the signal-vocabulary config layer (_signals.py).

Contract (frame/local split of the self-improvement signal hooks):
  - The matching MECHANISM ships in git; the VOCABULARY is config.
  - FULLY OPT-IN: with no config file the defaults are EMPTY, so neither
    acceptance nor correction ever fires (the frame layer holds no opinion on
    vocabulary — "ok" itself is environment-dependent).
  - local/signals.json (or FRAME_SIGNALS_FILE) overrides per LEAF key with
    REPLACE semantics: a defined key sets the list; undefined keys stay empty.
  - Broken config (invalid JSON / invalid regex entries) degrades to
    empty / skips the bad entry — never kills the hook.
  - The tracked signals.json.example (origin-environment JA pack) stays valid.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
ACCEPTANCE_HOOK = HOOKS_DIR / "detect_acceptance_signal.py"
CORRECTION_HOOK = HOOKS_DIR / "detect_correction_signal_v2.py"
EXAMPLE_PACK = HOOKS_DIR / "local" / "signals.json.example"


def _text(t: str) -> list:
    return [{"type": "text", "text": t}]


def _tools(n: int) -> list:
    return [{"type": "tool_use", "name": "Bash", "input": {}} for _ in range(n)] + [
        {"type": "text", "text": "done"}
    ]


def _msg(role: str, content) -> str:
    return json.dumps({"message": {"role": role, "content": content}})


def _write_transcript(lines: list[str]) -> str:
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="test_sigv_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _write_signals(obj_or_text) -> str:
    fd, path = tempfile.mkstemp(suffix=".json", prefix="test_sigv_signals_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        if isinstance(obj_or_text, str):
            f.write(obj_or_text)
        else:
            json.dump(obj_or_text, f, ensure_ascii=False)
    return path


def _run(hook: Path, prompt: str, transcript: str, sid: str,
         signals_file: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["FRAME_SIGNALS_FILE"] = signals_file
    # Hermetic: keep the real correction queue out of acceptance-gate decisions.
    env["CLAUDE_CORRECTION_QUEUE"] = f"/tmp/test_sigv_queue_{sid}.json"
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {"prompt": prompt, "transcript_path": transcript, "session_id": sid}
        ),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )


def _acceptance_fires(prompt: str, transcript: str, sid: str, signals_file: str) -> bool:
    return "AUTO-LEARN-META" in _run(
        ACCEPTANCE_HOOK, prompt, transcript, sid, signals_file
    ).stdout


def _correction_queued(prompt: str, transcript: str, sid: str, signals_file: str) -> bool:
    qpath = f"/tmp/test_sigv_queue_{sid}.json"
    try:
        os.unlink(qpath)
    except OSError:
        pass
    _run(CORRECTION_HOOK, prompt, transcript, sid, signals_file)
    try:
        items = json.loads(Path(qpath).read_text(encoding="utf-8")).get("items", [])
    except (OSError, json.JSONDecodeError):
        items = []
    finally:
        try:
            os.unlink(qpath)
        except OSError:
            pass
    return len(items) > 0


def main() -> int:
    pid = os.getpid()
    failures: list[str] = []
    tmp_paths: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        status = "ok" if cond else "FAIL"
        if not cond:
            failures.append(name)
        print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))

    # High-activity transcript so the acceptance cost gate always says fire.
    transcript = _write_transcript(
        [_msg("user", _text("実装して")), _msg("assistant", _tools(6))]
    )
    tmp_paths.append(transcript)
    missing = f"/tmp/test_sigv_missing_{pid}.json"  # never created → empty defaults

    # --- opt-in: no config file → nothing fires ---
    check(
        "optin_no_acceptance_fire",
        not _acceptance_fires("ok", transcript, f"sigv-{pid}-d1", missing),
        "empty defaults must not fire (fully opt-in)",
    )
    check(
        "optin_no_acceptance_done",
        not _acceptance_fires("done", transcript, f"sigv-{pid}-d2", missing),
    )
    check(
        "optin_no_acceptance_ja",
        not _acceptance_fires("完了", transcript, f"sigv-{pid}-d3", missing),
    )
    check(
        "optin_no_correction_en",
        not _correction_queued("that's wrong", transcript, f"sigv-{pid}-d4", missing),
    )
    check(
        "optin_no_correction_ja",
        not _correction_queued("それ違うでしょ", transcript, f"sigv-{pid}-d5", missing),
    )

    # --- override: a defined leaf key activates that vocabulary ---
    custom_acc = _write_signals({"acceptance": {"exact": ["承認"]}})
    tmp_paths.append(custom_acc)
    check(
        "custom_exact_fires",
        _acceptance_fires("承認", transcript, f"sigv-{pid}-c1", custom_acc),
    )
    check(
        "undefined_key_stays_empty",
        not _acceptance_fires("done", transcript, f"sigv-{pid}-c2", custom_acc),
        "exact_ci undefined → stays empty (no built-in default to inherit)",
    )

    replace_ci = _write_signals({"acceptance": {"exact_ci": ["ship it"]}})
    tmp_paths.append(replace_ci)
    check(
        "replace_new_word_ci",
        _acceptance_fires("SHIP IT", transcript, f"sigv-{pid}-r2", replace_ci),
    )
    check(
        "replace_ci_is_exact_only",
        not _acceptance_fires("ship it now", transcript, f"sigv-{pid}-r3", replace_ci),
        "exact_ci is still full-string match, not substring",
    )

    # --- correction override + invalid-regex resilience ---
    custom_corr = _write_signals(
        {"correction": {"patterns": ["([", "これは誤り"]}}
    )
    tmp_paths.append(custom_corr)
    check(
        "custom_correction_queues",
        _correction_queued("これは誤りです", transcript, f"sigv-{pid}-c3", custom_corr),
        "valid pattern must survive a sibling invalid regex",
    )
    check(
        "custom_correction_replaces",
        not _correction_queued(
            "that's wrong", transcript, f"sigv-{pid}-c4", custom_corr
        ),
        "defining patterns must drop the English defaults",
    )

    # --- broken config degrades to empty defaults (never crashes) ---
    garbage = _write_signals("{not json!!")
    tmp_paths.append(garbage)
    check(
        "invalid_json_degrades_empty",
        not _acceptance_fires("ok", transcript, f"sigv-{pid}-g1", garbage),
        "broken config → empty (opt-in), and must not crash the hook",
    )

    # --- tracked example pack stays valid ---
    check("example_pack_exists", EXAMPLE_PACK.exists(), str(EXAMPLE_PACK))
    check(
        "example_pack_ja_acceptance",
        _acceptance_fires("完了", transcript, f"sigv-{pid}-e1", str(EXAMPLE_PACK)),
    )
    check(
        "example_pack_ja_correction",
        _correction_queued("それ違うでしょ", transcript, f"sigv-{pid}-e2", str(EXAMPLE_PACK)),
    )
    check(
        "example_pack_third_party_negation",
        not _correction_queued(
            "なるほど。ありがとう。claudeがないとだめなんだね",
            transcript,
            f"sigv-{pid}-e3",
            str(EXAMPLE_PACK),
        ),
        "origin FP case must stay suppressed",
    )

    for p in tmp_paths:
        try:
            os.unlink(p)
        except OSError:
            pass

    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
