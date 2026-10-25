#!/bin/bash
# todo-loop runner — <プロジェクト>/TODO.md のストーリーを無人の claude -p で 1 SP = 1 セッションずつ実行し、
# 実行できる物が尽きたら lens-next で積み足す（積む側も無人の 1 セッション）。
#
#   bash run.sh <プロジェクトのディレクトリ>
#   TODO_LOOP_UNTIL=14:00 TODO_LOOP_END=15:00 bash run.sh <dir>   # 時刻指定の連続実行（下記）
#   TODO_LOOP_DRY=1 bash run.sh <dir>                             # 次に選ぶストーリーだけ表示
#   TODO_LOOP_REFILL_ONLY=1 bash run.sh <dir>                     # 積むだけ（定時の積み足し。passes があれば全部の回）
#
# 時刻指定の連続実行:
#   UNTIL（HH:MM）より前なら 1 SP = 1 セッションで次々に始める。UNTIL を過ぎたら新しいセッションを始めない。
#   END（HH:MM）があれば、各セッション（積む側も）の打ち切りをその時刻までに縮める。
#   UNTIL が無ければ COUNT 件（既定 1）だけ実行して終わる（無ければ積んでから実行）。
# 利用上限: メッセージの「resets 10:20am」の時刻（読めなければ 3 時間後）＋2 分まで待ち、同じストーリーを
#   進捗ファイルから再開する。再開時刻が UNTIL 以降なら止める。
# 選び方: doing があればそれを続ける → 無ければ上から、todo / approved で依存が全部済んだもの。
# 自動復帰: 打ち切り・途中終了（進捗ファイルを書いた）は doing に戻す。RESPLIT_AT 回で残りを分け直させ、MAX_RESUME 回で blocked。
#
# プロジェクトごとの差し込み（すべて <状態>/ に置く。無ければ従来どおり）:
#   lens            1 行目が積み足しの観点（パス・URL・文）。TODO_LOOP_LENS が優先
#   passes/<NN>_<名前>/{lens, prompt-addon.md, pre.sh}
#                   観点の複数回。積み足しのとき番号順に 1 回ずつ lens-next を回す。pre.sh はその回の直前に実行（観点を作り直す）、
#                   prompt-addon.md はその回の指示書の末尾に足す。回ごとに TODO.md を検査し、崩れた回だけその回の前に戻す。
#                   passes があれば lens / TODO_LOOP_LENS は使わない
#   pre-refill.sh   積み足しの直前に 1 回（passes の前）実行
#   pre-run.sh      起動ごとに 1 回、ストーリーを選ぶ前に実行（DRY では実行しない）。失敗しても続ける
#   story-addon.md / refill-addon.md   それぞれの指示書の末尾に足す
#   collect.sh      材料台帳の材料を JSON 行で出す道具（scripts/ledger.py）。collect.sh か ledger/ があれば台帳を使う
#   send.sh         送信: `send.sh <run|create> <PDF のパス or 空>` に本文を標準入力で渡す。
#                   無ければ <repo>/.local/todo-loop/notify.sh（repo 全体の予備。本文だけを渡す）。どちらも無ければ送らない
#   approve.sh      承認待ち: `approve.sh <ID>` に見出し・外部作用 欄の全文・approved にする手順を標準入力で渡す（scripts/approvals_out.py）。
#                   送った ID は <状態>/approvals_sent.json に記録し、外部作用 欄が変わらない限り再送しない。無ければ送らない
# 報告: 各セッションが <状態>/reports/<run|create>/<起動の時刻>/ に JSON を書き、起動の終わりに scripts/report.py で PDF にする
#   （報告の無いストーリーは TODO.md から補完して「報告なし」と明記。末尾に承認待ちの一覧と各 外部作用 欄）。
# 積み足しの検査: 末尾追記・見出しが読める・ID 重複 0・U+FFFD 0・1 件以上増えた、が崩れたら実行前に戻す。
#   積み足しの後に 1 件も実行できなければ空回りを避けて止まる。観点が無ければ積み足さない。
# 状態の置き場: <プロジェクト>/.local/todo-loop/（runs/・progress/・reports/・ledger/・running.pid）。TODO_LOOP_STATE で差し替え可（試験用）。
#
# 環境変数: TODO_LOOP_UNTIL / END / LENS / COUNT（既定 1）/ LIMIT_SEC（既定 3600）/ REFILL_LIMIT_SEC（既定 2700）/ RESPLIT_AT（既定 2）/
#   MAX_RESUME（既定 4）/ DRY / NO_REFILL / REFILL_ONLY（積むだけ）/ NO_NOTIFY / NO_REPORT / LEDGER（1 で台帳を強制）/
#   CHROME（PDF に使うブラウザ）/ CLAUDE（claude の代わりに呼ぶ物。試験用）/ STATE /
#   GRADE（1 で done になった回を scripts/grade.sh で採点。既定 off）/ GRADER（codex|claude）/ GRADE_*（grade.sh の先頭コメント）

set -u
P="$(cd "${1:?プロジェクトのディレクトリを渡す}" && pwd -P)" || exit 2
HERE="$(cd "$(dirname "$0")" && pwd -P)"
ROOT="$(git -C "$P" rev-parse --show-toplevel 2>/dev/null || echo "$P")"
TODO="$P/TODO.md"
STATE="${TODO_LOOP_STATE:-$P/.local/todo-loop}"
RUNS="$STATE/runs"; PDIR="$STATE/progress"; LOCK="$STATE/running.pid"
LIMIT="${TODO_LOOP_LIMIT_SEC:-3600}"
RESPLIT_AT="${TODO_LOOP_RESPLIT_AT:-2}"
MAX_RESUME="${TODO_LOOP_MAX_RESUME:-4}"
COUNT_MAX="${TODO_LOOP_COUNT:-1}"
LENS="${TODO_LOOP_LENS:-}"
[ -z "$LENS" ] && [ -f "$STATE/lens" ] && LENS="$(head -1 "$STATE/lens")"
NAME="$(basename "$P")"
CLAUDE="${TODO_LOOP_CLAUDE:-claude}"
REPORT_PY="$HERE/report.py"
LEDGER_PY="$HERE/ledger.py"
export PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin"
mkdir -p "$RUNS" "$PDIR"
[ -f "$TODO" ] || { echo "[todo-loop] $TODO が無い（Skill todo-loop の手順 1 で作る）" >&2; exit 2; }
REL="${P#$ROOT/}"

DAY="$(date '+%Y-%m-%d %H:%M')"
RUNSTAMP="$(date +%Y%m%d-%H%M%S)"
RUN_REPORT_DIR="$STATE/reports/run/$RUNSTAMP"
CREATE_REPORT_DIR="$STATE/reports/create/$RUNSTAMP"
SUMMARY=(); CREATE_SUMMARY=()
RAN_IDS=""; CREATED_IDS=""; REFILLED_ANY=""
HEAD_RE='^## (S-\d+) — (.+?) \| (調査|実装) \| (fable|opus) \| ([\w-]+)\s*$'

today_at() { [ -n "$1" ] && date -j -f "%Y-%m-%d %H:%M" "$(date +%Y-%m-%d) $1" +%s; }
UNTIL_TS="$(today_at "${TODO_LOOP_UNTIL:-}")"
END_TS="$(today_at "${TODO_LOOP_END:-}")"

ledger_on() { [ -n "${TODO_LOOP_LEDGER:-}" ] || [ -x "$STATE/collect.sh" ] || [ -d "$STATE/ledger" ]; }

# 指示書のひな形 $1 の {{KEY}} を環境変数 F_KEY で置き換える（sed の区切り文字・& に左右されない）
fill() {
  python3 - "$1" <<'PY'
import os, re, sys
t = open(sys.argv[1], encoding="utf-8").read()
sys.stdout.write(re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: os.environ.get("F_" + m.group(1), m.group(0)), t))
PY
}

# 足し書き: 指示書の末尾に足すファイル（あれば）
addon() { [ -s "$1" ] && printf '\n\n## このプロジェクトの足し書き（%s）\n\n%s' "$(basename "$1")" "$(cat "$1")"; }

reset_at() {
  python3 - "$1" <<'PY'
import datetime, re, sys
m = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)", sys.argv[1], re.I)
now = datetime.datetime.now()
if m:
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    t = now.replace(hour=h, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if t <= now:
        t += datetime.timedelta(days=1)
else:
    t = now + datetime.timedelta(hours=3)
print(int(t.timestamp()) + 120)
PY
}

cap_limit() {  # END までの残りで打ち切り秒数を縮める（最低 300 秒）
  local L="$1"
  if [ -n "$END_TS" ]; then
    local LEFT=$(( END_TS - $(date +%s) ))
    [ "$LEFT" -lt "$L" ] && L=$(( LEFT > 300 ? LEFT : 300 ))
  fi
  echo "$L"
}

# 送信: $1=kind（run / create）$2=PDF のパス（空可）$3=本文。<状態>/send.sh → 無ければ repo の notify.sh（本文だけ）
deliver() {
  [ -n "${TODO_LOOP_NO_NOTIFY:-}" ] && { echo "[todo-loop] 送信を省略（$1）"; return 0; }
  if [ -x "$STATE/send.sh" ]; then
    printf '%s' "$3" | ( cd "$P" && "$STATE/send.sh" "$1" "$2" ) || echo "[todo-loop] send.sh が失敗（結果には影響しない）" >&2
    return 0
  fi
  local hook="$ROOT/.local/todo-loop/notify.sh"
  [ -x "$hook" ] || return 0
  printf '%s' "$3" | "$hook" || echo "[todo-loop] 通知に失敗（結果には影響しない）" >&2
}

ids_now() { grep -oE '^## S-[0-9]+ — ' "$TODO" | sed -E 's/^## (S-[0-9]+) — /\1/'; }

pick_story() {  # stdout: ID|種別|モデル|状態|題名。無ければ exit 3
  python3 - "$TODO" "$HEAD_RE" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
head = re.compile(sys.argv[2], re.M)
stories = []
for m in head.finditer(text):
    nxt = head.search(text, m.end())
    body = text[m.end(): nxt.start() if nxt else len(text)]
    dep = re.search(r"^- 依存:\s*(.*)$", body, re.M)
    deps = re.findall(r"S-\d+", dep.group(1)) if dep and "なし" not in dep.group(1) else []
    stories.append((m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), deps))
done = {s[0] for s in stories if s[4] in ("done", "awaiting-approval", "approved")}
pick = next((s for s in stories if s[4] == "doing"), None)
if pick is None:
    pick = next((s for s in stories if s[4] in ("todo", "approved") and all(d in done for d in s[5])), None)
if pick is None:
    sys.exit(3)
print("|".join([pick[0], pick[2], pick[3], pick[4], pick[1]]))
PY
}

state_of() {
  python3 - "$TODO" "$1" <<'PY'
import re, sys
m = re.search(rf"^## {re.escape(sys.argv[2])} — .+? \| ([\w-]+)\s*$", open(sys.argv[1], encoding="utf-8").read(), re.M)
print(m.group(1) if m else "missing")
PY
}

post_check() {  # 状態が動いていなければ doing（自動復帰）か blocked にして引き継ぎを書く。stdout: 新状態
  MAX_RESUME="$MAX_RESUME" python3 - "$TODO" "$@" <<'PY'
import os, re, sys
path, sid, before, log, rc, why, att, prog = sys.argv[1:9]
att, max_resume = int(att), int(os.environ["MAX_RESUME"])
text = open(path, encoding="utf-8").read()
m = re.search(rf"^## {re.escape(sid)} — (.+?) \| (調査|実装) \| (fable|opus) \| ([\w-]+)\s*$", text, re.M)
if not m:
    print("missing"); sys.exit(0)
after = m.group(4)
if after in (before, "todo") or (after == "doing" and why):
    resume = bool(why) and att + 1 < max_resume
    new_state = "doing" if resume else "blocked"
    new_head = text[m.start():m.end()].rsplit("|", 1)[0] + "| " + new_state
    nxt = re.compile(r"^## S-\d+ — ", re.M).search(text, m.end())
    end = nxt.start() if nxt else len(text)
    block = text[m.end():end]
    if resume:
        note = f"runner {log}: {why}（中断 {att + 1} 回目）。次の回が進捗ファイル {prog} から再開する"
    elif why:
        note = f"runner {log} rc={rc}: {why}で中断が {att + 1} 回続いた（上限 {max_resume}）。進捗ファイル {prog} を見て判断（続行なら todo に戻す）"
    else:
        note = f"runner {log} rc={rc}: セッションが状態を変えずに終了。ログを見て判断（続行なら todo に戻す）"
    if re.search(r"^- 引き継ぎ:", block, re.M):
        block = re.sub(r"^- 引き継ぎ:.*$", lambda mm: mm.group(0).rstrip() + " " + note, block, count=1, flags=re.M)
    else:
        block = "\n- 引き継ぎ: " + note + block
    text = text[:m.start()] + new_head + block + text[end:]
    open(path, "w", encoding="utf-8").write(text)
    after = new_state
print(after)
PY
}

check_todo() {  # stdout: 見出し数。崩れていれば理由を出して exit 1。$1 があれば「その写しが先頭に残る」も検査
  python3 - "$TODO" "$HEAD_RE" "${1:-}" <<'PY'
import re, sys
path, head_re, before = sys.argv[1:4]
raw = open(path, "rb").read()
if b"\xef\xbf\xbd" in raw:
    print("U+FFFD がある"); sys.exit(1)
text = raw.decode("utf-8")
heads = re.findall(r"^## (S-\d+) — ", text, re.M)
if len(re.findall(head_re, text, re.M)) != len(heads):
    print("読めない見出しがある"); sys.exit(1)
if len(set(heads)) != len(heads):
    print("ID が重複している"); sys.exit(1)
if before and not text.startswith(open(before, encoding="utf-8").read()):
    print("既存部分が変わっている（末尾追記ではない）"); sys.exit(1)
print(len(heads))
PY
}

is_limit() { grep -qiE "hit your (session|usage|weekly) limit|usage limit reached" "$1"; }

DISALLOWED_BASE="Bash(git commit:*),Bash(git push:*),Bash(git tag:*),Bash(gh:*),Bash(launchctl:*),Bash(crontab:*),Bash(sudo:*),Bash(twine:*),Bash(cargo publish:*),Bash(npm publish:*),mcp__discord-mcp__post_message"
ALLOWED="Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Agent,Workflow,Skill,Bash"

# 台帳: 集めて pending.md を書く（台帳を使うプロジェクトだけ）。stdout: 指示書に入れる 1 行
ledger_collect() {
  if ! ledger_on; then echo "このプロジェクトは材料台帳を使っていない（飛ばす）。"; return 0; fi
  local out
  out="$(python3 "$LEDGER_PY" collect --state "$STATE" --todo "$TODO" 2>>"$RUNS/ledger.err")"
  echo "[todo-loop] $out" >&2
  echo "\`$STATE/ledger/pending.md\` を Read で最後まで読む（$(sed -E 's/^\[ledger\] //; s/:.*$//' <<<"$out")）。"
}
ledger_sweep() {
  ledger_on || return 0
  local out; out="$(python3 "$LEDGER_PY" sweep --state "$STATE" --todo "$TODO" 2>>"$RUNS/ledger.err")"
  echo "[todo-loop] $out"
  CREATE_SUMMARY+=("（台帳）${out#\[ledger\] }")
}

# 積み足しの 1 回。$1=回の名前（空 = passes なしの従来の 1 本）$2=回のディレクトリ（空可）$3=起動の時刻
# 戻り値 0=積めた / 1=積めなかった。上限なら LIMIT_HIT を立てる。失敗した回は TODO.md をその回の前に戻す
refill_pass() {
  local name="$1" pdir="$2" STAMP="$3" label lens LOG BACKUP N0 N1 RC LIM OK PROMPT
  label="${name:-refill}"
  LOG="$RUNS/${STAMP}_refill${name:+_$name}.log"; BACKUP="$RUNS/${STAMP}_${label}_TODO.before.md"
  if [ -n "$pdir" ] && [ -x "$pdir/pre.sh" ]; then
    echo "[todo-loop] pre: $pdir/pre.sh" >> "$RUNS/${STAMP}_${label}_pre.log"
    ( cd "$P" && "$pdir/pre.sh" ) >> "$RUNS/${STAMP}_${label}_pre.log" 2>&1 \
      || SUMMARY+=("（積み足し $label）pre.sh が失敗（${STAMP}_${label}_pre.log）。観点は前のまま")
  fi
  if [ -n "$pdir" ]; then lens="$(head -1 "$pdir/lens" 2>/dev/null)"; else lens="$LENS"; fi
  if [ -z "$lens" ]; then
    SUMMARY+=("（積み足し $label）見送り: 観点（lens の 1 行目）が空")
    echo "$STAMP refill $label fail 観点が空" >> "$RUNS/history.log"; return 1
  fi
  N0="$(check_todo)" || { SUMMARY+=("（積み足し $label）見送り: 実行前の TODO.md が検査に通らない（$N0）"); return 1; }
  cp "$TODO" "$BACKUP"
  LIM="$(cap_limit "${TODO_LOOP_REFILL_LIMIT_SEC:-2700}")"
  mkdir -p "$CREATE_REPORT_DIR"
  PROMPT="$(F_PROJECT="$REL" F_LENS="$lens" F_STATE="$STATE" F_REPORT_PY="$REPORT_PY" F_LEDGER="$LEDGER_LINE" fill "$HERE/refill-prompt.md")"
  PROMPT="$PROMPT$(addon "$STATE/refill-addon.md")"
  [ -n "$pdir" ] && PROMPT="$PROMPT$(addon "$pdir/prompt-addon.md")"
  PROMPT="$(printf '開始時刻: %s\n打ち切り: 開始から %s 秒\n観点の回: %s\n報告の置き場: %s\n報告のファイル名: %s.json\n\n%s' \
    "$(date '+%Y-%m-%d %H:%M')" "$LIM" "$label" "$CREATE_REPORT_DIR" "$label" "$PROMPT")"
  echo "[todo-loop] refill start $STAMP pass=$label lens=$lens limit=${LIM}s" > "$LOG"
  ( cd "$ROOT" && env -u ANTHROPIC_API_KEY "$CLAUDE" -p --model fable --permission-mode acceptEdits \
      --allowedTools "$ALLOWED" --disallowedTools "$DISALLOWED_BASE" --output-format text "$PROMPT" >> "$LOG" 2>&1 ) &
  local PID=$!
  ( sleep "$LIM"; kill -0 "$PID" 2>/dev/null && { kill "$PID"; echo "[todo-loop] ${LIM}s で打ち切り" >> "$LOG"; } ) &
  local WD=$!
  wait "$PID"; RC=$?; pkill -P "$WD" 2>/dev/null; kill "$WD" 2>/dev/null
  N1="$(check_todo "$BACKUP")"; OK=$?
  if [ "$OK" -ne 0 ] || [ "$N1" -le "$N0" ] 2>/dev/null; then
    cp "$BACKUP" "$TODO"; rm -f "$BACKUP"
    if is_limit "$LOG"; then
      LIMIT_HIT="$(grep -iE "limit" "$LOG" | tail -1)"; SUMMARY+=("（積み足し $label）利用上限で止まった（TODO.md は戻した）")
      echo "$STAMP refill $label limit log=$LOG" >> "$RUNS/history.log"
    else
      local WHY; WHY="$([ "$OK" -ne 0 ] && echo "$N1" || echo 新しいストーリーが無い)"
      SUMMARY+=("（積み足し $label）失敗 rc=$RC: $WHY → この回の前に戻した。ログ $LOG")
      echo "$STAMP refill $label fail rc=$RC $WHY log=$LOG" >> "$RUNS/history.log"
    fi
    return 1
  fi
  rm -f "$BACKUP"
  SUMMARY+=("（積み足し $label）ストーリー $N0 → $N1 件。$(awk '/^LENS-SUMMARY/{f=1;next} f&&/^- 選んだ/{print;exit}' "$LOG")")
  echo "$STAMP refill $label ok $N0->$N1 log=$LOG" >> "$RUNS/history.log"
  return 0
}

# 積み足し（lens-next）。passes があれば番号順に全部の回、無ければ観点 1 本。戻り値 0=1 回以上積めた / 1=積めなかった
refill() {
  local PASS_DIRS=() d
  for d in "$STATE"/passes/*/; do [ -f "${d}lens" ] && PASS_DIRS+=("${d%/}"); done
  if [ ${#PASS_DIRS[@]} -eq 0 ] && [ -z "$LENS" ]; then
    echo "[todo-loop] 観点（TODO_LOOP_LENS・<状態>/lens・<状態>/passes/）が無いので積み足さない"; return 1
  fi
  if [ -n "${TODO_LOOP_DRY:-}" ]; then   # DRY は回と観点を出すだけ（pre-refill・pre.sh・claude を走らせない）
    if [ ${#PASS_DIRS[@]} -gt 0 ]; then
      for d in "${PASS_DIRS[@]}"; do echo "[todo-loop] DRY 積み足しの回: $(basename "$d") 観点=$(head -1 "$d/lens")"; done
    else
      echo "[todo-loop] DRY 積み足しの回: refill 観点=$LENS"
    fi
    return 0
  fi
  local STAMP IDS0 ANY=1 N0
  STAMP="$(date +%Y%m%d-%H%M%S)"
  N0="$(check_todo)" || { SUMMARY+=("（積み足し）見送り: 実行前の TODO.md が検査に通らない（$N0）"); return 1; }
  if [ -x "$STATE/pre-refill.sh" ]; then
    echo "[todo-loop] pre-refill: $STATE/pre-refill.sh" >> "$RUNS/${STAMP}_pre-refill.log"
    ( cd "$P" && "$STATE/pre-refill.sh" ) >> "$RUNS/${STAMP}_pre-refill.log" 2>&1 \
      || SUMMARY+=("（積み足し）pre-refill が失敗（${STAMP}_pre-refill.log）。観点は前のまま")
  fi
  LEDGER_LINE="$(ledger_collect)"
  IDS0="$(ids_now)"; REFILLED_ANY=1
  if [ ${#PASS_DIRS[@]} -gt 0 ]; then
    for d in "${PASS_DIRS[@]}"; do
      local pname; pname="$(basename "$d")"
      if [ -n "$LIMIT_HIT" ]; then SUMMARY+=("（積み足し $pname）見送り: 前の回が利用上限で止まった"); continue; fi
      refill_pass "$pname" "$d" "$STAMP" && ANY=0
    done
  else
    refill_pass "" "" "$STAMP" && ANY=0
  fi
  ledger_sweep
  CREATED_IDS="$CREATED_IDS $(comm -13 <(printf '%s\n' "$IDS0" | sort) <(ids_now | sort) | tr '\n' ' ')"
  return $ANY
}

run_one() {  # 戻り値 0=実行した / 1=実行できる物が無い
  local sel; sel="$(pick_story)" || return 1
  local ID KIND MODEL ST TITLE
  IFS='|' read -r ID KIND MODEL ST TITLE <<<"$sel"
  local STAMP LOG; STAMP="$(date +%Y%m%d-%H%M%S)"; LOG="$RUNS/${STAMP}_${ID}.log"
  echo "[todo-loop] $STAMP 選択: $ID ($KIND / $MODEL / $ST) $TITLE"
  if [ -n "${TODO_LOOP_DRY:-}" ]; then return 0; fi
  local LIM; LIM="$(cap_limit "$LIMIT")"
  local PROG="$PDIR/$ID.md" ATT_FILE="$PDIR/$ID.attempts" ATT NOTE=""
  ATT="$(cat "$ATT_FILE" 2>/dev/null || echo 0)"
  [ "$ATT" -ge "$RESPLIT_AT" ] && NOTE="$(printf '\n\n## このストーリーは既に %s 回、終わらずに中断している（runner からの指示）\n作業に入る前に進捗ファイルを読み、残りを 1 SP に確実に収まる大きさに分け直す（「1 SP に収まらないと分かったとき」の手順）。やった分で完了条件を満たせるなら範囲をやった分に縮めて done、やった分が無ければ範囲を最初の小さな 1 SP に縮めてそれだけをやる。' "$ATT")"
  local DIS="$DISALLOWED_BASE"; [ "$ST" = "approved" ] && DIS="Bash(sudo:*)"
  mkdir -p "$RUN_REPORT_DIR"
  local PROMPT
  PROMPT="$(F_PROJECT="$REL" F_STATE="$STATE" F_REPORT_PY="$REPORT_PY" fill "$HERE/story-prompt.md")$(addon "$STATE/story-addon.md")"
  PROMPT="$(printf 'ストーリー ID: %s\n現在の状態: %s\n開始時刻: %s\n打ち切り: 開始から %s 秒\n進捗ファイル: %s\nこれまでの中断回数: %s\n報告の置き場: %s\n\n%s%s' \
    "$ID" "$ST" "$(date '+%Y-%m-%d %H:%M')" "$LIM" "$PROG" "$ATT" "$RUN_REPORT_DIR" "$PROMPT" "$NOTE")"
  echo "[todo-loop] start $STAMP id=$ID model=$MODEL state=$ST limit=${LIM}s" > "$LOG"
  local S0 PID WD RC PROG_M0
  S0="$(date +%s)"; PROG_M0="$(stat -f %m "$PROG" 2>/dev/null || echo 0)"
  ( cd "$ROOT" && env -u ANTHROPIC_API_KEY "$CLAUDE" -p --model "$MODEL" --permission-mode acceptEdits \
      --allowedTools "$ALLOWED" --disallowedTools "$DIS" --output-format text "$PROMPT" >> "$LOG" 2>&1 ) &
  PID=$!
  ( sleep "$LIM"; kill -0 "$PID" 2>/dev/null && { kill "$PID"; echo "[todo-loop] ${LIM}s で打ち切り" >> "$LOG"; } ) &
  WD=$!
  wait "$PID"; RC=$?; pkill -P "$WD" 2>/dev/null; kill "$WD" 2>/dev/null
  local SEC=$(( $(date +%s) - S0 )) NEW
  if [ "$RC" -ne 0 ] && is_limit "$LOG"; then
    # 利用上限はストーリーの不具合ではない。状態を残し（doing なら次の回が続ける）、中断回数にも数えない
    NEW="$(state_of "$ID")"; LIMIT_HIT="$(grep -iE "limit" "$LOG" | tail -1)"
  else
    local WHY=""
    grep -q "^\[todo-loop\] ${LIM}s で打ち切り$" "$LOG" && WHY="${LIM}s で打ち切り"
    [ -z "$WHY" ] && [ "$(stat -f %m "$PROG" 2>/dev/null || echo 0)" -gt "$PROG_M0" ] \
      && [ "$(state_of "$ID")" = doing ] && WHY="途中まで進めて終了"
    NEW="$(post_check "$ID" "$ST" "$LOG" "$RC" "$WHY" "$ATT" "$PROG")"
    if [ "$NEW" = doing ]; then echo $(( ATT + 1 )) > "$ATT_FILE"; else rm -f "$ATT_FILE"; fi
  fi
  echo "$STAMP $ID $KIND $MODEL $ST -> $NEW rc=$RC sec=$SEC log=$LOG" >> "$RUNS/history.log"
  echo "[todo-loop] 終了: $ID $ST -> $NEW (rc=$RC, ${SEC}s)"
  SUMMARY+=("$ID $TITLE: $ST → $NEW ($(( SEC / 60 ))分, $MODEL)")
  if [ -n "${TODO_LOOP_GRADE:-}" ] && [ "$NEW" = done ]; then   # 採点（opt-in）。不一致は 引き継ぎ 欄に追記、状態は変えない
    local G; G="$(TODO_LOOP_GRADE_NO_WAIT=1 bash "$HERE/grade.sh" "$P" story "$ID" --note 2>>"$RUNS/grade.err" | tail -1)"
    G="${G:-[grade] $ID 採点できなかった（$RUNS/grade.err）}"; echo "[todo-loop] $G"; SUMMARY+=("（採点）${G#\[grade\] }")
  fi
  case " $RAN_IDS " in *" $ID "*) ;; *) RAN_IDS="$RAN_IDS $ID" ;; esac
  return 0
}

wait_limit() {  # 上限明けまで待つ。0=待った / 1=UNTIL を過ぎるので止める
  [ -n "$UNTIL_TS" ] || { STOP_REASON="Claude の利用上限: $LIMIT_HIT"; return 1; }
  local WAKE; WAKE="$(reset_at "$LIMIT_HIT")"
  if [ "$WAKE" -ge "$UNTIL_TS" ]; then
    STOP_REASON="利用上限の再開時刻 $(date -r "$WAKE" '+%H:%M') が ${TODO_LOOP_UNTIL} 以降: $LIMIT_HIT"; return 1
  fi
  echo "[todo-loop] 利用上限: $LIMIT_HIT → $(date -r "$WAKE" '+%H:%M') まで待って再開"
  SUMMARY+=("（利用上限）$(date '+%H:%M') に上限 → $(date -r "$WAKE" '+%H:%M') に再開")
  deliver run "" "[todo-loop $NAME] 利用上限に当たったので $(date -r "$WAKE" '+%H:%M') まで待って再開します"
  local LEFT=$(( WAKE - $(date +%s) )); [ "$LEFT" -gt 0 ] && sleep "$LEFT"
  LIMIT_HIT=""; return 0
}

# 起動の終わり: 報告 PDF を作り、send.sh（無ければ notify.sh）へ渡す
finish_reports() {
  local BODY="$1" RUN_PDF="" CREATE_PDF="" out
  if [ -z "${TODO_LOOP_NO_NOTIFY:-}" ] && [ -x "$STATE/approve.sh" ]; then   # 承認待ちを承認の口へ（opt-in）
    out="$(python3 "$HERE/approvals_out.py" --todo "$TODO" --state "$STATE" --project "$P" 2>>"$RUNS/approvals.err")"
    echo "[todo-loop] $out"; BODY="$BODY"$'\n'"- （承認待ち）${out#\[approvals\] }"
  fi
  if [ -z "${TODO_LOOP_NO_REPORT:-}" ]; then
    if [ -n "$RAN_IDS" ]; then
      mkdir -p "$RUN_REPORT_DIR"
      # shellcheck disable=SC2086
      out="$(python3 "$REPORT_PY" build --kind run --dir "$RUN_REPORT_DIR" --title "TODO ラン $NAME $DAY" \
        --out "$RUN_REPORT_DIR/todo-run-$RUNSTAMP.pdf" --todo "$TODO" --fallback $RAN_IDS 2>&1)" \
        && RUN_PDF="$RUN_REPORT_DIR/todo-run-$RUNSTAMP.pdf"
      echo "[todo-loop] 報告（ラン）: ${out##*$'\n'}"
    fi
    if [ -n "$REFILLED_ANY" ]; then
      mkdir -p "$CREATE_REPORT_DIR"
      # shellcheck disable=SC2086
      out="$(python3 "$REPORT_PY" build --kind create --dir "$CREATE_REPORT_DIR" --title "TODO 出し $NAME $DAY" \
        --out "$CREATE_REPORT_DIR/todo-create-$RUNSTAMP.pdf" --todo "$TODO" --fallback $CREATED_IDS 2>&1)" \
        && CREATE_PDF="$CREATE_REPORT_DIR/todo-create-$RUNSTAMP.pdf"
      echo "[todo-loop] 報告（出し）: ${out##*$'\n'}"
    fi
  fi
  if [ -x "$STATE/send.sh" ]; then
    if [ -n "$REFILLED_ANY" ]; then
      local CB="[todo-loop $NAME] $DAY 積み足し（積んだ:${CREATED_IDS:- なし}）"
      for line in "${SUMMARY[@]+"${SUMMARY[@]}"}" "${CREATE_SUMMARY[@]+"${CREATE_SUMMARY[@]}"}"; do
        case "$line" in "（積み足し"*|"（台帳）"*) CB="$CB"$'\n'"- $line" ;; esac
      done
      deliver create "$CREATE_PDF" "$CB"
    fi
    if [ -n "$RAN_IDS" ] || [ -z "$REFILLED_ANY" ]; then deliver run "$RUN_PDF" "$BODY"; fi
  else
    [ -n "$RUN_PDF" ] && BODY="$BODY"$'\n'"報告 PDF（ラン）: $RUN_PDF"
    [ -n "$CREATE_PDF" ] && BODY="$BODY"$'\n'"報告 PDF（出し）: $CREATE_PDF"
    deliver run "$RUN_PDF" "$BODY"
  fi
}

if [ -z "${TODO_LOOP_DRY:-}" ]; then
  if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
    echo "[todo-loop] 別の runner（pid $(cat "$LOCK")）が実行中のため見送り"; exit 0
  fi
  echo $$ > "$LOCK"; trap 'rm -f "$LOCK"' EXIT
  if [ -x "$STATE/pre-run.sh" ]; then   # 起動ごとに 1 回、ストーリーを選ぶ前（例: 別の実行先への送り出し・戻りの取り込み）
    PRE_LOG="$RUNS/${RUNSTAMP}_pre-run.log"
    if ( cd "$P" && "$STATE/pre-run.sh" ) > "$PRE_LOG" 2>&1; then
      [ -s "$PRE_LOG" ] && SUMMARY+=("（実行前の処理）$(tail -3 "$PRE_LOG" | tr '\n' ' ' | cut -c1-300)")
    else
      SUMMARY+=("（実行前の処理）pre-run.sh が失敗（$PRE_LOG）。続行した")
    fi
  fi
fi

LIMIT_HIT=""; COUNT=0; STOP_REASON=""; RAN_SINCE_REFILL=1; LEDGER_LINE=""
if [ -n "${TODO_LOOP_REFILL_ONLY:-}" ]; then   # 積むだけ（消化しない）。未着手が残っていても今の観点で積み足す
  if refill; then STOP_REASON="積むだけの指定（TODO_LOOP_REFILL_ONLY）"; else STOP_REASON="積み足せなかった（積むだけの指定）"; fi
  [ -n "${TODO_LOOP_DRY:-}" ] && exit 0   # DRY は回の一覧を出すだけ。報告も送信もしない
fi
while [ -z "${TODO_LOOP_REFILL_ONLY:-}" ]; do
  if [ -n "$UNTIL_TS" ] && [ "$(date +%s)" -ge "$UNTIL_TS" ]; then
    STOP_REASON="${TODO_LOOP_UNTIL} を過ぎたので新しいセッションを始めない"; break
  fi
  if run_one; then
    [ -n "${TODO_LOOP_DRY:-}" ] && exit 0
    COUNT=$(( COUNT + 1 )); RAN_SINCE_REFILL=1
  else
    if [ -n "${TODO_LOOP_DRY:-}" ]; then echo "[todo-loop] 実行できるストーリーが無い（積み足しの対象）"; exit 0; fi
    if [ -z "$RAN_SINCE_REFILL" ] || [ -n "${TODO_LOOP_NO_REFILL:-}" ]; then
      STOP_REASON="実行できるストーリーが無い（積み足しの後も 0 件、または blocked・needs-user 待ち）"; break
    fi
    RAN_SINCE_REFILL=""
    echo "[todo-loop] 実行できるストーリーが無い → lens-next で積む"
    if ! refill && [ -z "$LIMIT_HIT" ]; then
      STOP_REASON="積み足せなかった"; break
    fi
  fi
  if [ -n "$LIMIT_HIT" ]; then
    RAN_SINCE_REFILL=1
    wait_limit && continue
    break
  fi
  [ -n "$UNTIL_TS" ] || { [ "$COUNT" -ge "$COUNT_MAX" ] && { STOP_REASON="$COUNT 件実行（時刻指定なし）"; break; }; }
done

BODY="[todo-loop $NAME] $DAY 実行 $COUNT 件"
for line in "${SUMMARY[@]+"${SUMMARY[@]}"}"; do BODY="$BODY"$'\n'"- $line"; done
BODY="$BODY"$'\n'"停止理由: $STOP_REASON"$'\n'"次の候補: $(pick_story 2>/dev/null || echo なし)"
echo "$BODY"
finish_reports "$BODY"
exit 0
