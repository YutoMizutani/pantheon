# Design: root-cause-auditor — 構造的正しさの監査器（premise-challenge + 根本修正提案）

| 項目 | 値 |
|---|---|
| 状態 | Proposed（2026-06-17）— 実装未着手 |
| 仮称 | `root-cause-auditor`（user 命名可） |
| 関連 | [self-improvement-loop.md](self-improvement-loop.md) / [design-self-improvement-two-tier-intake.md](design-self-improvement-two-tier-intake.md) / [incidents/2026-06-17-correction-queue-cross-session-drift.md](incidents/2026-06-17-correction-queue-cross-session-drift.md) |

## 1. 目的（最重要・取り違え注意）

**システム自身の構造の「正しさ」を監査する。** 具体的には:
1. **実装の誤りを検知** — フック / mechanism / settings 配線 / workflow が、その**意図どおりに動くか**・**そもそも正しい設計か**を点検（例: correction queue を *global* にしたのは意図と乖離した実装だった）
2. **memory 記載内の誤りを検知** — memory が**誤ったことを主張していないか**・**構造欠陥を覆い隠す対症療法 (band-aid) になっていないか**
3. **前提を提起** — 各構造に「**それってそもそも正しいんだっけ？ これが本来作るべきものだったか？ user の思い描く姿と合っているか？**」を問う
4. **根本修正を提案** — 今回（2026-06-17 の queue 退役 → 二層再設計）のような構造修正 / mechanism 再設計 / memory 訂正・削除を、根拠付きで提案する

> **非目的（明記）**: 「memory を減らすこと」は目的**ではない**。memory consolidation は上記 (2)(4) の一結果にすぎない。死んだ規範の GC は rule-auditor の責務であって本 agent ではない。本 agent の核は **構造の correctness と premise-challenge**。

## 2. 既存との差別化（責務が直交）

| agent | 問い | 性質 |
|---|---|---|
| self-reflection | このセッションで何を学べるか | per-session・**加算**（memory を書く） |
| rule-auditor | 何が死んでる/腐ってるか | 使用量ベースの **GC**・量的・後ろ向き |
| **root-cause-auditor（新）** | **この構造はそもそも正しいか / 意図と合っているか / 根はどこか** | cross-corpus + cross-mechanism の **correctness 監査**・質的・前提を疑う |

混線回避: 重い質的 correctness 分析を rule-auditor（安価な telemetry hygiene）に混ぜない。別 agent として分離。連携は可（rule-auditor が「腐ってないのに再発し続ける cluster」を本 agent へ渡す）。

## 3. スコープ（監査対象と入力）

- **memory corpus**（内容を読む。usage でなく主張の正しさ・band-aid 性）
- **mechanism マップ**: `.claude/hooks/*.py` / workflows / settings 配線 / 自己改善ループ自身の機構
- **signals**（どこが怪しいかの当たり）: `rule_adoption` の redo-rate（enforce 済みなのに再発）・`hook_fires`・`memory_touches`・`docs/incidents/`・直近の「根治して」発話とその帰結
- **設計意図の所在**: 各構造の docstring / design doc / それを生んだ memory・incident（＝「本来何をすべきだったか」の一次ソース）

## 4. 分析モード（出力する verdict）

各監査対象に対し、根拠（実ファイルの逐語引用）付きで:
- `correct` — 意図どおり・正しい
- `band-aid` — 症状を抑えるが構造欠陥を覆っている（同根の他 memory を列挙）
- `wrong-impl` — 実装が主張/意図と乖離（例: 「根治」と言うが clean form しか扱わない allow_tmp_rm の旧状態）
- `mechanism-vision-gap` — 機構が user の mental model と乖離（例: global correction queue）
- `premise-questionable` — そもそも存在/設計が正しいか疑わしい（「これ要る？」）
- → 各 verdict に **根本修正提案**（構造修正 / mechanism 再設計 / memory 訂正・削除）を添える

**分析の必須手順（§11 の replay 検証が強制した3点 — これが無いと self-justifying な機構を `correct` と誤判定する）:**
- (i) **症状を実際の carrier 機構まで辿る** — 過去の band-aid が当たった場所と root の機構は違いうる（本セッション: band-aid は subject-swap = self-reflection.md、実 carrier は global correction queue）。再発が band-aid 箇所で止まらず別経路で出ていないかを追う
- (ii) **構造の自称 intent を ground truth にしない** — 機構の docstring/design が「これは意図的」と書いていても、それは検証対象の *claim* であって correctness の spec ではない（[[feedback_self_authored_artifact_not_authoritative_spec]]）。claimed-intent を **実 outcome（再発症状）と user の revealed preference** に照らして test する。これが self-justifying な機構（例: 「global は orphan-recovery で意図的」と書く queue）に premise-challenge を効かせる肝
- (iii) **incident に依存しない** — primary evidence（code / memory / telemetry）から root-cause を自力生成する（agent の出力がのちの incident になる）。incident は在れば補助入力

## 5. process-design-frame チェックリスト

### 設計層
1. **タスク**: (a) スコープ確定（cluster / 特定 mechanism / 全 sweep） (b) signals 収集 (c) 各対象の意図 vs 実装の照合 (d) verdict 付け (e) 根本修正提案の起案
2. **フロー**: signals → 怪しい対象を絞る → 各対象を意図ソースと突合 → verdict + 提案 → human-gate queue。barrier 無し（対象ごと独立）
3. **成果物 I/O**: 入力 = scope（任意）+ signals。出力 = `pending_structural_reviews.json`（verdict + 根拠逐語 + 根本修正提案 + 影響範囲）。schema 固定
4. **プロンプト設計**: 単一責務（correctness 監査）/ 禁止（自動改変・推測断定・user mental model の捏造）/ 出力契約（verdict enum + 逐語根拠必須 + 提案は queue へ）

### 運用層（各要素に failure-it-prevents / observable-signal）
1. **状態**: なし（毎回 corpus を実読。権威ソースは実ファイル・telemetry）
   - failure-it-prevents: 古いキャッシュで現状と乖離した監査
   - observable-signal: 提案の根拠引用が現行ファイルと一致（ズレで無効）
2. **失敗境界**: 提案は **propose-only**・human-gate。誤判定しても queue に積むだけ（自動改変ゼロ）
   - failure-it-prevents: over-confident な「これは間違い」が機構を勝手に壊す（＝この agent 自身が次の over-build/over-claim になる）
   - observable-signal: queue 経由以外の mechanism 改変・memory 削除が 0 件
3. **観測性**: `record_fire` + queue 件数 + 採用率（提案のうち user が採った割合）
   - failure-it-prevents: 誤提案を量産しても気づかない
   - observable-signal: 提案採用率が低位安定なら verdict 基準を締める（kill-switch）
4. **契約**: 発火主体 = user 明示 / self-reflection escalation / 定期。idempotent。**role invariant: 自動改変しない・loop の安全ガードを緩める提案を出さない（HARD BLOCK / Self-Modification）**
   - failure-it-prevents: 自己改善ループ自身を監査する agent が安全弁を外す提案を通す
   - observable-signal: safety guard（propose-only/queue/危害レンズ/破壊系 hook）に触れる提案は queue 段階で却下フラグ
5. **コスト**: 高 judgment → **capable model（親 tier）で走らせる**。安価モデルに振らない。起動は稀（明示/escalation/定期）なので burn は限定
   - failure-it-prevents: 安価モデルで浅い correctness 判定 → 誤 verdict
   - observable-signal: model tier が親 tier で固定されている

## 6. トリガ（「明示的に呼べる」を本命に）

1. **user 明示起動**（「構造おかしくない？整理して」「根治候補出して」「この hook 正しい？」）
2. **self-reflection からの escalation**（核心）: per-session reflection が**再発シグナル**を観測したら（同根 memory が N 枚目 / enforce 済みなのに redo-rate 高 / 同 cluster が複数 session 再発）、memory を N+1 枚書く代わりに**構造レビューへ escalate**。
   - 実装注: sub-agent → sub-agent の直接 spawn は harness 上不可 → 「self-reflection が escalation signal を queue（`pending_structural_reviews.json` に trigger 行）→ 親が `root-cause-auditor` を起動」。再発シグナルは既存 telemetry から計算可能
3. （任意）定期 sweep（rule-auditor と同 cadence に相乗りするか独立かは loop 所有権の明示判断）

## 7. 出力と限界（不変の歯止め）

- 出力 = verdict + 逐語根拠 + **根本修正提案 ＋「この構造、あなたの思い描く姿と合ってる?」の問い** → human-gate queue。**自動改変・自動削除は一切しない**
- **限界（重要）**: 「本来作るべきだったもの / 本当に必要だったもの」は **intent gap**（Tree Swing の教訓: 下流は need を復元できない）。agent は **候補を差し出し・問いを早く立てる**だけで、最終判断は user。これがこの agent 自身が次の over-build にならない条件
- 今回の学び（over-claim 禁止・最小 blast radius）を**この agent 自身に適用**: 「これは間違い」を実ファイル逐語根拠なしに断定しない・提案は最小可逆から

## 8. 追加を弾く 3 ゲート（通過記録）

- **measure-first**: 防ぐ失敗（「根治」と誤申告し対症療法が積層・機構が意図と乖離）は実在・costly・semi-silent（再発まで隠れる）。本セッションが直接の exhibit。✔
- **generalize-on-recurrence の例外**: rule-auditor 拡張で済むかを検討したが、correctness 監査（質的）と usage GC（量的）は責務が直交し、混ぜると altitude が濁る → **別 agent が正しい分離**。乱立しているのは memory であって agent ではない。✔
- **instruction-first の例外**: 「規範より構造」という指示は既に在るのに発火しない — **単一セッションに cross-corpus/cross-mechanism の視界が無いから**。指示が正しくても視界を与える機構が要る稀なケース。✔

## 9. 実装手順（承認後）

1. `.claude/agents/root-cause-auditor.md`（agent 定義・親 tier・propose-only/human-gate/HARD-BLOCK self-mod の不変条件を明記）
2. 出力契約 `pending_structural_reviews.json` の schema 確定（verdict enum / 逐語根拠 / 提案 / 影響範囲 / trigger 種別）
3. self-reflection に **再発シグナル → escalation 行を queue** する分岐を追加（memory N+1 を書く代わりに escalate する閾値）
4. 親が queue の escalation を拾って `root-cause-auditor` を起動する経路（明示起動も）
5. （任意）定期 sweep の loop 所有権判断
6. docs/self-improvement-loop.md に三系統（self-reflection / rule-auditor / root-cause-auditor）として反映

## 10. open items
- 再発シグナルの具体閾値（同根 N 枚 / redo-rate %）— 既存 `rule_adoption_report` の指標で定義
- 「memory の同根クラスタリング」をどう接地するか（slug の links / description の意味類似 / 同 incident 由来タグ）
- 定期 sweep を入れるか（明示+escalation で足りるか measure してから）
- agent 自身が「自己改善ループの機構」を監査対象に含む → self-mod 安全不変条件の二重確認

## 11. Acceptance test: 今回セッションの改善を再生成できるか（INC-2026-06-17-01 replay）

**基準（user 指定）**: 本設計が「global correction queue は誤った機構だと検知し、premise を提起し、退役+二層再設計を提案する」を生み出せるか。

trace（band-aid 適用後・構造修正前の状態を入力と仮定）:
1. **トリガ**: cada4938 で band-aid（subject-anchor）適用後、ce6ae5b0 で drift 再発 → 「enforce 済みなのに再発」シグナル発火 → escalation ✓
2. **症状追跡**（必須手順 i）: band-aid は subject-swap(self-reflection.md) に当たっていたが、再発は correction channel 経由 → carrier = global queue まで辿る ✓
3. **前提提起**（必須手順 ii）: queue docstring は「global は orphan-recovery で意図的」と主張 → これを *claim* として test。実 outcome（cross-session 再発）が claim を反証 → `mechanism-vision-gap` / `premise-questionable` verdict ✓
4. **出力**: 「global queue は意図と乖離。候補 = per-session scoping / 退役。orphan-recovery と contamination の trade-off はどちらを取るか?」を human-gate queue + user への問いとして提出 ✓

**判定: passes** — ただし §4 の必須手順 (i)(ii)(iii) を追記して初めて通る。無しだと docstring の「global は意図的」を信じて `correct` と誤判定する穴があった（この replay がその穴を炙り出した）。

**正直な caveat（成功の定義）**: agent は「global は wrong」を**自律決定しない**。contamination > orphan-recovery の価値判断は user（Tree Swing の intent gap）。本セッションの実際の失敗は「誰も問いを立てず user が手で何ターンも押し込んだ」こと。agent の成功 = **その問いと候補を最初の再発で早く差し出す**こと。自律決定でなく**早期 surfacing** がこの agent の生む価値。この基準を満たさない設計変更は regression とみなす。
