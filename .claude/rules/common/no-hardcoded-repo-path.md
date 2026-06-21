---
description: frame 層の機構（hook / tool / 計器）を作成・編集するとき、リポジトリの絶対パスを直書きせず _paths.py（CLAUDE_PROJECT_DIR 由来）で解決する（全環境共通）
paths:
  - .claude/hooks/**
  - heaven/tools/**
---

# 機構にリポジトリの絶対パスをハードコードしない（frame は env 非依存）

> 背景: 複数の hook がリポジトリの絶対パス（例: `block_red_first_violation.py` の
> `LLM_ROOT = Path("/Users/you/Developer/llm")`）を直書きしていた。機構（環境非依存・git 出荷）に
> 単一運用者の絶対パスを埋めると、フレーム層の「どの環境でも再生できる」前提と矛盾し移植不能になる
> （meta-audit round-2 リスク#6 で観測）。**作成時に防げる再発パターン**なので規範化する。

<important if="`.claude/hooks/` または `heaven/tools/` 配下の hook / tool / 計器を新規作成・編集している">

1. **`/Users/you/Developer/llm` のような絶対パスを直書きしない（実 home パスも当然不可）。** リポジトリルート・状態ディレクトリ
   （memory / telemetry / runtime）は既存の [`_paths.py`](../../hooks/_paths.py) で解決する。これは
   `CLAUDE_PROJECT_DIR` 環境変数（Claude Code harness が供給）由来で env 非依存:
   ```python
   from _paths import PROJECT_DIR, STATE_DIR, MEMORY_DIR, TELEMETRY_DIR, RUNTIME_DIR
   ```
   `_paths.py` に無い派生先が要るなら、各ファイルでルートを再定義せず **`_paths.py` 側に追加**する。
2. **理由（3層モデル）**: 絶対パス・単一運用者前提の値は「機構」でなく「校正値」
   （[docs/design-rationale.md](../../../docs/design-rationale.md) の3層分類）。機構に校正値を埋めるのは
   層の混在で、frame の可搬性主張（「どの環境でも*再生できる*機構」）を掘り崩す。
3. **これは prevention（新規混入を止める）側**。既存の混入の掃除は別タスク
   （`projects/llm/apps/meta-audit/alignment-plan.md` の②、第二運用者が出てから）。
4. **hook 化はしない。** `/Users/you` のような home パスは docs・ログ・コメントに正当に現れるため、書き込みを止める
   blocking gate は false-positive が多い（guard-conflict）。この規範で再発が止まらないことを観測してから、
   observe-mode 付きの検出器を後付け検討する（instruction-first）。

</important>
