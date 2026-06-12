# 新規プロジェクト作成手順

ルート [CLAUDE.md](../CLAUDE.md) のルーティングポリシーで「新しい領域を継続的に扱いそう」と判断され、ユーザーが新規プロジェクト作成に合意した場合の作業手順。

## 標準ディレクトリ構成

新規プロジェクト作成時は、必ず以下の構成を揃える。テンプレート `projects/example-infra/` をベースにコピーし、`CLAUDE.md` の内容をドメインに合わせて編集する。

```
projects/<name>/
├── CLAUDE.md        # エージェントの人格・役割・禁止事項などの定義
├── .claude/         # Claude Code 拡張の置き場（テンプレートは settings.json の骨格のみ）
│                    #   ※ agents / commands / skills は必要になったときに自作して育てる
├── context/         # 追加のドメイン知識・設定を記述するためのファイル群
├── tools/           # 必要に応じて利用するツール・スクリプト群
├── logs/            # 思考過程・中間メモ（yyyyMMdd 形式のサブディレクトリに配置）
├── outputs/         # 最終成果物（後から参照する前提のアウトプット）
├── reference/       # 再利用する根拠データ・参考文献
└── tmp/             # 一時ファイル（削除前提）
```

## 作成手順

1. `projects/example-infra/` をコピーして `projects/<name>/` を作成する
2. `CLAUDE.md` をドメインに合わせて書き換える（役割・応答ポリシー・禁止事項）
3. 必要に応じて `.claude/rules/<name>/` にプロジェクト固有のルールを追加する
4. `README.md` は不要なら削除してよい（テンプレートの説明であるため）
5. `CLAUDE.local.md` の「既存プロジェクト一覧」節にエージェント名と 1 行説明を追記する（一覧はローカル層の単一インデックス — ルート CLAUDE.md には書かない）

## git の扱い

新規 `projects/<name>/` は**ローカル層が既定**（親リポジトリの `.gitignore` が `/projects/*` を ignore）。親 repo に commit されないので、コード資産を持つプロジェクトは `projects/<name>` 単位で独自に git 管理してよい。プロジェクトを親 repo で公開したい場合のみ、自分の fork の `.gitignore` に `!/projects/<name>/` を追記する。

## ルールの適用条件

`.claude/rules/` 配下のルールは、適用条件を 2 段階で指定できる:

1. **ファイル単位** — frontmatter `paths: ["deploy/**", "*.tf"]` で file-glob 条件 (harness が決定的に評価)。例: `projects/<name>/.claude/rules/deploy-lifecycle.md`
2. **セクション単位** — 本文中の `<important if="…">…</important>` / `<important unless="…">…</important>` で content 条件 (Claude が宣言的に self-apply)。

両者は補完関係。「常に適用」「path 条件のみ」「content 条件のみ」「path × content 両方」の 4 通りを使い分ける。

### `paths:` frontmatter（ファイル単位）

```markdown
---
paths: ["**/*.py", "tools/**"]
---
# このファイルのルールは上記 glob に合致するファイルを扱うときだけロードされる
```

path glob は決定的で安価だが、「いまのタスクはバグ修正か」のような**内容条件**を表現できない。そこを次の inline タグが埋める。

### Conditional Rule Tags（セクション単位）

```markdown
<important if="<predicate sentence in English or Japanese>">
The guidance that applies only when the predicate holds.
</important>

<important unless="<predicate>">
The guidance that applies except when the predicate holds.
</important>
```

述語の書き方:

- 短い 1 節にする。`and` / `or` が要るならタグを分ける。
- Claude が**現在のターンだけから自己評価できる語彙**を使う — 例: "the current task is a bug fix", "the file under edit is a test"。外部状態を要する述語は書かない。
- Default closed: 述語が曖昧なら**不一致として読み飛ばす**。

適用の手順: 述語を字義どおり読み、現在のターンの文脈（ユーザー発話・編集対象パス・タスク種別）と突合し、成立ならセクションを作業ルールに取り込み、不成立 or 曖昧なら黙ってスキップする。harness 側の強制は無い — ルールファイルがコンテキストとして読まれることに乗った self-marker の規約であり、だからこそ観測（telemetry）と棚卸しが必要になる。

使い分け: 常に適用 → タグ無しの平文 / ディレクトリ・拡張子で絞る → `paths:`（専用ファイルに切る）/ 内容条件で絞る → `<important if>`。併用可（`paths:` で絞ったファイルの中をさらに `if` / `unless` で絞ってよい）。配線済みの実例はルートの [.claude/rules/common/](../.claude/rules/common/) と [docs/recipes/rules/](recipes/rules/) にある。

## プロジェクト hook の書き方

プロジェクト hook（`projects/<name>/.claude/hooks/`）は、ツール実行の前後で harness が決定論的に走らせるスクリプト — LLM の「気をつける」をコードの「通さない」に変える層。実働する見本はルートの `.claude/hooks/`（tests 付き）、採用条件付きの非配線レシピは [docs/recipes/hooks/](recipes/hooks/) にある。新しい hook を書くときはまずどちらかを 1 本読むのが早い。

最小知識:

- `settings.json` の `hooks` に登録する。イベントは主に `PreToolUse`（実行前 — block 可能）/ `PostToolUse`（実行後 — 観測のみ）/ `UserPromptSubmit` / `Stop`。
- スクリプトは stdin で JSON（`tool_name`, `tool_input`, PostToolUse なら `tool_response`）を受け取る。
- exit code: `0` = 通過、`2` = block（stderr が Claude へのフィードバックになる）。
- パスは `$CLAUDE_PROJECT_DIR`（harness が供給）から導出し、ハードコードしない。
- **どの settings.json に登録するか**: harness が hook を読むのは**セッションを開いたディレクトリ**の `.claude/settings.json`（+ user settings）だけ。フレームルートで運用する通常モードでは、**ルートの** `.claude/settings.json` へ `projects/<name>/...` のフルパスで登録する。プロジェクト配下の `settings.json` が効くのは、そのプロジェクトを単独のセッションルートとして開いたときのみ。

増やすときの規律はフレームの設計ゲートと同じ: measure-first（silent かつ costly な失敗だけに張る）/ 新 hook はまず observe モード（警告のみ）で 1 日走らせてから exit 2 に昇格 / `_fire_counter.record_fire()` で発火を telemetry に残し、0 発火の hook は棚卸しで退役させる。
