# Effective Rules — デフォルト Claude 挙動を変えた介入の移植カタログ

本ファイルは**介入レシピカタログ** — 原環境の自己改善ループが育てた各規範について、置き換える素の挙動 / ルール / 効いている根拠 / 移植条件を記録する。hook / workflow の実体が `recipes/` に同梱されているものは、**同種の失敗を自環境で観測してから** (measure-first) 採用する。配線済みで出荷されるのはループの機構だけで、ここに載る規範は産物である。

原環境のリポジトリで、ルール化 (CLAUDE.md / MEMORY.md / hook / transport-layer) により**素の Claude Code と挙動が明確に変わった**取り組みのカタログ。他環境・他ユーザーの Claude Code 設定に移植する想定で書く。

## 設計原則

### 3 層 enforcement モデル

| 層 | 役割 | 効き目 | コスト |
|---|---|---|---|
| **CLAUDE.md (root)** | routing 直前に必ず読まれる。トップレベル制約として最強の prior。 | 高（毎ターン参照される） | tokens を毎ターン消費 |
| **MEMORY.md + `<name>.md`** | auto-memory として毎ターン load。長期記憶。 | 中（読まれるが判断は LLM 任せ） | 軽量 |
| **Hook (`.claude/hooks/*.py`)** | PreToolUse / Stop / PostToolUse などで deterministic に検出・block・rewrite | 非常に高（LLM 介在なし） | false positive リスクあり |

**意思決定フロー**: 新しい failure pattern を観測したら、まず **saying-fault vs judgement-fault** で分類してから層を選ぶ。saying-fault は hook 化、judgement-fault は CLAUDE.md/memory のみ ([details](#meta-classify-failure))。

### 「効いている」と判定する基準

| 基準 | シグナル |
|---|---|
| ユーザーが効果を実感 | 「良く回ってる」「楽になった」発言、または再叱責が止む |
| 多層 enforce 済 | CLAUDE.md + memory + hook の 2 層以上が揃う |
| 再発カウント減 | サブメモリが親に consolidate されて `archived/` 行き |
| 副作用なし | false positive 観測ログが空 / 自分の作業を阻害しない |

---

## カタログ

### 1. RED-first before bugfix (TDD-RED-OK)

**置き換える素の Claude 挙動**: bug 報告を受けるとすぐに「これが原因です」と推測を書き、修正 diff を出し始める。再現観測を飛ばす。

**ルール**: リポジトリ配下の bug 修正は、修正コードを書く前に必ず **(1) 再現 → (2) RED 観測 → (3) 根拠 1 段落共有** の順を踏む。typo / コメントのみは例外。test harness が無い領域 (prompt / CLAUDE.md / 外部チャット連携) は手動再現コマンドで代替可、観測結果を文章化する義務は同じ。

**3 層**:
- CLAUDE.md: 原環境ではルート CLAUDE.md に「バグ修正フロー」節を常設 (本フレームでは hook + [bug-fix workflow](recipes/workflows/bug-fix.js) で enforce。CLAUDE.md 節の追加は任意)
- Memory: `feedback_red_first_before_bugfix.md` (運用しながら自環境の memory に育てる)
- Hook: [`block_red_first_violation.py`](recipes/hooks/block_red_first_violation.py) — PreToolUse on `Edit|Write|MultiEdit`。直近に RED 観測 evidence or `# TDD-RED-OK:` マーカーが無いと block。

**効いている根拠**: ユーザーから「良く回ってそう」と明示 (原環境で)。

**移植手順**:
1. 自分の CLAUDE.md に「バグ修正フロー」節を copy
2. memory entry を `auto-memory` に追加
3. hook script を `.claude/hooks/` に copy し、`settings.json` の `PreToolUse` matcher `Edit|Write|MultiEdit` に登録
4. test harness が無いプロジェクトでは、再現コマンドの規約を CLAUDE.md に書く

---

### 2. 役割逆転禁止 (絶対命令)

**置き換える素の Claude 挙動**: ユーザーに「~を試してください」「~したら教えてください」と探索的操作を依頼する。失敗時は別の依頼を重ねる。

**ルール**: Claude が人間に「やってください」と依頼する構図そのものが原則禁止。例外は物理的・制度的に人間にしか不可能な操作 (生体認証 / 物理デバイス / 法的同意) に限定し、**副作用列挙 / 代替経路 2 つ / Plan B / 1 回完結** の 4 点を事前 self-check 通過必須。

**3 層**:
- CLAUDE.md: 原環境では「絶対命令: 役割逆転禁止」節を常設 (本フレームでは [CLAUDE.local.md.example](../CLAUDE.local.md.example) の「全体方針」節に採用例として記載 — 採用先はローカル層)
- Memory: `feedback_minimize_user_actions_absolute.md`
- Hook: 部分的 (`block_hedged_concerns.py` が「念のため」「確認のため」を Stop で audit)

**効いている根拠**: 原環境の VSCode tunnel 多重切断インシデント以降、user-action request の出現頻度が体感で激減。ユーザーから 2 度同事案を指摘された後、3 度目はまだ来ていない。

**移植手順**:
1. CLAUDE.md の「絶対命令」節を copy。「物理的にしか不可能な操作」の具体例リストは環境に合わせて差し替える
2. memory entry を copy
3. `block_hedged_concerns.py` を copy して Stop hook に登録

**注**: これは saying-fault と judgement-fault の hybrid。「やってください」など linguistic marker は hook 化済 (block_hedged_concerns)、「役割逆転の判断」自体は memory で運用 (`feedback_classify_failure_saying_vs_judgement.md`)。

---

### 3. 2 回目の同じ失敗で即停止 (絶対命令)

**置き換える素の Claude 挙動**: 同じコマンド・同じ説明・同じ修正方針で 2 回目以降も retry / 弁明 / 代替案の列挙を続ける。ユーザーの苛立ちサインが出ても提案を止めず、炎上を増幅する。

**ルール**: 同じ手段の 2 回目で**必ず停止**、「止まります。指示ください」を即座に出す。3 回目は絶対にやらない。ユーザーの苛立ちサイン (「なんで」「お前」「うるせえ」「死ね」等) を検知したら客観事実 1 行 + 「指示ください」だけを返す。permission gate 拒否時は同コマンド再送禁止 (gate が受け付ける承認語彙は実装依存なので、自環境で確認して memory に書いておく)。HARD BLOCK / Self-Modification エラーは回避策探しに走らず即時 escalate。

**3 層**:
- CLAUDE.md: 原環境では「絶対命令: 2 回目の同じ失敗で即停止」節を常設 (本フレームでは [CLAUDE.local.md.example](../CLAUDE.local.md.example) の「全体方針」節に採用例として記載 — 採用先はローカル層)
- Memory: `feedback_never_anger_user_absolute.md`
- Hook: なし (judgement-fault。同じ失敗の検知には session-level state tracking が必要で hook 化困難)

**効いている根拠**: 原環境での強い苛立ち表現の発言インシデント以降、3 回目の同型炎上は観測なし。memory 層のみだったため evidence は弱かったが、CLAUDE.md への promote で prior 強化。

**命名注**: 当初「二度とユーザーを怒らせない」だったが outcome-oriented で抽象的すぎたため、deterministic な behavior rule である「2 回目の同じ失敗で即停止」に rename。「苛立ちサイン」「permission gate」「HARD BLOCK」は subordinate trigger として併記。

**移植手順**:
1. CLAUDE.md に「絶対命令: 2 回目の同じ失敗で即停止」節を copy
2. memory file を copy。苛立ちサインの語彙は環境・言語に合わせて差し替える
3. (optional) 将来的に「同セッション内で同じ tool_use 引数を N 回失敗したら親に escalate」する hook を追加検討

---

### 4. Verify-before-claim (探索範囲併記の強制)

**置き換える素の Claude 挙動**: 「存在しません」「特定できました」「枯渇しています」と断定。grep ヒット 0 を「無い証拠」として扱う。

**ルール**: 否定／断定／hedge 型主張に **(1) 何を見たか (2) どの粒度で (3) どのキーワード・パターンで** を併記必須。「念のため」「影響不明」「リスク受け入れ」も hedge 型断定として扱う。Corpus 種別ごとの最小探索表 (Read 全文 / re-keyword grep / find 広域 / dense keyframe / canonical 照合) を併用。

**3 層**:
- CLAUDE.md: なし (memory のみ)
- Memory: `feedback_verify_before_negating.md` — 9 個のサブメモリを consolidate
- Hook: [`block_hedged_concerns.py`](recipes/hooks/block_hedged_concerns.py) — Stop 時に linguistic red-flag を audit

**効いている根拠**: 9 個のサブメモリ (false confidence in search hits / no negative existence in spec / sparse keyframe / `ls` 単発 / OCR ceiling / subagent visual / OCR proper nouns / read before grep / hedged concerns) が 1 親へ consolidate され `archived/` 行き。同型再発が止まったため統合可能になった。

**移植手順**:
1. memory file を copy (Decision table と sub-rule summary を含む)
2. hook を copy。本 hook は LLM 出力を Stop で text-scan して linguistic red-flag を audit log に蓄積する形式 (block ではない)

---

### 5. Cross-check evidence before destructive plan

**置き換える素の Claude 挙動**: 単一シグナル (count=0 / log 不在 / ファイル不在) だけで「対象が消失/壊れた」と diagnose し、logout / wipe / 再起動 / ユーザー依頼など破壊的 remediation に進む。

**ルール**: 破壊的 remediation の前に、既に取得済みの並列シグナル (process count / TCP / etime / 別経路 log) と整合チェック必須。矛盾があれば diagnose 保留、矛盾を解く query を 1 本足す。

**3 層**:
- CLAUDE.md: なし
- Memory: `feedback_cross_check_evidence_before_destructive_plan.md`
- Hook: `block_evidence_jump.py` (本フレーム未同梱 — レシピとして記載) — Stop 時に「N だけ起動」「残り N は消失」「壊れた」「provider 切替」など entity-disappearance / destructive-remediation switch phrase を検知

**効いている根拠**: 原環境での VSCode tunnel 復旧時に `pid_count` を見ながらウィンドウ消失を誤 diagnose した事故から導出。導入後、同型の「単一シグナル過信」は本人観測で再発なし。

**移植手順**:
1. memory file を copy。「ログ系 resource の latest ≠ 全数」テーブルは VSCode / vscode-server / browser cache など環境特有なので一般化して書き直す
2. hook を copy して Stop に登録

---

### 6. No version tokens / no remote-less v1 declaration

**置き換える素の Claude 挙動**: コメント・schema description・README に `v1.0` / `Status: Revised (2026-04-12)` / `RFC 2026-04-17` を埋め込む。git remote 未設定でも「v1 freeze」を宣言する。

**ルール**: バージョニング・変更履歴・日付は git tag / git log / git blame の責任。tracked source / spec / schema / docs に書かない。git remote 未設定リポジトリでは v1 / major bump 宣言禁止。

**3 層**:
- CLAUDE.md: なし
- Memory: `feedback_no_v1_declaration_before_git_remote.md`
- Hook: `block_version_tokens.py` (本フレーム未同梱 — レシピとして記載) — PreToolUse on `Write|Edit` で `v1.0` / `Status: ... (YYYY-MM-DD)` / `RFC YYYY-MM-DD` などを正規表現 block

**効いている根拠**: 原環境で 130+ 箇所の一括削除を user から依頼された痛みが background。導入後、Edit 時に hook が即座に block するため再混入が観測されなくなった (saying-fault の典型例)。

**移植手順**:
1. memory file を copy
2. hook を copy。正規表現は環境依存 (社内 versioning 規約があれば exception を追加) なので review してから登録

---

### 7. No gitignored refs from tracked files

**置き換える素の Claude 挙動**: tracked spec / docs / src / test から `.local/...` `tmp/...` `.venv/...` への Markdown link やパス参照を書く (consumer は解決不能)。

**ルール**: tracked file は gitignored path をリンク・引用・コードリテラル・コメントいずれの形でも参照しない。背景理由が `.local/` にあるなら inline する、または参照ごと削除する。

**3 層**:
- CLAUDE.md: なし
- Memory: `feedback_no_gitignored_refs_from_tracked.md`
- Hook: `block_repo_root_artifacts.py` (本フレーム未同梱 — レシピとして記載) — PreToolUse on `Write|Edit|MultiEdit|NotebookEdit|Bash`

**効いている根拠**: 原環境のあるプロジェクト spec 複数箇所の `.local/` ダングリングリンク事故から導出、導入後 hook が Edit 時に block するため再発なし。

**移植手順**:
1. memory file を copy。gitignored path list は環境ごとに `.gitignore` を読んで生成し直す
2. hook を copy

---

### 8. Strip user names from output

**置き換える素の Claude 挙動**: userEmail / git config から推測した名前 (例: `<name> さん`) を呼称として使う。Persona ファイル (例: `.claude/agents/<user-name>.md`) を「ユーザー本人」と混同する。

**ルール**: 個人名 (実名 / ハンドル / さん付け含む) を出力に出さない。中性二人称のみ (日本語は主語省略推奨)。

**3 層**:
- CLAUDE.md: なし (memory + hook で完結)
- Memory: `feedback_do_not_address_user_by_name.md`
- Hook: [`strip_user_names.py`](recipes/hooks/strip_user_names.py) — Stop + PostToolUse で出力レイヤーから名前を strip / rewrite

**効いている根拠**: user から実名呼びを明示否定された後、hook 導入で再混入ゼロ。

**移植手順**:
1. memory file を copy
2. hook を copy。strip 対象の名前リストは環境ごとに編集 (userEmail / git config から自動抽出する形に拡張可)

---

### 9. Mask home dir at transport layer

**置き換える素の Claude 挙動**: 出力に `/Users/<name>/...` を生で残す。あるいは Claude 側で都度 `~` 置換を試みて、Markdown link URL や fenced block で漏らす。

**ルール**: Claude の応答本文・link URL は絶対パスのまま (VSCode のクリック可能性を保つため)。マスクは Discord transport layer の `mask_home()` で一括処理。新規 Discord 送信経路を追加するときは必ず `mask_home()` を通す。

**3 層**:
- CLAUDE.md: なし
- Memory: `feedback_mask_home_in_user_text.md`
- Transport: 外部転送チョークポイント (原環境では chat-bridge の outbound 関数に実装)
- Hook: [`mask_home_in_text.py`](recipes/hooks/mask_home_in_text.py) — Stop + PostToolUse でソフトガード

**効いている根拠**: 旧運用 (Claude が都度マスク) は inline code / fenced block / URL で漏れる brittle 設計。transport-layer 集約後、漏洩観測なし。

**移植手順**:
1. Discord 等の中継 transport を持つ環境のみ移植価値あり。中継無しなら hook のみで運用
2. transport を追加する都度 `mask_home()` を通すルールを CLAUDE.md か該当 transport の CLAUDE.md に書く

---

### 10. No dangling script announcement

**置き換える素の Claude 挙動**: 「スクリプトを追加しました」だけで会話を終了。ユーザーは何を実行すればいいか分からない。

**ルール**: script announcement で終わらせない。**(a) コピペ可能な one-liner (絶対パス)** か **(b) "あなた側でやることは無い" 明示** のどちらかを必ず添える。

**3 層**:
- CLAUDE.md: なし
- Memory: `feedback_no_dangling_script_announcement.md`
- Hook: `audit_dangling_script_announcement.py` (本フレーム未同梱 — レシピとして記載) — Stop で linguistic pattern を audit

**効いている根拠**: ユーザー判断負荷 = 役割逆転の軽度版という観点で導出。導入後の再発カウント減 (audit log 観測)。

**移植手順**:
1. memory + hook を copy

---

### 11. Detect correction signal (UserPromptSubmit)

**置き換える素の Claude 挙動**: 「違う」「stop」「やめて」のユーザー correction を flat な追加要求として処理し、直前の方針を継続。

**ルール**: ユーザー prompt に correction phrase が含まれたら、その訂正を**キューに記録**し、タスク締め（acceptance シグナル）時に一括で reflection（memory / hook 起案）にかけて規範化する。やりとり中は silent — mid-conversation の自己発火は「過剰実装」として廃し batched-queue 方式に変更済み。

**3 層**:
- CLAUDE.md: なし
- Memory: 個別 memory なし（運用は hook が単独で行う meta-tool）
- Hook: [`detect_correction_signal_v2.py`](../.claude/hooks/detect_correction_signal_v2.py) (配線済み — 同梱 hook) — UserPromptSubmit。v2 は correction phrase を検出して訂正キューに 1 件 append し **silent**（third-party 否定・acceptance 接頭辞は除外）。AUTO-LEARN reflection（memory / hook 起案）は次の acceptance シグナル時に [`detect_acceptance_signal.py`](../.claude/hooks/detect_acceptance_signal.py) がキューを drain して一括起動する

**効いている根拠**: 「同じ失敗の 2 回目で停止」ルール (`feedback_never_anger_user_absolute.md`) を機械的に支える infrastructure。

**移植手順**:
1. hook を copy して UserPromptSubmit に登録

---

### 12. Heartbeat and responsiveness

**置き換える素の Claude 挙動**: 長時間タスク (subagent パイプライン / background eval) 中に IDE 出力が 10-30 分無音。または `ls + ps + "無し"` だけの shallow lookup で「動いてます」と擬態。

**ルール**: IDE 無音 ≤ 4 分 (ScheduleWakeup cache TTL のマージン)。subagent dispatch の直前・直後に親 1 行。`ls` + idle 宣言は heartbeat と見なさない。能動 work 探索 4 種 (user 指示完遂チェック / 直近 log content read / queue 静的着手 / eval 待ち補助 work) のいずれか 1 つを完了してから報告。

**3 層**:
- CLAUDE.md: 「実行環境について」節で memory への pointer
- Memory: `feedback_heartbeat_and_responsiveness.md` — 2 サブメモリを consolidate
- Hook: なし (judgement-fault のため hook 化せず、empirical-prompt-tuning (本フレーム未同梱の skill — 原環境の運用例) で運用)

**効いている根拠**: 2 サブメモリが親に consolidate された。Discord 経路の stall 訴え率が約 3.4 倍という測定値が CLAUDE.md にあり、heartbeat 規約は厳守。

**移植手順**:
1. memory file を copy
2. CLAUDE.md に「Discord 経路は観測性が低いため heartbeat 厳守」 (該当する transport を持つ環境のみ)

---

<a id="meta-classify-failure"></a>
## メタ: failure 分類によって enforcement 手段を選ぶ

新規 failure を観測したら、memory entry を draft する**前に**分類する。

| 種別 | 判定マーカー | enforcement | 例 |
|---|---|---|---|
| **saying-fault** | 出力に specific phrase / regex が現れる | Hook (deterministic block / audit) | 「念のため」「v1.0」「<name> さん」「<home>/」 |
| **judgement-fault** | reasoning / decision / framing 自体が誤り | Memory (LLM 側の判断を変える) + empirical-prompt-tuning (本フレーム未同梱の skill — 原環境の運用例) | 工数で deprioritize、effort-based 判断、貼って/登録/追加を勝手にスキーマ化 |
| **両側性** | saying marker と judgement の両方 | hybrid (両方の層を併用) | 役割逆転禁止 ("やってください" marker + 役割判断) |

詳細: `feedback_classify_failure_saying_vs_judgement.md` (運用しながら自環境の memory に育てる)

---

## 移植サマリ

### 最低構成 (CLAUDE.md / 標準 git workflow を持つ汎用プロジェクト向け)

| 優先度 | 介入 | 必要ファイル |
|---|---|---|
| MUST | RED-first | CLAUDE.md 節 + memory + `block_red_first_violation.py` |
| MUST | 役割逆転禁止 | CLAUDE.md 節 + memory + `block_hedged_concerns.py` |
| MUST | 2 回目の同じ失敗で即停止 | CLAUDE.md 節 + memory |
| MUST | Verify-before-claim | memory + `block_hedged_concerns.py` (役割逆転と共有) |
| SHOULD | No version tokens | memory + `block_version_tokens.py` |
| SHOULD | No gitignored refs | memory + `block_repo_root_artifacts.py` |
| SHOULD | Strip user names | memory + `strip_user_names.py` |
| OPTIONAL | Cross-check evidence | memory + `block_evidence_jump.py` |
| OPTIONAL | No dangling script | memory + `audit_dangling_script_announcement.py` |
| OPTIONAL | Detect correction signal | `detect_correction_signal_v2.py` |

### Discord / 中継 transport を持つ環境向け追加

- Mask home at transport (中継 transport の outbound 関数に `mask_home()` を実装)
- Heartbeat and responsiveness (CLAUDE.md 節 + memory)
- 停滞 session への kick プレフィックス規約 (固まった別 session を外部チャット post で起こす経路。傍観して user に状況確認させない)

### `settings.json` への登録例

同梱 hook の実配線は [.claude/settings.json](../.claude/settings.json) を参照 (コピーしてそのまま動く)。カタログ中の各 hook の event / matcher は以下 (✅ = 本フレーム同梱、レシピ = 自作する場合の目安):

| Hook | Event | Matcher | 同梱 |
|---|---|---|---|
| `block_red_first_violation.py` | PreToolUse | `Edit\|Write\|MultiEdit` | レシピ (非配線) |
| `block_hedged_concerns.py` | Stop | (all) | レシピ (非配線) |
| `audit_fetch_vs_sources.py` | Stop | (all) | レシピ (非配線) |
| `mask_home_in_text.py` | Stop / PostToolUse | (all) | レシピ (非配線) |
| `strip_user_names.py` | Stop / PostToolUse | (all) | レシピ (非配線) |
| `detect_correction_signal_v2.py` | UserPromptSubmit | (all) | ✅ (配線済み) |
| `block_version_tokens.py` | PreToolUse | `Write\|Edit` | レシピ (未同梱) |
| `block_repo_root_artifacts.py` | PreToolUse | `Write\|Edit\|MultiEdit\|NotebookEdit\|Bash` | レシピ (未同梱) |
| `block_evidence_jump.py` | Stop | (all) | レシピ (未同梱) |
| `audit_dangling_script_announcement.py` | Stop | (all) | レシピ (未同梱) |

---

## 含めなかったもの (= まだ評価できていない / portable でない)

- 原環境のドメイン固有ルール (特定のゲーム・データ解析ドメインに結びついた規範) — domain-specific
- 個別 runbook (`runbook_*`) — 環境固有
- Reference cache (外部サービスの ID ↔ 内容の対応表など) — そのまま運用データ
- 工数除外 / stuck-session-kick 絶対命令 — memory 層のみで運用中、hook 化していないため「ルール化で挙動が変わった」evidence が弱い (judgement-fault)。工数除外は empirical-prompt-tuning (本フレーム未同梱の skill — 原環境の運用例) での A/B 検証が現実的、stuck-session-kick は観測トリガ型で daemon 側 infrastructure の方が筋
