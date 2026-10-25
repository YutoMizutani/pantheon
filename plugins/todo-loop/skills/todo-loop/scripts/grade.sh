#!/bin/bash
# grade.sh — 無人セッションが書いた 証跡 を、作業者と別のモデルに再実行させて採点する。
#
#   bash grade.sh <プロジェクト> story <ID> [--note]   # 1 件を採点。--note なら 不一致 のとき 引き継ぎ 欄に追記（状態は変えない）
#   bash grade.sh <プロジェクト> batch                 # 前回の採点以降に done になった全件（常に --note 相当）
#   TODO_LOOP_GRADE_TODO=<複製.md> TODO_LOOP_GRADE_TAG=tampered bash grade.sh <P> story S-00   # 別の TODO（証跡を変えた複製など）を採点
#   TODO_LOOP_GRADER=claude bash grade.sh <P> story S-00                                       # Claude で採点
#   TODO_LOOP_GRADE_DRY=1 bash grade.sh <P> story S-00   # 採点者の選択と指示文を表示するだけ（batch では対象の一覧だけ）
#
# 採点者:
#   既定は Codex（`codex exec`、sandbox workspace-write・作業根はプロジェクト）。
#   `codex` が見つからなければ理由を監査ファイルに書き、作業者（見出し 4 列目）と違う Claude モデルで代える
#   （opus → sonnet、それ以外 → opus。TODO_LOOP_GRADE_MODEL で指定。作業者と同じなら終了コード 3）。
# 結果: <状態>/audits/<YYYY-MM-DD>_<ID>[_<TAG>].md。最終行が「判定: 合格|不一致|判定不能」。
#   標準出力の最終行は `[grade] <ID> <判定> (<秒>s, <採点者>) -> <監査ファイル>`。
# 採点者は audits を読まない。ただし採点するストーリーの 証跡 が名指しした audits 内のファイル（今ある物）だけは読んでよいと指示文に列挙する。
# 終了コード（story）: 0 = 合格 / 1 = 不一致 / 2 = 判定不能・採点者の失敗 / 3 = 引数・ストーリーの誤り。
#
# batch の対象: <状態>/runs/history.log の「<stamp> <ID> ... -> done ... sec=<秒>」から done になった時刻 = stamp + sec を得て、
#   下限（TODO_LOOP_GRADE_SINCE → <状態>/audits/batch_since → history.log の最初の stamp）より後で、今も TODO.md で done で、
#   台帳 <状態>/audits/graded.tsv にその時刻以降の採点が無いもの。途中で止まっても次の起動で続きから進む。
#   runner の実行中（<状態>/running.pid が生きていて自分の親でない）は TODO_LOOP_GRADE_WAIT_SEC 秒まで待つ。
# batch の終了コード: 0 = 全件合格か対象なし / 1 = 不一致または判定不能あり / 3 = 設定の誤り。
#
# 環境変数: TODO_LOOP_GRADER（codex|claude、既定 codex）/ GRADE_CODEX（既定 codex）/ GRADE_CLAUDE（既定 claude。TODO_LOOP_CLAUDE とは独立）/
#   GRADE_MODEL / GRADE_TODO / GRADE_TAG / GRADE_LIMIT_SEC（既定 1500）/ GRADE_DRY /
#   batch 用 GRADE_SINCE（YYYYmmdd-HHMMSS）/ GRADE_MAX（既定 0 = 無制限）/ GRADE_WAIT_SEC（既定 7200）/ GRADE_NO_WAIT /
#   STATE（状態の置き場。既定 <プロジェクト>/.local/todo-loop）

set -u
P="$(cd "${1:?プロジェクトのディレクトリを渡す}" && pwd -P)" || exit 3
HERE="$(cd "$(dirname "$0")" && pwd -P)"
ROOT="$(git -C "$P" rev-parse --show-toplevel 2>/dev/null || echo "$P")"
STATE="${TODO_LOOP_STATE:-$P/.local/todo-loop}"
TODO="${TODO_LOOP_GRADE_TODO:-$P/TODO.md}"
AUDITS="$STATE/audits"
RUNS="$STATE/runs"
LEDGER="$AUDITS/graded.tsv"
SINCE_FILE="$AUDITS/batch_since"
CODEX="${TODO_LOOP_GRADE_CODEX:-codex}"
CLAUDE="${TODO_LOOP_GRADE_CLAUDE:-claude}"
TIMEOUT="${TODO_LOOP_GRADE_LIMIT_SEC:-1500}"
MAX="${TODO_LOOP_GRADE_MAX:-0}"
TAG="${TODO_LOOP_GRADE_TAG:-}"
export PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin"

usage() { echo "usage: grade.sh <プロジェクト> story <S-NN> [--note] | batch" >&2; exit 3; }
[ -f "$TODO" ] || { echo "TODO が無い: $TODO" >&2; exit 3; }
mkdir -p "$AUDITS"

# ---- 不一致を TODO.md の 引き継ぎ 欄に追記する（状態は変えない・同じ追記は 1 回だけ） --------
append_handoff() {  # $1 = ID, $2 = 監査ファイル（repo 相対）
  python3 - "$TODO" "$1" "$2" <<'PY'
import re, sys
path, sid, audit = sys.argv[1:4]
text = open(path, encoding="utf-8").read()
m = re.search(rf"^## {re.escape(sid)} — .+$", text, re.M)
if not m:
    sys.exit(f"{sid} の見出しが無い")
nxt = re.compile(r"^## ", re.M).search(text, m.end())
end = nxt.start() if nxt else len(text)
block = text[m.start():end]
note = f"採点: 不一致（{audit}）。"
if note in block:
    sys.exit(0)
h = re.search(r"^- 引き継ぎ:[^\n]*$", block, re.M)
if h:
    line = h.group(0).rstrip()
    new_line = line + " " + note
    block = block[:h.start()] + new_line + block[h.end():]
else:
    block = block.rstrip("\n") + f"\n- 引き継ぎ: {note}\n\n"
open(path, "w", encoding="utf-8").write(text[:m.start()] + block + text[end:])
PY
}

# ---- 1 件を採点する（終了コードは上の story の表） -----------------------------------
grade_story() {  # $1 = ID, $2 = --note（任意）
  local ID="$1" NOTE="${2:-}"
  if ! [[ "$ID" =~ ^S-[0-9]+$ ]]; then usage; fi

  # ストーリーの見出しから次の見出しまでを全文で取り出す（stdout: 1 行目 = 作業モデル、2 行目以降 = 本文）
  local BLOCK
  BLOCK="$(python3 - "$TODO" "$ID" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
sid = sys.argv[2]
m = re.search(rf"^## {re.escape(sid)} — .+? \| (調査|実装) \| (\w+) \| ([\w-]+)\s*$", text, re.M)
if not m:
    sys.exit(4)
nxt = re.compile(r"^## ", re.M).search(text, m.end())
print(m.group(2))
print(text[m.start(): nxt.start() if nxt else len(text)].rstrip())
PY
)" || { echo "$ID が $TODO に見つからない" >&2; exit 3; }
  local WORKER STORY STAMP_DAY OUT
  WORKER="$(printf '%s\n' "$BLOCK" | head -1)"
  STORY="$(printf '%s\n' "$BLOCK" | tail -n +2)"
  STAMP_DAY="$(date +%Y-%m-%d)"
  OUT="$AUDITS/${STAMP_DAY}_${ID}${TAG:+_$TAG}.md"

  # 証跡 欄が名指しする audits 内のファイル（採点者に読ませてよいのはこれだけ）
  # `S-{68,70}_batch.md` の波括弧を展開し、`audits/` 以下の相対パスか、ファイル名だけで書かれたものを拾う。今ある物に限る。
  local ALLOWED AUDIT_RULE
  ALLOWED="$(printf '%s\n' "$STORY" | python3 -c '
import os, re, sys
audits, out = os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])
ev = "\n".join(l for l in sys.stdin.read().splitlines() if l.startswith("- 証跡:"))
def expand(tok):
    m = re.search(r"\{([^{}]*,[^{}]*)\}", tok)
    if not m:
        return [tok]
    return [x for alt in m.group(1).split(",") for x in expand(tok[:m.start()] + alt + tok[m.end():])]
found = []
for tok in re.findall(r"[\w./{},~-]+", ev):
    for t in expand(tok.rstrip(".,")):
        rel = t.rsplit("audits/", 1)[1] if "audits/" in t else os.path.basename(t)
        p = os.path.realpath(os.path.join(audits, rel))
        if rel and p.startswith(audits + os.sep) and p != out and os.path.isfile(p) and p not in found:
            found.append(p)
print("\n".join(found))
' "$AUDITS" "$OUT")"
  AUDIT_RULE="- \`${AUDITS#$P/}/\`（絶対パス $AUDITS）を読む・検索する（監査の出力と試験用の複製の置き場で、採点の材料ではない）。rg / grep をかけるときもこの場所を除く。"
  if [ -n "$ALLOWED" ]; then
    AUDIT_RULE="$AUDIT_RULE
  例外: 採点するストーリーの 証跡 が名指しした次のファイルだけは、主張を確かめるために読んでよい（これ以外の audits 内のファイルは読まない・検索しない）。
$(printf '%s\n' "$ALLOWED" | sed 's/^/    - /')"
  fi

  # 採点者を決める
  local GRADER="${TODO_LOOP_GRADER:-codex}" FALLBACK_NOTE="" CLAUDE_MODEL=""
  if [ "$GRADER" = "codex" ] && ! command -v "$CODEX" >/dev/null 2>&1; then
    FALLBACK_NOTE="codex CLI が見つからない（\`command -v $CODEX\` が空）ため Claude で代えた"
    GRADER=claude
  fi
  if [ "$GRADER" = "claude" ]; then
    case "$WORKER" in
      opus) CLAUDE_MODEL="${TODO_LOOP_GRADE_MODEL:-sonnet}" ;;
      *)    CLAUDE_MODEL="${TODO_LOOP_GRADE_MODEL:-opus}" ;;
    esac
    if [ "$CLAUDE_MODEL" = "$WORKER" ]; then echo "採点者が作業者と同じモデル ($WORKER)" >&2; exit 3; fi
  fi

  local PROMPT
  PROMPT="$(cat <<EOF
あなたは監査役です。下のストーリーは、別のモデル（$WORKER）の無人セッションが作業し、自分で 証跡 を書いたものです。
あなたはその作業者ではありません。証跡 の主張を信じず、実際に確かめて採点してください。

## 場所
- repo の根: $ROOT
- プロジェクトの根: $P（作業ディレクトリ）
- 証跡 のパスは repo 相対かプロジェクト相対のどちらか。両方を試してよい。

## してはいけないこと
- ファイルを作る・書き換える・消す（とくに TODO.md）。git の commit・checkout・reset・push。ネットワークへの送信。
$AUDIT_RULE
- 検証に書き込みが要るコマンド（ビルド・テスト）を走らせるときは、キャッシュ等の副作用を最小にする（例: pytest は \`-p no:cacheprovider\`）。10 分を超えそうなものは走らせず「確認不能（時間）」とする。

## 手順
1. 完了条件 と 証跡 から、確かめられる主張を 1 つずつ抜き出す（ファイルの有無、行数・中身、テスト件数、commit の hash、数値）。
2. 各主張について、証跡 に書かれた検証コマンドを再実行する。書かれていなければ同等の読み取り専用コマンドで確かめる。
3. 証跡 の値と、あなたが観測した値を並べ、一致 / 不一致 / 確認不能（理由）を付ける。
   後のストーリーの変更で値が変わった可能性があれば、その根拠（git log 等）も書く。ただし判定は観測どおりに付ける。
4. 完了条件 が満たされているかを 1 行で書く。

## 出力（最後のメッセージにこの形の Markdown だけを書く）
### 主張と観測
| # | 証跡 の主張 | 実行したコマンド | 観測 | 判定 |
|---|---|---|---|---|
（各行）

### 完了条件
（満たしている / 満たしていない と理由 1〜2 行）

判定: 合格 | 不一致 | 判定不能   ← どれか 1 語だけを残し、これを最後の行にする
- 合格 = 今も観測できる主張がすべて一致し、完了条件を満たす。
- 不一致 = 1 つでも観測と食い違う。
- 判定不能 = 完了条件に直結し、今も観測できるはずの主張を確かめられなかった（ファイルが読めない、コマンドが走らない等）。
- 過去の時点の状態や作業中の出来事（「開始時は不在だった」「コマンドが失敗した」など）は今は観測できない。表には「確認不能（過去）」と書くが、判定には数えない。
  完了条件が変化（「行が増えている」など）を求めるときは、証跡 が示す変化後の状態が今の観測と一致するかで判断する。

---
## 採点するストーリー（TODO: $TODO）

$STORY
EOF
)"

  if [ -n "${TODO_LOOP_GRADE_DRY:-}" ]; then
    echo "[grade] id=$ID worker=$WORKER grader=$GRADER${CLAUDE_MODEL:+ model=$CLAUDE_MODEL} out=$OUT"
    [ -n "$FALLBACK_NOTE" ] && echo "[grade] $FALLBACK_NOTE"
    printf '%s\n' "$PROMPT"
    exit 0
  fi

  # 採点者を走らせる
  local TODO_SHA0 TODO_SHA1 WORK LAST RAW T0 GRADER_DESC PID WD RC SEC VERDICT TODO_NOTE
  TODO_SHA0="$(shasum -a 256 "$TODO" | cut -d' ' -f1)"
  # 一時ファイルは採点者の作業根の外に置く（中に置くと採点者が自分の生ログを検索して材料にする）
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/grade.XXXXXX")"
  LAST="$WORK/last.md"
  RAW="$WORK/raw.log"
  : > "$LAST"
  T0="$(date +%s)"
  if [ "$GRADER" = "codex" ]; then
    GRADER_DESC="Codex（$("$CODEX" --version 2>/dev/null | head -1)、既定モデル、sandbox workspace-write、-C $P）"
    printf '%s' "$PROMPT" | "$CODEX" exec -s workspace-write -C "$P" --color never -o "$LAST" - > "$RAW" 2>&1 &
  else
    GRADER_DESC="Claude $CLAUDE_MODEL（作業者 $WORKER と別モデル、書き込みツールなし）"
    ( cd "$P" && "$CLAUDE" -p --model "$CLAUDE_MODEL" \
        --allowedTools "Read,Grep,Glob,Bash" \
        --disallowedTools "Edit,Write,NotebookEdit,Bash(git push:*),Bash(git commit:*),Bash(git checkout:*),Bash(git reset:*),Bash(rm:*),Bash(sudo:*)" \
        --output-format text "$PROMPT" > "$LAST" 2> "$RAW" ) &
  fi
  PID=$!
  ( sleep "$TIMEOUT"; kill -0 "$PID" 2>/dev/null && kill "$PID" 2>/dev/null && echo "[grade] ${TIMEOUT}s で打ち切り" >> "$RAW" ) &
  WD=$!
  wait "$PID"; RC=$?
  pkill -P "$WD" 2>/dev/null; kill "$WD" 2>/dev/null
  SEC=$(( $(date +%s) - T0 ))
  TODO_SHA1="$(shasum -a 256 "$TODO" | cut -d' ' -f1)"

  VERDICT="$(grep -E '^判定: *(合格|不一致|判定不能) *$' "$LAST" 2>/dev/null | tail -1 | sed -E 's/^判定: *//; s/ *$//')"
  [ -z "$VERDICT" ] && VERDICT="判定不能"
  TODO_NOTE="変更なし"
  [ "$TODO_SHA0" != "$TODO_SHA1" ] && TODO_NOTE="**採点中に変わった**（前 $TODO_SHA0 / 後 $TODO_SHA1。採点者か並行セッションの書き込み）"

  {
    echo "# 監査: $ID（$STAMP_DAY）"
    echo
    echo "- 採点したファイル: \`${TODO#$ROOT/}\`"
    echo "- 作業者のモデル: $WORKER"
    echo "- 採点者: $GRADER_DESC"
    [ -n "$FALLBACK_NOTE" ] && echo "- 代替の理由: $FALLBACK_NOTE"
    echo "- 所要: ${SEC} 秒、採点者の終了コード: $RC"
    echo "- 採点中の TODO の変化: $TODO_NOTE"
    echo "- 結論: **$VERDICT**"
    echo
    echo "## 採点者の報告"
    echo
    if [ -s "$LAST" ]; then cat "$LAST"; echo; else echo "（最後のメッセージが空。下の生ログを参照）"; fi
    echo
    echo "## 採点者の生ログ（末尾 40 行）"
    echo
    echo '```'
    tail -40 "$RAW"
    echo '```'
    echo
    echo "## 採点したストーリー（採点時点の全文）"
    echo
    printf '%s\n' "$STORY" | sed 's/^/> /'
    echo
    echo "判定: $VERDICT"
  } > "$OUT"
  rm -f "$LAST" "$RAW"; rmdir "$WORK" 2>/dev/null

  if [ "$NOTE" = "--note" ] && [ "$VERDICT" = "不一致" ]; then
    append_handoff "$ID" "${OUT#$ROOT/}" || echo "[grade] $ID の 引き継ぎ 欄に書けなかった" >&2
  fi
  echo "[grade] $ID $VERDICT (${SEC}s, $GRADER) -> $OUT"
  case "$VERDICT" in 合格) exit 0 ;; 不一致) exit 1 ;; *) exit 2 ;; esac
}

# ---- 前回の採点以降に done になった全件を採点する ---------------------------------------
grade_batch() {
  [ -f "$RUNS/history.log" ] || { echo "history.log が無い: $RUNS/history.log" >&2; exit 3; }
  local SINCE="${TODO_LOOP_GRADE_SINCE:-}"
  [ -z "$SINCE" ] && [ -f "$SINCE_FILE" ] && SINCE="$(head -1 "$SINCE_FILE")"
  [ -z "$SINCE" ] && SINCE="$(grep -oE '^[0-9]{8}-[0-9]{6}' "$RUNS/history.log" | head -1)"
  if ! [[ "$SINCE" =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
    echo "下限が決まらない: TODO_LOOP_GRADE_SINCE=YYYYmmdd-HHMMSS を渡す（今は '$SINCE'）" >&2; exit 3
  fi
  [ -n "${TODO_LOOP_GRADE_DRY:-}" ] || [ -f "$SINCE_FILE" ] || echo "$SINCE" > "$SINCE_FILE"

  # 対象を選ぶ（stdout: 1 行 1 件「<done 時刻> <ID>」、done 時刻の古い順）
  local TARGETS N_ALL
  TARGETS="$(python3 - "$RUNS/history.log" "$TODO" "$LEDGER" "$SINCE" <<'PY'
import datetime as dt, os, re, sys
hist, todo, ledger, since = sys.argv[1:5]
fmt = "%Y%m%d-%H%M%S"
since_t = dt.datetime.strptime(since, fmt)
done_at = {}
for line in open(hist, encoding="utf-8"):
    m = re.match(r"^(\d{8}-\d{6}) (S-\d+) \S+ \S+ \S+ -> done\b.*\bsec=(\d+)", line)
    if m:
        t = dt.datetime.strptime(m.group(1), fmt) + dt.timedelta(seconds=int(m.group(3)))
        done_at[m.group(2)] = max(done_at.get(m.group(2), t), t)
state = {m.group(1): m.group(2) for m in re.finditer(
    r"^## (S-\d+) — .+? \| (?:調査|実装) \| \w+ \| ([\w-]+)\s*$",
    open(todo, encoding="utf-8").read(), re.M)}
graded = {}
if os.path.exists(ledger):
    for line in open(ledger, encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        if len(f) >= 2 and re.match(r"^\d{8}-\d{6}$", f[0]):
            t = dt.datetime.strptime(f[0], fmt)
            graded[f[1]] = max(graded.get(f[1], t), t)
for sid, t in sorted(done_at.items(), key=lambda kv: kv[1]):
    if t > since_t and state.get(sid) == "done" and not (sid in graded and graded[sid] >= t):
        print(t.strftime(fmt), sid)
PY
)" || { echo "対象の選定に失敗" >&2; exit 3; }

  N_ALL="$(printf '%s' "$TARGETS" | grep -c . || true)"
  echo "[batch] since=$SINCE 対象 ${N_ALL} 件: $(printf '%s\n' "$TARGETS" | awk '{print $2}' | tr '\n' ' ')"
  [ -n "${TODO_LOOP_GRADE_DRY:-}" ] && exit 0
  [ "$N_ALL" -eq 0 ] && exit 0

  # runner の実行中は待つ（TODO.md の同時書き込みを避ける）。runner の中から呼ばれたとき（pid が親）は待たない
  local LOCK="$STATE/running.pid" W0 LPID
  W0="$(date +%s)"
  while [ -z "${TODO_LOOP_GRADE_NO_WAIT:-}" ] && [ -f "$LOCK" ] && LPID="$(cat "$LOCK" 2>/dev/null)" \
        && [ "$LPID" != "$PPID" ] && kill -0 "$LPID" 2>/dev/null; do
    if [ $(( $(date +%s) - W0 )) -ge "${TODO_LOOP_GRADE_WAIT_SEC:-7200}" ]; then
      echo "[batch] 見送り: runner（pid $LPID）が終わらない"; exit 0
    fi
    sleep 60
  done

  local PASS=0 FAIL=0 UNK=0 DONE_N=0 ID OUTLINE RC AUDIT AUDIT_REL V
  while read -r _ ID; do
    [ -z "$ID" ] && continue
    if [ "$MAX" -gt 0 ] && [ "$DONE_N" -ge "$MAX" ]; then echo "[batch] 上限 $MAX 件で止める（次の起動で続き）"; break; fi
    OUTLINE="$(TAG=batch; grade_story "$ID" --note < /dev/null)"; RC=$?
    echo "$OUTLINE"
    AUDIT="$(printf '%s\n' "$OUTLINE" | sed -nE 's/^\[grade\] .* -> (.+)$/\1/p' | tail -1)"
    AUDIT_REL="${AUDIT#$ROOT/}"
    case "$RC" in
      0) V=合格; PASS=$((PASS+1)) ;;
      1) V=不一致; FAIL=$((FAIL+1)) ;;
      3) echo "[batch] $ID は採点できなかった（引数・ストーリーの誤り）" >&2; continue ;;
      *) V=判定不能; UNK=$((UNK+1)) ;;
    esac
    printf '%s\t%s\t%s\t%s\n' "$(date +%Y%m%d-%H%M%S)" "$ID" "$V" "$AUDIT_REL" >> "$LEDGER"
    DONE_N=$((DONE_N+1))
  done <<< "$TARGETS"

  echo "[batch] 合格 $PASS / 不一致 $FAIL / 判定不能 $UNK（台帳 ${LEDGER#$ROOT/}）"
  [ $((FAIL+UNK)) -eq 0 ] && exit 0 || exit 1
}

case "${2:-}" in
  story) [ -n "${3:-}" ] || usage; grade_story "$3" "${4:-}" ;;
  batch) grade_batch ;;
  *) usage ;;
esac
