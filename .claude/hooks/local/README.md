# hooks/local — ローカル層 hook の置き場

ここは **ローカル層**（gitignore 済み — この README と `signals.json.example` だけがフレーム層として track される）。
ユーザー固有の語彙・固有名詞・個人の運用前提を encode した hook はここに置く。
どの環境でも成立する汎用機構の hook は 1 つ上の `.claude/hooks/`（フレーム層 — commit 候補）。

自己改善ループ（reflection subagent）が saying-fault から hook を起案するときの層判定:

- **local（ここ）**: 検出パターンにユーザー固有要素が入る。settings 登録は
  `settings.local.json` 向けに queue する。迷ったら local（誤 frame は
  ユーザー固有内容を commit 候補にしてしまう — 逆の害は小さい）。
- **frame（親 dir）**: 環境非依存の汎用機構。settings 登録は `settings.json` 向け。

hook 本体だけでなく**語彙設定**もここに置く: `signals.json`（acceptance / correction
シグナルのユーザー語彙 — gitignore 済み）。スキーマとマージ規則は `../_signals.py`、
サンプルは同 dir の `signals.json.example`、較正手順は
`docs/self-improvement-loop.md`「シグナル語彙の較正」節。

実装ノート:

- 共有 helper（`_paths` / `_fire_counter` 等）は親 dir にあるので、import 前に
  `sys.path.insert(0, str(Path(__file__).parent.parent))` を入れる。この形は置き場所に依存するので、
  hook を別の場所（`projects/<name>/.claude/hooks/` の plugin 等）へ移すときは、リポジトリのルートから
  `.claude/hooks` を指す形に直す（直さないと import が失敗し、例外を握る hook は黙って何もしなくなる）。
- telemetry は frame hook と同じ扱い — `record_fire()` を呼べば
  `heaven/tools/telemetry_report.py` の棚卸し（cold-hook 監査）に含まれる。
- settings の行は「ファイルが無ければ何もせず通す」形で書く（`python3 <path>` のままだと、ファイルを
  移した瞬間に exit 2 ＝ツールを止める、になり全セッションが止まる）:
  `f="$CLAUDE_PROJECT_DIR/.claude/hooks/local/<name>.py"; [ -f "$f" ] || exit 0; python3 "$f"`
  手で書かずに `python3 heaven/tools/hook_guard.py` で一括変換してよい（`--check` で検査）。
- hook を移す・消すときは、先に settings の行を外す（または移し先で登録して動くのを確かめてから外す）→
  ファイルを移す、の順。
