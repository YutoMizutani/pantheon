#!/bin/bash
# todo-loop の定時実行の雛形を埋めて <状態>/launchd/<ラベル>.plist に書き出す。launchctl は呼ばない（登録の手順を表示するだけ）。
#
#   bash schedule.sh <プロジェクト> <HH[:MM],HH[:MM],...> [--refill-only] [--label <ラベル>] [--env KEY=VALUE ...]
#
#   --refill-only   積み足しだけの定時（TODO_LOOP_REFILL_ONLY=1。passes があれば全部の回）
#   --label         既定は local.todo-loop.<プロジェクト名>（--refill-only なら末尾に .refill）
#   --env           plist の環境変数に足す（例: --env TODO_LOOP_UNTIL=06:00 --env TODO_LOOP_END=07:00）
# 状態の置き場は TODO_LOOP_STATE（既定 <プロジェクト>/.local/todo-loop）。
set -u
P="$(cd "${1:?プロジェクトのディレクトリを渡す}" && pwd -P)" || exit 2
TIMES_ARG="${2:?時刻を HH または HH:MM のカンマ区切りで渡す（例: 0,12 / 6:30）}"
shift 2
HERE="$(cd "$(dirname "$0")" && pwd -P)"
STATE="${TODO_LOOP_STATE:-$P/.local/todo-loop}"
LABEL=""; REFILL=""; ENVS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --refill-only) REFILL=1 ;;
    --label) LABEL="${2:?}"; shift ;;
    --env) ENVS+=("${2:?}"); shift ;;
    *) echo "[schedule] 知らない引数: $1" >&2; exit 2 ;;
  esac
  shift
done
SLUG="$(basename "$P" | tr -c 'A-Za-z0-9._\n-' '-')"
[ -z "$LABEL" ] && LABEL="local.todo-loop.$SLUG${REFILL:+.refill}"
[ -n "$REFILL" ] && ENVS+=("TODO_LOOP_REFILL_ONLY=1")

xml_escape() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }

TIMES=""
IFS=',' read -r -a TS <<<"$TIMES_ARG"
for t in "${TS[@]}"; do
  t="${t// /}"
  if [[ ! "$t" =~ ^([0-9]{1,2})(:([0-9]{1,2}))?$ ]]; then echo "[schedule] 時刻が読めない: $t" >&2; exit 2; fi
  H=$((10#${BASH_REMATCH[1]})); M=$((10#${BASH_REMATCH[3]:-0}))
  if [ "$H" -gt 23 ] || [ "$M" -gt 59 ]; then echo "[schedule] 時刻の範囲外: $t" >&2; exit 2; fi
  TIMES="$TIMES    <dict><key>Hour</key><integer>$H</integer><key>Minute</key><integer>$M</integer></dict>"$'\n'
done
ENVXML=""
for kv in "${ENVS[@]+"${ENVS[@]}"}"; do
  [[ "$kv" == *=* ]] || { echo "[schedule] --env は KEY=VALUE: $kv" >&2; exit 2; }
  ENVXML="$ENVXML    <key>$(xml_escape "${kv%%=*}")</key>"$'\n'"    <string>$(xml_escape "${kv#*=}")</string>"$'\n'
done

OUTDIR="$STATE/launchd"; mkdir -p "$OUTDIR"
OUT="$OUTDIR/$LABEL.plist"
F_LABEL="$(xml_escape "$LABEL")" F_RUNNER="$(xml_escape "$HERE/run.sh")" F_PROJECT="$(xml_escape "$P")" \
F_PATH="$(xml_escape "$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")" F_HOME="$(xml_escape "$HOME")" \
F_ENV="$ENVXML" F_TIMES="$TIMES" F_LOG="$(xml_escape "$OUTDIR/$LABEL.out")" \
python3 - "$HERE/../templates/launchd.plist.tmpl" "$OUT" <<'PY'
import os, re, sys
t = open(sys.argv[1], encoding="utf-8").read()
out = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: os.environ["F_" + m.group(1)], t)
open(sys.argv[2], "w", encoding="utf-8").write(out)
PY
plutil -lint "$OUT" >/dev/null 2>&1 && LINT="plutil -lint OK" || LINT="plutil -lint で確かめられなかった（plutil が無いか、書式の誤り）"
echo "[schedule] 書き出した: $OUT（$LINT）"
echo "[schedule] 時刻: $TIMES_ARG ${REFILL:+（積み足しだけ）}"
echo "[schedule] 登録の手順（このスクリプトは登録しない。人が実行する）:"
echo "  cp \"$OUT\" ~/Library/LaunchAgents/"
echo "  launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/$LABEL.plist"
echo "  # 外すとき: launchctl bootout gui/\$(id -u)/$LABEL && rm ~/Library/LaunchAgents/$LABEL.plist"
