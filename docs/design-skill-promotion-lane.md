# Design: skill-promotion lane — 手順型の学びを skill / workflow へ結晶化する正極性の昇格弁

> status: draft（2026-06-22 起案・未適用）。適用は self-reflection.md / docs/self-improvement-loop.md への
> in-session 承認後（self-modification: 親が user 承認を得てから適用）。
> 関連: [self-improvement-loop.md](self-improvement-loop.md) / [design-root-cause-auditor.md](design-root-cause-auditor.md) /
> [process-design-frame.md](../projects/llm/reference/process-design-frame.md)

## 1. 目的（最重要・取り違え注意）

自己改善ループは現在 **負極性しか持たない**。訂正・失敗から学び、その学びを
**memory / hook / rule / agent-def**（＝「Xするな」型の guardrail）へ落とす。これは
「既知のミスをしなくなる」装置で、学習の天井がそこで頭打ちになる。

欠けているのは **正極性の弁** — 「効く手順を再現する」昇格。器は guardrail でなく
**skill（Pantheon の実体は `.claude/commands/*.md`）／必要なら workflow 同梱**。
この設計はループに `kind=skill` の PROPOSAL 経路を 1 本足し、**手順型の学びが
常時ロード prose や prose-only memory に滞留するのを止める**。

**取り違え防止**: これは「成功を自動採掘して skill を量産する」機構ではない（それは churn を生む）。
trigger は下記 §4 の **「memory が実行可能な *手順* 型か」** という単一判定であり、
auto-mine ではなく既存の memory 生成を経由した *deliberate* な結晶化である。

## 2. 既存との差別化（責務が直交）

| 弁 | 極性 | trigger | 産物 |
|---|---|---|---|
| memory / hook / rule（既存） | 負（guardrail） | 訂正・失敗 | 「Xするな」型の prior / 決定論 block |
| agent-def 昇格（既存） | 負（prompt fault 是正） | agent の prompt に根がある fault | agent 定義の強化 |
| **skill-promotion（本設計）** | **正（capability）** | **memory が実行可能な手順型** | **`.claude/commands/<name>.md`（+ optional workflow）** |
| skill_gc（既存） | — | 未使用 skill | 可逆 archive（**死の弁**） |

**ループは死の弁（skill_gc）を既に持つが生の弁を持たない** — この非対称の解消が本設計。
品質 gate（`empirical-prompt-tuning`）も既にあるので、**欠けているのは入口（birth）だけ**。

## 3. スコープ（接地証拠）

本設計の母集団は仮説でなく実在する。手書きで生まれた skill が、いずれも
memory / incident に明示接地している（git frontmatter で確認済・2026-06-22）:

- `repo-ideas` — 「このセッションで効いた手順の忠実な写し」/ SSoT = memory
  `feedback_value_discovery_read_repo_as_revealed_preference`
- `reference-ideas` — 同上「効いた手順の写し」
- `forget-approach` — 「設計は本環境の maplestory.io 削除トレースに接地」/ incident doc 紐付け
- `bug-fix`（RED-first）/ `web-research`（一次ソース先行）— いずれも失敗 memory が含意した
  *手順* を手で skill+workflow に引き上げたもの

→ **正極性の結晶化は既に高頻度で起きており、効いており、100% 手動**。ループに入口が無いだけ。

## 4. trigger（単一判定 — 「この memory は手順か規範か」）

reflection は step 4 で memory を書く / touch する。そのとき **その memory の内容が
*実行可能な多段手順* か、*反射的に効くべき規範* か**を 1 行で自己判定する:

- **規範型**（「検証せず断定するな」「user を見下すな」）→ 従来どおり memory / hook。
  反射的に効く必要があり、能動 invoke を待てない。**skill 化しない**（invoke 漏れで死ぬ）。
- **手順型**（「repo を revealed preference として読み次を出す」「却下手法を残骸ごと除去する」）
  → 下記 gate を全通過するなら **`kind=skill` PROPOSAL** を追加で出す。

この判定 1 本で、(a) **手順欠落型の失敗**（手順を持っていなかったから失敗した — 答えは
「Xするな」でなく手順の付与）と (b) **成功の結晶化**（効いた方法の memory）の両方が拾える。
両者とも「memory が手順型」に帰着するため、別 trigger を増やさない。

### gate（全通過で初めて PROPOSAL — 追加を弾く 4 ゲート §7 と一致）

1. **手順型である**（上の判定）— 規範型は除外。
2. **generalize-on-recurrence**: その手順が複数 session で再導出された／再現価値がある。
   単発 success は memory 止まり（churn 防止）。
3. **measure-first の手順版**: 再導出が *silent かつ高コスト*。loud で安い再導出は prose memory で十分。
4. **既存 skill と重複しない**（`.claude/commands/` を grep）— あるなら新設でなく既存の拡張。

## 5. 産物と PROPOSAL 形式

`kind=skill` PROPOSAL は **command draft 本体を同梱**する。`reference-ideas` 等の作法を踏襲:

- 冒頭に **`> v0 (日付) 未検証・N=1` マーカー**を必ず付ける（無検証 skill を信頼させない）。
- `description:` は **model 自動 invoke 用**に書く（「〜なとき」形）。
- **deterministic な fan-out / verifiable 多段が要るなら `.claude/workflows/<name>.js` を併設**し、
  command はそれを `Workflow({name})` で起動する薄い wrapper にする（feature-loop / bug-fix と同型）。
  taste-heavy で単一 context の生成（ideas 型）は prompt-only command で足りる。

```
PROPOSAL: kind=skill
target_file: .claude/commands/<name>.md   # + optional .claude/workflows/<name>.js
layer: local|frame                         # §8 open item の層判定に従う
new_file: true
draft_body: |
  ---
  description: <model 自動 invoke 用の「〜なとき」形>
  argument-hint: "<...>"
  ---
  > v0 (YYYY-MM-DD, このセッションで効いた手順の写し)。未検証: N=1。
  > 信頼する前に empirical-prompt-tuning で標準実行者に回す。
  <手順本体>
source_memories: [<結晶化元の手順型 memory>]
rationale: <なぜ memory 止まりでなく skill か — 手順型 + 再現価値 1-2 行>
```

PROPOSAL の受け渡し・承認は既存と不変: **永続キューに書かず return-value で親へ返し、
親が同 session で user 承認を得てから適用**。未承認でセッションが閉じたら却下。

## 6. 安全不変条件（self-modification の歯止めを継承・拡張）

`kind=skill` も agent-def 昇格と同じ安全ゲートを継承する。skill は user-facing な
*capability* なので、guardrail より歯止めを強める:

- **強化方向のみ**: 提案 skill は方法・能力を *足す* もの。**ループ自身の安全ガード
  （propose-only / in-session 決着 / 危害レンズ / 破壊系 hook）を緩める・回避する skill は禁止**
  （HARD BLOCK / Self-Modification）。
- **破壊的 automation を gate 無しで内包しない**: rm / 外部送信 / 不可逆作用を含む手順は、
  既存の該当 gate（block_red_first / tmp-retention 等）を経由する形でしか提案しない。
  例: `forget-approach` が「削除前に必ず候補を user 提示」を明記しているのと同じ規律。
- **empirical-prompt-tuning を通すまで信頼しない**: `未検証 N=1` のまま採用させない。
- **死の弁は skill_gc**: 採用後に invoke されなければ archive される。birth と death が対称になる。

## 7. 追加を弾く 4 ゲート（通過記録）

本設計自身を process-design-frame の 4 ゲートに通した記録（自分のメタ追加にも適用）:

- **measure-first** ✅ — 新しい常時計装 hook を足さない。既に走る reflection 内の *routing 分岐* を
  1 本足すだけ。新規 fire コスト 0。
- **generalize-on-recurrence** ✅ — 並列の新システムでなく、**既存の PROPOSAL 昇格弁を一般化**して
  欠けていた極性／target を足す。trigger 自体も再発を要求する。
- **instruction-first** ✅ — 「reflection が手順型を skill へ流さない」の根は self-reflection.md step 5 の
  kind enum と target list（＝指示層）。補償機構でなく **指示層の編集で直す**。
- **guard-conflict** ✅ — 新しい blocking gate / exit-2 hook を一切足さない（propose-only 分岐）。
  既存の安全ガードの正当出力をブロックする面が無い。

4 ゲート全通過。silent×costly でない loud 事象に gate を積む愚は犯していない。

## 8. 実装手順（承認後・親が適用）

1. **self-reflection.md step 5**:
   - 昇格対象リストに 1 項追加: 「**memory が *実行可能な手順* 型で、§4 gate を全通過**（規範型は除外）」。
   - target list に 1 項追加: 「**手順型 memory → `.claude/commands/<name>.md`（+ optional
     `.claude/workflows/<name>.js`）**（layer: §8 open item に従う）」。
   - PROPOSAL kind enum を `kind=claudemd|hook|agent-def` → **`|skill`** 追加。
   - §6 の安全不変条件を agent-def 用記述の隣に併記。
2. **self-reflection.md Output**: 6 行サマリに **`proposed (skill): <PROPOSAL or none>`** を 1 行追加。
3. **docs/self-improvement-loop.md**: 「成果物の層ルーティング」表と昇格 target 列に skill 行を追加し、
   **birth（本弁）⇄ death（skill_gc）の対称**を明記。冒頭の図にも skill 弁を 1 本足す。
4. 回帰: `test_layer_routing.py` に「手順型 memory → command target / 規範型 → memory のまま」の
   ケースを追加。

## 9. open items

- **`.claude/commands/` の層判定**: 現状 frame（commit 候補）と local が混在
  （`mac-gui-codex.md` は局所運用らしき perms）。手順型 skill の frame/local 振り分け規則を
  hook 同様 `layer` フィールドで明示する必要がある。**迷ったら local**（誤 frame の害が小さい原則を踏襲）。
- **mac-gui-codex の正規化**: command 形だが model 自動 invoke 用 description ＝ 実質 skill。
  本弁の確立後、SKILL.md への正規化を別タスクで（挙動は不変・分類 cleanup）。
- **trigger の自動度**: §4 は reflection 内判定だが、初期は **「手順型かもしれない」を surface するだけ**で
  親/user が結晶化を最終判断する半自動から始め、誤結晶率を測ってから自動度を上げる
  （observe モードの skill 版）。

## 10. Acceptance test: repo-ideas を再生成できるか（replay）

本弁が正しければ、**2026-06-15 に memory `feedback_value_discovery_read_repo_as_revealed_preference`
が書かれた時点で、reflection が `kind=skill` PROPOSAL を出し、`repo-ideas` 相当の command draft を
生成できる**はず。replay 判定:

1. その memory は手順型か → ✅（「repo を revealed preference として読み次を出す」は多段手順）。
2. gate: 再現価値あり（reference 蓄積後の再走目的）/ 再導出が silent×costly（毎回 repo 全読の方法を組み直す）→ ✅。
3. PROPOSAL の draft_body が実物 `repo-ideas.md` の手順骨子（自画像読み→conviction-bet→場面付き出力）を含むか。

3 を満たせば、**手書きで起きた結晶化を本弁が機械的に再生成できる**ことの確認になる
（INC replay と同型の合格条件）。
