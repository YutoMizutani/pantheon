# Skill の提案（self-reflection 係が `kind=skill` / `kind=skill-edit` を出す前に Read する）

## kind=skill-edit — 既存の Skill を使った作業で起きた fault

Skill の手順・それが使う道具や検査・その係の定義に根があるものは、その Skill の本文（`.claude/skills/<name>/SKILL.md`・
`projects/<X>/.claude/skills/<name>/SKILL.md`・`.claude/commands/<name>.md`）、Skill が使う道具・検査、係の定義
（`.claude/agents/`・`projects/<X>/.claude/agents/`）を直す。memory に置いて済ませない（係は memory を読まない）。

手段は `.claude/rules-ondemand/skill-revision.md` の 2 の候補（検査を前に出す／出す側を直す／検査で止める／手順の組み方を
変える／読む文を書き換える／消す・まとめる）を**全部比べて**選び、user に言われた手段や前回の手段をそのまま採らない。
`rationale` に〈起き方（出た工程／見つかった工程）→ 選んだ手段と選ばなかった上流の手段の理由 → 確かめ方（その誤りが
出た実物で止まる・消えるか）〉を書く。

## kind=skill — 手順型 memory の結晶化

- 新設の前に `.claude/skills/`・`.claude/commands/`・`projects/*/.claude/skills/`・`projects/*/.claude/commands/` を grep し、
  同じ用途の Skill があれば新設せず `kind=skill-edit` で既存を直す。
- 置き場は `.claude/commands/<name>.md`（taste-heavy で単一 context の生成は prompt-only / verifiable な多段 fan-out は
  `.claude/workflows/<name>.js` を併設し command は `Workflow({name})` を起動する薄い wrapper にする）。
  layer は層判定に従う・迷ったら local。
- 判別: **反射的に効くべき規範型は除外**（skill は能動 invoke を待てず死ぬ — 観測接地・中立採点等は memory/hook のまま）。
  設計: docs/design-skill-promotion-lane.md

## skill 昇格の追加不変条件（kind=skill）

skill は user-facing な capability なので guardrail より歯止めを強める —
1. ループ自身の安全ガード（propose-only / in-session 決着 / 危害レンズ / 破壊系 hook）を緩める・回避する skill は禁止
   （HARD BLOCK）
2. rm / 外部送信 / 不可逆作用を含む手順は既存の該当 gate（block_red_first / tmp-retention 等）を経由する形でしか提案しない
   （例: forget-approach の「削除前に候補を user 提示」）
3. `insert_after_section/diff_to_apply` の代わりに **`new_file: true` / `draft_body:`** を使い、draft 本体に
   **`> v0 (日付) 未検証 N=1`** マーカーと「信頼前に empirical-prompt-tuning で標準実行者に回す」を必ず書く
4. 採用後 invoke されなければ skill_gc が archive する（birth ⇄ death 対称）
