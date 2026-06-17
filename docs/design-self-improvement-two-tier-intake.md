# Design: 自己改善ループ intake の二層化（stateless correction nudge + acceptance gate）

| 項目 | 値 |
|---|---|
| 状態 | Proposed（2026-06-17）— 実装未着手 |
| 置換対象 | 退役した global correction queue（INC-2026-06-17-01） |
| 関連 | [incidents/2026-06-17-correction-queue-cross-session-drift.md](incidents/2026-06-17-correction-queue-cross-session-drift.md) / [self-improvement-loop.md](self-improvement-loop.md) |

## 1. 目的と原則

global queue 経路（cross-session 汚染 + 「自己改善」self-feed）を、**構造的に**それらを起こせない形へ置換する。

- **surface-not-persist（correction/負）**: correction 検出時は main Claude に「何を外したか」を in-context で促すだけ。queue も memory も持たない。
- **human-gate-for-durable（acceptance/正）**: memory 書き込みは明示の acceptance（「完了/ok」）でのみ。"memory without review" を原理排除。
- **stateless / per-session**: correction 側に永続状態を持たない → cross-session 概念そのものが消滅。
- **robust-to-false-positives**: 誤検出のコストを「1 行 nudge × 1 回」に限定（誤っても安い）。

## 2. アーキテクチャ（二層・相互排他）

| 層 | 引き金 | スコープ | 動作 | 永続副作用 |
|---|---|---|---|---|
| Tier 1: correction nudge（負） | correction 語彙（`違う`/`だめ`/`間違って`…）に部分一致 | 当該セッションのみ（stateless） | main Claude へ nudge を**注入するだけ** | **無し**（memory/queue/spawn なし） |
| Tier 2: acceptance reflection（正） | 完全一致「完了/ok/ありがとう/できた」 | 当該セッションのみ | reflection サブエージェント spawn（**唯一の memory writer**） | memory（subject-anchor で保護済） |

**相互排他**: acceptance は prompt 全体の完全一致、correction は critique 語の部分一致。correction 発話は本文を伴い完全一致しないので両立しない。global queue・drain・cross-session の連結は一切無し。

## 3. process-design-frame チェックリスト

### 設計層
1. **タスク**: (a) correction 検出（語彙＋guards） (b) nudge 注入 (c) acceptance 検出→reflection（既存・不変）
2. **フロー**: UserPromptSubmit ごとに correction 検出 *or* acceptance 検出（分岐のみ）。barrier 無し・queue 無し・状態遷移無し。
3. **成果物 I/O**: correction hook の出力 = UserPromptSubmit の context 注入（instruction 1 ブロック）。decision は出さない（ターンを止めない）。acceptance = 既存の Agent spawn。
4. **プロンプト設計**: 下記 nudge text（単一責務 / 禁止事項 / 出力契約 明記）。

### 運用層（各要素に failure-it-prevents / observable-signal）
1. **状態**: correction = 無し（stateless）/ acceptance = per-session debounce file（既存）
   - failure-it-prevents: global queue 由来の cross-session 汚染（INC-2026-06-17-01 で実発生）
   - observable-signal: `pending_correction_reflections.json` が二度と生成されない（存在＝regression）
2. **失敗境界**: hook 例外 → fail-open（stderr 1 行＋exit 0、ツールを止めない）
   - failure-it-prevents: hook バグが user のターンをブロック
   - observable-signal: 例外時も exit 0、UserPromptSubmit が常に通る
3. **観測性**: `record_fire("correction_nudge","audit")` で発火を telemetry へ
   - failure-it-prevents: 誤検出の過剰発火を見逃す（kill-switch 用）
   - observable-signal: `hook_fires.jsonl` の `correction_nudge` 件数/日（閾値超で vocab 見直し）
4. **契約**: input=UserPromptSubmit payload（prompt/transcript_path/cwd/session_id）。発火主体=hook。idempotent（debounce で同一やり取りに多重 nudge しない）。**role invariant: correction 経路は memory を書かない・spawn しない**
   - failure-it-prevents: nudge が durable 副作用を持ち session/log を汚す（退役前の症状）
   - observable-signal: correction 経路から memory 書き込み/queue/spawn が grep で 0 件
5. **コスト**: regex 1 回 + 注入 1 ブロック。LLM call も spawn も無し
   - failure-it-prevents: v1 即時 spawn（14 fires/日）の noise 再来
   - observable-signal: correction あたり追加コスト ≈ nudge 1 ブロックのみ（spawn telemetry 0）

## 4. コンポーネント spec: correction nudge hook

- **配置/層**: 機構は汎用 → frame（`.claude/hooks/inject_correction_nudge.py`、または退役した `detect_correction_signal_v2.py` を rewrite）。vocab は local（`signals.json`）。
- **event**: UserPromptSubmit
- **検出と guards**:
  - correction 語彙に部分一致
  - 既存の **third-party-negation 除外** / **acceptance-prefix 除外** を維持（既知 FP）
  - **語彙から topic 語を除去**: `自己改善` / `次から` / `今後は` / `再発防止` / `分析して…修正` を correction トリガから外す（self-feed #3 の根。これらは「改善要求」であって「失敗の指摘」ではない）
  - prev turn = assistant（訂正対象がある） / debounce（per-session の短い窓） / system・wakeup prompt は skip
- **アクション**: UserPromptSubmit の context（additionalContext）に nudge を注入するのみ
- **nudge text（案）**:
  > [correction-signal] 直前のあなた(Claude)の応答への訂正の可能性があります。応答の前に、直近のやり取りで自分が外した点を 1–3 行で自己診断し、その場で修正してください。**reflection の spawn・memory 書き込みはしないこと** — durable な学習は user の明示 acceptance（「完了」「ok」等）を待ちます。訂正でなければ（第三者の状態説明等）無視して通常応答してください。
- **state**: 無し。**退役機構（queue/drain）は復活させない**。

## 5. signals.json 変更（local）

- 退役で `correction` ブロックごと削除済 → **tightened な correction block を再追加**:
  - `patterns`: `違う`/`ちがう`/`だめ`/`ダメ`/`間違って(る|いる)`/`間違い`/`…じゃない\?`/`おかしい`… は維持。**topic 語（自己改善/次から/今後は/再発防止/分析して…修正）は入れない**
  - `third_party_negation` / `acceptance_prefix`: 維持
  - `explicit_improvement`: acceptance 側でしか使わない → correction nudge では参照しない（不要なら削除）
- `acceptance` ブロックは現状維持。

## 6. 配線（grounded）

- UserPromptSubmit は単一 entry: 全 `~/.claude/settings.json` → `user_prompt_submit.sh` → `_run_hook.sh user_prompt_submit` → `session_bridge.hooks.user_prompt_submit`（app venv で実行、scope guard で `~/Developer/llm` 外は no-op）。
- acceptance 検出もこの orchestrator から走る。correction-nudge は **同じ entry に並べて呼ぶ**。
- **【実装時 open item】**: `session_bridge.hooks.user_prompt_submit` が現在どこで `detect_acceptance_signal`（および退役前の correction 検出）を chain しているかを特定し、同じ場所に nudge 検出を追加する。

## 7. 追加を弾く 3 ゲート（通過記録）

- **measure-first**: 防ぐ失敗（Claude が correction に under-react → 同テーマで反復差し戻し→user フラストレーション）は本セッションで実観測。機構コストは near-zero（regex+1 行）。loud だが「active 注入で確実に react させる」価値で正当化。✔
- **generalize-on-recurrence**: 既存規範（`feedback_never_anger_user_absolute` / `feedback_user_observation_outranks_proxy_diagnosis`）は「怒らせるな/観測を上書きするな」止まりで「correction を検出して in-context で react させる」構造を持たない → narrow 新設でなく本機構で enforce。✔
- **instruction-first**: 受動的 CLAUDE.md norm（Claude の気付き依存・本セッションで実際に滑った）でなく、**正しい瞬間に instruction を注入する能動形**。✔

## 8. テスト（`tests/test_correction_nudge.py`）

- correction 語彙一致 → context に nudge が出る
- **topic 語単独（自己改善/次から…）→ nudge 出ない**（self-feed 防止の回帰テスト）
- third-party-negation / acceptance-prefix → 出ない（既存 FP 防止）
- prev turn が user / system prompt → 出ない
- **いかなる入力でも memory/queue/spawn を作らない**（副作用ゼロ assert）
- 完全一致「完了」→ correction nudge は出ず acceptance 経路（既存テスト不変）

## 9. 移行・実装手順（退役 commit と同時に行う）

1. correction-nudge hook 追加（frame）
2. `signals.json` に tightened correction vocab 追加（local）
3. 配線（§6 の entry に追加）
4. テスト追加・GREEN、**live 1 回で nudge が実際に注入されるか確認**（over-claim しない）
5. `docs/self-improvement-loop.md` を二層 intake に更新 + 本 design / INC へリンク
6. 退役（archive・cleanup、現 working-tree）+ 本実装を **1 commit にまとめる**（user 指示: 退役単独 commit はしない）

## 10. リスク / open items

- 配線の正確な chain 点（§6）— 実装の最初に特定
- nudge が UserPromptSubmit context で確実に main に届くか — 実装時に live 1 回で観測してから完了と言う（unit test は proxy）
- debounce 窓のチューニング（連続訂正で毎ターン nudge は冗長 → 短い per-session 窓）
- topic 語除去後の vocab 精度（FP/FN を `hook_fires` で観測、kill-switch）
