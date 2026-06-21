#!/usr/bin/env bash
# relink-local.sh — `.local/` に集約したローカル層を harness 期待パスへ symlink し直す。
#
# 用途: 別マシンへ移植したとき、リポジトリを clone → 旧環境の `.local/` を配置 →
#       本スクリプトを 1 回実行すると、harness が期待する固定パス
#       (root の CLAUDE.local.md 等) への symlink を全て再生成する。
#       symlink はパス文字列を保持するだけなので、移植先では必ず張り直しが要る。
#
# 冪等: 既存 symlink は ln -sfn で原子的に張り直す。期待パスに「実体ファイル」が
#       居る場合は壊さず SKIP して警告する (先に .local/ へ退避してから再実行)。
#
# frame 層: 特定の hook ファイル名はハードコードせず `.local/hooks/*` を走査する。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # heaven/tools/ → repo root
cd "$ROOT"

[[ -d .local ]] || { echo "ERROR: $ROOT/.local が無い。移植元の .local/ を配置してから実行"; exit 1; }

# link <symlink を置くパス> <symlink の張り先 (symlink のあるディレクトリからの相対)>
link() {
  local lp="$1" tgt="$2"
  if [[ -e "$lp" && ! -L "$lp" ]]; then
    echo "SKIP (実体が存在): $lp — 先に .local/ へ退避してから再実行"
    return
  fi
  mkdir -p "$(dirname "$lp")"
  ln -sfn "$tgt" "$lp"
  echo "linked: $lp -> $tgt"
}

# --- Pantheon 固定構造 (frame が定める期待パス) ---
[[ -e .local/CLAUDE.local.md     ]] && link CLAUDE.local.md             .local/CLAUDE.local.md
[[ -e .local/PROJECTS.md         ]] && link PROJECTS.md                 .local/PROJECTS.md
[[ -e .local/settings.local.json ]] && link .claude/settings.local.json ../.local/settings.local.json

# --- local hook 群 (ユーザー固有・汎用走査。frame の *.example / README は触らない) ---
if [[ -d .local/hooks ]]; then
  for f in .local/hooks/*; do
    [[ -e "$f" ]] || continue
    link ".claude/hooks/local/$(basename "$f")" "../../../.local/hooks/$(basename "$f")"
  done
fi

# --- memory: SSoT は heaven/memory。.local からの可視 symlink を張り直す ---
[[ -e heaven/memory ]] && link .local/memory ../heaven/memory

# --- memory の ~/.claude 側 symlink (slug 依存・cross-boundary) ---
slug="$(printf '%s' "$ROOT" | sed 's/[^a-zA-Z0-9]/-/g')"
cdir="$HOME/.claude/projects/$slug"
if [[ -d "$cdir" ]]; then
  ln -sfn "$ROOT/heaven/memory" "$cdir/memory"
  echo "linked: $cdir/memory -> $ROOT/heaven/memory"
else
  echo "NOTE: $cdir が無い — ~/.claude の memory link は初回 setup 完了後に再実行のこと"
fi

echo "done."
