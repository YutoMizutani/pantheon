#!/usr/bin/env python3
"""RED-first test: 自己改善ループ成果物の二層ルーティング。

二層構成 (frame = pantheon git 同梱の汎用機構 / local = ユーザー固有・gitignore 済み)
に自己改善ループが追随していることを固定する:

  1. propose_claudemd_updates: 非 project cwd の昇格 target はルート CLAUDE.md
     (フレーム層 — ルーティングと機構の説明のみ) ではなく CLAUDE.local.md。
  2. propose_claudemd_updates: queue entry 用に target → layer を導出できる
     (_layer_for_target)。
  3. detect_acceptance_signal の reflection prompt: 昇格 target 候補が二層対応
     (CLAUDE.local.md / .claude/rules/common/) で root CLAUDE.md を offer せず、
     hook 起案にも層判定 (.claude/hooks/local/ + settings.local.json) を指示する。
  4. telemetry_report: 棚卸しの enumeration が .claude/hooks/local/*.py も拾う
     (local 層 hook が cold-hook 監査から漏れない)。
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
REPO = HOOKS.parent.parent

# hook 本体は hooks dir が sys.path に載った状態で実行される前提 (_paths import)
sys.path.insert(0, str(HOOKS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


propose = _load("propose_claudemd_updates", HOOKS / "propose_claudemd_updates.py")
accept = _load("detect_acceptance_signal", HOOKS / "detect_acceptance_signal.py")


def main() -> int:
    results: list[tuple[str, bool]] = []

    def check(name: str, cond: bool) -> None:
        results.append((name, bool(cond)))

    # --- 1. propose_claudemd_updates: target 解決 ---
    tgt, scope = propose._resolve_target(str(REPO))
    check("root_cwd_targets_claude_local", tgt.name == "CLAUDE.local.md")
    tgt2, _ = propose._resolve_target(str(REPO / "projects/example-infra/app"))
    check(
        "project_cwd_targets_project_claudemd",
        str(tgt2).endswith("projects/example-infra/CLAUDE.md"),
    )

    # --- 2. layer 導出 helper ---
    has_helper = hasattr(propose, "_layer_for_target")
    check("layer_helper_exists", has_helper)
    if has_helper:
        check(
            "claude_local_is_local_layer",
            propose._layer_for_target(REPO / "CLAUDE.local.md") == "local",
        )
        check(
            "projects_is_local_layer",
            propose._layer_for_target(REPO / "projects/x/CLAUDE.md") == "local",
        )
        check(
            "rules_common_is_frame_layer",
            propose._layer_for_target(REPO / ".claude/rules/common/foo.md") == "frame",
        )

    # --- 3. reflection prompt の二層対応 ---
    r = accept._REMINDER
    check("reminder_offers_claude_local", "CLAUDE.local.md" in r)
    check("reminder_offers_rules_common", ".claude/rules/common/" in r)
    check("reminder_drops_root_claudemd_target", "ルートの `CLAUDE.md` または" not in r)
    check("reminder_requires_layer_field", "`layer`" in r)
    check("reminder_routes_local_hooks", ".claude/hooks/local/" in r)
    check("reminder_local_hooks_register_in_settings_local", "settings.local.json" in r)

    blk = accept._corrections_block(
        [{"ts": "t", "session_id": "s", "transcript_path": "p", "prompt_excerpt": "x"}]
    )
    check("corrections_block_mentions_layer", "層判定" in blk)

    # --- 4. telemetry_report が local/ 配下も列挙する ---
    telem = _load("telemetry_report", REPO / "heaven/tools/telemetry_report.py")
    tmp = Path(tempfile.mkdtemp(prefix="layer_routing_hooks_"))
    (tmp / "local").mkdir()
    (tmp / "a.py").write_text("# frame hook\n", encoding="utf-8")
    (tmp / "local" / "b.py").write_text("# local hook\n", encoding="utf-8")
    (tmp / "_private.py").write_text("", encoding="utf-8")
    telem.HOOKS_DIR = str(tmp)
    telem.TELEM = str(tmp / "nonexistent.jsonl")
    buf = io.StringIO()
    old_argv = sys.argv
    sys.argv = ["telemetry_report.py", "--days", "1"]
    try:
        with redirect_stdout(buf):
            telem.main()
    finally:
        sys.argv = old_argv
    check(
        "telemetry_enumerates_local_hooks",
        re.search(r"hook scripts found:\s+2\b", buf.getvalue()) is not None,
    )

    failures = [n for n, ok in results if not ok]
    for n, ok in results:
        print(f"  [{'ok' if ok else 'FAIL'}] {n}")
    if failures:
        print(f"\n{len(failures)} FAILED")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
