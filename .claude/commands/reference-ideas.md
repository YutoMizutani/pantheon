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

## 手順

### 1. 対象を把握する (deterministic・cheap)

- reference 一覧: `ls projects/llm/reference/{tools,prompts,workflows,models}` + `find ... -type f | wc -l`。
- 適用先 unit 一覧: `find projects heaven -name CLAUDE.md` (粒度 = 最近接 CLAUDE.md unit が canonical) + `PROJECTS.md`。

### 2. reference を本文まで読む — 並列 worker で digest (token-heavy → 委譲)

reference は**タイトルでなく本文の技術的中身**を読む。158本を親 1 人で読むのは非効率なので、テーマ別クラスタ (例: 研究/知識 ・ メディア/収益 ・ 自動化/取引/収集) に分割し、`Explore` worker を並列起動する (model-routing: token-heavy×verifiable は worker)。

各 worker への指示テンプレ (返すもの):
1. reference名 → 適用先 project名 (最近接 CLAUDE.md unit)
2. 使える具体技術を 1-2文 (本文から。一般論禁止)
3. 適用の具体形: その project に何を足す/変えるか 1-2文
4. 確度: **「資産実在 (高)」と「価値が出る仮説 (中/低)」を分けて**書く
5. 既に同等機能があれば **「既存で充足」と明記** (重複提案を弾くため)
6. 刺さらない reference は無理に当てない

worker は evidence (逐語要点) を返し、**採否は親が握る** (worker の結論をそのまま採らない)。

### 3. 親が裁定する — 崩落モードを落とす

worker の素材から、上記崩落モード 1-5 に該当するものを**親が落とす**。残すのは「新しい挙動・出口を生む」マッチだけ。

### 4. 適用先の実在を確認してから中継 (必須)

worker が挙げた適用先 app/path を `ls projects/<name>/apps/` 等で**実在確認**してから出力に含める。存在しない app を中継したら崩落モード 6。

### 5. 出力

- **刺さる順に少数** (目安 3-6本)。各々: 知見の具体技術 / 適用の具体形 / 確度 (資産実在 vs 価値仮説を分離) / 接地 (実在確認した app パス)。
- **推しを 1つ** + 逆算した便益。
- **最低1本は自己批判**で落とした案を明示する (なぜ落としたか)。さらに推しの弱点も 1つ暴く。
- 日本語。user が 1本以上を指したら、そこから先 (設計→実装) は通常フローへ。この command は **生成まで**。

## 検証フック

この command を改訂したら `empirical-prompt-tuning` で再検証する。崩落モード (上記6つ) のどれかが出たら指示側の曖昧さを疑い、本ファイルを直す。
