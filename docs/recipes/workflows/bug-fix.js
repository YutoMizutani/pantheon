/**
 * RECIPE (非配線): 原環境の自己改善ループが育てた規範の実体。
 * 採用条件 = bug 修正で RED 観測なしに修正を始める失敗を自環境で観測したこと。
 * 採用手順と再校正の注意は ../README.md (docs/recipes/README.md)。
 * 採用時は本ファイルを .claude/workflows/ へ、対の hook は (採用後) .claude/hooks/block_red_first_violation.py / レシピ実体: docs/recipes/hooks/。
 *
 * bug-fix workflow — このリポジトリ配下のバグ報告を RED-first フローで閉じる.
 *
 * ==== 設計層 (4 要素) ====
 *   - タスク洗い出し : Reproduce / Analyze / Fix / Verify を独立 subagent に分解
 *   - フロー定義     : 直列. Reproduce で unreproducible → 早期 return (user 承認後 marker で再起動).
 *                     Fix→Verify で still-RED → next_action 付き return (orchestrator が決定)
 *   - 成果物定義     : 各フェーズが schema 付き JSON artifact を返す (会話文ではなく構造化)
 *   - プロンプト設計 : 各 subagent に「単一の責務 / 禁止事項 / 出力契約」を明示
 *
 * ==== 運用層 (5 要素 + 各 2 文 SLI) — docs/effective-rules.md 参照 ====
 *
 *   1. 状態
 *      - 内容: per-invocation のみ. workflow run 単位で完結、永続なし
 *      - failure-it-prevents: 異なる bug 報告が同じ run の artifact を共有して混線する
 *      - observable-signal: 各 run の args が closed (report 必須), runId が args hash と 1:1 対応
 *
 *   2. 失敗境界
 *      - 内容: unreproducible → 早期 return + user 承認後 marker 再起動.
 *              still-RED → status='still-red' + next_action (orchestrator 判断)
 *      - failure-it-prevents: 推測修正で進む / fix 失敗を黙って終わる
 *      - observable-signal: return.status ∈ {green, still-red, unreproducible} の 3 値で分岐先必須
 *
 *   3. 観測性
 *      - 内容: log() narration + workflow 内蔵 phase tree + block_red_first_violation.py の audit log
 *      - failure-it-prevents: 中断に気付けない / どの phase で失敗したか追えない
 *      - observable-signal: /workflows で実行中 tree が見える / block_red_first_audit.log の deny/pass 記録
 *
 *   4. 契約
 *      - 内容: args.report 必須 / Fix subagent は自分で再現コマンドを再実行 (hook と cross-process 整合) /
 *              Reproduce/Analyze/Verify は read-only (編集禁止を prompt で declarative)
 *      - failure-it-prevents: args 不足で空 run / 編集権限の暗黙化で意図せぬ phase が編集
 *      - observable-signal: args 不足は throw / 編集権限違反は prompt の「禁止事項」と schema で検出
 *
 *   5. コスト
 *      - 内容: 全 phase main-loop model 継承. 将来 Reproduce/Verify を sonnet 化する余地あり (Bash 中心)
 *      - failure-it-prevents: Bash 観測のみに高コストモデルを使う
 *      - observable-signal: budget.spent() / 各 phase の token 内訳が /workflows で可視
 *
 * ==== 規範 / hook との関係 ====
 *   対応する規範: docs/effective-rules.md「RED-first before bugfix」
 *   対応する hook: (採用後) .claude/hooks/block_red_first_violation.py / レシピ実体: docs/recipes/hooks/ (reactive deny)
 *   役割分担     : hook = 違反を物理的に止める / workflow = 正しい順序で誘導 + artifact を残す
 */

export const meta = {
  name: 'bug-fix',
  description:
    'このリポジトリ配下のバグ報告を Reproduce → Analyze → Fix → Verify の直列フローで閉じる. 各フェーズが schema 付き artifact を返し、RED 観測なしに Fix に進まない',
  whenToUse:
    'ユーザーからこのリポジトリ配下の bug 報告/修正依頼を受けたとき. feature 追加 / typo / doc-only には使わない',
  phases: [
    { title: 'Reproduce', detail: '再現コマンドを走らせ RED を観測 (編集不可)' },
    { title: 'Analyze', detail: '読み取り専用で根本原因と修正範囲を特定' },
    { title: 'Fix', detail: '修正適用 (Fix subagent 自身で再現コマンドを再実行し RED 観測してから編集)' },
    { title: 'Verify', detail: '同じ再現コマンドで GREEN 確認 + 関連 unit の回帰 check' },
  ],
}

// ============================================================
// args 契約
// ------------------------------------------------------------
//   report       : string   (required) user から受け取ったバグ報告 verbatim
//   repro_hint?  : string   再現コマンド / 手順のヒント (任意)
//   scope_paths? : string[] 関連ファイル/ディレクトリ (任意)
//   user_ok_marker? : string  前回 unreproducible で user 承認を得たときの根拠.
//                             これが渡されたら RED-UNREPRODUCIBLE-USER-OK マーカーを fix 側で出す
// ============================================================

// args は object として渡される想定だが、harness によっては JSON string で来ることもある.
// その場合は parse する (string→object 変換失敗は throw).
let _args = args
if (typeof _args === 'string') {
  try {
    _args = JSON.parse(_args)
  } catch (e) {
    throw new Error(`bug-fix workflow: args was a string but not valid JSON: ${e.message}`)
  }
}

if (!_args || typeof _args.report !== 'string' || !_args.report.trim()) {
  throw new Error('bug-fix workflow requires args.report (string, bug description)')
}

const report = _args.report
const repro_hint = typeof _args.repro_hint === 'string' ? _args.repro_hint : ''
const scope_paths = Array.isArray(_args.scope_paths) ? _args.scope_paths.filter(p => typeof p === 'string') : []
const user_ok_marker = typeof _args.user_ok_marker === 'string' && _args.user_ok_marker.trim()
  ? _args.user_ok_marker.trim()
  : null

const scopeText = scope_paths.length
  ? `\n## 関連パス (orchestrator 指定)\n${scope_paths.map(p => `- ${p}`).join('\n')}`
  : ''
const hintText = repro_hint
  ? `\n## 再現ヒント (orchestrator 指定)\n${repro_hint}`
  : ''

// ============================================================
// 成果物定義 (schemas)
// ============================================================

const REPRO_SCHEMA = {
  type: 'object',
  required: ['reproduced', 'evidence_paragraph'],
  additionalProperties: false,
  properties: {
    reproduced: { type: 'boolean' },
    command: { type: 'string', description: '実行した再現コマンド (or 手順 1 行サマリ)' },
    exit_code: { type: ['integer', 'null'] },
    observed_output: { type: 'string', description: 'stdout/stderr の該当箇所 (full dump ではなく抜粋)' },
    failure_location: { type: 'string', description: 'file:line もしくは現象が顕在化した位置' },
    evidence_paragraph: {
      type: 'string',
      description: '1 段落 (3-5 行) の RED 根拠. command / 観測 / 想定挙動 を含む',
    },
    why_unreproducible: {
      type: 'string',
      description: 'reproduced=false のときのみ. 試した手順と再現失敗の境界を 1 段落',
    },
  },
}

const ANALYSIS_SCHEMA = {
  type: 'object',
  required: ['root_cause', 'fix_approach', 'affected_files'],
  additionalProperties: false,
  properties: {
    root_cause: { type: 'string', description: '1-2 文の原因仮説 (症状ではなく原因)' },
    affected_files: {
      type: 'array',
      items: { type: 'string' },
      description: '修正対象になりうる absolute path',
    },
    fix_approach: { type: 'string', description: '取る方針 + 副作用予測 + 代替案検討の結果' },
    risk_notes: { type: 'string', description: '回帰しうる領域 / 関連系 (verify phase の risk check に使う)' },
  },
}

const FIX_SCHEMA = {
  type: 'object',
  required: ['files_changed', 'hunks_summary', 'repro_rerun_done'],
  additionalProperties: false,
  properties: {
    files_changed: { type: 'array', items: { type: 'string' } },
    hunks_summary: { type: 'string', description: '何を変えたか 1 行/file' },
    repro_rerun_done: {
      type: 'boolean',
      description: 'Fix subagent が自分で再現コマンドを走らせ RED を再観測したか (hook 要件のため必須)',
    },
    marker_used: {
      type: ['string', 'null'],
      description: 'RED-UNREPRODUCIBLE-USER-OK / TDD-RED-OK を使った場合その文字列',
    },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['green', 'observed'],
  additionalProperties: false,
  properties: {
    green: { type: 'boolean' },
    command: { type: 'string', description: '走らせた検証コマンド (Reproduce と同一が原則)' },
    observed: { type: 'string', description: '今回の出力. 前回 RED との差分を 1 行' },
    regression_check: { type: 'string', description: '関連 unit / 周辺機能の状態確認結果' },
  },
}

// ============================================================
// Phase 1: Reproduce
// ============================================================
phase('Reproduce')

const reproPrompt = `あなたは bug-fix workflow の **再現フェーズ専任サブエージェント** です.

## 単一の責務
ユーザーから受け取ったバグ報告を再現し、RED (失敗) を観測する. **コード/設定ファイルを編集してはならない**. Bash でコマンドを走らせ、観測のみを行う.

## バグ報告 (verbatim)
${report}
${hintText}${scopeText}

## やること (順番厳守)
1. 報告内容から再現方法を推定. 既存テスト/script があれば優先. なければ最小再現コマンドを組む
2. Bash で実行. 失敗 (non-zero exit / 例外 / 期待と異なる出力) を観測したら、その出力の該当箇所だけを抜き出す (full dump 禁止)
3. \`evidence_paragraph\` を 3-5 行で書く: 走らせた command + 観測した失敗 + 期待していた挙動

## 再現できなかったとき
- 試した手順を **3 つ以上** 展開しても再現できなかった場合のみ \`reproduced: false\` で返す
- \`why_unreproducible\` に「試した手順」「再現失敗の境界」を 1 段落で書く
- 推測修正には絶対進まない (workflow 全体が unreproducible 分岐を持つ)

## 禁止事項
- Edit / Write / MultiEdit / NotebookEdit の使用 (この phase は読み取り専用)
- 修正方針の提案 (このフェーズの責務外, Analyze の仕事)
- 「動いている可能性が高いので reproduced=true」のような hedge

## 出力
schema (REPRO_SCHEMA) に従った JSON のみ. 前置きや説明文を含めない.`

const repro = await agent(reproPrompt, {
  label: 'reproduce',
  phase: 'Reproduce',
  schema: REPRO_SCHEMA,
})

// ----- 分岐: unreproducible のとき -----
if (!repro.reproduced) {
  if (!user_ok_marker) {
    log('Reproduce で RED を観測できませんでした. user に続行可否を確認してください.')
    return {
      status: 'unreproducible',
      evidence: repro.evidence_paragraph || repro.why_unreproducible || '(根拠未取得)',
      next_action:
        'user に「再現不能だが続行してよいか」を 1 度だけ確認し、承認後 args.user_ok_marker に承認根拠を入れて再実行する',
      repro,
    }
  }
  log(`unreproducible だが user_ok_marker あり (${user_ok_marker}). Analyze に進む.`)
}

// ============================================================
// Phase 2: Analyze
// ============================================================
phase('Analyze')

const analysisPrompt = `あなたは bug-fix workflow の **診断フェーズ専任サブエージェント** です.

## 単一の責務
再現フェーズが観測した RED を元に、根本原因と修正方針を特定する. **編集はしない** (Read / Grep / Bash 読み取りのみ).

## バグ報告 (verbatim)
${report}

## 再現フェーズの観測
\`\`\`json
${JSON.stringify(repro, null, 2)}
\`\`\`

## やること
1. 再現フェーズの evidence_paragraph と failure_location を起点に、関連ファイルを Read / Grep で追跡
2. \`root_cause\` を 1-2 文で特定. **症状ではなく原因** を書く (例: 「null チェック漏れ」ではなく「caller が allow_none=False のはずだが contract が逆になっている」)
3. \`fix_approach\` に方針 + 副作用予測 + 代替案検討の結果を書く
4. 関連 unit / 周辺機能で巻き込まれそうな場所を \`risk_notes\` に列挙 (verify phase が見る)

## 禁止事項
- Edit / Write / MultiEdit / NotebookEdit の使用
- 「とりあえずこう直す」式の対症療法を root_cause に書く
- affected_files を「ありそうな全ファイル」で水増しする (確信ある絞り込みのみ)

## 出力
schema (ANALYSIS_SCHEMA) に従った JSON のみ.`

const analysis = await agent(analysisPrompt, {
  label: 'analyze',
  phase: 'Analyze',
  schema: ANALYSIS_SCHEMA,
})

// ============================================================
// Phase 3: Fix
// ============================================================
phase('Fix')

const reproCommand = repro.command || '(再現フェーズで command 未記録 — 再現手順を Reproduce 観測から復元せよ)'
const markerForUnreproducible = user_ok_marker
  ? `# RED-UNREPRODUCIBLE-USER-OK: ${user_ok_marker}`
  : null

const fixPrompt = `あなたは bug-fix workflow の **修正フェーズ専任サブエージェント** です.

## 単一の責務
診断フェーズの fix_approach に従って編集を適用する.

## 重要 (hook 仕様による必須手順)
\`block_red_first_violation.py\` は **このサブエージェント自身の transcript** で RED 観測 (Bash の is_error=true) を要求する. 別サブエージェントが Reproduce で観測した RED は cross-process では伝わらない. したがって編集前に **必ず自分の手で再現コマンドを 1 度走らせ RED を観測** すること.

- 再現コマンド: \`${reproCommand}\`
${markerForUnreproducible
  ? `\n- 加えて user が unreproducible でも続行を承認しているため、編集前に次のマーカーを assistant message のどこかに 1 度書く:\n  \`\`\`\n  ${markerForUnreproducible}\n  \`\`\``
  : ''}

## バグ報告 (verbatim)
${report}

## 再現フェーズの観測
\`\`\`json
${JSON.stringify(repro, null, 2)}
\`\`\`

## 診断フェーズの方針
\`\`\`json
${JSON.stringify(analysis, null, 2)}
\`\`\`

## やること (順番厳守)
1. Bash で再現コマンドを 1 度走らせ RED を観測 (is_error=true の tool_result を出す)
2. 診断方針に従って Edit / MultiEdit を適用. 範囲は \`affected_files\` に限定. 副作用最小
3. \`hunks_summary\` を 1 行/file で書く
4. \`repro_rerun_done: true\` を必ず立てる
5. schema (FIX_SCHEMA) 通りの JSON を返す

## 禁止事項
- 診断方針外の cleanup / refactor / 命名変更
- 「ついでに」式のスコープ拡大
- テスト追加 (Verify フェーズの責務)
- RED 観測を飛ばして編集に入る (hook が deny する)

## 出力
schema (FIX_SCHEMA) に従った JSON のみ.`

const fix = await agent(fixPrompt, {
  label: 'fix',
  phase: 'Fix',
  schema: FIX_SCHEMA,
})

// ============================================================
// Phase 4: Verify
// ============================================================
phase('Verify')

const verifyPrompt = `あなたは bug-fix workflow の **検証フェーズ専任サブエージェント** です.

## 単一の責務
修正フェーズの変更が GREEN を達成したかを実観測する. **新たな編集はしない** (Bash + Read のみ).

## 再現コマンド (Reproduce phase と同一)
\`${reproCommand}\`

## 修正サマリ
\`\`\`json
${JSON.stringify(fix, null, 2)}
\`\`\`

## 回帰チェック対象 (Analyze の risk_notes)
${analysis.risk_notes || '(指定なし — 修正ファイルの import 元 / 同一モジュール内の隣接関数を最小限 check)'}

## やること
1. Bash で再現コマンドを再実行. 今度は GREEN (success / 期待出力) になることを確認
2. risk_notes の関連 unit / 周辺機能を最小限 check (Bash / Read)
3. \`observed\` に「前回 RED → 今回 GREEN の差分」を 1 行で書く
4. schema (VERIFY_SCHEMA) 通りの JSON を返す

## 禁止事項
- Edit / Write / MultiEdit / NotebookEdit の使用 (検証専任)
- 「たぶん直っている」「ログを読む限り OK」式の hedge. green は実観測のみ
- 修正範囲外の cleanup / 追加検証

## 出力
schema (VERIFY_SCHEMA) に従った JSON のみ.`

const verify = await agent(verifyPrompt, {
  label: 'verify',
  phase: 'Verify',
  schema: VERIFY_SCHEMA,
})

// ============================================================
// 返却
// ============================================================
return {
  status: verify.green ? 'green' : 'still-red',
  summary: verify.green
    ? `GREEN: ${verify.observed}`
    : `STILL RED: ${verify.observed}`,
  next_action: verify.green
    ? '完了. orchestrator が user に結果を 1 段落で報告'
    : '修正で RED 解消できず. 選択肢 (orchestrator が決定): (i) workflow を再起動して analysis を改訂 / (ii) 変更を revert して user に追加情報を求める / (iii) Plan B (本番近い再現環境 / 周辺ログ) を検討',
  repro,
  analysis,
  fix,
  verify,
}
