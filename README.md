# pantheon

> 一つの窓口（メタエージェント）の奥に、専門エージェントたちが並ぶ万神殿。

Claude Code で「複数の専門エージェントを 1 リポジトリで運用し、**運用しながら自己改善させる**」ためのフレーム。2 つの柱からなる:

1. **ルーティング**: ルートの [CLAUDE.md](CLAUDE.md) がメタエージェントとして振る舞い、`projects/<name>/CLAUDE.md` を人格定義とする専門エージェントへ入力を振り分ける。
2. **自己改善ループ**: ユーザーの訂正（「違う」）と受諾（「ok」完全一致）を hook が検出 → background reflection が memory / hook として規範化 → 全 hook の発火を telemetry に記録 → 効いていない規範を棚卸しツールと rule-auditor agent が退役提案する閉ループ。→ [docs/self-improvement-loop.md](docs/self-improvement-loop.md)

## 構成

```
CLAUDE.md              # メタエージェント（ルーティングと機構のみ — フレーム層）
CLAUDE.local.md        # ユーザー固有設定（プロジェクト一覧・個人原則 — ローカル層、git 管理外）
CLAUDE.local.md.example  # ↑の雛形（セットアップ時にコピーされる）
.gitignore             # 二層の境界を定義（下記「二層構造」参照）
.ignore                # ripgrep ignore — 横断検索を安く保つ
.claude/
├── settings.json  # hook 配線（$CLAUDE_PROJECT_DIR ベース、コピーしてそのまま動く）
├── hooks/         # 自己改善ループの「機構」hook のみを配線（tests/ 付き）
│   ├── _paths.py                      # 環境導出のパス解決（ハードコード無し）
│   ├── detect_correction_signal_v2.py # 訂正シグナル → AUTO-LEARN reflection 起動
│   ├── detect_acceptance_signal.py    # 完了シグナル（ok 等の完全一致）→ META reflection
│   ├── propose_claudemd_updates.py    # Stop 時に project CLAUDE.md 更新案を queue
│   ├── _fire_counter.py / _memory_touch.py / touch_memory_on_read.py  # telemetry
│   └── block_memory_duplicate.py / block_memory_index_bloat.py        # memory 衛生
├── agents/rule-auditor.md             # 規範棚卸しエージェント
└── rules/common/                      # 全プロジェクト共通ルール置き場（初期状態は空 — ループが育てる）
heaven/            # エージェントの自由領域（projects/ はユーザーの持ち物、ここはエージェントの持ち物）
├── memory/        # auto-memory の実体（ローカル層 — git 管理外）。~/.claude/projects/<slug>/memory
│                  # からここへ symlink を張る → heaven/README.md
└── tools/         # ループの棚卸し計器: telemetry_report / rule_adoption_report /
                   # memory_adoption_report / memory_lint / skill_gc（tests 付き）
docs/
├── design-rationale.md        # 設計原理 — 3 つの核 / 3 層モデル / ループのデータ依存
├── built-in-mechanisms.md     # 組み込み機構の機能説明（退役機構 / 条件付きルール / 自己修復 / memory 衛生）
├── self-improvement-loop.md   # 自己改善ループの全体像（まずこれを読む）
├── effective-rules.md         # 介入レシピカタログ — 原環境で効いた規範・設計理由・採用条件
├── recipes/                   # レシピの実体（hooks / workflows / rules / tools）。配線されていない
└── new-project-setup.md       # 新規プロジェクト作成手順・ルール適用条件と hook の仕様
projects/
└── example-infra/             # プロジェクトテンプレート（コピー元・骨格のみ）
    ├── CLAUDE.md              # 人格・役割・禁止事項（必ず書き換える）
    ├── .claude/               # settings.json の骨格のみ（拡張は必要時に自作）
    └── context|tools|logs|outputs|reference|tmp/
```

## 二層構造

このリポジトリは「フレーム層 = git 管理」「ローカル層 = git 管理外」の二層で運用する。**実際に運用しながら、汎用的な改善だけを commit してフレームを育てる**ための構造で、境界は [.gitignore](.gitignore) が定義する。原則は **1 パス 1 オーナー** — どのパスもどちらか一方にだけ属し、同一パスを「リポジトリ版とローカル版で別内容」にしない。

| 層 | 中身 | 例 |
|---|---|---|
| フレーム層（tracked） | どの環境でも通用する機構 | CLAUDE.md・hook・計器・docs・example-infra |
| ローカル層（ignored） | この環境のユーザー固有の人格・状態 | CLAUDE.local.md・projects/*・heaven/memory/*・settings.local.json |

- ルート直下とユーザー所有領域（`projects/` `heaven/`）は **ローカルが既定**: 運用中に増えた個人ファイルは commit 対象に現れない。
- フレーム層ディレクトリ（`.claude/` `docs/` `heaven/tools/`）の内側は **tracked が既定**: 運用中に育てた hook / doc が `git status` に現れ、「commit してフレームを育てるか、ローカルに残すか」を判断する動線になる。
- 環境固有の ignore 追加は `.gitignore` でなく `.git/info/exclude` へ（`.gitignore` 自体がフレーム層のため）。

## Install

No `curl | sh`, no setup script — just tell Claude: `このリポジトリをセットアップして`

セットアップでエージェントが `CLAUDE.local.md` の作成（雛形: [CLAUDE.local.md.example](CLAUDE.local.md.example)）と memory symlink の配線まで行う。

## Usage

人間が手を動かすのは Install まで。以降の拡張も会話で依頼する:

- 最初のプロジェクトを作る — 「`<ドメイン>` 用のプロジェクトを作って」（example-infra のコピー・CLAUDE.md 書き換え・`CLAUDE.local.md` の一覧への追記までエージェントの仕事）
- `CLAUDE.local.md` の「全体方針（ユーザー固有の原則）」を自分の運用方針に合わせて埋める（ここだけは人間の価値判断）

詳細は [docs/new-project-setup.md](docs/new-project-setup.md)。

## 設計原則

- **二層構造（1 パス 1 オーナー）**: フレーム層（tracked = 汎用機構）とローカル層（ignored = ユーザー固有の人格・状態）を `.gitignore` で分離し、実運用から汎用的な改善だけを commit する。→ 上記「二層構造」
- **配線するのは機構、産物はレシピ**: settings.json に配線して出荷するのは自己改善ループの機構（検出 / reflection / telemetry / memory 衛生 / 棚卸し）だけ。原環境の失敗から育った規範（RED-first / 一次ソース監査 / tmp-retention 等）は [docs/recipes/](docs/recipes/) に採用条件付きの非配線レシピとして置き、**同じ失敗を自環境で観測してから**採用する（measure-first）。これにより telemetry の cold 判定が「まだ失敗していないだけ」と「この環境では効かない」を混同せず、棚卸しが初日から機能する。
- **校正値は再導出手順とセット**: 機構は移植できるが、閾値（類似度・行数上限など）は原環境の corpus で校正された定数であり移植できない。校正値を持つ hook は docstring に再導出手順を持つ — 定数だけを信じない。
- **ルーティングの単一インデックス**: プロジェクト一覧は `CLAUDE.local.md` の「既存プロジェクト一覧」節だけに書く（更新箇所を 1 つに保つ。一覧はユーザー固有なのでローカル層）。
- **ルートは薄く**: ルート CLAUDE.md にはルーティングと抽象方針のみ。具体ルールは各プロジェクトの `CLAUDE.md` / `.claude/rules/` へ。
- **規範は構造で enforce**: 「気をつける」を増やすのではなく、saying-fault は hook、judgement-fault は memory に落とす。増やした規範は telemetry で計測し、効いていないものは退役させる。→ [docs/self-improvement-loop.md](docs/self-improvement-loop.md)
- **ルールの適用条件**: ファイル単位（frontmatter `paths:`）とセクション単位（`<important if="...">`）の 2 段階で、ルールが効く範囲を指定できる。仕様は [docs/new-project-setup.md](docs/new-project-setup.md)。
- **プロジェクト単位の git**: 親リポジトリ（このフレーム）が track するのはフレーム層だけで、`projects/*` はローカル層として ignore される（example-infra を除く）。したがってコード資産を持つプロジェクトは `projects/<name>` 単位で独自に git 管理してよく、親 repo と衝突しない。公開したいプロジェクトがある場合のみ、自分の fork で `.gitignore` に `!/projects/<name>/` を追記する。
- **拡張は同梱せず自作で育てる**: テンプレートは骨格のみで、拡張（agents / commands / skills / rules / hooks）を同梱しない。必要になったものだけを自作し、自己改善ループで育てる（外部の agent 集を使いたい場合は各プロジェクト配下に clone すればよい）。書き方の仕様は [docs/new-project-setup.md](docs/new-project-setup.md)、実働の見本はルートの `.claude/`。
