# hook の提案（self-reflection 係が `kind=hook` を出す前に Read する）

## 起案と層判定

安定した phrase を持つ saying-fault なら: hook スクリプトを起案し、settings 登録 diff を `PROPOSAL:` ブロック
（kind=hook）で親へ返す（settings 直接編集禁止・永続キュー不使用・in-session で user 承認後に親が適用）。

**起案前に層判定**（二層構成: frame = pantheon git 同梱の汎用機構 / local = ユーザー固有・gitignore 済み）:

- 検出パターンにユーザー固有の語彙・固有名詞・個人の運用前提が入るなら **local** — 置き場
  `.claude/hooks/local/<name>.py`、登録 diff は `settings.local.json` 向け、冒頭で
  `sys.path.insert(0, str(Path(__file__).parent.parent))` してから `_paths`/`_fire_counter` を import する。
- どの環境でも成立する汎用機構なら **frame** — 置き場 `.claude/hooks/<name>.py`、登録 diff は `settings.json` 向け
  （commit 候補として git status に現れる）。
- 提案に `"layer": "local"|"frame"` を必ず含める。**迷ったら local**（誤 frame はユーザー固有内容を commit 候補にする —
  逆の害は小さい）。
- 機構に repo の絶対パスを書かない（`_paths.py` で解決する — `.claude/rules/common/no-hardcoded-repo-path.md`）。

## 検証不変条件（既存 hook を書き換える・新設するとき必須）

`diff_to_apply` を「関数ごと置換」等の範囲指定や要旨だけで済ませない。出す前に次の 6 点を実際に行い、
結果を `rationale` に 1 行で書く —

1. 対象 hook の実ファイルを scratchpad へ複製し、提案の差分をその複製へ実際に当てる（新設なら scratchpad に本体を書く。
   本番の `.claude/hooks/` には置かない）
2. 当てた複製を本番と同じ呼ばれ方（標準入力に JSON を渡して起動）で動かし、例外で落ちないことと exit code を確かめる
3. 置換で消える関数・変数が残りのコードから参照されていないかを grep で確かめる
4. テストケースの「止めるべき / 止めるべきでない」の分類は、実シェル（zsh）で 1 回ずつ実行した結果か transcript の実例で
   付ける（推測で分類しない）。止める型の hook は、既存の安全ガードの正当な出力（「止まります。指示ください」等）が
   通るケースを必ず入れる
5. 添付の検証コードと提案本文のコードを同じものにする（別版で検証しない）
6. 当てた複製を、手元で最大の会話記録で測り、最大メモリと秒を書く（`python3 heaven/tools/hook_facts.py --cost <複製のパス> --event <event> --matcher <matcher>`。hook は応答のたびに同時に走るので、1 本の重さが全体に効く）。「読まなかった」と出たら、記録を読む経路に入る入力で測り直す
