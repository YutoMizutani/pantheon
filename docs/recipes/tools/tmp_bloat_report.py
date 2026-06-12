#!/usr/bin/env python3
# RECIPE (非配線): 原環境の自己改善ループが育てた規範の実体。
# 採用条件 = tmp/ 肥大による問題を自環境で観測したこと。
# 採用手順と再校正の注意は ../README.md (docs/recipes/README.md)。
# 採用時は本ファイルを heaven/tools/ へ配置し、定期実行 (cron 等) に配線する。
"""tmp bloat report — projects/**/tmp の肥大を検出する。

Deterministic — NO LLM. Read-only. 定期実行 (cron 等) に配線する。

「tmp は削除前提」なのに消す主体が存在しなかった欠陥 (原環境実測: 数十〜百 GB 規模の蓄積で発覚)
への backstop。閾値超過の tmp を greppable に報告し、
`🧹 [tmp-cleanup]` マーカーで次セッションに片付けを促す。

retention 規約: .claude/rules/common/tmp-retention.md
"""
import argparse
import subprocess
import sys
from pathlib import Path

import os

REPO_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))  # location-independent (script may live under heaven/tools/)

# user が「元データ参照として保持」と明示したパス (tmp-retention.md と同期)
ALLOWLIST: list[str] = [
    # 例: "projects/<name>/tmp/<保持する元データ>",  # 理由を併記。削除は user 確認必須
]

DEFAULT_THRESHOLD_GB = 3.0


def du_kb(path: Path) -> int:
    try:
        out = subprocess.run(
            ["du", "-sk", str(path)], capture_output=True, text=True, timeout=600
        ).stdout
        return int(out.split()[0])
    except Exception:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold-gb", type=float, default=DEFAULT_THRESHOLD_GB,
                    help="allowlist 除外後にこのサイズを超えた tmp/ を offender とする")
    args = ap.parse_args()

    tmp_dirs = sorted(
        d for d in REPO_ROOT.glob("projects/*/tmp") if d.is_dir()
    ) + sorted(
        d for d in REPO_ROOT.glob("projects/*/apps/*/tmp") if d.is_dir()
    )

    offenders = []
    total_kb = 0
    for tmp in tmp_dirs:
        size_kb = du_kb(tmp)
        allow_kb = sum(
            du_kb(REPO_ROOT / a) for a in ALLOWLIST
            if (REPO_ROOT / a).is_relative_to(tmp) and (REPO_ROOT / a).exists()
        )
        effective_kb = max(0, size_kb - allow_kb)
        total_kb += effective_kb
        gb = effective_kb / (1024 ** 2)
        if gb > args.threshold_gb:
            offenders.append((tmp, gb))
            rel = tmp.relative_to(REPO_ROOT)
            print(f"OFFENDER {rel} {gb:.1f}G (allowlist 除外後)")

    print(f"TMP_BLOAT offenders={len(offenders)} "
          f"effective_total={total_kb / 1024 ** 2:.1f}G "
          f"threshold={args.threshold_gb}G scanned={len(tmp_dirs)}")
    if offenders:
        print("🧹 [tmp-cleanup] 閾値超過の tmp あり — "
              ".claude/rules/common/tmp-retention.md に従い片付けること")
    return 0


if __name__ == "__main__":
    sys.exit(main())
