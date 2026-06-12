#!/usr/bin/env python3
"""Telemetry consumer: report which hooks are instrumented, which have fired,
and which rules/hooks are cold (deprecation candidates).

Deterministic — NO LLM, NO `claude -p`. Safe to run anytime, or to wire into
定期実行 (cron / 朝の定期チェック等) から呼ぶ想定. Read-only.

Usage:
    python3 tools/telemetry_report.py [--days 30]
"""
import argparse
import glob
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
_SLUG = re.sub(r"[^A-Za-z0-9]", "-", _PROJECT_DIR)
HOOKS_DIR = os.path.join(_PROJECT_DIR, ".claude/hooks")
TELEM = os.path.join(HOME, ".claude/projects", _SLUG, "telemetry/hook_fires.jsonl")

# Outcome classes. Violation fires (block/warn) are the deprecation/efficacy
# signal — a rule that catches a violation or nudges. Activity fires
# (audit/transform/allow) are high-frequency observability or helper fires
# (e.g. touch_memory_on_read fires near-every-turn) that must NOT drown the
# violation signal in COLD/HOT. Any unknown/missing outcome is treated as a
# violation so nothing is silently hidden.
ACTIVITY_OUTCOMES = {"audit", "transform", "allow"}


def load_fires():
    fires = []
    if not os.path.exists(TELEM):
        return fires
    with open(TELEM, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                fires.append(json.loads(line))
            except Exception:
                pass
    return fires


def is_test_fixture(rec):
    extra = rec.get("extra") or {}
    return "test fixture" in str(extra.get("reason", "")).lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    # 1. enumerate hook scripts (skip private _helpers). local/ はローカル層 hook
    #    (gitignore 済み・ユーザー固有) — cold-hook 監査の対象には同じく含める。
    hook_paths = {
        os.path.basename(p)[:-3]: p
        for p in sorted(
            glob.glob(os.path.join(HOOKS_DIR, "*.py"))
            + glob.glob(os.path.join(HOOKS_DIR, "local", "*.py"))
        )
        if not os.path.basename(p).startswith("_")
    }
    hook_files = sorted(hook_paths)

    # 2. which hooks even call record_fire (are instrumented)?
    instrumented = set()
    for name in hook_files:
        path = hook_paths[name]
        try:
            with open(path, errors="replace") as f:
                src = f.read()
        except Exception:
            continue
        if re.search(r"record_fire|_fire_counter|hook_fires", src):
            instrumented.add(name)

    # 3. load fires, drop test fixtures, window by days
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    real_fires = []
    for rec in load_fires():
        if is_test_fixture(rec):
            continue
        ts = rec.get("ts", "")
        try:
            t = datetime.fromisoformat(ts)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except Exception:
            t = None
        if t is None or t >= cutoff:
            real_fires.append(rec)

    # Split fires by outcome CLASS so high-frequency observability/helper fires
    # (audit/transform/allow) don't drown the violation signal (block/warn) that
    # drives deprecation decisions.
    viol_fires = [r for r in real_fires if r.get("outcome") not in ACTIVITY_OUTCOMES]
    act_fires = [r for r in real_fires if r.get("outcome") in ACTIVITY_OUTCOMES]
    viol_counts = Counter(r.get("hook", "?") for r in viol_fires)
    act_counts = Counter(r.get("hook", "?") for r in act_fires)
    fired_any = set(viol_counts) | set(act_counts)

    print(f"=== Telemetry report (last {args.days}d) ===")
    print(f"hook scripts found:     {len(hook_files)}")
    print(f"instrumented (call record_fire): {len(instrumented)}")
    print(f"real fires: {len(real_fires)}  "
          f"(violation={len(viol_fires)} activity={len(act_fires)})\n")

    not_instrumented = [h for h in hook_files if h not in instrumented]
    if not_instrumented:
        print(f"-- NOT instrumented ({len(not_instrumented)}) — telemetry blind, "
              "cannot judge cold/hot:")
        for h in not_instrumented:
            print(f"   · {h}")
        print()

    # COLD = instrumented but NEVER fired (any class). A hook that only ever emits
    # activity fires is alive (doing its job) and is NOT a deprecation candidate.
    cold = sorted(instrumented - fired_any)
    if cold:
        print(f"-- COLD ({len(cold)}) — instrumented but 0 fires in {args.days}d "
              "(deprecation candidates):")
        for h in cold:
            print(f"   · {h}")
        print()

    if viol_counts:
        print("-- HOT (violations: block/warn) — efficacy / deprecation signal:")
        for h, n in viol_counts.most_common():
            print(f"   {n:4d}  {h}")
        print()

    if act_counts:
        print("-- ACTIVITY (audit/transform/allow) — observability/helper fires, "
              "NOT a deprecation signal:")
        for h, n in act_counts.most_common():
            print(f"   {n:4d}  {h}")
        print()

    # actionable summary line (greppable, 定期実行での集計に利用)
    print(f"TELEMETRY_SUMMARY hooks={len(hook_files)} "
          f"instrumented={len(instrumented)} "
          f"uninstrumented={len(not_instrumented)} "
          f"cold={len(cold)} fires_{args.days}d={len(real_fires)} "
          f"viol_{args.days}d={len(viol_fires)} "
          f"activity_{args.days}d={len(act_fires)}")


if __name__ == "__main__":
    main()
