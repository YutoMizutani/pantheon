# hooks/local — ローカル層 hook の置き場

ここは **ローカル層**（gitignore 済み — この README だけがフレーム層として track される）。
ユーザー固有の語彙・固有名詞・個人の運用前提を encode した hook はここに置く。
どの環境でも成立する汎用機構の hook は 1 つ上の `.claude/hooks/`（フレーム層 — commit 候補）。

自己改善ループ（reflection subagent）が saying-fault から hook を起案するときの層判定:

- **local（ここ）**: 検出パターンにユーザー固有要素が入る。settings 登録は
  `settings.local.json` 向けに queue する。迷ったら local（誤 frame は
  ユーザー固有内容を commit 候補にしてしまう — 逆の害は小さい）。
- **frame（親 dir）**: 環境非依存の汎用機構。settings 登録は `settings.json` 向け。

実装ノート:

- 共有 helper（`_paths` / `_fire_counter` 等）は親 dir にあるので、import 前に
  `sys.path.insert(0, str(Path(__file__).parent.parent))` を入れる。
- telemetry は frame hook と同じ扱い — `record_fire()` を呼べば
  `heaven/tools/telemetry_report.py` の棚卸し（cold-hook 監査）に含まれる。
- settings からの起動はフルパス指定なので配置だけで動く:
  `python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/local/<name>.py"`
