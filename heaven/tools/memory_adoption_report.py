#!/usr/bin/env python3
"""Telemetry consumer: report memory *adoption*, not just memory *reads*.

Motivation (自己改善ループの棚卸し機構の一部 (規範の adoption / 再発率の計測)):
    自動化の本質は「ヒット数」ではなく「最終アウトプットに実際に採用されたか」。
    既存の telemetry は次の 3 tier のうち真ん中しか測っていない:
      - surfaced : SessionStart の <system-reminder> で recall 注入された (未ログ)
      - read     : Claude が Read ツールで .md を開いた → memory_touches.jsonl
      - adopted  : その guidance が実際に応答/行動を変えた → memory_adoption.jsonl (本機構が導入)
    `read` (= ヒット数相当) だけで cold/hot を判定すると、記事が straw man にした
    「ヒット数スコアリング」そのもの。本レポートは read 軸と adopted 軸を分離し、
    「読まれる(or recall される)が一度も採用されない」memory = Copilot 型 (ヒットするが
    採用されない → 降格候補) を炙り出す。

Deterministic — NO LLM, NO `claude -p`. Read-only. 破壊しない (退場候補を列挙するだけ)。
定期実行 (cron / 朝の定期チェック等) に telemetry_report.py と並べて配線可。

採用ログ (`memory_adoption.jsonl`) の書き手:
    detect_acceptance_signal.py の closure-reflection sub-agent が、タスク終了時に
    そのタスクで recall/read した memory ごとに 1 行 append する。本ファイルはその
    contract の権威ソース。1 行のスキーマ:
        {"ts": "<ISO8601 UTC>", "memory": "<slug = path.stem>",
         "verdict": "adopted" | "surfaced_unused",
         "session": "<session_id|'' >", "evidence": "<short, optional>"}
    - verdict=adopted        : その memory の指示が実際に応答/行動を変えた
    - verdict=surfaced_unused : recall/read されたが応答に影響しなかった
    memory_touches.jsonl と key を揃えるため "memory" は slug (拡張子なし) を使う。

Usage:
    python3 tools/memory_adoption_report.py [--days 30] [--cap N]
        --days : 集計ウィンドウ (touch / adoption / fire 共通)
        --cap  : soft cap。指定時のみ「上限超過 + 退場レビュー候補」を出す。
                 magic number を発明しないため未指定なら cap 判定は skip。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
_SLUG = re.sub(r"[^A-Za-z0-9]", "-", _PROJECT_DIR)
MEMORY_DIR = os.path.join(HOME, ".claude/projects", _SLUG, "memory")
TELEM_DIR = os.path.join(HOME, ".claude/projects", _SLUG, "telemetry")
TOUCHES_LOG = os.path.join(TELEM_DIR, "memory_touches.jsonl")
ADOPTION_LOG = os.path.join(TELEM_DIR, "memory_adoption.jsonl")
FIRES_LOG = os.path.join(TELEM_DIR, "hook_fires.jsonl")

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_TYPE_RE = re.compile(r"^\s*type:\s*(\S+)\s*$", re.MULTILINE)
_LAST_REINFORCED_RE = re.compile(r"^last_reinforced:\s*(\S+)\s*$", re.MULTILINE)
_ENFORCEMENT_RE = re.compile(r"^\s*enforcement:\s*(\S+)\s*$", re.MULTILINE)

# Staleness-exempt types: lookup tables / external pointers. A reference memory
# that is "never read" is NOT a deprecation candidate — it is a table you grep
# when you need it (e.g. 特定 ID のキャッシュ表). 退場ロジックに乗せてはならない。
# (caveat: machine-1 axis-independent retention)
STALENESS_EXEMPT_TYPES = frozenset({"reference"})


def is_load_bearing(mtype: str, enforcement: str | None, fired: int) -> bool:
    """True if a memory must NOT be listed as a deprecation candidate.

    Three reasons a low read/adopt score is NOT disuse:
      - reference type : lookup table, grepped on demand (existing exemption).
      - hook-enforced  : a hook carries the rule, so the guidance fires at the
        tool layer and never surfaces as a response-level 'adoption'. Prevention
        rules (do-not-address-by-name, 特定の命名規約ルール) read as adopted=0
        precisely *because* they worked. Mislabeling them 降格候補 invites the
        exact false-deletion this report exists to avoid.
      - fired in-window: a hook actually fired (rule_id==slug) → demonstrably live.
    """
    if mtype in STALENESS_EXEMPT_TYPES:
        return True
    if enforcement and "hook" in enforcement.lower():
        return True
    if fired:
        return True
    return False


def _load_jsonl(path: str) -> list[dict]:
    out: list[dict] = []
    if not os.path.exists(path):
        return out
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _in_window(ts: str, cutoff: datetime) -> bool:
    """True if ts is missing/unparseable (count it) or >= cutoff."""
    if not ts:
        return True
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except Exception:
        return True
    return t >= cutoff


def _read_frontmatter(path: str) -> tuple[str, str | None, str | None]:
    """Return (type, last_reinforced, enforcement) from a memory's frontmatter.
    type defaults to 'unknown'; last_reinforced/enforcement are None if absent."""
    try:
        with open(path, errors="replace") as f:
            text = f.read()
    except OSError:
        return "unknown", None, None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return "unknown", None, None
    body = m.group(1)
    tm = _TYPE_RE.search(body)
    lr = _LAST_REINFORCED_RE.search(body)
    en = _ENFORCEMENT_RE.search(body)
    return ((tm.group(1) if tm else "unknown"),
            (lr.group(1) if lr else None),
            (en.group(1) if en else None))


def _inventory() -> dict[str, dict]:
    """All active memory slugs → {type, last_reinforced}. Excludes MEMORY.md
    and the archived/ tomb (mirrors _memory_touch.is_memory_path)."""
    inv: dict[str, dict] = {}
    for p in glob.glob(os.path.join(MEMORY_DIR, "*.md")):
        base = os.path.basename(p)
        if base == "MEMORY.md":
            continue
        slug = base[:-3]
        mtype, last_reinf, enforcement = _read_frontmatter(p)
        inv[slug] = {"type": mtype, "last_reinforced": last_reinf,
                     "enforcement": enforcement}
    return inv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--cap", type=int, default=None,
                    help="soft cap; only when set do we emit over-cap eviction-review candidates")
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    inv = _inventory()

    # read axis (touch = Read-tool open). status field is irrelevant; opening counts.
    reads: Counter = Counter()
    for r in _load_jsonl(TOUCHES_LOG):
        if _in_window(r.get("ts", ""), cutoff):
            slug = r.get("memory")
            if slug:
                reads[slug] += 1

    # adopted axis (the 機構2 signal). split adopted vs surfaced_unused.
    adoption_recs = _load_jsonl(ADOPTION_LOG)
    adopted: Counter = Counter()
    surfaced_unused: Counter = Counter()
    for r in adoption_recs:
        if not _in_window(r.get("ts", ""), cutoff):
            continue
        slug = r.get("memory")
        if not slug:
            continue
        if r.get("verdict") == "adopted":
            adopted[slug] += 1
        elif r.get("verdict") == "surfaced_unused":
            surfaced_unused[slug] += 1

    # enforced axis (a hook actually fired enforcing this memory's rule).
    fired: Counter = Counter()
    for r in _load_jsonl(FIRES_LOG):
        if _in_window(r.get("ts", ""), cutoff):
            rid = r.get("rule_id")
            if rid:
                fired[rid] += 1

    n = len(inv)
    adoption_log_live = bool(adoption_recs)

    print(f"=== Memory adoption report (last {args.days}d) ===")
    print(f"active memories (inventory):  {n}")
    print(f"  read   (touched ≥1):        {sum(1 for s in inv if reads.get(s))}")
    print(f"  adopted (verdict adopted):  {sum(1 for s in inv if adopted.get(s))}")
    print(f"  enforced (hook fired):      {sum(1 for s in inv if fired.get(s))}")
    print(f"adoption-log events:          {len(adoption_recs)} "
          f"({'LIVE' if adoption_log_live else 'EMPTY — 機構2 signal not flowing yet'})")
    print()

    # NEVER-READ (and not recall-via-reminder-detectable). Honest caveat: a memory
    # injected only via <system-reminder> is never Read, so 'never read' is an
    # UPPER bound on disuse, not proof. reference-type is staleness-exempt.
    never_read = sorted(
        s for s in inv
        if not reads.get(s)
        and not is_load_bearing(inv[s]["type"], inv[s]["enforcement"], fired.get(s, 0))
    )
    exempt_never_read = sorted(
        s for s in inv
        if not reads.get(s)
        and is_load_bearing(inv[s]["type"], inv[s]["enforcement"], fired.get(s, 0))
    )
    if never_read:
        print(f"-- NEVER-READ ({len(never_read)}) — 0 Read-opens in {args.days}d, "
              "non-reference (caveat: may still be recalled via <system-reminder>):")
        for s in never_read:
            print(f"   · {s}  [{inv[s]['type']}]")
        print()
    if exempt_never_read:
        print(f"-- never-read but LOAD-BEARING ({len(exempt_never_read)}) — "
              "reference lookup tables / hook-enforced / hook-fired, NOT deprecation candidates:")
        for s in exempt_never_read:
            enf = inv[s]["enforcement"]
            why = inv[s]["type"] if inv[s]["type"] in STALENESS_EXEMPT_TYPES else (
                f"enforcement:{enf}" if enf and "hook" in enf.lower() else "hook-fired")
            print(f"   · {s}  [{why}]")
        print()

    # READ-BUT-NEVER-ADOPTED — the Copilot signal (hit but never adopted → demote).
    # Only meaningful once adoption-log has data.
    if adoption_log_live:
        read_not_adopted = sorted(
            s for s in inv
            if reads.get(s) and not adopted.get(s)
            and not is_load_bearing(inv[s]["type"], inv[s]["enforcement"], fired.get(s, 0))
        )
        if read_not_adopted:
            print(f"-- READ-BUT-NEVER-ADOPTED ({len(read_not_adopted)}) — recalled/read "
                  "but never shaped a response (Copilot 型: ヒットするが採用されない → 降格候補):")
            for s in read_not_adopted:
                su = surfaced_unused.get(s, 0)
                print(f"   {reads[s]:3d}r/{su:2d}u  {s}  [{inv[s]['type']}]")
            print()
    else:
        print("-- READ-BUT-NEVER-ADOPTED: (採用ログが空のため判定不能。"
              "detect_acceptance_signal の closure-reflection が memory_adoption.jsonl を"
              "書き始めると有効化される)\n")

    # CAP — opt-in only. eviction-review candidates = lowest signal on every axis.
    if args.cap is not None and n > args.cap:
        zero_signal = sorted(
            s for s in inv
            if not reads.get(s) and not adopted.get(s)
            and not is_load_bearing(inv[s]["type"], inv[s]["enforcement"], fired.get(s, 0))
        )
        over = n - args.cap
        print(f"-- OVER CAP: {n} > {args.cap} (over by {over}). "
              f"eviction-REVIEW candidates (0 read / 0 adopted / 0 fire, non-reference) "
              f"= {len(zero_signal)} — 提案のみ、archive は人間承認:")
        for s in zero_signal[: max(over, len(zero_signal))]:
            print(f"   · {s}  [{inv[s]['type']}]")
        print()

    # greppable summary (定期実行での集計に利用)
    print(f"MEMORY_ADOPTION_SUMMARY active={n} "
          f"read={sum(1 for s in inv if reads.get(s))} "
          f"adopted={sum(1 for s in inv if adopted.get(s))} "
          f"enforced={sum(1 for s in inv if fired.get(s))} "
          f"never_read={len(never_read)} "
          f"adoption_log={'live' if adoption_log_live else 'empty'} "
          f"days={args.days}")


if __name__ == "__main__":
    main()
