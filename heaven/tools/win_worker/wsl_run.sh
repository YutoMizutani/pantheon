#!/bin/bash
# win_worker の WSL 側。Mac の worker.sh が、wsl_mount.sh でリポジトリをつないだ後に wsl で呼ぶ。
#   bash <リポジトリ>/heaven/tools/win_worker/wsl_run.sh <ジョブファイル>
# ジョブファイルは bash の変数定義: MODEL / PROMPT_FILE / LOG / LIMIT / ALLOWED / DIS
# リポジトリは Mac のものを同じパスにつないだもの。このファイルの置き場から求める。
set -u
JOB="${1:?ジョブファイル}"
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export TMPDIR=/tmp
# shellcheck disable=SC1090
. "$JOB"
cd "$REPO" || exit 91
echo "[win-worker/wsl] $(date '+%F %T') start model=$MODEL limit=${LIMIT}s host=$(hostname)" >> "$LOG"
timeout --signal=TERM --kill-after=30 "$LIMIT" env -u ANTHROPIC_API_KEY claude -p --model "$MODEL" --permission-mode acceptEdits \
  --allowedTools "$ALLOWED" --disallowedTools "$DIS" --output-format text "$(cat "$PROMPT_FILE")" >> "$LOG" 2>&1 </dev/null
RC=$?
[ "$RC" -eq 124 ] && echo "[todo-loop] ${LIMIT}s で打ち切り" >> "$LOG"
echo "[win-worker/wsl] $(date '+%F %T') end rc=$RC" >> "$LOG"
exit "$RC"
