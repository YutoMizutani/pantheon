# 設計原理 — このフレームは何で、なぜこの形か

## 3 つの核

このフレームの価値は次の 3 つに集約される。他のすべてはこの 3 つに仕える計器か、採用待ちのレシピである。

1. **ルーティング** — ルート `CLAUDE.md` がメタエージェントとして入力を `projects/<name>/CLAUDE.md`（人格定義）へ振り分ける。人間は常に 1 つの窓口とだけ対話する。
2. **heaven/** — projects/ はユーザーの持ち物、heaven/ はエージェントの持ち物という所有権の分離。memory の実体を heaven/ に置いて symlink することで、エージェントの長期記憶の持ち運び・バックアップの単位がリポジトリディレクトリに揃う（内容はユーザー固有なのでローカル層 — git 管理外）。
3. **自己改善ループ** — 訂正（「違う」）と受諾（「ok」完全一致）の両シグナルから規範を獲得し、telemetry で効き目を計測し、効かない規範を退役させる閉ループ。受諾側（META reflection）は「ユーザーがわざわざ指摘した失敗しか学べない」という訂正シグナルの非対称を補う。

## 3 層モデル — 同梱物の選別基準

抽出・配布するものは「**効力の根拠がどこに在るか**」で 3 層に分類している。表層の属人性（名前・パス）を消すだけでは足りない — 根拠ごと運べないものを配線して出荷すると、受領者は効き目の評価も退役判断もできなくなる。

| 層 | 定義 | 扱い |
|---|---|---|
| **機構** | 環境非依存。ループ自体（検出 / reflection / telemetry / memory 衛生 / 棚卸し） | 配線して出荷（`.claude/settings.json`） |
| **校正値** | 機構は一般だが、定数（類似度閾値・行数上限）は原環境の corpus で校正されたもの | 環境変数で上書き可能にし、docstring に**再導出手順**を同梱。定数だけを信じない |
| **産物** | 原環境の失敗観測に根拠を持つ規範（RED-first / 一次ソース監査 / hedge 監査 / tmp-retention 等） | `docs/recipes/` に**非配線**で同梱。採用条件 = 同種の失敗を自環境で観測したこと |

産物を初日から配線しない理由: (1) フレーム自身の measure-first ゲート（hook を足すのは silent×costly な失敗を観測してから）と矛盾する。(2) 発火履歴ゼロの規範が棚卸しに混ざると、cold 判定が「まだ失敗していないだけ」と「この環境では効かない」を区別できず、退役ループが初日から機能不全になる。

## 自己改善ループのデータ依存

ループが必要とするデータと、その供給元:

| データ | 供給元 | 備考 |
|---|---|---|
| `session_id` / `transcript_path`（全文ログ jsonl へのポインタ） | **Claude Code harness が hook の stdin payload で毎イベント供給** | フレーム側で記録する必要はない。検出 hook が reflection subagent のプロンプトに埋め込み、subagent が transcript 全文を読んで失敗を特定する |
| 全文ログ jsonl 本体 | harness が `~/.claude/projects/<slug>/*.jsonl` に自動記録 | **保持期間に注意**: Claude Code の `cleanupPeriodDays`（既定 30 日）で消える。reflection はシグナル発生の直後に走るためループ運用には十分だが、過去セッションの長期横断分析をしたい場合は設定延長か退避を検討 |
| hook 発火 telemetry | `_fire_counter.record_fire()` → `~/.claude/projects/<slug>/telemetry/hook_fires.jsonl` | 棚卸し（cold hook 検出・再発率）の入力 |
| memory 参照 telemetry | `touch_memory_on_read.py` → 同 `telemetry/memory_touches.jsonl` | 読まれていない memory の検出 |
| memory 実体 | `heaven/memory/`（symlink 経由で auto-memory 規約パスと一致） | リポジトリディレクトリ内だがローカル層（git 管理外） — バックアップ単位が揃うだけで commit はしない |

つまり「セッション ID と全文ログ」はフレームの外（harness）が一次供給し、フレームはそれを**消費する配線**（payload 読み取り → reflection への注入 → session 単位の debounce / queue 記録）を持つ。リポジトリ内に永続するのは telemetry と memory で、transcript は harness 管理という分担。

## ルーティングと規範配置の原則

- プロジェクト一覧は `CLAUDE.local.md` の 1 節だけ（更新箇所を 1 つに保つ。一覧はユーザー固有なのでローカル層）。
- ルートは薄く: ルーティングと機構のみ。ユーザー固有の原則は `CLAUDE.local.md`、具体ルールはプロジェクト配下へ。衝突時はローカル層 > プロジェクト固有 > 共通 rules。
- 規範は「気をつける」でなく構造で enforce: saying-fault → hook、judgement-fault → memory。enforce したら telemetry で計測し、効かなければ退役。
