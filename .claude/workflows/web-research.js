/**
 * web-research workflow — 外部事実 (ゲーム仕様 / 製品仕様 / 数値) を
 *   「一次ソースを実取得し確度ラベル付きで一度に確定する」フローで閉じる.
 *
 * 解く失敗 (過去5セッション 33fdb45a/32f68fba/3f55745d/5a029e91/6240f31c の横断分析):
 *   #2 WebSearch の AI サマリを一次ソース扱い (実ページを fetch せず断定)
 *   #3 fetch 失敗 (403/404/405) の沈黙吸収 (失敗を Sources に記名 / 「Nソース」に水増し)
 *   #4 一次ソース回避 (公式が検索結果に出ても fetch せずコミュニティ要約/推測で断定)
 *   #5 co-occurrence 誤推論 (query 語が結果に出ただけで属性確定)
 *   #6 datamine フィールド誤読 (levels[] 件数を上限と混同, maxLevel を探さない)
 *   #7 確度ラベル形骸化 (未確認前提が下流推奨に確度を伝播しない)
 *   #8 並列2本頭打ち (失敗ソースの代替探索をせず即断定)
 *   #10 浅い workflow (subagent に「一次ソース必須」未付与 / synthesis が sonnet のまま)
 *
 * ==== 設計層 (4 要素) ====
 *   - タスク洗い出し : Inventory(命題分解+疎通) / Research(精読) / Adversarial(反証) / Synthesis(裁定)
 *   - フロー定義     : Inventory → [Research → Adversarial] を命題ごとに pipeline → Synthesis で barrier.
 *                     Inventory で一次ソース fetch 成功 0 → 即 abort (Research に進ませない = #3,#4 の構造的解決)
 *   - 成果物定義     : 各 phase が schema 付き JSON artifact を返す.
 *                     source_type/fetch_status/confidence/co_occurrence_warning を必須化し #2,#3,#5,#7 を schema で塞ぐ
 *   - プロンプト設計 : 各 subagent に「実ページ逐語引用のみ / snippet 禁止 / 代替URL必須 / 確定不能は unverified」を明示
 *
 * ==== 運用層 (5 要素 + 各 2 文 SLI) — CLAUDE.md「プロセス設計フレーム」参照 ====
 *
 *   1. 状態
 *      - 内容: per-invocation のみ. atomic_claims[] と source_results[] を run 内で受け渡し, 永続なし.
 *              権威ソースは「実 fetch した content_excerpt の逐語引用」, AI summary は権威に昇格させない
 *      - failure-it-prevents: 一次ソース未取得のまま「研究しました」という出力だけが通る (失敗 #2,#4)
 *      - observable-signal: Inventory.fetch_success_count===0 の run は abort し status='no_primary_sources' を返す
 *
 *   2. 失敗境界
 *      - 内容: fetch 成功 0 → abort. synthesis の unverified_assumptions が閾値超 → status='low_confidence' + next_action.
 *              Research/Adversarial が throw した claim は null へ落とし (pipeline 仕様) synthesis で除外
 *      - failure-it-prevents: 全 URL 403 でも「5ソース引用」に見せかけ synthesis まで流れる (失敗 #3 の実例)
 *      - observable-signal: return.status ∈ {done, no_primary_sources, low_confidence} の 3 値で分岐先必須
 *
 *   3. 観測性
 *      - 内容: log() narration + phase tree + source_results[].fetch_status 集計 + co_occurrence_warning フラグ
 *      - failure-it-prevents: どの URL が 403 だったか / co-occurrence 罠を踏んだかが invisible のまま synthesis に流れる
 *      - observable-signal: fetch_fail_count>0 と co_occurrence_warning>0 を log() で明示, /workflows で phase tree が見える
 *
 *   4. 契約
 *      - 内容: args.question 必須. Research subagent は source_type に 'ai_summary' を持たない (enum で構造的に排除).
 *              direct_evidence は verbatim_quote (実ページ原文) 必須. synthesis は unverified_assumptions を必須フィールドで返す
 *      - failure-it-prevents: args 不足の空 run / AI summary が evidence に混入し synthesis が確定と読む (失敗 #2,#7)
 *      - observable-signal: args 不足は throw. schema validation 不一致は agent() がリトライ強制 (tool-call 層)
 *
 *   5. コスト
 *      - 内容: Inventory=sonnet, Research fan-out=sonnet (最大 args.max_parallel 本), Adversarial=sonnet,
 *              Synthesis のみ opus (裁定は深い推論が要る = 失敗 #10「synthesis が sonnet のまま」対策)
 *      - failure-it-prevents: fan-out 全本を opus にして高コスト / 逆に裁定を sonnet にして浅い統合
 *      - observable-signal: budget.spent() が /workflows で per-phase 表示. Synthesis の opus 呼出しは phase ラベルで識別可能
 *
 * ==== 規範 / hook との関係 ====
 *   対応する規範: CLAUDE.md「web 調査フロー: 一次ソース先行原則」節
 *   対応する hook: .claude/hooks/audit_fetch_vs_sources.py (Stop, observe モード = Sources 節の URL と実 fetch を突合)
 *   役割分担     : 規範 = 起動を proactive に促す / workflow = 一次ソース取得を schema で強制 / hook = 事後に未取得記名を audit
 *   起動         : Workflow({ name: 'web-research', args: { question, claims?, primary_urls?, project?, budget?, max_parallel? } })
 */

export const meta = {
  name: 'web-research',
  description:
    '外部事実 (ゲーム仕様/製品仕様/数値) を一次ソースを実取得して確度ラベル付きで確定する. Inventory→Research→Adversarial→Synthesis. 一次ソース fetch 成功 0 なら abort し推測で埋めない',
  whenToUse:
    '正解が datamine/公式ドキュメント/wiki に在りうる外部事実を reference/outputs に書く前. または「〜は本当か」の既存主張 verify. conceptual な質問や URL 事前不明の汎用リサーチ (それは deep-research skill) には使わない. args は JSON object {question (必須), claims?, primary_urls?, project?, budget?, max_parallel?} 推奨 — bare string も question として受理',
  phases: [
    { title: 'Inventory', detail: '質問を atomic 命題に分解 + 一次ソース列挙 + 各 URL の疎通 fetch (成功0なら abort)' },
    { title: 'Research', detail: '命題ごとに一次ソースを精読し direct_evidence を逐語抽出 (snippet 禁止, 代替URL必須)', model: 'sonnet' },
    { title: 'Adversarial', detail: '各命題の証拠を反証 — co-occurrence/弱い引用/再現不能を unverified へ格下げ', model: 'sonnet' },
    { title: 'Synthesis', detail: 'confirmed/unverified/refuted を裁定し cited report. 推奨は前提確度を伝播', model: 'opus' },
  ],
}

// ============================================================
// args 契約
// ------------------------------------------------------------
//   question     : string   (required) 調査対象クエスチョン
//   claims?      : string[] adversarial verify したい既存主張 (「Lv不問で+50%固定」等)
//   primary_urls?: string[] 既知の一次ソース URL (datamine/wiki/公式). project CLAUDE.md の既定値を渡す
//   project?     : string   プロジェクト名 (context 注入用. "taskbar-hero" 等)
//   budget?      : string   調査範囲 / 予算制約の宣言 ("datamine + wiki 最大10fetch" / "現SP=41" 等)
//   max_parallel?: number   Research fan-out の上限 (default 5)
// ============================================================

// args は object 想定だが harness/Skill ブリッジは string で渡す. JSON string なら parse、
// bare string は question そのものとして受理 (feature-loop.js と同じ graceful 契約.
// throw すると Skill 経由の初回起動が必ず launch→silent fail→re-launch になる —
// 2026-06-11 session 10412678 で bug-fix.js の同型 throw により実害).
let _args = args
if (typeof _args === 'string') {
  const raw = _args
  try {
    const parsed = JSON.parse(raw)
    _args = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : { question: raw }
  } catch (e) {
    _args = { question: raw }
  }
}

if (!_args || typeof _args.question !== 'string' || !_args.question.trim()) {
  throw new Error('web-research workflow requires args.question (string, 調査対象クエスチョン)')
}

const question = _args.question.trim()
const claims = Array.isArray(_args.claims) ? _args.claims.filter(c => typeof c === 'string' && c.trim()) : []
const primary_urls = Array.isArray(_args.primary_urls)
  ? _args.primary_urls.filter(u => typeof u === 'string' && u.trim())
  : []
const project = typeof _args.project === 'string' ? _args.project.trim() : ''
const budget = typeof _args.budget === 'string' ? _args.budget.trim() : ''
const max_parallel = Number.isInteger(_args.max_parallel) && _args.max_parallel > 0 ? Math.min(_args.max_parallel, 8) : 5

const projectText = project ? `\n## プロジェクト文脈\n${project}` : ''
const budgetText = budget ? `\n## 予算 / 調査範囲の宣言 (orchestrator 指定)\n${budget}` : ''
const claimsText = claims.length
  ? `\n## verify したい既存主張 (これが真か反証込みで検証)\n${claims.map((c, i) => `${i + 1}. ${c}`).join('\n')}`
  : ''
const primaryUrlsText = primary_urls.length
  ? `\n## 既知の一次ソース URL (orchestrator 指定 — まずここを当たる)\n${primary_urls.map(u => `- ${u}`).join('\n')}`
  : ''

// ============================================================
// 成果物定義 (schemas)
// ============================================================

// 一次ソース 1 本の fetch 結果. source_type は 'ai_summary' を enum に持たない
//   = WebSearch の生成サマリを「ソース」として記録する経路を構造的に塞ぐ (失敗 #2)
const SOURCE_RESULT_SCHEMA = {
  type: 'object',
  required: ['url', 'source_type', 'fetch_status'],
  additionalProperties: false,
  properties: {
    url: { type: 'string' },
    source_type: {
      type: 'string',
      enum: ['datamine', 'official', 'wiki', 'community', 'market_api'],
      description: 'AI サマリは含めない. 実 fetch したページの種別のみ',
    },
    fetch_status: {
      type: 'string',
      enum: ['success', '403', '404', '405', 'timeout', 'no_relevant_content', 'other_fail'],
    },
    content_excerpt: {
      type: 'string',
      description: '実際に読んだページの逐語抜粋. fetch_status=success のとき 120 文字以上必須. 失敗時は空',
    },
    fallback_tried: { type: 'boolean', description: '403/404/timeout のとき代替 URL/手段 (curl, 別ドメイン) を試みたか' },
    fallback_url: { type: ['string', 'null'] },
  },
}

const INVENTORY_SCHEMA = {
  type: 'object',
  required: ['atomic_claims', 'source_results', 'fetch_success_count', 'fetch_fail_count'],
  additionalProperties: false,
  properties: {
    atomic_claims: {
      type: 'array',
      description: '質問を「一次ソースで真偽判定できる」最小命題へ分解. 各命題は独立に検証可能なこと',
      items: {
        type: 'object',
        required: ['id', 'claim', 'candidate_sources'],
        additionalProperties: false,
        properties: {
          id: { type: 'string', description: 'c1, c2, ... の短い識別子' },
          claim: { type: 'string', description: '真偽判定できる 1 文の命題' },
          candidate_sources: {
            type: 'array',
            items: { type: 'string' },
            description: 'この命題を確定できそうな一次ソース URL (Inventory で疎通確認したもの優先)',
          },
        },
      },
    },
    source_results: { type: 'array', items: SOURCE_RESULT_SCHEMA },
    fetch_success_count: { type: 'integer', description: 'fetch_status=success の本数' },
    fetch_fail_count: { type: 'integer', description: '403/404/timeout/other_fail の本数' },
    search_queries_issued: { type: 'array', items: { type: 'string' } },
  },
}

const RESEARCH_SCHEMA = {
  type: 'object',
  required: ['claim_id', 'verdict', 'direct_evidence', 'unreached_scope'],
  additionalProperties: false,
  properties: {
    claim_id: { type: 'string' },
    verdict: {
      type: 'string',
      enum: ['supported', 'contradicted', 'unverified'],
      description: '一次ソースの逐語証拠に基づく暫定判定. 証拠が取れなければ unverified (推測で埋めない)',
    },
    direct_evidence: {
      type: 'array',
      description: '実ページから逐語引用した直接証拠のみ. AI 補完 / snippet 要約 / 推論は禁止',
      items: {
        type: 'object',
        required: ['source_url', 'source_type', 'verbatim_quote', 'confidence'],
        additionalProperties: false,
        properties: {
          source_url: { type: 'string' },
          source_type: { type: 'string', enum: ['datamine', 'official', 'wiki', 'community', 'market_api'] },
          verbatim_quote: {
            type: 'string',
            description: 'ページ原文の逐語引用 (フィールド名+値 / 文章). 20 文字未満は証拠として弱い扱い',
          },
          confidence: { type: 'string', enum: ['high', 'medium', 'low', 'unverified'] },
          co_occurrence_warning: {
            type: 'boolean',
            description: 'query 語がページに出現しただけ / フィールド名と値の対応が取れていない可能性があれば true',
          },
        },
      },
    },
    fetch_failures: {
      type: 'array',
      items: { type: 'string' },
      description: '試して 403/404/timeout だった URL (沈黙吸収せず明示). 代替を試したならそれも併記',
    },
    unreached_scope: { type: 'string', description: 'このソース群では確認できなかった事項 (確定不能を正直に)' },
  },
}

const ADVERSARIAL_SCHEMA = {
  type: 'object',
  required: ['claim_id', 'final_verdict', 'downgraded'],
  additionalProperties: false,
  properties: {
    claim_id: { type: 'string' },
    final_verdict: {
      type: 'string',
      enum: ['confirmed', 'refuted', 'unverified'],
      description: '反証を試みた後の判定. 一次ソースで再確認できない / co-occurrence のみ → unverified or refuted',
    },
    downgraded: {
      type: 'boolean',
      description: 'Research の verdict から確度を下げたか (co-occurrence / 弱い引用 / 再現不能を検出した)',
    },
    refutation_attempts: {
      type: 'string',
      description: 'どんな反証を試みたか (別ソース照合 / フィールド名の直接確認 / 反例探索) を 1-2 文',
    },
    surviving_evidence: {
      type: 'string',
      description: 'confirmed のとき: 反証に耐えた逐語証拠 (source_url + quote). それ以外は空可',
    },
  },
}

const SYNTHESIS_SCHEMA = {
  type: 'object',
  required: ['confirmed', 'unverified', 'refuted', 'unverified_assumptions_in_recommendation', 'report'],
  additionalProperties: false,
  properties: {
    confirmed: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'source_url', 'verbatim_quote'],
        additionalProperties: false,
        properties: {
          claim: { type: 'string' },
          source_url: { type: 'string' },
          verbatim_quote: { type: 'string' },
        },
      },
      description: '一次ソースの逐語証拠が反証に耐えた命題. 必ず source_url + quote を併記 (cited)',
    },
    unverified: { type: 'array', items: { type: 'string' }, description: '確定できなかった命題 (断定しない)' },
    refuted: { type: 'array', items: { type: 'string' }, description: '反証された命題 (既存主張の誤りを含む)' },
    unverified_assumptions_in_recommendation: {
      type: 'array',
      items: { type: 'string' },
      description: '回答/推奨が依存している未確認前提. 1 件でもあれば回答全体の確度を下げて user に明示 (失敗 #7 対策)',
    },
    budget_check: {
      type: 'string',
      description: 'args.budget に予算/数量制約があれば Σ必要 ≤ 現resource を算術検算した結果 (失敗 #9). 無ければ "N/A"',
    },
    report: {
      type: 'string',
      description: 'user 向け最終レポート. confirmed は cited, unverified は「未確定」と明示, refuted は訂正として',
    },
  },
}

// ============================================================
// Phase 1: Inventory — 命題分解 + 一次ソース疎通
// ============================================================
phase('Inventory')

const inventoryPrompt = `あなたは web-research workflow の **棚卸しフェーズ専任サブエージェント** です.

## 単一の責務
調査クエスチョンを「一次ソースで真偽判定できる最小命題」へ分解し、各命題を確定できそうな一次ソースを列挙して **実際に fetch して疎通を確認** する. この段では精読しない — 命題分解とソースの生存確認が責務.

## 調査クエスチョン
${question}
${claimsText}${primaryUrlsText}${projectText}${budgetText}

## やること (順番厳守)
1. クエスチョン (と verify 対象主張) を atomic_claims に分解. 各命題は「一次ソース 1 本で真偽が付く」粒度. 曖昧な大命題は分割する
2. 各命題について candidate_sources を埋める. 優先順位: **一次ソース (datamine/公式/wiki/market_api) > コミュニティ要約**. orchestrator 指定の primary_urls を最優先で当たる
3. candidate_sources の各 URL を **WebFetch または Bash(curl) で実際に 1 度 fetch** し、source_results に記録:
   - 成功したら source_type と content_excerpt (120 文字以上の逐語抜粋) を埋める
   - 403/404/405/timeout なら fetch_status にその値を入れ、**代替 URL / 別ドメイン / curl を最低 1 本試して** fallback_tried=true にする (失敗を黙って捨てない)
4. fetch_success_count / fetch_fail_count を正確に集計

## 一次ソースの探し方
- primary_urls が空なら WebSearch でクエスチョンの一次ソース (公式 wiki / datamine JSON / 公式ドキュメント) を探す. search_queries_issued に投げた query を全部記録
- **WebSearch の結果サマリ (検索エンジン生成の要約) はソースではない**. 必ず実ページ URL を fetch してから source_results に入れる

## 禁止事項
- WebSearch のサマリだけで source_results を埋める (実 fetch 必須)
- fetch 失敗を記録せず別ソースで穴埋めして「取れた」ことにする
- 精読・結論出し (それは Research フェーズの責務)

## 出力
schema (INVENTORY_SCHEMA) に従った JSON のみ. 前置きや説明文を含めない.`

const inventory = await agent(inventoryPrompt, {
  label: 'inventory',
  phase: 'Inventory',
  schema: INVENTORY_SCHEMA,
})

log(
  `Inventory: 命題 ${inventory.atomic_claims.length} 件, 一次ソース fetch 成功 ${inventory.fetch_success_count} / 失敗 ${inventory.fetch_fail_count}`,
)

// ----- 分岐: 一次ソース fetch 成功 0 のとき abort (#3,#4 の構造的解決) -----
if (inventory.fetch_success_count === 0) {
  log('一次ソースの実取得が 0 件. 推測で埋めず abort します. 代替手段を検討して再起動してください.')
  return {
    status: 'no_primary_sources',
    question,
    reason:
      '一次ソース (datamine/公式/wiki) を 1 本も実 fetch できなかった. WebSearch サマリや推測で断定するのを構造的に禁止しているため Research に進まない.',
    next_action:
      '(i) primary_urls を正しい一次ソース URL で再指定 / (ii) curl・別ドメイン・archive.org 等の代替取得を試す / (iii) 一次ソースが存在しないなら user に「conceptual 推測でよいか」を 1 度確認',
    inventory,
  }
}

// ============================================================
// Phase 2+3: 命題ごとに Research → Adversarial を pipeline
//   (barrier 不要 — 命題は独立. 早い命題は遅い命題を待たずに反証へ進む)
// ============================================================
const inventoryJson = JSON.stringify(
  { source_results: inventory.source_results, all_claims: inventory.atomic_claims.map(c => ({ id: c.id, claim: c.claim })) },
  null,
  2,
)

const perClaim = await pipeline(
  inventory.atomic_claims,

  // --- Stage 1: Research (精読・逐語証拠抽出) ---
  (claim) =>
    agent(
      `あなたは web-research workflow の **精読フェーズ専任サブエージェント** です.

## 単一の責務
1 つの命題を、一次ソースを **実際に WebFetch して** 逐語証拠で検証する. 担当命題のみ. 他命題は見ない.

## 担当命題
id: ${claim.id}
命題: ${claim.claim}

## 当たるべきソース (Inventory が疎通確認済み)
${(claim.candidate_sources || []).map(u => `- ${u}`).join('\n') || '(候補なし — 自分で WebSearch して一次ソースを探す)'}
${projectText}

## Inventory が取得済みのソース (再利用可)
\`\`\`json
${inventoryJson}
\`\`\`

## やること
1. candidate_sources を **WebFetch (or curl) で実取得**して読む. 命題を直接支持/否定する原文を探す
2. 見つけた証拠を direct_evidence に逐語引用 (verbatim_quote). **フィールド名と値のペア** で示す (例: \`"maxLevel": 5\` / 「Swift Surge は10レベルで段階的にスケール」)
3. 各証拠に confidence を付ける. **query 語がページに出ただけ / フィールド名と値の対応が曖昧** なら co_occurrence_warning=true
4. fetch が 403/404/timeout なら fetch_failures に URL を記録し、**代替 URL/ドメイン/curl を最低 2 本試す**. 失敗を黙って別ソースで埋めない
5. 確定できなければ verdict='unverified'. 推測で 'supported' にしない

## datamine を読むときの注意 (過去の誤読対策)
- 配列の **件数 (length) は「振れる上限」ではない** 場合がある. \`maxLevel\`/\`MaxLevel\`/\`cap\` フィールドを別に探す. 無ければその主張は confidence='unverified'
- timing/range/type など「自分の結論に都合よく解釈しやすい」フィールドは、フィールドの実値を verbatim で引用してから結論する

## 禁止事項
- WebSearch の生成サマリ / snippet を direct_evidence にする (実ページ逐語のみ)
- query に含めた語が出てきただけで属性確定する (co-occurrence)
- 1 ソースで打ち切って即断定する. 命題が重要なら 2 本以上で交差確認

## 出力
schema (RESEARCH_SCHEMA) に従った JSON のみ.`,
      { label: `research:${claim.id}`, phase: 'Research', model: 'sonnet', schema: RESEARCH_SCHEMA },
    ),

  // --- Stage 2: Adversarial (反証) ---
  (research, claim) =>
    agent(
      `あなたは web-research workflow の **反証フェーズ専任サブエージェント** です. 立場は懐疑者.

## 単一の責務
精読フェーズが出した 1 命題の判定を **反証しに行く**. 「本当にこの逐語証拠は命題を支持するか」を疑う. デフォルトは「疑わしきは unverified」.

## 担当命題
id: ${claim.id}
命題: ${claim.claim}

## 精読フェーズの結果 (これを疑う)
\`\`\`json
${JSON.stringify(research, null, 2)}
\`\`\`

## やること
1. direct_evidence を 1 つずつ点検:
   - verbatim_quote が命題を **直接** 支持しているか (フィールド名と値が命題に対応しているか). 対応が曖昧 / 20 文字未満で文脈不足 → 格下げ
   - co_occurrence_warning=true の証拠は原則 unverified へ
2. 可能なら **別の一次ソースで交差確認** (WebFetch). 1 本でも反例 (反対の値/記述) が出たら final_verdict を下げる
3. 精読が unverified にした命題を無理に confirmed に上げない. 証拠が反証に耐えたときだけ confirmed
4. final_verdict ∈ {confirmed, refuted, unverified}. Research から確度を下げたら downgraded=true

## 禁止事項
- 精読結果をそのまま追認する (反証を試みずに confirmed)
- 反証の根拠なく refuted にする (反例の逐語が要る)

## 出力
schema (ADVERSARIAL_SCHEMA) に従った JSON のみ.`,
      { label: `verify:${claim.id}`, phase: 'Adversarial', model: 'sonnet', schema: ADVERSARIAL_SCHEMA },
    ).then(adv => ({ claim, research, adversarial: adv })),
)

const settled = perClaim.filter(Boolean)
const confirmedCount = settled.filter(s => s.adversarial?.final_verdict === 'confirmed').length
const downgradedCount = settled.filter(s => s.adversarial?.downgraded).length
log(`命題 ${settled.length}/${inventory.atomic_claims.length} 件 settle. confirmed ${confirmedCount}, 反証で格下げ ${downgradedCount}`)

// ============================================================
// Phase 4: Synthesis — 裁定 + cited report (opus)
// ============================================================
phase('Synthesis')

const synthesis = await agent(
  `あなたは web-research workflow の **統合フェーズ専任サブエージェント** です. 最終裁定者.

## 単一の責務
命題ごとの [精読→反証] 結果を統合し、confirmed/unverified/refuted に裁定して cited report を書く.

## 調査クエスチョン
${question}
${claimsText}${budgetText}

## 命題ごとの settle 結果 (research + adversarial)
\`\`\`json
${JSON.stringify(settled.map(s => ({ claim: s.claim.claim, claim_id: s.claim.id, research: s.research, adversarial: s.adversarial })), null, 2)}
\`\`\`

## やること
1. 各命題を adversarial.final_verdict に従って confirmed / unverified / refuted に振り分ける
2. confirmed には必ず **source_url + verbatim_quote** を併記 (cited). 引用の無い「確認済み」は禁止
3. verify 対象の既存主張が反証されていたら refuted に入れ、正しい値を confirmed 側で示す
4. **推奨/結論を出すなら、それが依存している未確認前提を unverified_assumptions_in_recommendation に列挙**.
   1 件でもあれば report で回答全体の確度を下げて user に明示する (確度を下流へ伝播)
5. budget に予算/数量制約 (現SP / gold / 素材数) があれば **Σ必要 ≤ 現resource を算術で検算** し budget_check に書く. 無ければ "N/A"
6. report は user 向け: confirmed=cited, unverified=「未確定」と明示, refuted=訂正として. 断定と未確定を混ぜない

## 禁止事項
- unverified を確定的口調で書く / 引用なしで confirmed にする
- 未確認前提に依存した推奨を「確実」と書く (前提の確度を無視)

## 出力
schema (SYNTHESIS_SCHEMA) に従った JSON のみ.`,
  { label: 'synthesis', phase: 'Synthesis', model: 'opus', schema: SYNTHESIS_SCHEMA },
)

// ============================================================
// 返却
// ============================================================
const lowConfidence =
  (synthesis.unverified_assumptions_in_recommendation || []).length >= 3 ||
  (synthesis.confirmed || []).length === 0

return {
  status: lowConfidence ? 'low_confidence' : 'done',
  question,
  summary: `confirmed ${synthesis.confirmed.length} / unverified ${synthesis.unverified.length} / refuted ${synthesis.refuted.length}`,
  next_action: lowConfidence
    ? '確度が低い (confirmed 0 or 未確認前提 3+). user に report を確度明示で提示し、追加の一次ソース取得可否を確認'
    : '完了. orchestrator は report を user に提示. refuted があれば既存 reference/outputs の訂正を反映',
  inventory_summary: {
    claims: inventory.atomic_claims.length,
    fetch_success: inventory.fetch_success_count,
    fetch_fail: inventory.fetch_fail_count,
  },
  settled_count: settled.length,
  synthesis,
}
