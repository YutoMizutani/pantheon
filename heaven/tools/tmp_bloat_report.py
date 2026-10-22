#!/usr/bin/env python3
"""tmp bloat report — projects/**/tmp の肥大を検出する。

Deterministic — NO LLM. Read-only. morning-check.sh から呼ばれる。

「tmp は削除前提」なのに消す主体が存在しなかった欠陥 (2026-06-11, 111GB 蓄積・
残容量 52GB) への backstop。閾値超過の tmp を greppable に報告し、
`🧹 [tmp-cleanup]` マーカーで次セッションに片付けを促す。

retention 規約: .claude/rules/common/tmp-retention.md
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path.home() / "Developer/llm"  # location-independent (script may live under heaven/tools/)

# user が「元データ参照として保持」と明示したパス。
# allowlist は各プロジェクトが own する — projects/<name>/.claude/rules/tmp-retention.md の
# RETAIN-ALLOWLIST ブロックに宣言し、ここで全プロジェクト分を集約する（共通規約・本ツールに
# プロジェクト固有パスを書かない＝1 パス 1 オーナー）。
RETAIN_BEGIN = "<!-- RETAIN-ALLOWLIST -->"
RETAIN_END = "<!-- /RETAIN-ALLOWLIST -->"


def load_allowlist() -> list[str]:
    """各 project の .claude/rules/tmp-retention.md の RETAIN-ALLOWLIST ブロックを集約する。"""
    paths: list[str] = []
    for f in sorted(REPO_ROOT.glob("projects/*/.claude/rules/tmp-retention.md")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        in_block = False
        for ln in lines:
            s = ln.strip()
            if s == RETAIN_BEGIN:
                in_block = True
            elif s == RETAIN_END:
                in_block = False
            elif in_block and s and not s.startswith("#"):
                paths.append(s)
    return paths

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

    allowlist = load_allowlist()

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
            du_kb(REPO_ROOT / a) for a in allowlist
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
