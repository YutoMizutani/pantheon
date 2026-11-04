# 構造レビューへの上申（self-reflection 係が `ESCALATION:` を出す前に Read する）

## 出す条件

その fault が **既存 memory / enforce 済み hook がありながら再発**、または **user が「前にも言った／そう言ったはず／またか」と
再発を申告**している場合（シグナル: user の再発申告 — memory が無くても該当し、過去の同型を全 jsonl から型の語で引いて
事例を添える / 同根 memory が既にあり本 session で N 枚目になる — **全 jsonl を型の語で引いた過去の同型が 1 件以上かつ
原因三分類 (i) 未到達なら該当** / `rule_adoption` redo-rate 高 / 同 cluster が複数 session 再発）、memory N+1 を書くのは
その場しのぎの上塗りになりうる。

## 出し方

キューファイルには書かず、最終メッセージ末尾に次のブロックをそのまま 1 件出力して親に返す（親が同じ完了ターンで
`root-cause-auditor` を inline 起動する。sub-agent→sub-agent の直接 spawn ができないため return-value で受け渡す）:

```
ESCALATION: recurrence-despite-memory
target_hint: <疑う機構 / クラスタ>
signal: <再発の根拠 1 行 (既存 memory 名 + 何枚目 + 再発症状)>
origin_session: <sid>
```

memory にも独立価値があれば書いてよいが、「これはその場しのぎ / 機構側が誤っている可能性」を ESCALATION ブロックで
必ず出す（設計: docs/design-root-cause-auditor.md）。出したらサマリにも `escalated: <target_hint / 理由>` の 1 行を足す。
**ESCALATION ブロックの出力だけが許可** — root-cause-auditor の起動・構造の改変は親 / user が別途行う。

## 配線状態の主張は実ファイルと telemetry で接地してから書く

`signal` 欄や memory 本文に「hook が配線済み/未配線」「fire=0 / fire=N」「queue 滞留」など機構の状態を断定する前に:
- (a) 配線の有無は **`rg --no-ignore-vcs <hook名> .claude/settings*.json` か直接 Read** で確認する — `Grep` ツール / 素の `grep` は
  .gitignore を尊重し `settings.local.json` や `.claude/hooks/local/` に盲目になるため、`.claude/settings.json` だけを見て
  「未配線」と結論しない（[[feedback_grep_residual_refs_blind_to_gitignored_config]]）
- (b) fire 数は `~/.claude/projects/<slug>/telemetry/hook_fires.jsonl` を当該 rule_id で grep して実数を出す
