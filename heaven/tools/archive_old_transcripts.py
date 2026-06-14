#!/usr/bin/env python3
"""archive old transcripts — N日超の Claude session jsonl を gzip して projects-archive へ退避。

Deterministic — NO LLM. launchd (週次) から --apply で呼ばれる。未指定は dry-run。

背景: cleanupPeriodDays は「削除」しかできずアーカイブ機能が無い (公式 settings.md 確認:
"Session files older than this period are deleted at startup")。半年(180d)超のログを消さず
gzip 保存する要件のため、cleanup 削除対象の ~/.claude/projects/ 外 (~/.claude/projects-archive/)
へ mtime>N日 の jsonl を move する。projects/ 外なので CLI は二度と触らない。
cleanupPeriodDays=200 を安全レールに設定済 — archiver(180d) が先に走るので cleanup 削除は起きない。

対象は再帰: 親 session の jsonl だけでなく <slug>/<session>/subagents/**.jsonl ・
.../subagents/workflows/wf_*/*.jsonl (subagent / workflow 転写) も含む。これらが全体の ~80%
(実測 3632 件中 ~3000 が nested) なので flat glob では大半を取りこぼす。相対パスを保って退避。

設計メモ:
- mtime>N日 を対象 → アクティブ書き込み中ファイル (mtime が新しい) は構造的に除外。
- os.walk(followlinks=False) で symlink (memory→heaven 等) を辿らない。
- move は「dest を gzip 生成 → 実在&非空を verify → その後に source を unlink」。
  検証前に source を消さない ([[feedback_verify_move_landed_before_rm]] / mv 失敗は rm の前で HARD STOP)。
- 冪等: dest が既存ならスキップ。gzip 保存で zgrep 可 → 自己改善ループの transcript 想起にも使える。
- env 上書き (ARCHIVE_PROJECTS_DIR / ARCHIVE_DEST_DIR) は fixture テスト専用。本番は既定パス。

この docstring が retention アーカイブ設計の SSoT。閾値変更時はここと launchd plist を両方更新。
"""
import argparse
import gzip
import os
import shutil
import sys
import time
from pathlib import Path

PROJECTS = Path(os.environ.get("ARCHIVE_PROJECTS_DIR") or (Path.home() / ".claude/projects"))
ARCHIVE = Path(os.environ.get("ARCHIVE_DEST_DIR") or (Path.home() / ".claude/projects-archive"))
LOG = ARCHIVE / "archive.log"
DEFAULT_DAYS = 180


def iter_old_jsonl(days: int):
    """PROJECTS 配下の *.jsonl を再帰走査し、mtime が days 日より古いものを yield。

    os.walk(followlinks=False) なので memory→heaven 等の symlink は辿らない。
    """
    cutoff = time.time() - days * 86400
    if not PROJECTS.is_dir():
        return
    for root, _dirs, files in os.walk(PROJECTS, followlinks=False):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            f = Path(root) / name
            if f.is_symlink():
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    yield f
            except OSError:
                continue


def main() -> int:
    ap = argparse.ArgumentParser(
        description="archive old Claude transcripts (gzip + move out of ~/.claude/projects/)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"この日数より古い (mtime) jsonl を退避 (default {DEFAULT_DAYS})")
    ap.add_argument("--apply", action="store_true",
                    help="実際に move する。未指定は dry-run (何も変更しない)")
    args = ap.parse_args()

    moved = 0
    src_bytes = 0
    gz_bytes = 0
    errors = 0

    for f in iter_old_jsonl(args.days):
        rel = f.relative_to(PROJECTS)
        dest = ARCHIVE / rel.parent / (f.name + ".gz")
        if dest.exists():
            continue  # 冪等: 既にアーカイブ済み
        try:
            size = f.stat().st_size
        except OSError:
            continue

        if not args.apply:
            print(f"[dry-run] {rel} ({size / 1048576:.2f}MB)")
            moved += 1
            src_bytes += size
            continue

        tmp = dest.parent / (f.name + ".gz.partial")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "rb") as fin, gzip.open(tmp, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            tmp.replace(dest)
            # verify-before-rm: dest が実在し非空であることを確認してから source を消す
            if dest.exists() and dest.stat().st_size > 0:
                gz_bytes += dest.stat().st_size
                f.unlink()
                moved += 1
                src_bytes += size
            else:
                errors += 1
                sys.stderr.write(f"[archive] dest verify failed, source kept: {f}\n")
        except Exception as e:  # noqa: BLE001
            errors += 1
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            sys.stderr.write(f"[archive] error {f}: {e}\n")

    mode = "APPLY" if args.apply else "DRY-RUN"
    summary = (f"{mode} days>{args.days}: moved={moved} "
               f"src={src_bytes / 1048576:.1f}MB gz={gz_bytes / 1048576:.1f}MB errors={errors}")
    print(summary)
    if args.apply:
        try:
            ARCHIVE.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as lf:
                lf.write(f"{int(time.time())} {summary}\n")
        except OSError:
            pass
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
