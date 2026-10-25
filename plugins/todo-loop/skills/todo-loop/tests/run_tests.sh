#!/bin/bash
# todo-loop の試験 — 偽の claude（fake_claude.py）と一時ディレクトリだけで runner の機能を確かめる。実の TODO.md・claude には触れない。
#
#   bash tests/run_tests.sh
#   TODO_LOOP_TEST_TODO=<TODO.md の写し> bash tests/run_tests.sh   # (f) の DRY をその写しにもかける（写しを一時ディレクトリへさらに写して使う）
#
# 置き場: mktemp -d（TMPDIR に従う）。終わったら消す（TODO_LOOP_TEST_KEEP=1 なら残す）。
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
S="$(cd "$HERE/../scripts" && pwd -P)"
RUN="$S/run.sh"
FAKE="$HERE/fake_claude.py"
T="$(mktemp -d "${TMPDIR:-/tmp}/todo-loop-test.XXXXXX")"
[ -z "${TODO_LOOP_TEST_KEEP:-}" ] && trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ok   $1"; }
ng()  { FAIL=$((FAIL + 1)); echo "  FAIL $1"; }
chk() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else ng "$d"; fi; }
has() { grep -qF -- "$2" "$1"; }
CHROME_OK=""; python3 -B -c "import sys; sys.path.insert(0,'$S'); import report; sys.exit(0 if report.find_chrome() else 1)" && CHROME_OK=1

story() {  # $1=ID $2=題 $3=状態 [$4=外部作用]
  printf '\n## %s — %s | 実装 | opus | %s\n- 範囲: %s の範囲\n- 完了条件: true\n- 依存: なし\n- 外部作用: %s\n- 証跡: %s の証跡\n- 引き継ぎ:\n' \
    "$1" "$2" "$3" "$1" "${4:-なし}" "$1"
}
mkproj() {  # $1=ケース名。stdout なし。$T/$1/{proj,state,prompts} を作る
  mkdir -p "$T/$1/proj" "$T/$1/state" "$T/$1/prompts"
  printf '# 試験 TODO\n\n## 運用（人が読む部分）\n\n- 試験用\n\n---\n' > "$T/$1/proj/TODO.md"
}
plan() { printf '%s' "$2" > "$T/$1/plan.json"; }
send_sh() {  # $1=ケース名。send.sh が受け取った kind・PDF・本文を sent.txt に残す
  cat > "$T/$1/state/send.sh" <<EOF
#!/bin/bash
{ echo "KIND=\$1"; echo "PDF=\$2"; [ -n "\$2" ] && [ -s "\$2" ] && echo "PDF_EXISTS=1"; echo "BODY<<"; cat; echo; echo ">>"; } >> "$T/$1/sent.txt"
EOF
  chmod +x "$T/$1/state/send.sh"
}
runner() {  # $1=ケース名、残りは env。出力は $T/$1/out.txt
  local c="$1"; shift
  env TODO_LOOP_CLAUDE="$FAKE" TODO_LOOP_STATE="$T/$c/state" FAKE_PLAN="$T/$c/plan.json" FAKE_TODO="$T/$c/proj/TODO.md" \
      FAKE_LOG="$T/$c/prompts" "$@" bash "$RUN" "$T/$c/proj" > "$T/$c/out.txt" 2>&1
}
prompt_with() { grep -lF -- "$2" "$T/$1"/prompts/*.txt 2>/dev/null | wc -l | tr -d ' '; }

echo "== (a) passes 2 本: 1 本目は積める・2 本目は既存行を壊す → 2 本目だけ戻る（積み足しだけの実行）"
mkproj a
{ story S-01 済んだ物 done; } >> "$T/a/proj/TODO.md"
mkdir -p "$T/a/state/passes/01_one" "$T/a/state/passes/02_two"
echo "観点1の文" > "$T/a/state/passes/01_one/lens"; echo "PASS-MARK-ONE" > "$T/a/state/passes/01_one/prompt-addon.md"
echo "観点2の文" > "$T/a/state/passes/02_two/lens"; echo "PASS-MARK-TWO" > "$T/a/state/passes/02_two/prompt-addon.md"
printf '#!/bin/bash\necho ran >> "%s"\n' "$T/a/pre2.ran" > "$T/a/state/passes/02_two/pre.sh"; chmod +x "$T/a/state/passes/02_two/pre.sh"
send_sh a
plan a '{"refill": {"01_one": {"do": "append", "n": 2, "report": true}, "02_two": {"do": "break", "n": 1}}}'
cp "$T/a/proj/TODO.md" "$T/a/before.md"
runner a TODO_LOOP_REFILL_ONLY=1
chk "1 本目の 2 件が積まれている" test "$(grep -c '積んだ物 01_one' "$T/a/proj/TODO.md")" -eq 2
chk "2 本目の積み足しは戻っている（積んだ物 02_two が無い）" bash -c "! grep -q '積んだ物 02_two' '$T/a/proj/TODO.md'"
chk "2 本目が壊した先頭行は戻っている" bash -c "! grep -q '壊した先頭行' '$T/a/proj/TODO.md'"
chk "既存部分が先頭にそのまま残る" python3 -c "import sys; sys.exit(0 if open('$T/a/proj/TODO.md').read().startswith(open('$T/a/before.md').read()) else 1)"
chk "2 本目の pre.sh が 1 回走った" test "$(wc -l < "$T/a/pre2.ran" 2>/dev/null | tr -d ' ')" = 1
chk "history に 01_one ok と 02_two fail" bash -c "grep -q 'refill 01_one ok' '$T/a/state/runs/history.log' && grep -q 'refill 02_two fail' '$T/a/state/runs/history.log'"
chk "回ごとの指示書に、その回の足し書きと観点だけが入る" bash -c "[ \$(grep -lF PASS-MARK-ONE '$T/a'/prompts/*.txt | xargs grep -L PASS-MARK-TWO | xargs grep -l '観点1の文' | wc -l) -eq 1 ] && [ \$(grep -lF PASS-MARK-TWO '$T/a'/prompts/*.txt | xargs grep -L PASS-MARK-ONE | xargs grep -l '観点2の文' | wc -l) -eq 1 ]"
chk "積み足しの起動は 2 回（passes の数）" test "$(ls "$T/a/prompts" | wc -l | tr -d ' ')" -eq 2
chk "send.sh に create が届いた" has "$T/a/sent.txt" "KIND=create"
chk "send.sh の本文に 02_two の失敗が載る" bash -c "grep -A30 'KIND=create' '$T/a/sent.txt' | grep -q '積み足し 02_two）失敗'"
chk "消化はしていない（run の送信なし）" bash -c "! grep -q 'KIND=run' '$T/a/sent.txt'"

echo "== (b) passes 無し: 従来どおり lens 1 本・refill-addon.md が足される・台帳は使わない"
mkproj b
{ story S-01 済んだ物 done; } >> "$T/b/proj/TODO.md"
echo "単一の観点の文" > "$T/b/state/lens"; echo "REFILL-ADDON-MARK" > "$T/b/state/refill-addon.md"
plan b '{"refill": {"refill": {"do": "append", "n": 1}}}'
runner b TODO_LOOP_REFILL_ONLY=1 TODO_LOOP_NO_NOTIFY=1
chk "積み足しの起動は 1 回" test "$(ls "$T/b/prompts" | wc -l | tr -d ' ')" -eq 1
chk "指示書に lens の 1 行目" test "$(prompt_with b 単一の観点の文)" -eq 1
chk "指示書に refill-addon.md" test "$(prompt_with b REFILL-ADDON-MARK)" -eq 1
chk "指示書に「台帳を使っていない」" test "$(prompt_with b 材料台帳を使っていない)" -eq 1
chk "1 件積まれた" test "$(grep -c '積んだ物 refill' "$T/b/proj/TODO.md")" -eq 1
chk "ログ名は従来の <時刻>_refill.log" bash -c "ls '$T/b/state/runs/' | grep -qE '^[0-9]{8}-[0-9]{6}_refill\.log$'"
chk "ledger/ を作っていない" test ! -e "$T/b/state/ledger"

echo "== (c)(d) ストーリー 2 件実行・1 件だけ報告 → PDF に 2 件（1 件は報告なし）と承認待ちの節。send.sh が kind と PDF を受け取る"
mkproj c
{ story S-01 一つ目 todo; story S-02 二つ目 todo
  printf '\n## S-03 — 公開する | 実装 | opus | awaiting-approval\n- 範囲: 公開\n- 完了条件: true\n- 依存: なし\n- 外部作用: git push origin main\n  続きの行: 公開ページを再公開する\n- 証跡:\n- 引き継ぎ:\n'; } >> "$T/c/proj/TODO.md"
echo "STORY-ADDON-MARK" > "$T/c/state/story-addon.md"
printf '#!/bin/bash\necho pre-run >> "%s"\n' "$T/c/pre-run.count" > "$T/c/state/pre-run.sh"; chmod +x "$T/c/state/pre-run.sh"
send_sh c
plan c '{"story": {"S-01": {"state": "done", "report": true}, "S-02": {"state": "done"}}}'
runner c TODO_LOOP_COUNT=2 TODO_LOOP_NO_REFILL=1
HTML="$(ls "$T"/c/state/reports/run/*/todo-run-*.html 2>/dev/null | head -1)"
chk "2 件実行した" has "$T/c/out.txt" "実行 2 件"
chk "pre-run.sh が起動ごとに 1 回" test "$(wc -l < "$T/c/pre-run.count" | tr -d ' ')" = 1
chk "両方の指示書に story-addon.md と報告の置き場" test "$(grep -lF STORY-ADDON-MARK "$T"/c/prompts/*.txt | xargs grep -l "報告の置き場: $T/c/state/reports/run/" | wc -l | tr -d ' ')" -eq 2
chk "指示書の {{REPORT_PY}} が report.py の絶対パスに置き換わる" test "$(prompt_with c "python3 $S/report.py check")" -eq 2
chk "報告の HTML ができた" test -n "$HTML"
chk "HTML に S-01 の報告" has "$HTML" "S-01 の施策"
chk "HTML に S-02（TODO.md から補完）" has "$HTML" "二つ目"
chk "「報告なし」の印は 1 件だけ" test "$(grep -o '報告なし・TODO.md から補完' "$HTML" | wc -l | tr -d ' ')" -eq 1
chk "承認待ちの節がある" has "$HTML" "承認待ち（awaiting-approval）の一覧"
chk "承認待ちの節に S-03 と外部作用（続きの行まで）" bash -c "grep -q 'S-03 — 公開する' '$HTML' && grep -q 'git push origin main' '$HTML' && grep -q '続きの行: 公開ページを再公開する' '$HTML'"
if [ -n "$CHROME_OK" ]; then
  PDF="${HTML%.html}.pdf"
  chk "PDF ができた" test -s "$PDF"
  if command -v pdftotext >/dev/null; then
    chk "PDF の本文に 承認待ち・報告なし・S-01・S-02" bash -c "pdftotext '$PDF' - | tr -d ' ' | grep -q '承認待ち' && pdftotext '$PDF' - | grep -q '報告なし' && pdftotext '$PDF' - | grep -q 'S-01' && pdftotext '$PDF' - | grep -q 'S-02'"
  fi
  chk "(d) send.sh が kind=run と PDF のパスを受け取った" bash -c "grep -q 'KIND=run' '$T/c/sent.txt' && grep -qF 'PDF=$PDF' '$T/c/sent.txt' && grep -q PDF_EXISTS=1 '$T/c/sent.txt'"
else
  echo "  （Chrome が見つからないので PDF の確認は飛ばした）"
  chk "(d) send.sh が kind=run と空の PDF を受け取った" bash -c "grep -q 'KIND=run' '$T/c/sent.txt' && grep -qx 'PDF=' '$T/c/sent.txt'"
fi
chk "(d) send.sh の本文に実行件数" bash -c "grep -A20 'KIND=run' '$T/c/sent.txt' | grep -q '実行 2 件'"
chk "approve.sh が無ければ approvals_sent.json を作らない" test ! -e "$T/c/state/approvals_sent.json"
chk "check: 書かれた報告は OK" python3 "$S/report.py" check "$(ls "$T"/c/state/reports/run/*/S-01.json)"
echo '[{"施策": "x", "ストーリー": ["S-1"]}]' > "$T/c/bad.json"
chk "check: 欠けた報告は NG" bash -c "! python3 '$S/report.py' check '$T/c/bad.json'"

echo "== (e) 台帳: collect.sh の材料が pending に入り、範囲 に番号を書いた積み足しの後 resolved に移る"
mkproj e
{ story S-01 済んだ物 done; } >> "$T/e/proj/TODO.md"
echo "台帳の観点の文" > "$T/e/state/lens"
cat > "$T/e/state/collect.sh" <<'EOF'
#!/bin/bash
echo '{"id": "Q-1234abcd", "kind": "指摘", "text": "材料A"}'
echo '{"kind": "指摘", "text": "材料B"}'
echo 'JSON でない行は無視される'
EOF
chmod +x "$T/e/state/collect.sh"
mkdir -p "$T/e/state/runs"
printf '%s\n' "20260101-000000 S-90 実装 opus todo -> doing rc=1 sec=10 log=x" "20260101-010000 S-90 実装 opus doing -> doing rc=1 sec=10 log=y" \
  "20260101-020000 S-91 実装 opus todo -> blocked rc=0 sec=10 log=z" "20260101-030000 S-92 実装 opus todo -> done rc=0 sec=10 log=w" > "$T/e/state/runs/history.log"
plan e '{"refill": {"refill": {"do": "append", "n": 1, "cite": ["Q-1234abcd"]}}}'
runner e TODO_LOOP_REFILL_ONLY=1 TODO_LOOP_NO_NOTIFY=1 TODO_LOOP_NO_REPORT=1
L="$T/e/state/ledger"
chk "指示書に台帳の一覧のパス" test "$(prompt_with e "$L/pending.md")" -eq 1
chk "Q-1234abcd は resolved に移った（理由: 範囲に記載）" bash -c "grep -q '\"Q-1234abcd\"' '$L/resolved.jsonl' && grep -q '範囲に記載: S-02' '$L/resolved.jsonl'"
chk "Q-1234abcd は pending に無い" bash -c "! grep -q Q-1234abcd '$L/pending.jsonl'"
chk "番号の無い材料B は M- の番号で pending に残る" bash -c "grep -q '材料B' '$L/pending.jsonl' && grep '材料B' '$L/pending.jsonl' | grep -qE '\"M-[0-9a-f]{8}\"'"
chk "history から: S-90 の中断 2 回・S-91 の blocked（S-92 の done は入らない）" bash -c "grep -q 'S-90 が終わらずに中断' '$L/pending.jsonl' && grep -q 'S-91 が blocked' '$L/pending.jsonl' && ! grep -q S-92 '$L/pending.jsonl'"
chk "pending.md がある" test -s "$L/pending.md"
python3 "$S/ledger.py" collect --state "$T/e/state" --todo "$T/e/proj/TODO.md" > /dev/null 2>&1
chk "集め直しても resolved の材料は戻らない" bash -c "! grep -q Q-1234abcd '$L/pending.jsonl'"
chk "集め直しても重ならない（未処理 3 件）" test "$(wc -l < "$L/pending.jsonl" | tr -d ' ')" -eq 3
printf '%s\n' "20260102-000000 refill 02_two fail rc=0 新しいストーリーが無い log=v" >> "$T/e/state/runs/history.log"
python3 "$S/ledger.py" collect --state "$T/e/state" --todo "$T/e/proj/TODO.md" > /dev/null 2>&1
chk "history の積み足しの失敗も材料になる" grep -q '積み足し（02_two）が失敗' "$L/pending.jsonl"

echo "== (f) DRY は従来どおり選択行を出す（何も書き換えない）"
mkproj f
{ story S-01 済んだ物 done; story S-02 続きの物 doing; story S-03 次の物 todo; } >> "$T/f/proj/TODO.md"
printf '#!/bin/bash\necho ran > "%s"\n' "$T/f/pre-run.ran" > "$T/f/state/pre-run.sh"; chmod +x "$T/f/state/pre-run.sh"
cp "$T/f/proj/TODO.md" "$T/f/before.md"
runner f TODO_LOOP_DRY=1
chk "選択: S-02（doing を先に）" bash -c "grep -qE '^\[todo-loop\] [0-9]{8}-[0-9]{6} 選択: S-02 \(実装 / opus / doing\) 続きの物$' '$T/f/out.txt'"
chk "TODO.md は変わらない" cmp -s "$T/f/proj/TODO.md" "$T/f/before.md"
chk "DRY では pre-run.sh を走らせない" test ! -e "$T/f/pre-run.ran"
chk "DRY では claude を呼ばない" test "$(ls "$T/f/prompts" | wc -l | tr -d ' ')" -eq 0
echo "== (f3) DRY ＋ 積むだけ: 回の一覧と観点だけ出し、pre-refill・pre.sh・claude を走らせない"
mkproj f3
{ story S-01 済んだ物 done; } >> "$T/f3/proj/TODO.md"
mkdir -p "$T/f3/state/passes/01_one" "$T/f3/state/passes/02_two"
echo "観点1の文" > "$T/f3/state/passes/01_one/lens"; echo "観点2の文" > "$T/f3/state/passes/02_two/lens"
printf '#!/bin/bash\necho ran > "%s"\n' "$T/f3/pre-refill.ran" > "$T/f3/state/pre-refill.sh"; chmod +x "$T/f3/state/pre-refill.sh"
printf '#!/bin/bash\necho ran > "%s"\n' "$T/f3/pre2.ran" > "$T/f3/state/passes/02_two/pre.sh"; chmod +x "$T/f3/state/passes/02_two/pre.sh"
plan f3 '{}'
send_sh f3   # 送信を止める指定は付けない（付けると DRY の送信漏れを見逃す）
cp "$T/f3/proj/TODO.md" "$T/f3/before.md"
runner f3 TODO_LOOP_DRY=1 TODO_LOOP_REFILL_ONLY=1
chk "回の一覧に 2 本と観点が出る" bash -c "grep -q '01_one.*観点1の文' '$T/f3/out.txt' && grep -q '02_two.*観点2の文' '$T/f3/out.txt'"
chk "DRY では pre-refill.sh を走らせない" test ! -e "$T/f3/pre-refill.ran"
chk "DRY では回の pre.sh を走らせない" test ! -e "$T/f3/pre2.ran"
chk "DRY ＋ 積むだけでも claude を呼ばない" test "$(ls "$T/f3/prompts" | wc -l | tr -d ' ')" -eq 0
chk "DRY ＋ 積むだけでも TODO.md は変わらない" cmp -s "$T/f3/proj/TODO.md" "$T/f3/before.md"
chk "DRY ＋ 積むだけでも送信しない（send.sh を呼ばない）" test ! -e "$T/f3/sent.txt"
chk "DRY ＋ 積むだけでも報告を作らない" test ! -d "$T/f3/state/reports"
if [ -n "${TODO_LOOP_TEST_TODO:-}" ] && [ -f "$TODO_LOOP_TEST_TODO" ]; then
  mkdir -p "$T/f2/proj" "$T/f2/state" "$T/f2/prompts"; cp "$TODO_LOOP_TEST_TODO" "$T/f2/proj/TODO.md"; echo '{}' > "$T/f2/plan.json"
  runner f2 TODO_LOOP_DRY=1
  chk "写しの TODO.md でも DRY が選択行か「無い」を出す" bash -c "grep -qE '選択: S-[0-9]+|実行できるストーリーが無い' '$T/f2/out.txt'"
  sed -n '1p' "$T/f2/out.txt" | sed 's/^/    /'
fi

echo "== (g) 従来の使い方: state/lens + pre-refill.sh、send.sh 無し → repo の notify.sh に 1 通（本文だけ）"
mkproj g
{ story S-01 済んだ物 done; } >> "$T/g/proj/TODO.md"
mkdir -p "$T/g/proj/.local/todo-loop"
echo "従来の観点" > "$T/g/proj/.local/todo-loop/lens"
printf '#!/bin/bash\necho ran >> "%s"\n' "$T/g/pre-refill.ran" > "$T/g/proj/.local/todo-loop/pre-refill.sh"; chmod +x "$T/g/proj/.local/todo-loop/pre-refill.sh"
printf '#!/bin/bash\n{ echo "ARGS=$#"; cat; echo; echo ">>"; } >> "%s"\n' "$T/g/notify.txt" > "$T/g/proj/.local/todo-loop/notify.sh"; chmod +x "$T/g/proj/.local/todo-loop/notify.sh"
plan g '{"refill": {"refill": {"do": "append", "n": 1}}, "story": {"S-02": {"state": "done"}}}'
env TODO_LOOP_CLAUDE="$FAKE" FAKE_PLAN="$T/g/plan.json" FAKE_TODO="$T/g/proj/TODO.md" FAKE_LOG="$T/g/prompts" bash "$RUN" "$T/g/proj" > "$T/g/out.txt" 2>&1
chk "積んでから 1 件実行（時刻指定なし）" bash -c "grep -q '実行 1 件' '$T/g/out.txt' && grep -q '停止理由: 1 件実行（時刻指定なし）' '$T/g/out.txt'"
chk "pre-refill.sh が 1 回走った" test "$(wc -l < "$T/g/pre-refill.ran" | tr -d ' ')" = 1
chk "notify.sh に 1 通（引数なし・本文だけ）" bash -c "[ \$(grep -c '^ARGS=0$' '$T/g/notify.txt') -eq 1 ] && grep -q '実行 1 件' '$T/g/notify.txt'"
chk "台帳を作っていない" test ! -e "$T/g/proj/.local/todo-loop/ledger"
chk "状態は既定の <プロジェクト>/.local/todo-loop/ に置かれた" test -s "$T/g/proj/.local/todo-loop/runs/history.log"

echo "== (j) 承認待ちを approve.sh へ渡す: ID と 外部作用 の全文が届く・記録される・2 回目は再送しない・欄が変われば送り直す"
mkproj j
{ story S-01 一つ目 todo
  printf '\n## S-03 — 公開する | 実装 | opus | awaiting-approval\n- 範囲: 公開\n- 完了条件: true\n- 依存: なし\n- 外部作用: git push origin main\n  続きの行: 公開ページを再公開する\n- 証跡:\n- 引き継ぎ:\n'; } >> "$T/j/proj/TODO.md"
cat > "$T/j/state/approve.sh" <<EOF
#!/bin/bash
{ echo "ID=\$1"; echo "IN<<"; cat; echo ">>"; } >> "$T/j/approved.txt"; echo "A-0000-test01"; echo sent
EOF
chmod +x "$T/j/state/approve.sh"
send_sh j
plan j '{"story": {"S-01": {"state": "done"}}}'
runner j TODO_LOOP_COUNT=1 TODO_LOOP_NO_REFILL=1
chk "approve.sh に S-03 が 1 回届いた" test "$(grep -c '^ID=S-03$' "$T/j/approved.txt")" -eq 1
TJ="$(cd "$T/j/proj" && pwd -P)"   # runner は実体パス（pwd -P）で渡す
chk "標準入力に見出し・TODO の絶対パス・外部作用の全文（続きの行まで）・approved にする手順" bash -c "grep -q 'S-03 — 公開する | 実装 | opus | awaiting-approval' '$T/j/approved.txt' && grep -qF 'TODO: $TJ/TODO.md' '$T/j/approved.txt' && grep -q 'git push origin main' '$T/j/approved.txt' && grep -q '続きの行: 公開ページを再公開する' '$T/j/approved.txt' && grep -q 'approved に書き換える' '$T/j/approved.txt'"
chk "approvals_sent.json に S-03 → A-0000-test01 (sent)" python3 -c "import json; d=json.load(open('$T/j/state/approvals_sent.json')); assert d['S-03']['approval']=='A-0000-test01' and d['S-03']['delivery']=='sent'"
chk "送信の本文に（承認待ち）の行" bash -c "grep -A20 'KIND=run' '$T/j/sent.txt' | grep -q '承認待ち）.*S-03→A-0000-test01'"
{ story S-02 二つ目 todo; } >> "$T/j/proj/TODO.md"
plan j '{"story": {"S-02": {"state": "done"}}}'
runner j TODO_LOOP_COUNT=1 TODO_LOOP_NO_REFILL=1
chk "2 回目の起動では再送しない（届いた回数 1 のまま）" test "$(grep -c '^ID=S-03$' "$T/j/approved.txt")" -eq 1
chk "2 回目の本文は skipped=1" bash -c "grep -q 'skipped=1' '$T/j/out.txt'"
python3 - "$T/j/proj/TODO.md" <<'PY'
import sys; p=sys.argv[1]; t=open(p,encoding="utf-8").read(); open(p,"w",encoding="utf-8").write(t.replace("git push origin main", "git push origin main --tags"))
PY
{ story S-04 三つ目 todo; } >> "$T/j/proj/TODO.md"
plan j '{"story": {"S-04": {"state": "done"}}}'
runner j TODO_LOOP_COUNT=1 TODO_LOOP_NO_REFILL=1
chk "外部作用 欄が変わったら送り直す（届いた回数 2）" test "$(grep -c '^ID=S-03$' "$T/j/approved.txt")" -eq 2
{ story S-05 四つ目 todo; } >> "$T/j/proj/TODO.md"
rm -f "$T/j/state/approvals_sent.json"
plan j '{"story": {"S-05": {"state": "done"}}}'
runner j TODO_LOOP_COUNT=1 TODO_LOOP_NO_REFILL=1 TODO_LOOP_NO_NOTIFY=1
chk "TODO_LOOP_NO_NOTIFY では approve.sh を呼ばない（記録を消しても届いた回数 2 のまま）" test "$(grep -c '^ID=S-03$' "$T/j/approved.txt")" -eq 2

echo "== (h) 定時の雛形: schedule.sh が plist を書き出す（launchctl は呼ばない）"
mkproj h
OUTH="$(TODO_LOOP_STATE="$T/h/state" bash "$S/schedule.sh" "$T/h/proj" "0,6:30" --refill-only --env TODO_LOOP_END=07:00 2>&1)"
PL="$T/h/state/launchd/local.todo-loop.proj.refill.plist"
chk "plist を書き出した" test -s "$PL"
chk "plutil -lint が通る" plutil -lint "$PL"
chk "時刻 0:00 と 6:30・積み足しだけ・追加の環境変数・runner のパス" bash -c "grep -q '<integer>6</integer><key>Minute</key><integer>30</integer>' '$PL' && grep -q '<integer>0</integer><key>Minute</key><integer>0</integer>' '$PL' && grep -q TODO_LOOP_REFILL_ONLY '$PL' && grep -q TODO_LOOP_END '$PL' && grep -qF '$RUN' '$PL'"
chk "登録の手順を表示し、自分では登録しない" bash -c "echo '$OUTH' | grep -q 'launchctl bootstrap' && ! launchctl print gui/\$(id -u)/local.todo-loop.proj.refill >/dev/null 2>&1"
chk "読めない時刻は止める" bash -c "! TODO_LOOP_STATE='$T/h/state' bash '$S/schedule.sh' '$T/h/proj' '25' >/dev/null 2>&1"

GRADE="$S/grade.sh"
FG="$HERE/fake_grader.py"
grader() {  # $1=ケース名 $2=FAKE_GRADE の JSON、残りは「env の追加… -- grade.sh の引数」。出力は $T/$1/out.txt、終了コードを返す
  local c="$1" g="$2"; shift 2
  local envs=(); while [ "$1" != "--" ]; do envs+=("$1"); shift; done; shift
  env TODO_LOOP_STATE="$T/$c/state" TODO_LOOP_GRADER=claude TODO_LOOP_GRADE_CLAUDE="$FG" FAKE_GRADE="$g" FAKE_LOG="$T/$c/grader.log" \
      ${envs[@]+"${envs[@]}"} bash "$GRADE" "$T/$c/proj" "$@" > "$T/$c/out.txt" 2>&1
}
calls() { if [ -f "$T/$1/grader.log" ]; then wc -l < "$T/$1/grader.log" | tr -d ' '; else echo 0; fi; }
DAYK="$(date +%Y-%m-%d)"
G3='{"S-01": "合格", "S-02": "不一致", "S-03": null}'

echo "== (k1) 採点 1 件: 合格 / 不一致 / 判定不能 と終了コード・監査ファイル"
mkproj k1
{ story S-01 一つ目 done; story S-02 二つ目 done; story S-03 三つ目 done; } >> "$T/k1/proj/TODO.md"
grader k1 "$G3" -- story S-01; R1=$?
grader k1 "$G3" -- story S-02; R2=$?
grader k1 "$G3" -- story S-03; R3=$?
A="$T/k1/state/audits"
chk "終了コード 0 / 1 / 2" test "$R1$R2$R3" = 012
chk "監査ファイル <日付>_S-01.md の最終行が 判定: 合格" test "$(tail -1 "$A/${DAYK}_S-01.md")" = "判定: 合格"
chk "S-02 の最終行が 判定: 不一致" test "$(tail -1 "$A/${DAYK}_S-02.md")" = "判定: 不一致"
chk "S-03: 採点者の報告の節はあるが判定行は最終行の 1 本だけ（判定不能）" bash -c "grep -q '^## 採点者の報告' '$A/${DAYK}_S-03.md' && [ \$(grep -c '^判定:' '$A/${DAYK}_S-03.md') -eq 1 ] && [ \"\$(tail -1 '$A/${DAYK}_S-03.md')\" = '判定: 判定不能' ]"
chk "標準出力の最終行 [grade] S-03 判定不能 (…s, claude) -> 監査ファイル" bash -c "tail -1 '$T/k1/out.txt' | grep -qE '^\[grade\] S-03 判定不能 \([0-9]+s, claude\) -> .*/${DAYK}_S-03\.md$'"
chk "監査ファイルに節（採点者 / 所要 / TODO の変化 / ストーリー全文）" bash -c "f='$A/${DAYK}_S-01.md'; grep -q '^- 採点者: Claude sonnet' \"\$f\" && grep -q '^- 所要: ' \"\$f\" && grep -q '^- 採点中の TODO の変化: 変更なし' \"\$f\" && grep -q '^> ## S-01 — 一つ目' \"\$f\""
chk "--note なしでは TODO.md に追記しない" bash -c "! grep -q '採点: 不一致' '$T/k1/proj/TODO.md'"

echo "== (k2) --note: 不一致を 引き継ぎ 欄に 1 回だけ追記・状態は変えない"
mkproj k2
{ story S-01 一つ目 done; story S-02 二つ目 done; } >> "$T/k2/proj/TODO.md"
grader k2 "$G3" -- story S-02 --note
grader k2 "$G3" -- story S-02 --note
grader k2 "$G3" -- story S-01 --note
chk "S-02 の 引き継ぎ に 採点: 不一致（…）。が 1 回" test "$(grep -c '^- 引き継ぎ:.*採点: 不一致（.*_S-02\.md）。' "$T/k2/proj/TODO.md")" -eq 1
chk "追記は 1 か所だけ（2 回採点しても重ならない）" test "$(grep -o '採点: 不一致' "$T/k2/proj/TODO.md" | wc -l | tr -d ' ')" -eq 1
chk "見出しの状態は done のまま" bash -c "grep -q '^## S-02 — 二つ目 | 実装 | opus | done$' '$T/k2/proj/TODO.md' && grep -q '^## S-01 — 一つ目 | 実装 | opus | done$' '$T/k2/proj/TODO.md'"

echo "== (k3) 採点者のモデル: 作業者 opus → sonnet / fable → opus / 同じモデルの指定は exit 3"
mkproj k3
{ story S-01 一つ目 done
  printf '\n## S-02 — 調べ物 | 調査 | fable | done\n- 範囲: 調査\n- 完了条件: true\n- 依存: なし\n- 外部作用: なし\n- 証跡: 調査の証跡\n- 引き継ぎ:\n'; } >> "$T/k3/proj/TODO.md"
grader k3 '{"S-01": "合格", "S-02": "合格"}' -- story S-01
grader k3 '{"S-01": "合格", "S-02": "合格"}' -- story S-02
chk "作業者 opus → --model sonnet" grep -qx 'S-01 form=claude model=sonnet' "$T/k3/grader.log"
chk "作業者 fable → --model opus" grep -qx 'S-02 form=claude model=opus' "$T/k3/grader.log"
N3="$(calls k3)"
grader k3 '{}' TODO_LOOP_GRADE_MODEL=opus -- story S-01; R=$?
chk "TODO_LOOP_GRADE_MODEL=opus と作業者 opus → exit 3・採点者を呼ばない" bash -c "[ $R -eq 3 ] && [ $(calls k3) -eq $N3 ]"
grader k3 '{}' -- story S-99; R=$?
chk "無い ID は exit 3" test "$R" -eq 3
grader k3 '{}' -- story xx; R=$?
chk "形の違う ID は exit 3" test "$R" -eq 3

echo "== (k4) codex の形（-o で書く）と、codex が無いときの Claude への代替"
mkproj k4
{ story S-01 一つ目 done; } >> "$T/k4/proj/TODO.md"
grader k4 '{"S-01": "合格"}' TODO_LOOP_GRADER=codex TODO_LOOP_GRADE_CODEX="$FG" -- story S-01; R=$?
chk "codex の形で合格（rc 0・-o 経路）" bash -c "[ $R -eq 0 ] && grep -qx 'S-01 form=codex model=' '$T/k4/grader.log'"
chk "監査ファイルの採点者が Codex（版の 1 行つき）" grep -q '^- 採点者: Codex（fake-codex 0.0' "$T/k4/state/audits/${DAYK}_S-01.md"
grader k4 '{"S-01": "合格"}' TODO_LOOP_GRADER=codex TODO_LOOP_GRADE_CODEX=/nonexistent/codex TODO_LOOP_GRADE_TAG=fb -- story S-01; R=$?
chk "codex 不在 → claude 経路で採点（rc 0・sonnet）" bash -c "[ $R -eq 0 ] && tail -1 '$T/k4/grader.log' | grep -qx 'S-01 form=claude model=sonnet'"
chk "代替の理由が監査ファイル（TAG 付きの名前）に書かれる" grep -q '^- 代替の理由: codex CLI が見つからない' "$T/k4/state/audits/${DAYK}_S-01_fb.md"

echo "== (k5) batch: history.log から対象を選ぶ・graded.tsv・batch_since の自動・2 回目は 0 件・_MAX"
mkproj k5
{ story S-01 一つ目 done; story S-02 二つ目 done; story S-03 戻った物 doing; } >> "$T/k5/proj/TODO.md"
mkdir -p "$T/k5/state/runs"
printf '%s\n' "20260101-000000 S-01 実装 opus todo -> done rc=0 sec=10 log=a" "20260101-010000 S-02 実装 opus todo -> done rc=0 sec=10 log=b" \
  "20260101-020000 S-03 実装 opus todo -> done rc=0 sec=10 log=c" > "$T/k5/state/runs/history.log"
cp -R "$T/k5" "$T/k5m"; : > "$T/k5m/grader.log"
grader k5 "$G3" -- batch; R=$?
chk "対象 2 件（S-03 は今 doing なので外す）" grep -q '対象 2 件: S-01 S-02' "$T/k5/out.txt"
chk "batch_since が history の最初の stamp で作られる" test "$(cat "$T/k5/state/audits/batch_since")" = 20260101-000000
chk "graded.tsv に 2 行（S-01 合格・S-02 不一致）" test "$(awk -F'\t' '{print $2 "=" $3}' "$T/k5/state/audits/graded.tsv" 2>/dev/null | tr '\n' ' ')" = "S-01=合格 S-02=不一致 "
chk "監査ファイルは _batch 付き" bash -c "[ -f '$T/k5/state/audits/${DAYK}_S-01_batch.md' ] && [ -f '$T/k5/state/audits/${DAYK}_S-02_batch.md' ]"
chk "不一致の S-02 は 引き継ぎ に追記・終了コード 1" bash -c "[ $R -eq 1 ] && grep -q '^- 引き継ぎ:.*採点: 不一致（.*_S-02_batch\.md）。' '$T/k5/proj/TODO.md'"
grader k5 "$G3" -- batch; R=$?
chk "もう一度 batch → 対象 0 件・exit 0・採点者を呼ばない" bash -c "grep -q '対象 0 件' '$T/k5/out.txt' && [ $R -eq 0 ] && [ $(calls k5) -eq 2 ]"
grader k5m "$G3" TODO_LOOP_GRADE_MAX=1 -- batch
chk "_MAX=1 で 1 件だけ採点" bash -c "[ \$(wc -l < '$T/k5m/state/audits/graded.tsv') -eq 1 ] && grep -q '上限 1 件で止める' '$T/k5m/out.txt'"
grader k5m "$G3" TODO_LOOP_GRADE_MAX=1 -- batch
chk "次の起動で残りの 1 件（S-02）" bash -c "[ \$(wc -l < '$T/k5m/state/audits/graded.tsv') -eq 2 ] && grep -q '対象 1 件: S-02' '$T/k5m/out.txt'"
mkproj k5n
grader k5n '{}' -- batch; R=$?
chk "history.log が無ければ exit 3" test "$R" -eq 3

echo "== (k6) DRY: 採点者を呼ばず指示文を出す（場所・ストーリー全文が入り、固有の窓口名が無い）"
mkproj k6
{ story S-01 一つ目 done; } >> "$T/k6/proj/TODO.md"
grader k6 '{"S-01": "合格"}' TODO_LOOP_GRADE_DRY=1 -- story S-01; R=$?
TK6="$(cd "$T/k6/proj" && pwd -P)"
chk "rc 0・採点者を呼ばない・監査ファイルを作らない" bash -c "[ $R -eq 0 ] && [ $(calls k6) -eq 0 ] && [ -z \"\$(ls '$T/k6/state/audits' 2>/dev/null)\" ]"
chk "1 行目に採点者の選択" bash -c "head -1 '$T/k6/out.txt' | grep -q '^\[grade\] id=S-01 worker=opus grader=claude model=sonnet'"
chk "指示文に repo の根・プロジェクトの根・ストーリー全文" bash -c "grep -qF -- '- repo の根: $TK6' '$T/k6/out.txt' && grep -qF -- '- プロジェクトの根: $TK6（作業ディレクトリ）' '$T/k6/out.txt' && grep -q '^## S-01 — 一つ目 | 実装 | opus | done' '$T/k6/out.txt' && grep -q '^- 証跡: S-01 の証跡' '$T/k6/out.txt'"
chk "指示文に operantkit の語が無い" bash -c "! grep -qi operantkit '$T/k6/out.txt'"
cp "$T/k6/proj/TODO.md" "$T/k6/tampered.md"; sed -i '' 's/S-01 の証跡/名指し S-01_x.md/' "$T/k6/tampered.md"
mkdir -p "$T/k6/state/audits"; echo x > "$T/k6/state/audits/S-01_x.md"
grader k6 '{}' TODO_LOOP_GRADE_DRY=1 TODO_LOOP_GRADE_TODO="$T/k6/tampered.md" -- story S-01
chk "GRADE_TODO の複製を採点し、証跡が名指しした audits 内のファイルだけ例外に並ぶ" bash -c "grep -qF '採点するストーリー（TODO: $T/k6/tampered.md）' '$T/k6/out.txt' && grep -qE '^    - .*/audits/S-01_x\.md$' '$T/k6/out.txt'"

echo "== (l) runner から採点（TODO_LOOP_GRADE=1）: done の回ごとに 1 回・不一致は 引き継ぎ に追記・状態は done のまま・本文に（採点）"
for c in l l0; do
  mkproj $c
  { story S-01 一つ目 todo; story S-02 二つ目 todo; } >> "$T/$c/proj/TODO.md"
  send_sh $c
  plan $c '{"story": {"S-01": {"state": "done"}, "S-02": {"state": "done"}}}'
done
GL=(TODO_LOOP_GRADER=claude TODO_LOOP_GRADE_CLAUDE="$FG" FAKE_GRADE='{"S-01": "合格", "S-02": "不一致"}')
runner l TODO_LOOP_COUNT=2 TODO_LOOP_NO_REFILL=1 TODO_LOOP_GRADE=1 FAKE_GRADE_LOG="$T/l/grader.log" "${GL[@]}"
chk "偽の採点者が 2 回呼ばれた（S-01・S-02、作業者 opus → sonnet）" bash -c "[ \"\$(tr '\n' ' ' < '$T/l/grader.log')\" = 'S-01 form=claude model=sonnet S-02 form=claude model=sonnet ' ]"
chk "S-02 の 引き継ぎ に 採点: 不一致（…）。" grep -qE '^- 引き継ぎ:.*採点: 不一致（.*_S-02\.md）。' "$T/l/proj/TODO.md"
chk "S-01（合格）の 引き継ぎ には追記しない" bash -c "[ \$(grep -o '採点:' '$T/l/proj/TODO.md' | wc -l | tr -d ' ') -eq 1 ]"
chk "状態は両方 done のまま" bash -c "grep -q '^## S-01 — 一つ目 | 実装 | opus | done$' '$T/l/proj/TODO.md' && grep -q '^## S-02 — 二つ目 | 実装 | opus | done$' '$T/l/proj/TODO.md'"
chk "送信の本文に（採点）の行が 2 本（合格・不一致）" bash -c "[ \$(grep -A20 'KIND=run' '$T/l/sent.txt' | grep -c '^- （採点）S-0[12] ') -eq 2 ] && grep -q '^- （採点）S-01 合格' '$T/l/sent.txt' && grep -q '^- （採点）S-02 不一致' '$T/l/sent.txt'"
chk "標準出力に [todo-loop] [grade] の行" bash -c "[ \$(grep -c '^\[todo-loop\] \[grade\] S-0[12] ' '$T/l/out.txt') -eq 2 ]"
chk "audits/ に監査 2 本" test "$(ls "$T/l/state/audits/${DAYK}"_S-0[12].md 2>/dev/null | wc -l | tr -d ' ')" -eq 2
runner l0 TODO_LOOP_COUNT=2 TODO_LOOP_NO_REFILL=1 FAKE_GRADE_LOG="$T/l0/grader.log" "${GL[@]}"
chk "TODO_LOOP_GRADE 未設定 → 2 件実行・偽の採点者 0 回・audits/ 無し・本文に（採点）無し" bash -c "grep -q '実行 2 件' '$T/l0/out.txt' && [ $(calls l0) -eq 0 ] && [ ! -e '$T/l0/state/audits' ] && ! grep -q '（採点）' '$T/l0/sent.txt'"

echo "== (i) 全スクリプトの構文・文字化け"
for f in "$S"/*.sh "$HERE"/*.sh; do chk "bash -n $(basename "$f")" bash -n "$f"; done
for f in "$S"/*.py "$HERE"/*.py; do chk "py_compile $(basename "$f")" python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f"; done
chk "U+FFFD が 0（plugin の全ファイル）" bash -c "! grep -rl $'\xef\xbf\xbd' '$HERE/../../..'"

echo
echo "結果: 通った $PASS / 落ちた $FAIL"
[ "$FAIL" -eq 0 ]
