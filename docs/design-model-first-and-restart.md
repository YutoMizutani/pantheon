# Design: model-first（先頭固定）と restart（建て直し）— 連鎖結合した「先頭」への front-load と recovery

> status: draft（2026-06-22 起案・未適用）。適用は本 doc 承認後（self-modification: 親が user 承認を得てから
> `.claude/skills/` に実装）。動機・接地は §3（過去の手詰まりセッションに根がある — 外部事例由来ではない）。
> 関連: [self-improvement-loop.md](self-improvement-loop.md) / [design-skill-promotion-lane.md](design-skill-promotion-lane.md) /
> [process-design-frame.md](../projects/llm/reference/process-design-frame.md) /
> `.claude/skills/calibrate` / `.claude/skills/fix` / `.claude/commands/forget-approach.md`

## 1. 原則（最重要・1 行）

**ビルドの「先頭」＝モデル／抽象／語彙は連鎖結合している。そこを誤ると下流のパッチでは直らず（連鎖破綻）、
直す唯一の手は再モデル化からの建て直しである。**

事例が示すのは「AI が賢くない」ではなく、**相互依存するルール群を暗黙仕様のまま破綻なく維持するのが
苦手**という性質。ある状態値が別の正当値に silent に化けても気づけないのは、**その値の型・値域を保証する
不変条件が暗黙でどこにも明示されていない**から。先頭でモデルと不変条件を**外部化（明文・型・assert）**して
おけば、同じ崩れが silent でなく loud になる。

この原則は新規ではなく、既存の front-load 規律（先頭で接地を固定してから生成する）の系列に属する。
欠けているのは、その系列の **2 面**:

- **(A) 欠けているインスタンス = model-first** — 相互依存システムを実装する前に、モデル（実体・不変条件・語彙）を
  明示して固定する。RED-first（bug の再現を先頭で loud にする）の「モデル版」。
- **(B) 本当の gap = recovery 側 = restart** — 解のモデルが壊れた project を「連鎖破綻」と判定し、
  パッチを止めて再モデル化から建て直す。

## 2. 既存との差別化（カバレッジ — 責務が直交）

| 既存インスタンス | 先頭で固定するもの | enforce | 極性 |
|---|---|---|---|
| RED-first（bug） | 再現・RED 観測 | `block_red_first_violation.py` + `fix`/`bug-fix` | front-load |
| 一次ソース先行（web） | 一次ソースの逐語 | `web-research` + `audit_fetch_vs_sources.py` | front-load |
| calibrate（意図） | user の真意（汚染なし別文脈で再構成） | `calibrate` skill | front-load |
| process-design-frame | automation 設計の 2 層契約 | doc + 4 ゲート | front-load |
| **(A) model-first（本設計）** | **相互依存システムのモデル・不変条件・語彙** | **新 skill（v0）** | **front-load** |
| **(B) restart（本設計）** | **連鎖破綻の検知 → 再モデル化建て直し** | **新 skill（v0）** | **recovery** |

近接機構と「なぜ別物か」:

- `calibrate` は**着手前の意図**を再構成する。**解のモデルが壊れた project の建て直し**手順は持たない
  → restart が calibrate を**内部 invoke** して意図接地に使う（置換しない）。
- `forget-approach` は**却下手法の参照除去**。建て直しの判定・再モデル化は持たない → restart が必要なら invoke。
- `codex:rescue` は**別モデルへの建て直し委譲**。連鎖 vs 局所の診断・salvage 判定は親に残る → restart の step で委譲先に使える。
- `fix`/RED-first は**局所バグ**を閉じる。restart の step1 が「局所」と判定したらここへ routing（建て直さない）。
- `autorun` は**完了まで走り切る**モードで直交（手法が正しい前提）。restart は「手法が誤っている、変える」。

→ restart は **calibrate + model-first +（必要なら）forget-approach / codex:rescue を束ねる orchestrator** であり、
固有に新しいのは **step1 の「連鎖破綻 vs 局所バグ」診断ゲート**だけ。

## 3. スコープと接地（2026-06-23 監査で定量）

動機は **user 自身の過去の手詰まりセッション**にある — 先頭の方針・前提を誤ったまま走り、後から修正が
効かなくなった対話。外部の議論は着想のきっかけにすぎず、設計の接地ではない。

**監査**（このリポの transcript 570 本 / 対話 349 本）で再発を実測した:
- 言語化フィルタ（やり直し／破綻／手詰まり）でヒットした 29 本のうち**強候補 8 本を実読**し、
  **3 本が明確に・3 本が部分的に「先頭モデル誤り→カスケード→建て直し」型**（誤検出 2 本）。
- ドメイン横断（ダッシュボード配色・TBHルーンプランナー・VSCodeテーマ目標・equipment localization・
  discordブリッジ設計）— 特定領域でなく**横断する失敗モード**。
- 確定 exemplar: `dda6e5b1`（配色トークン非集約＋calm-mode×theme 二重機構）/ `33fdb45a`（ルーン
  プランナーが SP予算・tier通行料の不変条件を欠く）/ `4e5bd9d7`（追う目標テーマを手渡し画像でなく推論で誤確定）。

計数の限界（両方向・正直に）:
- **下限**: 言語化 proxy は *silent な尾* を取りこぼす（破綻に気づいて言語化する頃には手遅れ＝本パターンの
  silent 性）。残り 320 本の非フラグセッションに silent な該当が潜みうる。**6 は下限**。
- **過検出**: キーワード三分類は過検出（強候補の 2/8 は実読で除外）。件数はキーワードから読めず、
  **実読診断（restart step1）が load-bearing**。

→ **再発は実在し横断する。ただし trigger は決定時点で silent**（キーワードで pre-hoc 検出できない）。
この 2 点が「可逆 opt-in な skill にし、常時計装 hook は積まない」判断を裏づける。したがって本設計は:

- **hook / 常時計装は足さない**（measure-first — §6。silent な trigger は信頼できる pre-hoc 検出器を作れない）。
- **skill は `v0 未検証 N=1` マーカー付き**で出し、`empirical-prompt-tuning` で検証するまで信頼させない。
- 未使用なら `skill_gc` が archive（birth ⇄ death 対称）。

監査はさらに、**実際に解決した手が本設計の remedy と一致**することを示した: `33fdb45a` は「不変条件を
encode＋memory化」（＝model-first step2/4）、`4e5bd9d7` は「手渡し ground truth へ再接地」（＝calibrate）で
収束。観測された 2 つの収束経路が本設計の 2 sub-skill に対応する。

## 4. (A) model-first skill 設計

```
description（model 自動 invoke 用）:
  相互依存ルール＋状態＋関与が多いシステムを実装する前に、モデル（実体・不変条件・語彙）を
  明示して固定する front-load。暗黙モデルの連鎖破綻（多実体の整合が silent に崩れる型）を loud な不変条件に変える。
  ゲーム/シミュレータ/スケジューラ/パーサ/プロトコル等、局所変更が全体に波及しうる実装の着手前に起動。
argument-hint: "<これから作るシステムの1行>"
```

### いつ起動するか（自分の plan 側で判定）

- 多数の実体／ルール／状態が相互作用し、**局所の変更が全体に波及しうる**実装に着手しようとしている。
- **除外**: 孤立した CRUD / typo / doc / 単一関数 / 既にモデルが明示・固定済み。

### 手順（先頭で固定する 6 段）

1. **モデル面の列挙** — 実体（ドメインのモノ）・状態変数・関与（actor）・操作を書き出す。
   「同時に交錯し合う要素」を漏れなく並べる。
2. **不変条件の命名** — 常に成り立つべき横断制約（例: 保存量が常に一定／ある値の型・値域が遷移で変わらない／部分集合の和が全体に一致）。
   **連鎖破綻はここが破れたときに起きる**。暗黙にせず明示する。
3. **語彙の固定** — 1 概念 1 正式名。同義語を禁ずる（暗黙仕様 drift の根を断つ）。
4. **artifact として lock** — モデル（実体＋状態＋不変条件＋語彙）を**実装前に** doc／型／schema に書き、SSoT にする。
   **不変条件は可能な限り実行可能な形（runtime assert／型／test）で encode** し、破れが silent でなく loud に出るようにする
   — これが状態が silent に化ける型の破綻への直接の解。
5. **先行実装の seed** — 正準実装／OSS があれば下敷きにする。発明前に正準モデルを探す。
6. **lock 済みモデルに対して実装** — 不変条件を実行ガードとして常時効かせながら生成する。

### 規範化できる核

失敗は「賢さ不足」でなく「**多要素を常に正確に展開したまま操作する**のが苦手」。
→ モデルを暗黙に保持せず **artifact ＋実行可能な不変条件として外部化**する。これが機械化できる本体。
RED-first が bug を loud にするのと同型で、model-first はモデルの破れを loud にする。

## 5. (B) restart skill 設計（standalone）

```
description（model 自動 invoke 用）:
  手詰まりが見えている session/project を「連鎖破綻」と判定し、パッチを止めて再モデル化から建て直す recovery。
  局所バグなら fix へ routing し、建て直さない（過剰 restart 防止）。salvage→意図再接地→再モデル化→建て直しの順。
argument-hint: "<手詰まりの症状＋対象 project（あれば継承元 session id）>"
```

### 起動トリガ（連鎖破綻の検知）

- 各修正が**別箇所の破綻を生む**（もぐら叩き）— 損傷がモデル階層であり局所でない信号。
- モデルが一度も明示されず（暗黙仕様）、破綻が多数の実体に**またがる**。
- user が「手詰まり／もう無理／断念」を述べた。

> 「同一手 2 回目で停止」は**愚直 retry**（認識が増えていない再送）を止める規範。restart が拾うのはその上の階層 —
> 各修正は別物（認識は増えている）なのに**連鎖し続ける**パターンで、それ自体が「先頭が誤っている」信号。

### 手順（6 段）

1. **連鎖 vs 局所の診断ゲート（本設計で固有に新しい唯一の部分）** —
   局所バグ（→ `fix`/RED-first へ routing・建て直さない）か、モデル階層の連鎖破綻（→ restart 続行）か。
   判定軸: X を直すと Y が確実に壊れるか／破綻が多数の相互依存実体にまたがるか／明示 lock 済みモデルが無いか。
   **局所なら restart しない**（建て直しは高コスト・過剰 restart は固有の失敗モード）。
2. **salvage 棚卸し** — 壊れたモデルから独立に再利用できる資産を明示列挙: データ・素材・test ケース・UI 殻・
   固まった語彙・そして**要件そのもの**（要件は今も有効）。KEEP と DISCARD を分ける（良い部品を捨てない）。
3. **意図の再接地** — `calibrate` を内部 invoke し、汚染された session の解釈を持ち込まず user の真意を逐語から再構成。
   建て直しを「失敗した解に積もった前提」でなく**本物の要件**に接地させる。
4. **正しい先頭から再モデル化** — `model-first`（§4）を内部 invoke し、元のビルドに欠けていた明示モデル＋不変条件を作る。
   §4 step5 に従い OSS／正準実装を seed してよい。lock 後の重い再実装は `codex:rescue`（別 quota・別モデル）へ委譲可。
5. **lock 済みモデルに対して建て直し** — 不変条件を実行ガードにして、新ビルドが同じ連鎖を silent に再取得できないようにする。
6. **事後検視を残す** — 最初のビルドが連鎖した理由（どの不変条件が暗黙だったか）を記録し、自己改善ループへ橋渡しする。

## 6. 追加を弾く 4 ゲート（通過記録）

process-design-frame の 4 ゲートに両 skill を通した記録:

| ゲート | model-first | restart |
|---|---|---|
| **measure-first** | ✅ 常時計装 hook を足さない（opt-in skill・fire コスト 0）。再発は監査で定量済だが trigger が決定時点で silent で pre-hoc 検出器を作れない（強候補 2/8 が実読で誤検出）→ hook 化せず v0 skill が正解（§3） | ✅ 同上。opt-in skill のみ |
| **generalize-on-recurrence** | ✅ 既存 front-load 系列の**新インスタンス**（並列新システムでない）。再発は監査で定量済（横断・下限6・§3） | ✅ recovery 側の一般化。既存 skill を orchestrate するだけ |
| **instruction-first** | ✅ 補償機構でなく新 capability。指示層の偏り是正ではない | ✅ 同上 |
| **guard-conflict** | ✅ blocking gate / exit-2 hook を足さない | ✅ blocking gate を足さない。step1 診断ゲートは**自己内**の routing で、既存安全ガードの正当出力をブロックする面が無い |

honest な注記: 2026-06-23 監査で再発は定量した（§3・下限6・横断）。それでも hook でなく v0 skill に留めるのは、
**trigger が決定時点で silent でキーワード pre-hoc 検出が過検出/過少検出する**（強候補 2/8 が実読で誤検出）ため
— 信頼できる常時計装を作れないことがゲート遵守の理由。enforced 化は『silent trigger を捕える検出器』が
見つかってから（現状は無い）。

## 7. 安全不変条件

- **両 skill は `v0 未検証 N=1`**。`empirical-prompt-tuning` を通すまで信頼させない（skill-promotion lane の規律を継承）。
- **restart は work を捨てて建て直す** → **salvage を必ず先行**し、旧ビルドは新ビルドが検証されるまで**消さない**。
  rm／移動を含む段は既存ガード（`tmp-retention` / `block_recursive_rm_unrecoverable` / `verify_move_landed_before_rm`）を
  経由する形でしか踏まない（[[feedback_verify_move_landed_before_rm]]）。
- **restart を難問からの反射的逃避にしない**（過剰 restart は固有の失敗モード — give-up 方向の過剰一般化）。
  step1 の連鎖 vs 局所ゲートがその歯止め。局所は必ず `fix` へ routing。
- **ループ自身の安全ガードを緩める skill にしない**（HARD BLOCK / Self-Modification — skill-promotion lane §6 継承）。

## 8. open item — 層判定（frame / local）

`.claude/skills/`（現状 untracked: autorun / calibrate / fix）と `.claude/commands/`（tracked と local 混在）の
層規約が未確定（skill-promotion lane §9 の open item と同じ）。model-first / restart は**環境非依存の汎用機構**
（固有名詞を含まない）なので frame 候補だが、既存の新 skill 群（fix/calibrate/autorun）と同じ扱いに揃える。
**層（commit するか否か）は user 裁定**とし、迷ったら local（誤 frame の害が小さい原則）。

## 9. Acceptance test — 過去の手詰まりセッションの replay（§3 監査で実施）

本設計が正しければ、**過去の手詰まりセッション**を restart に渡すと、診断→salvage→意図再接地→再モデル化→
建て直しの流れで解ける。§3 監査の確定 exemplar が replay 対象で、各々が別の sub-skill を主に検証する:

- `33fdb45a`（ルーンプランナーが SP予算・tier通行料の不変条件を欠く）— step4 model-first の**不変条件 encode**面。
  実際の収束手が「不変条件を encode＋memory化」で、本設計の remedy と一致。
- `4e5bd9d7`（追う目標テーマを手渡し画像でなく推論で誤確定）— step3 calibrate の**ground truth 再接地**面。
  実際の収束手が「user 手渡し画像へ再接地」で一致。
- `dda6e5b1`（配色トークン非集約＋calm-mode×theme 二重機構）— step1 で局所 patch（もぐら叩き）でなく
  **連鎖破綻と診断**し、calm-mode を捨てて再モデル化する面。

各 exemplar で期待する弧:
1. **step1 診断**: 修正が別破綻を生む・モデル暗黙・多実体にまたがる → 連鎖破綻（監査要求／局所バグでない）。
2. **salvage**: 再利用できる成果物・固まった語彙・要件を KEEP、壊れた解の本体を DISCARD。
3. **calibrate 意図再接地**: user の逐語要件（4e5bd9d7 なら手渡し画像）を一次に据え直す。
4. **model-first 再モデル化**: 実体・状態・関与を列挙＋連鎖する不変条件（33fdb45a なら Σpt≤Lv）を assert／型／test に encode。
5. **建て直し** → 同型の silent な破綻が **loud な assert 失敗**になる。

監査（§3）は (a) 実際の収束手が本設計の remedy と一致、(b) 6 件の横断的再発を確認した — 合格条件は満たしている
（skill-promotion lane §10 の replay と同型）。残るのは v0 skill を実運用で回し `empirical-prompt-tuning` で検証する段（§10）。

## 10. 実装手順（step1-3 は 2026-06-23 実装済）

1. ✅ `.claude/skills/model-first/SKILL.md` を §4 の description ＋ 6 段手順で作成（冒頭に `> v0 (2026-06-23) 未検証 N=1`）。
2. ✅ `.claude/skills/restart/SKILL.md` を §5 の description ＋ 6 段手順で作成（同マーカー）。calibrate / model-first /
   forget-approach / codex:rescue / fix への内部 invoke 境界を §2・§5 どおり明記（step1 監査-vs-カスケード分岐・step4 再モデル化注意は §3 監査で追補）。
3. ✅ `docs/self-improvement-loop.md` の「成果物の層ルーティング」表の skill 行に、本 2 skill を 1 行追記。
4. 回帰は当面置かない（手順型 skill で deterministic な分岐が薄いため）。`empirical-prompt-tuning` での検証を優先（次手）。
5. **enforced 化（hook 昇格）は再発確認だけでは行わない** — §3 のとおり trigger が決定時点で silent で、
   キーワード pre-hoc 検出が過検出/過少検出するため。silent trigger を信頼して捕える検出器が見つかったときのみ昇格（現状は無い）。
