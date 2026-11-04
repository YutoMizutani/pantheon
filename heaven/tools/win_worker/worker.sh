#!/bin/bash
# win_worker の Mac 側。<プロジェクト>/TODO.md のストーリーを 1 件ずつ選んで Windows 機の WSL に渡し、
# 無人の claude -p で 1 SP = 1 セッション実行させる。Mac の todo-loop runner と並べて動かすための係。
#
#   WIN_WORKER_WINRUN=<winrun.sh のパス> bash worker.sh <プロジェクトのディレクトリ>
#   WIN_WORKER_UNTIL=19:00 ... bash worker.sh <dir>   # UNTIL より前なら次々に始める（無ければ 1 件で終わる）
#   WIN_WORKER_DRY=1 ... bash worker.sh <dir>         # 次に選ぶストーリーだけ表示
#   WIN_WORKER_SLOTS=5 ... bash worker.sh <dir>       # Windows で同時に 5 本（枠ごとに自分を起動し直す。既定 1）
#
#   bash worker.sh <dir> settle <ID> [引き継ぎに足す文]                   # 走り終えた回の判定だけを後から行う
#
# 選び方: 本文に `- 実行先: win` の行があり、todo / approved で依存が全部済み、引き継ぎに win-unsuitable が無いもの
#   （doing は Mac の runner が続けるので取らない）。Python などの環境の違いがあるので、Windows に向く物（大きな計算・
#   Mac の資源では回らない物・環境に依らない調査）にだけ人か積む側が印を付ける。WIN_WORKER_ANY=1 なら印を問わない。
#   状態 win-todo は Windows 専用の積み方（Mac の runner は todo / approved / doing しか選ばないので取らない）。印は要らない。
#   印があっても、本文に commit・.venv・mise exec・launchctl・osascript・winrun が出る物、CARGO_TARGET_DIR=/tmp の無い cargo は取らない。
#   選んだら見出しの状態を doing-win にする（Mac の runner はこれを選ばない）。
# 判定は Mac の TODO.md で行う: 終了後も doing-win のままなら todo に戻し引き継ぎを書く。利用上限なら todo に戻すだけ。
# 成果物はリポジトリ（Mac の物を WSL の同じパスに sshfs でつないだもの）に直接書かれる。
# 環境変数: WIN_WORKER_WINRUN（必須。PowerShell を Windows で実行する道具）/ WIN_WORKER_SCP_KEY（scp の鍵）/
#   WIN_WORKER_WIN（scp 先の user@host）/ WIN_WORKER_MAC（WSL から見た Mac の user@host。既定は en0 の IP）/
#   WIN_WORKER_DISTRO（既定 Ubuntu）/ WIN_WORKER_LIMIT_SEC（既定 3600）/ WIN_WORKER_UNTIL / WIN_WORKER_DRY / WIN_WORKER_SLOTS
# 複数の枠・Mac の runner と TODO.md を取り合うので、選ぶ・判定するの読み書きは $WDIR/todo.lock の flock で 1 本ずつ通す。

set -u
P="$(cd "${1:?プロジェクトのディレクトリを渡す}" && pwd -P)" || exit 2
HERE="$(cd "$(dirname "$0")" && pwd -P)"
ROOT="$(git -C "$P" rev-parse --show-toplevel 2>/dev/null || echo "$P")"
LOOP="$ROOT/plugins/todo-loop/skills/todo-loop/scripts"
TODO="$P/TODO.md"
SLOT="${WIN_WORKER_SLOT:-1}"; SLOTS="${WIN_WORKER_SLOTS:-1}"
STATE="$P/.local/todo-loop"; RUNS="$STATE/runs"; WDIR="$STATE/win"; LOCK="$WDIR/running.$SLOT.pid"
WINRUN="${WIN_WORKER_WINRUN:-}"; WIN="${WIN_WORKER_WIN:-}"; KEY="${WIN_WORKER_SCP_KEY:-}"
MAC="${WIN_WORKER_MAC:-$(whoami)@$(ipconfig getifaddr en0)}"
DISTRO="${WIN_WORKER_DISTRO:-Ubuntu}"
LIMIT="${WIN_WORKER_LIMIT_SEC:-3600}"
REL="${P#$ROOT/}"
mkdir -p "$RUNS" "$WDIR"
[ -f "$TODO" ] || { echo "[win-worker] $TODO が無い" >&2; exit 2; }

today_at() { [ -n "$1" ] || return 0; local t; t="$(date -j -f "%Y-%m-%d %H:%M" "$(date +%Y-%m-%d) $1" +%s)" || return 1; [ "$t" -le "$(date +%s)" ] && t=$(( t + 86400 )); echo "$t"; }
UNTIL_TS="$(today_at "${WIN_WORKER_UNTIL:-}")"

# 選んで doing-win にする（読んで書くまでを 1 回の python で行う）。stdout: ID|モデル|状態|題名。無ければ exit 3
claim_story() {
  python3 - "$TODO" "${WIN_WORKER_DRY:-}" "$WDIR/todo.lock" "${WIN_WORKER_ANY:-}" <<'PY'
import fcntl, re, sys
path, dry, lock, any_ = sys.argv[1:5]
fcntl.flock(open(lock, "w"), fcntl.LOCK_EX)
text = open(path, encoding="utf-8").read()
head = re.compile(r"^## (S-\d+) — (.+?) \| (調査|実装) \| (fable|opus) \| ([\w-]+)\s*$", re.M)
stories = []
for m in head.finditer(text):
    nxt = head.search(text, m.end())
    body = text[m.end(): nxt.start() if nxt else len(text)]
    dep = re.search(r"^- 依存:\s*(.*)$", body, re.M)
    deps = re.findall(r"S-\d+", dep.group(1)) if dep and "なし" not in dep.group(1) else []
    # 印があっても、Windows で完了できない物は取らない（commit・Mac の venv・共有の cargo / mise が要る物）
    # cargo は、組み立て先を WSL の /tmp に分けると本文に書いた物（CARGO_TARGET_DIR=/tmp）だけ通す
    cargo_ng = "cargo" in body and "CARGO_TARGET_DIR=/tmp" not in body
    mac_only = cargo_ng or re.search(r"commit|\.venv|-venv|mise exec|launchctl|osascript|winrun", body)
    marked = any_ or m.group(5) == "win-todo" or re.search(r"^- 実行先:\s*win\b", body, re.M)
    ng = "win-unsuitable" in body or bool(mac_only) or not marked
    stories.append((m, deps, ng))
done = {m.group(1) for m, _, _ in stories if m.group(5) in ("done", "awaiting-approval", "approved")}
pick = next((m for m, deps, ng in stories
             if m.group(5) in ("todo", "approved", "win-todo") and not ng and all(d in done for d in deps)), None)
if pick is None:
    sys.exit(3)
if not dry:
    line = text[pick.start():pick.end()]
    text = text[:pick.start()] + line.rsplit("|", 1)[0] + "| doing-win" + text[pick.end():]
    open(path, "w", encoding="utf-8").write(text)
nxt = head.search(text, pick.end())
cargo_ok = "CARGO_TARGET_DIR=/tmp" in text[pick.end(): nxt.start() if nxt else len(text)]
print("|".join([pick.group(1), pick.group(4), pick.group(5), pick.group(2), "cargo" if cargo_ok else ""]))
PY
}

# 終了後の判定。doing-win のままなら元の状態（$3。既定 todo、win-todo の物は win-todo）に戻し、$2 を引き継ぎに足す。stdout: 新状態
settle() {
  python3 - "$TODO" "$1" "$2" "$WDIR/todo.lock" "${3:-todo}" <<'PY'
import fcntl, re, sys
path, sid, note, lock, back = sys.argv[1:6]
fcntl.flock(open(lock, "w"), fcntl.LOCK_EX)
text = open(path, encoding="utf-8").read()
m = re.search(rf"^## {re.escape(sid)} — .+? \| ([\w-]+)\s*$", text, re.M)
if not m:
    print("missing"); sys.exit(0)
if m.group(1) != "doing-win":
    print(m.group(1)); sys.exit(0)
line = m.group(0)
nxt = re.compile(r"^## S-\d+ — ", re.M).search(text, m.end())
end = nxt.start() if nxt else len(text)
block = text[m.end():end]
if note:
    if re.search(r"^- 引き継ぎ:", block, re.M):
        block = re.sub(r"^- 引き継ぎ:.*$", lambda mm: mm.group(0).rstrip() + " " + note, block, count=1, flags=re.M)
    else:
        block = "\n- 引き継ぎ: " + note + block
text = text[:m.start()] + line.rsplit("|", 1)[0] + "| " + back + block + text[end:]
open(path, "w", encoding="utf-8").write(text)
print(back)
PY
}

mount_repo() {
  scp -q -i "$KEY" "$HERE/wsl_mount.sh" "$WIN:win-worker-mount.sh" || return 1
  "$WINRUN" "wsl -d $DISTRO --cd \$env:USERPROFILE -e bash win-worker-mount.sh $MAC $ROOT" 2>&1 | LC_ALL=C tr -d '\000' | grep -q MOUNT-OK
}

DIS="Bash(git commit:*),Bash(git push:*),Bash(git tag:*),Bash(gh:*),Bash(launchctl:*),Bash(crontab:*),Bash(sudo:*),Bash(twine:*),Bash(cargo:*),Bash(npm publish:*),mcp__discord-mcp__post_message"
ALLOWED="Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Agent,Workflow,Skill,Bash"

run_one() {  # 0=実行した / 1=実行できる物が無い / 2=利用上限
  local sel; sel="$(claim_story)" || return 1
  local ID MODEL ST TITLE CARGO; IFS='|' read -r ID MODEL ST TITLE CARGO <<<"$sel"
  local STAMP; STAMP="$(date +%Y%m%d-%H%M%S)"
  echo "[win-worker#$SLOT] $STAMP 選択: $ID ($MODEL / $ST) $TITLE"
  [ -n "${WIN_WORKER_DRY:-}" ] && return 0
  local LOG="$RUNS/${STAMP}_${ID}_win${SLOT}.log" PF="$WDIR/${STAMP}_${ID}.prompt" JOB="$WDIR/${STAMP}_${ID}.job"
  local BACK=todo; [ "$ST" = win-todo ] && BACK=win-todo
  local DIS_ONE="$DIS"; [ "$ST" = approved ] && DIS_ONE="Bash(sudo:*),Bash(git commit:*),Bash(cargo:*)"
  # 選ぶ側が通した cargo ストーリー（組み立て先を /tmp に分ける）は cargo を使わせる。入れる・公開するは禁止のまま
  [ "$CARGO" = cargo ] && DIS_ONE="${DIS_ONE//Bash(cargo:*)/Bash(cargo install:*),Bash(cargo publish:*)}"
  {
    printf 'ストーリー ID: %s\n現在の状態: %s（見出しは runner が doing-win にした）\n開始時刻: %s\n打ち切り: 開始から %s 秒\n進捗ファイル: %s\n\n' \
      "$ID" "$ST" "$(date '+%Y-%m-%d %H:%M')" "$LIMIT" "$STATE/progress/$ID.md"
    sed -e "s#{{PROJECT}}#$REL#g" "$LOOP/story-prompt.md"
    printf '\n'; cat "$HERE/win-section.md"
  } > "$PF"
  printf 'MODEL=%q\nPROMPT_FILE=%q\nLOG=%q\nLIMIT=%q\nALLOWED=%q\nDIS=%q\n' "$MODEL" "$PF" "$LOG" "$LIMIT" "$ALLOWED" "$DIS_ONE" > "$JOB"
  echo "[win-worker] start $STAMP id=$ID model=$MODEL state=$ST limit=${LIMIT}s" > "$LOG"
  local S0 RC NEW NOTE=""; S0="$(date +%s)"
  if ! mount_repo; then
    echo "[win-worker] WSL からリポジトリをつなげない" >> "$LOG"; settle "$ID" "" "$BACK" >/dev/null
    echo "[win-worker] $ID: WSL のつなぎ込みに失敗 → todo に戻した"; return 3
  fi
  "$WINRUN" "wsl -d $DISTRO -e bash $HERE/wsl_run.sh $JOB" >/dev/null 2>&1; RC=$?
  local SEC=$(( $(date +%s) - S0 ))
  if grep -qiE "hit your ([a-z0-9._-]+ )*limit|usage limit reached" "$LOG"; then
    NEW="$(settle "$ID" "" "$BACK")"; echo "$STAMP $ID $MODEL $ST -> $NEW (win, 利用上限) sec=$SEC log=$LOG" >> "$RUNS/history.log"
    echo "[win-worker] $ID: 利用上限 → $NEW"; return 2
  fi
  if grep -qiE "Failed to authenticate|OAuth session expired|Invalid API key|Please run /login|not logged in" "$LOG"; then
    # ログイン切れはストーリーの不具合ではない。引き継ぎに書かずに元の状態へ戻し、次のストーリーへ進まない
    NEW="$(settle "$ID" "" "$BACK")"; echo "$STAMP $ID $MODEL $ST -> $NEW (win, ログイン切れ) sec=$SEC log=$LOG" >> "$RUNS/history.log"
    echo "[win-worker] $ID: ログイン切れ → $NEW"; return 4
  fi
  NOTE="win-worker $LOG rc=$RC: Windows の回が状態を変えずに終了。ログを見て判断"
  NEW="$(settle "$ID" "$NOTE" "$BACK")"
  echo "$STAMP $ID $MODEL $ST -> $NEW (win) rc=$RC sec=$SEC log=$LOG" >> "$RUNS/history.log"
  echo "[win-worker] 終了: $ID $ST -> $NEW (rc=$RC, ${SEC}s)"
  return 0
}

if [ "${2:-}" = settle ]; then settle "${3:?ID}" "${4:-}" "${5:-todo}"; exit 0; fi
[ -n "$WINRUN" ] && [ -n "$WIN" ] && [ -n "$KEY" ] || [ -n "${WIN_WORKER_DRY:-}" ] || {
  echo "[win-worker] WIN_WORKER_WINRUN（winrun.sh のパス）・WIN_WORKER_WIN（scp 先）・WIN_WORKER_SCP_KEY（鍵）を渡す" >&2; exit 2; }

if [ -z "${WIN_WORKER_DRY:-}" ] && [ -z "${WIN_WORKER_SLOT:-}" ] && [ "$SLOTS" -gt 1 ]; then
  # 枠ごとに自分を起動し直す。同じ瞬間に WSL のつなぎ込みが重ならないよう 20 秒ずつずらす
  for i in $(seq 1 "$SLOTS"); do WIN_WORKER_SLOT=$i bash "$0" "$P" & sleep 20; done
  wait; exit 0
fi

if [ -z "${WIN_WORKER_DRY:-}" ]; then
  if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
    echo "[win-worker] 別の win-worker（pid $(cat "$LOCK")）が実行中のため見送り"; exit 0
  fi
  echo $$ > "$LOCK"; trap 'rm -f "$LOCK"' EXIT
fi

COUNT=0
while :; do
  if [ -n "$UNTIL_TS" ] && [ "$(date +%s)" -ge "$UNTIL_TS" ]; then echo "[win-worker] ${WIN_WORKER_UNTIL} を過ぎたので止める"; break; fi
  run_one; R=$?
  [ -n "${WIN_WORKER_DRY:-}" ] && exit 0
  case $R in
    0) COUNT=$(( COUNT + 1 )) ;;
    1) echo "[win-worker] 実行できるストーリーが無い"; break ;;
    2) echo "[win-worker] 利用上限で止める（Mac の runner の上限待ちに任せる）"; break ;;
    4) echo "[win-worker] Windows 側の Claude のログインが切れているので止める（/login の後に再開）"; break ;;
    *) echo "[win-worker] WSL に届かないので止める"; break ;;
  esac
  [ -n "$UNTIL_TS" ] || break
done
echo "[win-worker#$SLOT] 実行 $COUNT 件"
