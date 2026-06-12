# recipes — 自己改善ループの「産物」（非配線）

ここにあるのは、原環境の自己改善ループが**実際の失敗から育てた規範**の実体（hook / workflow / rule / tool）。
フレームの機構（`.claude/hooks/` に配線済みのもの）とは性質が違う:

- **機構**は環境非依存 — どの環境でもループを回すために初日から配線する。
- **産物**は原環境の失敗観測に根拠を持つ — あなたの環境でその失敗がまだ観測されていないなら、
  配線しても telemetry 上ノイズになるだけで、効き目の評価も退役判断もできない。

## 採用条件と手順

**採用条件は「同種の失敗を自環境で観測したこと」**（measure-first）。観測したら:

1. [effective-rules.md](../effective-rules.md) の該当エントリを読む（置き換える素の挙動 / ルール / 効いている根拠 / 移植条件）
2. `hooks/` → `.claude/hooks/` へコピーし、`settings.json` に配線（新 hook は 1 日 observe モード推奨）
   `workflows/` → `.claude/workflows/` へコピー
   `rules/` → `.claude/rules/common/`（または該当プロジェクトの rules）へコピー
   `tools/` → `heaven/tools/` へコピー
3. ファイル冒頭の RECIPE 注記を読み、**原環境の校正値・ドメイン語彙を自環境に合わせて再校正する**

## 一覧

| レシピ | 防ぐ失敗 | 構成物 |
|---|---|---|
| RED-first | bug 修正で再現を取らず直し始め、直っていない修正を積む | `hooks/block_red_first_violation.py` + `workflows/bug-fix.js` |
| 一次ソース監査 | 外部事実を記憶で書いて誤る / 出典を実取得せず引用する | `hooks/audit_fetch_vs_sources.py` + `workflows/web-research.js`（ドメイン校正が深い — 要再校正） |
| hedge 監査 | 「念のため」「影響不明」で根拠の無い懸念を量産する | `hooks/block_hedged_concerns.py`（reflection が新 hook を起案する際の雛形でもある） |
| home パス / 個人名マスク | 出力を外部へ中継する環境で生パス・個人名が漏れる | `hooks/mask_home_in_text.py` + `hooks/strip_user_names.py`（中継 transport を持つ環境のみ価値あり） |
| tmp retention | tmp/ の heavy 中間物が削除主体不在のまま蓄積する | `rules/tmp-retention.md` + `tools/tmp_bloat_report.py` |
