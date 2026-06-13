#!/usr/bin/env python3
"""pending queue report — 自己改善ループの未消化 promotion queue を検出する。

Deterministic — NO LLM. Read-only. morning-check.sh から呼ばれる。

自己改善ループ (reflection / propose hooks) は hook 登録・CLAUDE.md 昇格・agent 定義
変更を `~/.claude/runtime/pending_*.json` に **propose-only で積む**が、消化する主体・
トリガが定義されておらず (tmp-retention と同型の「実行主体未定義」欠陥)、提案が silent に
腐る。閾値 (既定 1 日) を超えて滞留した queue を greppable に報告し、`📋 [pending-queue]`
マーカーで人間レビューを促す。

背景 memory: feedback_surface_pending_queue_staleness_not_new_enqueue.md
注意: 自動 apply はしない — 特に agent 定義変更 (self-modification) は必ず人間レビュー。
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

RUNTIME = Path(os.environ.get("PENDING_QUEUE_RUNTIME", Path.home() / ".claude/runtime"))
# frame 層: user 固有値をハードコードしない。repo root は harness 供給の CLAUDE_PROJECT_DIR から
# 導出 (無ければ tools/.. を辿る)。memory dir の slug も絶対パスから機械的に導出する
# (Claude Code 規約: 非英数字 → '-')。
REPO_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR")
                 or Path(__file__).resolve().parents[2])
# 自己改善ループの昇格対象外 (設計変更前の stale 提案が宛てがち)。
EXCLUDED_TARGETS = {str(REPO_ROOT / "CLAUDE.md")}  # ルート CLAUDE.md = frame 専用
_SLUG = re.sub(r"[^A-Za-z0-9]", "-", str(REPO_ROOT))
MEMORY_DIR = Path.home() / ".claude/projects" / _SLUG / "memory"

QUEUES = [
    ("hook",      "pending_hook_registrations.json",  "hook 登録"),
    ("claudemd",  "pending_claudemd_updates.json",    "CLAUDE.md/rules 昇格"),
    ("agent-def", "pending_agent_def_updates.json",   "agent 定義変更 (self-mod)"),
]

DEFAULT_STALE_DAYS = 1.0
_TS_KEYS = ("queued_at", "queued", "queued_ts", "ts", "created_at")


def _items(doc):
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        return doc.get("items", [])
    return []


def _parse_ts(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _obsolete_reason(item):
    """決定論で判定できる obsolete 理由を返す (無ければ None)。深い判断 (内容が別所へ
    適用済みか) は rule-auditor が on-demand で担うので、ここでは安価で確実な2種のみ。"""
    if not isinstance(item, dict):
        return None
    tgt = item.get("target_file")
    if tgt and str(tgt) in EXCLUDED_TARGETS:
        return "昇格対象外 target (ルート CLAUDE.md = frame 専用)"
    srcs = item.get("source_memories") or []
    if isinstance(srcs, list) and srcs and MEMORY_DIR.is_dir():
        missing = [s for s in srcs if isinstance(s, str) and not (MEMORY_DIR / s).exists()]
        if missing and len(missing) == len([s for s in srcs if isinstance(s, str)]):
            return f"source memory 全て不在 ({', '.join(missing[:3])})"
    return None


def _oldest_age_days(items, now):
    ages = []
    for it in items:
        if not isinstance(it, dict):
            continue
        for k in _TS_KEYS:
            dt = _parse_ts(it.get(k))
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ages.append((now - dt).total_seconds() / 86400.0)
                break
    return max(ages) if ages else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale-days", type=float, default=DEFAULT_STALE_DAYS,
                    help="この日数以上滞留していたら 📋 マーカーを出す")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    total = 0
    obsolete_total = 0
    stale_lines = []

    for key, fname, label in QUEUES:
        path = RUNTIME / fname
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            print(f"PENDING {key}: 読み取り失敗 ({fname})")
            continue
        items = _items(doc)
        n = len(items)
        if n == 0:
            continue
        total += n
        obsolete = [(it, r) for it in items if (r := _obsolete_reason(it))]
        obsolete_total += len(obsolete)
        age = _oldest_age_days(items, now)
        age_str = f"最古 {age:.1f}d" if age is not None else "queued_at 不明"
        ob_str = f", DROP候補 {len(obsolete)}" if obsolete else ""
        print(f"PENDING {key}: {n} 件 ({age_str}{ob_str}) — {label}")
        for it, reason in obsolete:
            print(f"  DROP候補 {it.get('target_file','?')} — {reason}")
        if age is not None and age >= args.stale_days:
            stale_lines.append(f"{key}={n}件/{age:.0f}d")

    print(f"PENDING_TOTAL items={total} obsolete_dropcandidates={obsolete_total} "
          f"stale_queues={len(stale_lines)}")
    if stale_lines:
        ob = f"、うち DROP候補 {obsolete_total} 件" if obsolete_total else ""
        print(f"📋 [pending-queue] 未消化の promotion 提案が滞留 ({', '.join(stale_lines)}){ob} — "
              f"~/.claude/runtime/pending_*.json をレビューして適用 or 破棄 "
              f"(深い triage は rule-auditor)。自動 apply 不可 (特に agent-def=self-mod は人間ゲート必須)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
