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
3. **これは prevention（新規混入を止める）側。tracked への既存混入は 2026-06-21 に一掃済み**
   （home パス直書きを `_paths.py` 由来へ置換）。gitignored のローカル層は対象外 — privacy hook
   （`mask_home_in_text.py` / `strip_user_names.py`）や `allow_tmp_rm.py`・`.local/` は実 home が必要で、
   非露出ゆえ直書きしてよい（むしろ消すと壊れる）。**境界は「tracked か gitignored か」であり「frame か」ではない**。
4. **enforce は「tracked ファイル限定の走査テスト」で行う（Write を止める hook にはしない）。**
   実 home パスは上記ローカル層や docs・コメントに正当に現れるため、書き込みを止める blocking gate は
   false-positive が多い（guard-conflict — measure-first でも観測前の always-on gate は不可）。正しい境界は
   **tracked か gitignored か**で、commit される物だけがクリーンであればよい。
   [`test_no_hardcoded_home_in_tracked.py`](../../hooks/tests/test_no_hardcoded_home_in_tracked.py) が
   `git grep`（tracked のみ走査）で実 home パスとその slug 形（`-Users-...`）を検出し 1 件でも fail する。
   **新規に frame ファイル（とくに新規ファイル）を commit する前にこのテストを走らせる。**
   2026-06-21 promotion: c19f4dd で `cd /Users/you/Developer/llm` 形（実際は実 home）入りの新規コマンドが公開 history へ push され
   （history rewrite でしか消せない実害）、instruction-only では「新規 commit 前の走査」が抜けたのを受け、
   検出テスト付きへ昇格した。再発が続けば morning-check への wiring（日次 surface）を次段として検討する。

</important>
