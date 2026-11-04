# PROPOSAL ブロックの出し方（self-reflection 係が PROPOSAL を出す前に Read する）

## 形と必須フィールド

**永続キューには一切書かない**（退役済み）。最終メッセージ末尾に下の `PROPOSAL:` ブロックを 1 件ずつ出力して親へ返す。
親が**この同じセッション内で** user に提示し、user が承認したものだけを**親がその場で適用する**。未承認のまま
セッションが閉じたら**却下**（次セッション等へ残さない）。

必須フィールド: `target_file`（定義の「target の選び方」のいずれか）、`approval`（`required`/`not-required` —
required = hook・settings・CLAUDE.md 系・agent 定義・新しい Skill の新設・不可逆な外部作用。not-required = 道具・
プロジェクトのファイル・memory 本文・既存の Skill の改訂（`kind=skill-edit`）。迷ったら required。親は not-required を
そのターンで適用して報告し、required だけを user に 1 行の可否で訊く）、`layer`（`"local"`/`"frame"` — target から導出）、
`insert_after_section` または `replace_section`、`diff_to_apply`（実テキスト）、`source_memories`、`rationale`
（なぜ memory 止まりではなくこの層・この target か 1-2 行 — ユーザー固有か環境汎用かの判定根拠を含める）。

```
PROPOSAL: <kind=claudemd|hook|agent-def|skill|skill-edit|remove>
target_file: <...>
approval: required|not-required
layer: local|frame
insert_after_section | replace_section: <...>
diff_to_apply: |
  <実テキスト>
source_memories: [...]
rationale: <1-2 行>
```

## 再提案 churn の抑制（PROPOSAL を出す *前* に必ず実行）

`python3 heaven/tools/proposal_ledger.py check-and-record --source "<source_memories をカンマ区切り>" --target "<target_file>" --change "<diff_to_apply の本文>"`
を実行する。出力が `SUPPRESS count=N` なら **その `PROPOSAL:` ブロックは出さない** — 代わりにサマリへ
`proposed-suppressed: <見出し> (TTL 窓内に既に N 回提案・未採用 → churn 抑制)` の 1 行を出す（user は気づいて望めば
明示再依頼できる）。`PROCEED count=N` なら通常どおり `PROPOSAL:` を出す。台帳は抑制専用（read して「出さない」だけ）で
TTL 自動失効する。

## 種類ごとの追加手順

- `kind=hook` → 同じフォルダの `hook.md` を Read してから出す
- `kind=skill` / `kind=skill-edit` → 同じフォルダの `skill.md` を Read してから出す
- `kind=remove` → `diff_to_apply` に消す物を全部並べる（settings の登録・本体・テスト・ログ/状態ファイル・symlink・
  `rg -uu` で引いた参照）。`rationale` に経歴の数字（狙った失敗の頻度の前後・自分の誤作動で直された回数）と、
  撤去後も狙った失敗を受け持つ既存の機構を書く。`approval` は required
