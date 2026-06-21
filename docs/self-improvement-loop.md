# 自己改善ループ — このフレームの中核機構

このフレームの価値は「ルーティング構造」ではなく、**エージェントが自分の失敗から規範を獲得し、効いていない規範を退役させる閉ループ**にある。構成要素と流れ:

```
ユーザー発話 (UserPromptSubmit) ── 二層 intake (相互排他)
  │
  ├─ 訂正シグナル「違う/だめ/おかしい…」→ inject_correction_nudge.py
  │     └─ main Claude へ <system-reminder> を注入し、in-context で自己診断・即修正を促すだけ
  │        STATELESS — queue/spawn/memory なし。durable 化は下の acceptance を待つ
  │
  └─ acceptance「完了/ok」→ detect_acceptance_signal.py
        └─ 親が background reflection subagent を spawn ＝唯一の memory 書き込みゲート
              transcript を読み、失敗を分類 (saying-fault / judgement-fault / hybrid)
              → memory ファイルを書く (+ saying-fault なら hook を起案)
              → hook の settings 登録は直接編集せず pending キューへ (人間が一括レビュー)
  ▼
規範が 3 層に定着
  │   CLAUDE.md (毎ターンの prior) / memory (長期記憶) / hook (決定論的 enforce)
  ▼
全 hook が発火を記録 ── _fire_counter.record_fire() → telemetry/hook_fires.jsonl
memory の参照も記録 ── touch_memory_on_read.py → telemetry/memory_touches.jsonl
  ▼
棚卸し (週次 or 任意)
      heaven/tools/telemetry_report.py        — 0 発火の cold hook を列挙
      heaven/tools/rule_adoption_report.py    — 再発率 (enforce 後に再発した規範) を算出
      heaven/tools/memory_adoption_report.py  — 読まれていない memory を列挙
      heaven/tools/memory_lint.py             — memory ファイルの形式検査
      heaven/tools/skill_gc.py                — 使われない skill の可逆 archive
      .claude/agents/rule-auditor.md          — 上記を統合し deprecation 候補を提案
  ▼
退役・統合 ── 効かない規範を削り、再発する規範は hook へ昇格
```

> **2026-06-17 — correction intake の二層化（global queue 退役 → stateless nudge へ置換）**
> 設計: [design-self-improvement-two-tier-intake.md](design-self-improvement-two-tier-intake.md)
>
> 旧 `detect_correction_signal_v2.py` + global correction queue を**退役**（`.claude/hooks/.archive/` 退避・
> queue 空・`detect_acceptance_signal.py` の drain コード除去済・signals.json の旧 correction 語彙削除）。
> 退役理由は 2 構造欠陥: (1) **cross-subject drift** — global queue が acceptance 時に全セッションの
> 未処理 correction を一括 drain し、無関係セッションの reflection に混入（あるセッションの振り返りが
> 別セッション由来の提案差し戻しを語った実例）。(2) **自己給餌** — detector が「自己改善」等
> ループ自身の meta 議論にも発火し queue を補充し続けた。
>
> **置換 = 二層 intake**（上図）:
> - **correction（負）= `inject_correction_nudge.py`**（frame・vocab は local/signals.json）— stateless 軽量 nudge。
>   main Claude へ <system-reminder> を注入し in-context で自己診断・即修正を促すだけ。**queue/spawn/memory なし**。
>   topic 語（自己改善 / 次から / 今後は / 再発防止 / 分析して…修正）は correction トリガから除外（self-feed の根）。
> - **acceptance（正）= `detect_acceptance_signal.py`** — **唯一の memory 書き込みゲート**（存続。subject 分析は正常だった）。
>
> 効果: cross-session 概念が構造的に消滅し、誤検出コストは「1 行 nudge × 1 回」に限定（durable 化は明示 acceptance のみ）。

補完する 2 系統（どちらも同梱・配線済み）:

- **detect_acceptance_signal.py** — 「ok / 完了」等の**完全一致**でのみ発火し、META reflection を起動する（どの語が acceptance かは較正値 — 下記「シグナル語彙の較正」）。訂正シグナルだけでは「ユーザーがわざわざ指摘した失敗」しか学べない、という非対称への手当て。完全一致トリガなので observe モード不要で運用できる。**責務はシグナル検出と起動のみ** — 振り返りの方針（一次レンズ・採掘 category・ワークフロー・layer 判定）は `.claude/agents/self-reflection.md`（frame 層エージェント定義）に分離してあり、hook は session_id / transcript / 訂正キューの**動的入力だけ**を渡して spawn する。エージェントは各セッションを**まず危害・不可逆性レンズでゲートし**（バックアップの無い user データの破壊・外部への不可逆作用があれば対話・効率より優先し、踏んだ正確なコマンドを gate=hook 等に食わせて block を実テスト確認する）、次に**『user と Claude の対話』として一次レンズで読み**（user の framing に Claude が収束したか乖離したか／同趣旨の言い直し・差し戻しの反復）、最後に効率・プロセスの採掘 category（冗長手順・避けられた往復・遅すぎた診断 等）を**三次軸**として回す。順序は固定（危害・不可逆性 → 対話 → 効率）。重心を危害と対話理解に置くのは、効率採掘へ偏った reflection が「不可逆な破壊」や「user が正しく言い続けたのに自説で動き続けた」型の最重要失敗を構造的に取りこぼしたため。なお prompt 自体に根がある fault（reflection 自身の優先順位を含む）は memory でなく **agent 定義の昇格**（`PROPOSAL:` ブロックで親へ返す・強化方向のみ・in-session で user 承認後に親が適用）で直す。
- ~~**propose_claudemd_updates.py**~~ — **退役（2026-06-20 user 裁定）**。Stop（セッション終了時）発火で in-session の user 確認ができず、永続キューに規範化候補を貯める設計だったため、永続キュー全廃と同時に settings 登録を外した。規範化候補は self-reflection が `PROPOSAL:` ブロックで親へ返し in-session で決着させる経路に一本化。

## 反省/監査の三系統と escalation 弁（加算だけにしない）

self-improvement は放っておくと「失敗 → memory を 1 枚足す」と**加算**に偏る。これを是正する 3 系統が責務を分けて働く:

- **self-reflection**（per-session・加算）— 1 セッションから学びを memory/hook に落とす。subject 固定。
- **rule-auditor**（cross-corpus・**使用量 GC**・量的）— 0 発火 hook / 未読 memory / queue 滞留を列挙し deprecation 候補。「何が死んでるか」。
- **root-cause-auditor**（cross-corpus + cross-mechanism・**correctness 監査**・質的）— フック等実装 / memory 記載 / 機構設計の **誤り・band-aid・意図乖離** を検知し「**そもそも正しいか?**」を提起、根本修正を提案する（今回の global correction queue 退役+二層再設計が原型）。設計: [design-root-cause-auditor.md](design-root-cause-auditor.md)。判断のみ・自動改変ゼロ・human-gate。

**escalation 弁**: self-reflection が「**既存 memory/hook がありながら同根 fault が再発**」を観測したら、memory N+1（band-aid の上塗り）でなく **完了メッセージに `ESCALATION:` ブロックを返す**。「規範を増やす」から「構造がそもそも正しいか問う」へ上げる弁。sub-agent→sub-agent の直接 spawn は harness 上不可なので、self-reflection は escalation を **return-value** で親へ渡し、**親が同じ完了ターンで root-cause-auditor を inline 起動する（inline drain）**。

> **2026-06-19 設計変更**: 旧方式は escalation を `~/.claude/runtime/pending_structural_reviews.json` に永続キューし `heaven/tools/pending_queue_report.py` が滞留 surface → user/parent が手動起動、だった。だが「消費する主体・トリガ」が parent の自動 drain として実装されておらず、9 件まで滞留して機能しなかった（pending_queue_report.py 自身が警告する「実行主体未定義」欠陥の再発）。永続キューを廃し return-value の inline drain へ移行。
>
> **2026-06-20 設計変更（user 裁定）**: 残っていた self-modification 承認キュー (`pending_hook/claudemd/agent-def`) **も全廃**。理由は (i) 同型の「消費主体・トリガ未定義 → 滞留 → セッション越しに残る」欠陥、(ii) 滞留物が将来の自己改善エージェントに拾われ得る懸念。**自己改善エージェント（reflection/auditor）由来の提案は永続キューに一切積まず、`PROPOSAL:` ブロックで親へ返し、親がその同じセッション内で user に提示・承認・適用する。未承認のままセッションが閉じたら却下（次セッションへ残さない）。** 安全ゲート（reflection/auditor が CLAUDE.md/hook/settings を直接書かない・適用前に必ず user 明示承認）は維持 — 承認の場が「永続キューの後日レビュー」から「in-session 確認」へ移っただけ。`propose_claudemd_updates.py`（Stop 発火で in-session 確認不能）は settings 登録を外して退役。

なぜ別 agent か: 質的 correctness 監査と量的 usage GC は altitude が違い、混ぜると責務が濁る。**乱立しているのは memory であって agent ではなく**、この agent の存在目的はむしろ band-aid memory の積層を構造修正へ畳むこと。限界: 「本当に必要だったもの」は intent gap（Tree Swing の教訓: 下流は need を復元できない）なので、agent は候補と前提の問いを surface するだけ・価値判断は user。

## 機構と産物 — 何が配線されていて、何がされていないか

このフレームが配線して出荷するのは上記の**機構**（検出 / reflection / telemetry / memory 衛生 / 棚卸し）だけ。
原環境でこのループ自身が育てた**産物**（RED-first、一次ソース監査、hedge 監査、tmp-retention 等）は
[recipes/](recipes/) に非配線で同梱してある。産物の効き目の根拠は原環境の失敗観測にあり、
あなたの環境にはまだその根拠が無い — 同種の失敗を観測してから採用する（下記 measure-first）。
こうすることで、棚卸しの cold 判定が「まだ失敗していないだけ」と「効かない」を混同しない。

## 3 層 enforcement モデル

| 層 | 役割 | 効き目 | コスト |
|---|---|---|---|
| CLAUDE.md | 毎ターン読まれる最強の prior | 高 | tokens を毎ターン消費 |
| memory (`~/.claude/projects/<slug>/memory/`) | 長期記憶。index (MEMORY.md) + 1 fact 1 file | 中 | 軽量 |
| hook (`.claude/hooks/*.py`) | LLM を介さない決定論的な検出・block | 非常に高 | false positive リスク |

failure を観測したら **saying-fault**（特定の言い回し・パターンとして検出可能）か **judgement-fault**（判断の誤り）かを分類してから層を選ぶ。saying-fault → hook 化、judgement-fault → memory のみ。介入カタログと各規範の設計理由は [effective-rules.md](effective-rules.md)。

## 暴走を防ぐ設計ゲート

自己改善ループは放っておくと規範を増やし続ける。以下のゲートで増殖そのものを抑制する:

- **measure-first**: 常時計測 (hook) を足すのは「silent かつ costly な失敗」だけ。loud で rare で安い失敗には訂正時の memory tag で十分。recipes の採用判断にも同じゲートを使う。
- **generalize-on-recurrence**: 再発したら narrow な新規規範を足すのではなく、既存規範を一般化する。
- **observe モード 1 日**: 新 hook は block でなく audit (観測のみ) で 1 日走らせ、false positive を見てから blocking に昇格する。即 block 化した hook は翌日 disable される、というのが原環境実測の教訓。
- **登録は人間ゲート経由**: reflection subagent は settings.json を直接編集せず、登録案を pending キューに積む。

## 成果物の層ルーティング（二層構成との整合）

ループが生む成果物は、フレーム層（pantheon git 同梱の汎用機構）とローカル層（ユーザー固有・gitignore 済み）のどちらかに必ず属する。判定基準は一貫して「**ユーザー固有（固有名詞・個人の運用規則）か、環境非依存の汎用機構か**」 — 迷ったら local（誤 frame はユーザー固有内容を commit 候補にする。逆の害は小さい）。

| 成果物 | 置き場 | 層 | 誤配置の防ぎ方 |
|---|---|---|---|
| memory 実体 + MEMORY.md | `heaven/memory/`（symlink 経由） | local | 構造（`.gitignore` の `/heaven/memory/*`） |
| telemetry / queue | `~/.claude/projects/<slug>/telemetry/`・`~/.claude/runtime/` | repo 外 | 構造（リポジトリの外） |
| hook（saying-fault） | frame: `.claude/hooks/` + `settings.json` / local: `.claude/hooks/local/` + `settings.local.json` | 起案時に reflection が層判定 | 構造（`local/` は gitignore）+ 登録 queue の `layer` フィールド |
| 上位層への昇格（規範の promotion） | project 固有 → `projects/<X>/CLAUDE.md`・横断×ユーザー固有 → `CLAUDE.local.md`・横断×環境汎用 → `.claude/rules/common/`・**prompt 由来 → agent 定義 `.claude/agents/<name>.md`（reflection 自身を含む）** | target から導出 | 人間ゲート（queue 経由のみ）+ ルート `CLAUDE.md` は対象外。**agent-def 昇格は強化方向のみ — ループ自身の安全ガード（propose-only/queue/危害レンズ/破壊系 hook）を緩める提案は禁止（HARD BLOCK / Self-Modification）** |

ルート `CLAUDE.md` はルーティングと機構の説明だけを持つフレーム層であり、ループの昇格 target にならない。回帰テストは [.claude/hooks/tests/test_layer_routing.py](../.claude/hooks/tests/test_layer_routing.py)。

## シグナル語彙の較正（acceptance / correction）

検出 hook は**機構**（acceptance の全文一致・correction の queue+batch-drain・debounce・cost gate）と**語彙**（どの言い回しが完了/訂正か）を分離している。機構はフレーム層でこのまま使う。語彙は「特定ユーザーが完了/訂正をどう言うか」を encode した較正定数で、根拠（原環境の transcript corpus と FP 観測）は環境と一緒に運べない — だからローカル層に置く:

| ファイル | 層 | 役割 |
|---|---|---|
| `.claude/hooks/_signals.py` | frame | ローダー + **空デフォルト**（完全 opt-in — 機構は語彙について意見を持たない） |
| `.claude/hooks/local/signals.json` | local（gitignore） | あなたの語彙。**leaf キー単位の置換**でデフォルトを上書き（削除も可能） |
| `.claude/hooks/local/signals.json.example` | frame | 原環境で較正済みの日本語パック（コピー元の出発点） |

**完全 opt-in（重要）**: デフォルト語彙は空で、`signals.json` が無い間は acceptance / correction の検出が**一切発火しない**。これは意図的 — 「ok」のような語ですら意味は環境依存（ここでは reflection 予約語、別環境では単なる承認）で、フレーム層が決め打ちで発火させると意味衝突と costly な誤起動（acceptance は背景サブエージェント spawn）を生むため。**起動の入口は 2 つ**:

- **初回セットアップ**（doc を読まないユーザー向けの主経路）: ルート `CLAUDE.md`「初回セットアップ」節の step 5 で、エージェントが「完了/訂正をどう言うか」を聞き取って `signals.json` を作る。「セットアップして」の一言で語彙が入る。
- **example のコピー**: `cp .claude/hooks/local/signals.json.example .claude/hooks/local/signals.json` で原環境較正済みパックから出発し、自分の語彙に調整する。

env `FRAME_SIGNALS_FILE` で読み込み先を差し替えられる（テストの hermetic 化用）。

**自環境語彙の再導出手順**（example から出発したら、数日運用してから自分の語彙へ調整する）:

1. **acceptance の採掘**: 自分の transcript から「単独の全文として打った短い完了発話」を頻度集計する。例:
   ```bash
   python3 -c "
   import json, glob, collections
   c = collections.Counter()
   for f in glob.glob('$HOME/.claude/projects/*/*.jsonl'):
       for line in open(f, encoding='utf-8', errors='ignore'):
           try: m = json.loads(line).get('message') or {}
           except Exception: continue
           if m.get('role') != 'user' or not isinstance(m.get('content'), str): continue
           t = m['content'].strip()
           if 0 < len(t) <= 10 and not t.startswith('<'): c[t] += 1
   print(*c.most_common(30), sep='\n')"
   ```
   上位のうち「タスクを閉じる意図でしか打たない語」だけを `acceptance.exact`（verbatim）/ `exact_ci`（大文字小文字無視）へ。
2. **全文一致は緩めない**: 部分一致にしたい誘惑（「ok、次やって」も拾いたい等）は拒否する。全文一致こそが status 質問・引用・カジュアル混在の 3 FP クラスを根絶し、observe モード無しで enforcing 運用できている根拠（原環境では substring 版 hook が追加翌日に disable された）。
3. **correction は high-precision な言い回しのみ**: 自分が Claude を訂正するときの定型句を足す。汎用すぎる語（例: 単独の "wrong"）は質問文に誤爆する。
4. **FP を観測したら除外パターンで対処**: 語彙から消すのではなく `third_party_negation`（「X がないとだめ」= 第三者対象の構造記述。原環境の実 FP 由来）や `acceptance_prefix`（冒頭が受領なら抑制）に文脈除外を足す。
5. **telemetry で刈る**: `telemetry/hook_fires.jsonl` と `reflection_gate.jsonl` で発火/skip を棚卸しし（`heaven/tools/telemetry_report.py`）、発火しない語・誤爆する語を削る。

回帰テストは [.claude/hooks/tests/test_signals_vocab.py](../.claude/hooks/tests/test_signals_vocab.py)（デフォルト動作・置換マージ・壊れた設定の degrade・example パックの妥当性を常時検証）。

## 移植時の前提

- 各スクリプトのパスは `CLAUDE_PROJECT_DIR` 環境変数（hook 実行時に harness が供給）から導出する。手動実行時はプロジェクトルートを cwd にするか env を設定する（[.claude/hooks/_paths.py](../.claude/hooks/_paths.py)）。
- 校正値を持つ hook（例: `block_memory_duplicate.py` の類似度閾値）は原環境 corpus 由来の初期値で動き出す。docstring の再導出手順に従い、自環境の memory が育ったら再校正する。
- memory の**実体**は [heaven/memory/](../heaven/memory/) に置き、Claude Code の auto-memory 規約パス `~/.claude/projects/<slug>/memory/` からそこへ symlink を張る（手順は [heaven/README.md](../heaven/README.md)）。これでループが蓄積する memory の持ち運び・バックアップの単位がリポジトリディレクトリに揃う。ただし内容はユーザー固有なので**ローカル層**（git 管理外 — `.gitignore` の `/heaven/memory/*`）であり、commit はしない。index（`MEMORY.md`）は `# Memory Index` 見出しと 1 行ポインタだけを持つ — 本文を index に書かない（肥大は `block_memory_index_bloat.py` が検知する）。
