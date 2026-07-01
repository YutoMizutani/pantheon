#!/usr/bin/env python3
"""Telemetry consumer: GC for the PROMOTION layer (CLAUDE.local.md 常時原則).

自己改善ループの棚卸し機構の一部。memory 層には birth⇄death の対称機構がある
(skill_gc / rule-auditor の cold 判定 / on-demand 分離) が、圧力の逃がし弁である
**昇格層 (always-loaded な CLAUDE.local.md 常時原則)** には死滅機構が無く単調増加する
— これは毎ターン全文ロードされる最高コストの層なので、birth (昇格) に対する death
(降格候補の surface) を与えて対称化する。本ツールはその surface 側 (deterministic・
read-only・判断は user)。

## 測れないものを正直に測る (設計上の難所)

常時原則は always-loaded ゆえ「surfaced イベント」が無い — memory のように
「recall されたが採用されなかった」を直接は測れない。本ツールが使うのは **弱い proxy**:
各節が `[[source_memory]]` で参照する memory の adoption / touch / 関連 hook 発火。
これには既知の偽陰性がある — **昇格後は on-demand 側の source memory が読まれなくなり
(常時層の写しが仕事をする)、adoption が 0 に落ちても原則自体は効いている**ことがある。
よって本ツールは「cold = 無効」と verdict しない。出すのは
**「直近 N 日に独立した効きの証拠が無い節 = その always-loaded コストに見合うか human に
問う候補」** であって、無効の証明ではない。判断は user (rule-auditor と同じ surface-only)。

## 使い方
    python3 heaven/tools/promotion_layer_report.py [--days 30] [--file <path>]
        --days : adoption / touch / fire の集計ウィンドウ
        --file : 監査対象 (既定: $CLAUDE_PROJECT_DIR/CLAUDE.local.md)
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
_SLUG = re.sub(r"[^A-Za-z0-9]", "-", _PROJECT_DIR)
# FRAME_TELEMETRY_DIR: same hermetic-test seam as the hooks (_paths.py). Lets the
# regression test point telemetry at a tmp dir instead of the operator's real logs.
TELEM_DIR = os.environ.get("FRAME_TELEMETRY_DIR") or os.path.join(
    HOME, ".claude/projects", _SLUG, "telemetry")
ADOPTION_LOG = os.path.join(TELEM_DIR, "memory_adoption.jsonl")
TOUCHES_LOG = os.path.join(TELEM_DIR, "memory_touches.jsonl")
FIRES_LOG = os.path.join(TELEM_DIR, "hook_fires.jsonl")
DEFAULT_FILE = os.path.join(_PROJECT_DIR, "CLAUDE.local.md")

_WIKILINK_RE = re.compile(r"\[\[([A-Za-z0-9_-]+)\]\]")
# A section is an auditable always-loaded NORM if its heading or body marks it as
# 常時 / 絶対命令 / 派生原則 / フロー, or it cites source memories. Pure index
# sections (既存プロジェクト一覧 / 入力解釈 / 応答スタイル) are skipped.
_NORM_HEADING_RE = re.compile(r"(常時|絶対命令|派生原則|フロー|原則)")
_DATE_RE = re.compile(r"(20\d\d-\d\d-\d\d)")


def _norm(slug: str) -> str:
    """Normalize a wikilink/slug so hyphen and underscore aliases match the file
    stem (memory files use underscores; CLAUDE.local.md mixes both)."""
    return re.sub(r"[-]", "_", slug.strip()).lower()


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _load_jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def _parse_ts(rec: dict):
    ts = rec.get("ts", "")
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _adoption_counts(days: int):
    """Per-slug {adopted, surfaced_unused} within window."""
    cut = _cutoff(days)
    counts = defaultdict(lambda: {"adopted": 0, "surfaced_unused": 0})
    for rec in _load_jsonl(ADOPTION_LOG):
        ts = _parse_ts(rec)
        if ts and ts < cut:
            continue
        slug = _norm(str(rec.get("memory", "")))
        verdict = rec.get("verdict", "")
        if slug and verdict in ("adopted", "surfaced_unused"):
            counts[slug][verdict] += 1
    return counts


def _touch_counts(days: int):
    cut = _cutoff(days)
    counts = defaultdict(int)
    for rec in _load_jsonl(TOUCHES_LOG):
        ts = _parse_ts(rec)
        if ts and ts < cut:
            continue
        slug = _norm(str(rec.get("memory", rec.get("slug", ""))))
        if slug:
            counts[slug] += 1
    return counts


def _fire_counts(days: int):
    """Per rule_id hook fires within window (rule_id mirrors a memory slug)."""
    cut = _cutoff(days)
    counts = defaultdict(int)
    for rec in _load_jsonl(FIRES_LOG):
        ts = _parse_ts(rec)
        if ts and ts < cut:
            continue
        rid = _norm(str(rec.get("rule_id", "")))
        if rid:
            counts[rid] += 1
    return counts


def parse_sections(text: str):
    """Yield (heading, body, [source_slugs], promo_date|None) for each `## `/`### `
    section that is an auditable always-loaded norm."""
    lines = text.splitlines()
    idxs = [i for i, ln in enumerate(lines) if re.match(r"^#{2,3}\s", ln)]
    idxs.append(len(lines))
    for k in range(len(idxs) - 1):
        start, end = idxs[k], idxs[k + 1]
        heading = re.sub(r"^#{2,3}\s+", "", lines[start]).strip()
        body = "\n".join(lines[start:end])
        slugs = sorted({_norm(m) for m in _WIKILINK_RE.findall(body)})
        is_norm = bool(_NORM_HEADING_RE.search(heading)) or bool(slugs)
        if not is_norm:
            continue
        dates = _DATE_RE.findall(body)
        promo_date = max(dates) if dates else None
        yield heading, body, slugs, promo_date


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--file", default=DEFAULT_FILE)
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"PROMOTION_LAYER_SUMMARY file_missing={args.file}")
        return 0

    text = open(args.file, encoding="utf-8", errors="ignore").read()
    adopt = _adoption_counts(args.days)
    touch = _touch_counts(args.days)
    fires = _fire_counts(args.days)

    sections = list(parse_sections(text))
    rows = []
    for heading, body, slugs, promo_date in sections:
        a = sum(adopt[s]["adopted"] for s in slugs)
        u = sum(adopt[s]["surfaced_unused"] for s in slugs)
        t = sum(touch[s] for s in slugs)
        f = sum(fires[s] for s in slugs)
        signal = a + t + f  # any independent evidence it mattered
        rows.append({"heading": heading, "slugs": slugs, "promo_date": promo_date,
                     "adopted": a, "surfaced_unused": u, "touch": t, "fire": f,
                     "signal": signal})

    cold = [r for r in rows if r["signal"] == 0 and r["slugs"]]
    no_source = [r for r in rows if not r["slugs"]]

    print(f"PROMOTION_LAYER_SUMMARY sections={len(rows)} "
          f"zero_signal={len(cold)} window_days={args.days}")
    print("# proxy 注意: 常時原則は surfaced イベントが無いため、signal=0 は『無効の証明』では")
    print("#   ない (昇格後は on-demand 側 source memory が読まれず adoption が 0 に落ちうる)。")
    print("#   出力は『独立した効きの証拠が直近に無い節 = always-loaded コストに見合うか human")
    print("#   に問う候補』。降格/削除は user 判断 (rule-auditor と同じ surface-only)。")
    print()

    print("## 全節の signal (adopted / surfaced_unused / touch / fire)")
    for r in sorted(rows, key=lambda x: x["signal"]):
        tag = "  ← zero-signal" if r["signal"] == 0 and r["slugs"] else ""
        src = f" src={len(r['slugs'])}" if r["slugs"] else " src=0(リンク無)"
        date = f" promo~{r['promo_date']}" if r["promo_date"] else ""
        print(f"- [{r['adopted']}/{r['surfaced_unused']}/{r['touch']}/{r['fire']}]"
              f"{src}{date}  {r['heading']}{tag}")

    if cold:
        print()
        print(f"## 降格候補 (zero-signal・要 human 判断) — {len(cold)} 件")
        for r in cold:
            print(f"- 「{r['heading']}」: 直近{args.days}日 adopted/touch/fire すべて 0。"
                  f"source=[{', '.join(r['slugs']) or '-'}]。"
                  f"判断: on-demand へ降格を提案 / 様子見 (proxy 偽陰性の可能性・promo_date 確認)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
