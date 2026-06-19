---
description: projects/llm/reference/ に蓄積した外部知見 (記事/ツール/prompt/workflow) を実際に本文まで読み、既存 projects に *適用* できる案を少数・確信・接地付きで出す。repo-ideas (新規ビルド生成器) と別レイヤの「既存資産に外部知見を移植する」生成器
argument-hint: "(引数なし。reference 全体 × 既存 project 全体を対象にする)"
---

> v0 (2026-06-15, このセッションで効いた手順の忠実な写し)。**未検証**: N=1・user 操舵込みで効いただけ。
> 信頼する前に `empirical-prompt-tuning` で標準実行者に回し、崩落モード (下記) に落ちないか確認する。
> reference の蓄積が増えた後の再実行を主目的に作った command。

# reference-ideas — 「溜めた外部知見を既存 project にどう移植するか」を出す

## これは何で、何でないか

- **これ**: `projects/llm/reference/` (158+ ファイルの外部記事/ツール/prompt/workflow) を**本文まで読み**、各知見が**既存 project の具体機能に刺さるか**を判定して、移植案を出す。
- **これでない**: repo-ideas (= repo を自画像として読み「次に何を *新しく* 作るか」)。reference-ideas は新規ビルドでなく、**既存 project に外部知見を *移植* する**。両者は別レイヤ。
- **これでない**: feature-loop (= 単一 project の内部シグナルからの改善案)。reference-ideas の入力は**外部知見**であって project の内部使用シグナルではない。

## 絶対に避ける崩落モード (このセッションで実際に弾いたもの)

1. **incremental 詐称**: 「既存機能を高速化/自動化度UP」を適用案と呼ぶ。→ 禁止。新しい挙動・出口を生まないものは落とす ([[feedback_durable_automation_foundational_substrate_not_ephemeral]])。
2. **思想補強のみ**: 「この project の運用思想と一致する」だけで新機能性ゼロ。→ 落とす。
3. **主目的外 cosmetic**: project の主目的でない UI/装飾に外部知見を当てる。→ 落とす。
4. **asset-wiring (%削減)**: 価値を工数% / コスト%削減で語る。→ 禁止。
5. **survey**: 尤もらしいマッチを大量に並べて user に選ばせる。確信欠如のサイン ([[feedback_no_user_pick_from_self_options]])。→ 少数に絞り推しを明示。
6. **worker 提案の無検証中継**: worker が挙げた適用先 app/path を実在確認せず出す。→ 禁止。中継前に親が `ls`/grep で接地 ([[feedback_run_own_gate_before_surfacing_subagent_proposal]] / [[feedback_confirm_observation_before_asserting]])。
7. **mechanism-blind マッチ (最頻・最高コスト)**: reference の宣伝 capability を適用先の*表層説明*に当て、その capability が適用先の*実 bottleneck/失敗機構*に作用するか未確認のまま value を付ける。→ 禁止。v0 実行で **★推し含む 2/2 検証済み案がこれで全滅**: task1 (Scrapling: 対象は CSS セレクタ不使用＝ Smart Element Tracking が効く面ゼロ)・task2 (GLM-OCR: bottleneck は item_id sibling 曖昧性で文字判読でない＋2nd OCR が既に held-out で net-negative A/B)。**value 付与前に必ず mechanism-fit precheck (step 3) を通す** ([[feedback_match_remedy_to_failure_mechanism]] / [[feedback_verify_existing_mechanism_coverage_before_claiming_gap]])。

## 手順

### 1. 対象を把握する (deterministic・cheap)

- reference 一覧 (= sweep 対象): `find projects/llm/reference/{tools,prompts,workflows,models} -type f -name '*.md' | wc -l` で件数確認 (現状 ≈154本)。**`projects/llm/reference/` 直下の index.md と内部フレーム文書 (process-design-frame.md / effort-and-fanout-policy.md / codex-worker-setup.md) は「溜めた外部知見」でないので対象外** — sweep するのは上記 4 サブディレクトリ配下のみ。
- 適用先 unit 一覧: `find projects heaven -name CLAUDE.md` (粒度 = 最近接 CLAUDE.md unit が canonical) + `PROJECTS.md`。

### 2. reference を本文まで読む — 並列 worker で digest (token-heavy → 委譲)

reference は**タイトルでなく本文の技術的中身**を読む。step 1 の一覧 (≈154本) を親 1 人で読むのは非効率なので、`Explore` worker を並列起動して digest を分担する (model-routing: token-heavy×verifiable は worker)。

**クラスタ分割ルール (worker 担当の決め方 — 裁量で割らない)**:
1. **テーマは「reference の技術ジャンル」で切る**（「適用先 project のドメイン」でなく。worker が digest しやすい単位にする）。
2. **1 worker の担当が ~30本を超えないように割る**。`tools/` (~97本) が最大塊なので**必ずサブテーマで複数 worker に分割**し、単一 worker に渡さない。サブテーマ例: ①Claude Code 運用/設定/CLAUDE.md ②skills/agents/MCP ③外部 OSS ツール・データ収集 ④UI/メディア/その他。`prompts`(15)・`models`(5) は各 1 worker、`workflows`(37) は重ければ 2 分割。結果として**合計おおむね 6-9 worker**になる。
3. **分割後に「各 worker 割当数の合計 == step 1 の件数」を検算**し、取りこぼし・重複ゼロを確認する（silent coverage gap 防止 — 1本でも未割当なら、その reference は永遠に評価されない）。

各 worker への指示テンプレ (返すもの):
1. reference名 → 適用先 project名 (最近接 CLAUDE.md unit)
2. 使える具体技術を 1-2文 (本文から。一般論禁止)
3. 適用の具体形: その project に何を足す/変えるか 1-2文
4. 確度: **「資産実在 (高)」と「価値が出る仮説 (中/低)」を分けて**書く
5. 既に同等機能があれば **「既存で充足」と明記** (重複提案を弾くため)
6. 刺さらない reference は無理に当てない
7. **適用先の実 bottleneck/失敗機構を cheap な内部証拠 1 つで接地** (適用先コードの grep / 最新 log / 過去 A-B)。その上で「この技術はその機構の面に作用するか」を yes/no で明記。reference 側の宣伝でなく**適用先の実体**を読む。当てられない/作用しないなら自分で落とす。

worker は evidence (逐語要点) を返し、**採否は親が握る** (worker の結論をそのまま採らない)。

### 3. 親が裁定する — 崩落モード + mechanism-fit precheck で落とす

worker の素材から、上記崩落モード 1-7 該当を**親が落とす**。残った候補には **value を付ける前に mechanism-fit precheck を必須で通す** (崩落モード 7 対策の本丸):

1. **適用先の現 bottleneck/失敗機構を内部証拠 1 つで名指し**する — 適用先コードの grep / 最新 eval log / 過去 A-B 結果。*reference の説明でなく適用先の実体*を読む。
2. **「この reference の capability はその機構の面に作用するか」を yes/no で接地**。no なら value=低で **drop**。
3. **等価な手が既に試され却下／既に実装・移植済みでないか**を確認 (過去 A-B で net-negative / 同等機能が実装済み 等)。試済み却下・実装済みなら **drop** (ただし実装済みで*未配線*なら「配線残作業」として survive 可)。

precheck を通過したものだけ「新しい挙動・出口を生むか」で最終選別する。

> precheck は survive 候補 (3-6本) にだけ走るので cheap。v0 で全滅した 2 件も生成時に落とせた — task1=『対象 scraper を grep → CSS セレクタ不使用』、task2=『最新 eval log を読む → 2nd OCR が net-negative＋miss は item_id sibling』。**いずれも 1 grep / 1 log read で判明する**。

### 4. 適用先の実在を確認してから中継 (必須)

worker が挙げた適用先 app/path を `ls projects/<name>/apps/` 等で**実在確認**してから出力に含める。存在しない app を中継したら崩落モード 6。

### 5. 出力

- **刺さる順に少数** (目安 3-6本)。「刺さる順」は次の **lexicographic 順** で決める (上位軸が同点のときだけ下位軸を見る): ① mechanism-fit=YES (NO は survive 不可) → ② **新しい挙動・出口を生む強さ** (incremental は下位・崩落モード1) → ③ 価値 → ④ 問題の確度 (tie-break)。各々: 知見の具体技術 / 適用の具体形 / 確度 (資産実在 vs 価値仮説を分離) / 接地 (実在確認した app パス)。
  - 注: ② が ③ より上位。価値が高くても新挙動が弱い incremental 案は、価値中でも新挙動が強い案より下になる。
- **推しを 1つ**: 上記順の最上位 (= mechanism-fit YES かつ新挙動が強く価値が高いもの) + 逆算した便益。
- **survive が 0 本のとき (全候補が precheck で drop)**: 無理に推しをでっち上げない。出力は「刺さる案 0」+ 各 drop の接地 (適用先内部証拠 + capability の作用面が no) + 最有力だった表層マッチを自己批判枠で明示、の形にする。**precheck で全滅は失敗でなく正常な結果** (mechanism-blind を防いだ証拠)。
- **最低1本は自己批判**で落とした案を明示する (なぜ落としたか)。さらに推しの弱点も 1つ暴く。
- 日本語。user が 1本以上を指したら、そこから先 (設計→実装) は通常フローへ。この command は **生成まで**。

## 検証フック

この command を改訂したら `empirical-prompt-tuning` で再検証する。崩落モード (上記6つ) のどれかが出たら指示側の曖昧さを疑い、本ファイルを直す。
