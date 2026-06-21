---
description: Pantheon ハーネスの自己改善ループが「実際に機能しているか」を、計装済みテレメトリの数えられる事実だけで証拠ダイジェストにする。評価語禁止・正直な裏面必須・第三者が同じ script を再実行して検証できる形。projects/ の生成物でなくハーネス自動化そのものを対象にする
argument-hint: "[窓日数] (省略時 30。例: /review:pantheon 7)"
---

> v3 (2026-06-20)。`empirical-prompt-tuning` 3 iter / 9 ラン（標準実行者・「映え圧」「提案要求」の adversarial 含む）で収束。
> critical 軸（評価語ゼロ / 裏面非空 / projects ズレ無）は 9/9 で 100%。tuning ledger 81175d50 = provisional
> （ship 後の hook 突合は手動 confirm）。改訂したら再び empirical-prompt-tuning にかけること。
> 起点は session e621adf8 の Part 1 中立評価と tmp/glossary-tendency-draft.md の再フレーム。

# /review:pantheon — ハーネス自動化の証拠ダイジェスト

## これは何で、何でないか

- **これ**: Pantheon の自己改善ループ（会話→シグナル検出→reflection→memory/hook 化→telemetry→棚卸し）が
  **実際に回っているか**を、計装済みツールの **count だけ**で人間可読のダイジェストにする。
  「会話から情報を蓄積し自動化する機構」が文章で実際に確認でき、第三者が同じ script を再実行して
  数字を突き合わせられる ＝ 自己主張でなく**外部監査可能な証拠**になる。
- **これでない**:
  - **rule-auditor ではない**（同じテレメトリを読むが**別レンズ**）。rule-auditor は使用量 GC ＝
    「何が死んでるか・何を退役させるか」の**提案リスト**。本コマンドは「ループが回っている実数」の
    **証拠ダイジェスト**で、deprecation 提案も改善 todo も出さない。
  - **projects/ の成果物レビューではない**。対象はハーネスのメタ層（loop / routing / hook / memory）だけ。
    個別 project のコード品質は別物。
  - **自己評価ナラティブではない**。`非常に優れています` `◯◯より構造的に最適です` のような評価語は、
    出た時点でこのコマンドの失敗。**数字に語らせ、形容を足さない**。

## 規律（load-bearing — ここが本体）

1. **評価語の全面禁止**。`優れ/最適/秀逸/堅牢/素晴らしい/最先端/世界水準/excellent/superior` および
   比較断定（`◯◯より良い/最適`）を出力に入れない。出たら書き直す。
2. **全ての主張に `count + 出所ツール + 窓` を併記**する。形式: `<事実> (出所: <tool>, 直近N日)`。
   出所を示せない文は載せない（それは自己申告）。
3. **正直な裏面を必ず 1 ブロック出す**。COLD hook・NEVER-READ memory・再発中（NOT-INTERNALIZED）rule・
   滞留 queue・redo-rate。**裏面ゼロのダイジェストは赤信号**（ツールは常に何かしら surface する。
   ゼロなら集計が落ちている）。
4. **ツールの解釈意味を反転しない**。とくに **hook の高発火 = good ではない**
   （enforcement rule の HOT は「再発し続け lesson が未内面化」のサイン）。memory の総数増加だけを
   健全さと読まない（read/adopted/enforced の方が効力指標）。
5. **決定論的に集計する**。subagent に「どれだけ優秀か」を採点させない（自己採点の罠に逆戻りする）。
   親が下記 script を実行し、出た数字をそのまま整形する。

## データソース（実在確認済み・2026-06-20）

**窓の実効範囲を誤魔化さない（重要）**: `$ARGUMENTS`（空なら 30）は「要求窓」だが、**実効するのは memory 実体の
`find -mtime` だけ**。report 系（telemetry/rule/memory_adoption）は固定 30d 出力、jsonl 系（correction/reflection）は
`wc`/`grep` で全期間集計、redo/queue は全期間 tally。つまり窓は揃わない。**だから窓ラベルは要求窓でなく「各行の実出力期間」を書く**
（report 系=`直近30日` / jsonl 系=`全期間` / memory mtime=`要求窓` ※要求窓 7 なら `直近7日`、省略時 30 なら `直近30日`）。
要求窓と実窓が食い違う行はその旨を 1 語で添える。header には要求窓と「ツール別実窓は各行参照」を両方書く。
**実行コマンドを header に列挙**して再現可能にする（監査の核）。

```bash
cd "$CLAUDE_PROJECT_DIR"
SLUG=~/.claude/projects/$(printf '%s' "$CLAUDE_PROJECT_DIR" | sed 's/[^A-Za-z0-9]/-/g')

# (1) hook 計装・発火・COLD・HOT
python3 heaven/tools/telemetry_report.py

# (2) rule 内面化（HOT = 再発 = 未定着。NOT-INTERNALIZED を裏面へ）
python3 heaven/tools/rule_adoption_report.py

# (3) memory 蓄積と採用（active/read/adopted/enforced・NEVER-READ を裏面へ）
python3 heaven/tools/memory_adoption_report.py

# (4) 会話シグナルの検出と reflection 発火（＝「会話から蓄積」の一次証拠）
wc -l "$SLUG/telemetry/correction_dispatch.jsonl" "$SLUG/telemetry/reflection_gate.jsonl"  # <a>=dispatch行数 / <b2>=reflection_gate行数(gate評価総数)
grep -c '"decision": "fire"' "$SLUG/telemetry/reflection_gate.jsonl"  # <b>=発火数(decision=fire)

# (5) memory 実体の蓄積速度（git でなく mtime — heaven/memory は gitignored）
WIN=30   # ← 要求窓。$ARGUMENTS があればその数値に置換（空なら 30 のまま）。mtime はこの WIN に追従させる
find heaven/memory -name '*.md' | wc -l                  # <c>=総数（窓に依らない実体数。結論の memory 代表値はこれを使う）
find heaven/memory -name '*.md' -mtime -${WIN} | wc -l   # <d>=要求窓内の追加更新（窓が実効する唯一の行 → 実窓=要求窓で一致）

# (6) 委譲の redo-rate（kill-switch 状態）
python3 projects/llm/apps/model-router/redo_rate.py

# (7) 滞留 queue（消費主体未定義の溜まり＝裏面）
python3 heaven/tools/pending_queue_report.py 2>/dev/null
```

## 手順

1. `$ARGUMENTS` を窓日数とする（空なら 30）。上記 (1)〜(7) を実行し、生 count を集める。
2. 下記テンプレに数字を流し込む。**規律 1〜5 を 1 行ずつ self-check** してから出す。
3. 各数字に出所ツールを併記。評価語が混ざっていないか最終 grep（自分の出力を見て 優れ/最適/superior を探す）。
4. ユーザに日本語で簡潔に提示。**結論行は count の再掲のみ**（「閉ループが回っている根拠 = 訂正 X 件検出・
   reflection Y 回発火・memory Z 件 enforce。同時に効いていない物 = COLD N・NEVER-READ M・再発中 K」）。

## 出力テンプレ

各 `<...>` は実数で置換。窓ラベルは行ごとの実出力期間（上記「窓の実効範囲」に従う）。

```
# Pantheon ハーネス証拠ダイジェスト — 要求窓 <N> 日（ツール別実窓は各行参照）
> 実行: <列挙した script>。誰でも再実行して数字を突き合わせられる。

## 1. 閉ループの実数（会話 → 蓄積 → enforce）
- 会話シグナル検出: 訂正 dispatch <a> 件 / reflection 発火（=gate decision=fire）<b> 回（gate 評価 <b2> 回中） (出所: correction_dispatch.jsonl + reflection_gate.jsonl, 全期間)
- 蓄積（速度）: memory 実体 <c> 件・要求窓内で <d> 件 mtime 更新 (出所: find heaven/memory, 要求窓)
- 蓄積（採用）: active <m> 件 / read <e>・adopted <f>・enforced <g> (出所: memory_adoption_report, 直近30日)
- 自動化 enforce: hook <h> 本・うち計装 <i>・発火 <j> 回 (violation <k> / activity <l>) (出所: telemetry_report, 直近30日)

## 2. 効いていない実数（正直な裏面）
- COLD hook（30日 0 発火・退役候補）: <列挙> (出所: telemetry_report, 直近30日)
- NEVER-READ memory: <数>件（うち load-bearing で退役非候補 <数>件） (出所: memory_adoption_report, 直近30日)
- READ-BUT-NEVER-ADOPTED memory（recall されるが応答に反映されない降格候補）: <数>件 (出所: memory_adoption_report, 直近30日)
- 再発中の rule（lesson 未内面化）: <数>件・上位 <例（hits 付き）> (出所: rule_adoption_report, 直近30日)
- 滞留 queue / redo-rate: <状態> (出所: pending_queue_report / redo_rate, 全期間)

## 3. 結論（count の再掲のみ・形容なし）
<上記の数字を 1〜2 文で再掲（memory 代表値は実体総数 <c>・enforce 数は <g>）。評価語ゼロ。>
```

## rule-auditor との境界（重複回避）

同じ `heaven/tools/*.py` を読むが、**出力の意図が直交**する:

| | 入力 | 出力 | 起動 |
|---|---|---|---|
| rule-auditor (agent) | 同テレメトリ | 退役候補・未計装 hook・滞留の**提案リスト**（GC） | 週次 /「ルール整理して」 |
| **/review:pantheon** | 同テレメトリ | ループが回る**証拠ダイジェスト**（count のみ・提案なし） | 「ハーネス評価」「pantheon どう機能してる?」 |

データ収集は再実装しない（既存ツールを呼ぶ）。提案が欲しくなったら rule-auditor、証拠が欲しいなら本コマンド。

## 任意の補助入力（spine ではない）

tmp/glossary-tendency-draft.md の §2（意図≠命令のエスカレーション判定台帳）は、**writer hook が配線されたら**
§1 の補助セクションに足してよい。ただし現状 writer は未配線で、自己申告になるため**背骨にしない**
（measure-first: 「エスカレーション読み違いが silent×costly に再発」を確認してから writer を配線）。

## 検証フック

改訂したら `empirical-prompt-tuning` で再検証。実行結果に評価語が 1 つでも混ざる / 裏面ブロックが空になる /
projects 成果物の話が紛れ込む、のどれかが出たら指示側の曖昧さを疑い本ファイルを直す。
