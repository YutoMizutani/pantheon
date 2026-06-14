/**
 * feature-loop workflow — プロジェクトを破壊せず価値を底上げする改善案(新機能/UX摩擦除去/統合)を
 *   実使用シグナルに根ざして生成する. 無指示 improve ループが test-coverage/重箱の隅に収束する
 *   構造問題への対策 (projects/feature-loop/SCOPE.md が設計記録, reference/usage-signal-contract.md が
 *   使用シグナル契約).
 *
 * ==== 設計層 (4 要素) ====
 *   - タスク洗い出し : Gather(収集) / Diverge(発散) / Judge(判定) / Synthesize(統合) を独立 subagent に分解
 *   - フロー定義     : Gather→[Diverge→Judge を lens 単位で pipeline]→Synthesize(barrier).
 *                     Diverge は lens(改善タイプ)ごとに fan-out, 各案は produce 直後に Judge へ流れる.
 *   - 成果物定義     : 各フェーズが schema 付き JSON artifact. Synthesize は ranked_markdown + 構造化ランク.
 *   - プロンプト設計 : 各 subagent に「単一責務 / 禁止事項 / 出力契約」. Diverge は grounding+provenance 必須,
 *                     Judge は nitpick/捏造 grounding を kill する敵対的検証.
 *
 * ==== 本 loop の核心 (なぜ普通の improve ループと違うか) ====
 *   understand フェーズの発見: grounding の強さと価値が逆相関する. deterministic な oracle が揃うのは
 *   defect-fix(=重箱) で, 狙いたい ux-friction/integration は weak, perf は観測 oracle 皆無.
 *   → 「全案を実使用シグナルに紐づけよ」だけでは依然 defect-fix に崩落する. よって構造で反転させる:
 *     (a) LENSES の quota を grounding 勾配と逆にする(高価値 weak を過剰代表, defect-fix は capped 1枠, perf 枠なし)
 *     (b) grounding_signal.provenance を observed↔inferred の2値で強制(inferred を observed に偽装させない)
 *     (c) Judge が test/lint/命名/型の重箱を必ず kill, DARK シグナル単独 grounding を kill(bootstrapping のみ speculative で生存)
 *     (d) DARK シグナル自体を un-blind する new-feature を1枠用意(loop の最弱観測点を将来 LIVE 化)
 *
 * ==== 運用層 (5 要素 + 各 2 文 SLI) — CLAUDE.md「プロセス設計フレーム」参照 ====
 *
 *   1. 状態
 *      - 内容: per-invocation. args.project 単位で 1 run 完結, 永続なし(将来 seen-ledger で run 跨ぎ dedup 余地)
 *      - failure-it-prevents: 異なる対象 project の案が同 run で混線する
 *      - observable-signal: return.project が args.project と 1:1, /workflows の phase tree が project ラベル付き
 *
 *   2. 失敗境界
 *      - 内容: Gather の query 失敗は query_notes に正直記録(捏造しない). Diverge/Judge で agent skip → null は filter.
 *              survivors 0 件でも Synthesize は走り「観測 oracle が無く出せなかった」を blind_spot_caveat に記す
 *      - failure-it-prevents: 取れないシグナルを「ある」と埋める / 重箱を価値案に偽装して件数を稼ぐ
 *      - observable-signal: return.counts {diverged, survived, killed}, killed>0 かつ survived 妥当なら gate 機能
 *
 *   3. 観測性
 *      - 内容: log() narration(gather件数/survive/kill) + workflow 内蔵 phase tree. Synthesize が suppressed_summary で抑制内容を開示
 *      - failure-it-prevents: 何が kill され何が出たか追えない / 重箱を黙って混ぜる
 *      - observable-signal: /workflows で Diverge→Judge の生存率が見える, suppressed_summary に defect-fix/perf の抑制理由
 *
 *   4. 契約
 *      - 内容: LIVE_SIGNALS/DARK_SIGNALS 定数を Judge に渡し grounding を機械照合.
 *              全 agent read-only(Gather の query 含む, 編集禁止を prompt で declarative). 提案は実装でなく「案」.
 *      - failure-it-prevents: DARK を observed と偽装 / loop が勝手にコード改変
 *      - observable-signal: verdict.grounding_real / provenance_honest が false → kill, 編集系 tool 使用なし
 *
 *   5. コスト
 *      - 内容: Gather=sonnet(query 中心), Diverge=main-loop 継承(opus, 発散は質が要), Judge=sonnet(checklist 検証), Synthesize=opus
 *      - failure-it-prevents: 機械的 query/検証に高コストモデル / 発散の核に安価モデルで凡庸案
 *      - observable-signal: budget.spent() / phase 別 token 内訳が /workflows で可視
 *
 * ==== 規範 / hook との関係 ====
 *   対応する規範: CLAUDE.md「プロセス設計フレーム」, projects/feature-loop/CLAUDE.md 設計原則 6 項
 *   対応する記録: projects/feature-loop/reference/usage-signal-contract.md (LIVE/DARK の SSoT)
 *   役割分担     : 契約 = 何が観測可能かの真実, workflow = それを quota+provenance+judge で価値方向に変換
 */

export const meta = {
  name: 'feature-loop',
  description:
    'プロジェクトを破壊せず価値を底上げする改善案(新機能/UX摩擦除去/統合)を実使用シグナルに根ざして生成する. quota で grounding 勾配を反転し test-coverage/重箱への崩落を構造で防ぐ',
  whenToUse:
    '~/Developer/llm/ 配下の特定 project を「便利にする」改善案が欲しいとき, または /loop で定期的に改善候補を出させたいとき. bug 修正は bug-fix workflow, 外部事実調査は web-research を使う',
  phases: [
    { title: 'Gather', detail: '対象 project の実使用シグナルを deterministic に query (案は出さない)' },
    { title: 'Diverge', detail: 'lens(改善タイプ)ごとに fan-out. quota は高価値 weak-grounded を過剰代表, perf 除外' },
    { title: 'Judge', detail: '案ごとに敵対的検証. nitpick / 捏造 grounding を kill, bootstrapping は speculative で生存' },
    { title: 'Synthesize', detail: 'user_value×grounding_confidence でランク, 抑制内容と blind spot を開示 (opus)' },
  ],
}

// ROOT は args parse 後に env 非依存で導出する (args.root || '.')。下の parsedArgs ブロック参照。

// 使用シグナル契約 (projects/feature-loop/reference/usage-signal-contract.md) の LIVE/DARK 分類.
// Judge が案の grounding を機械照合するための SSoT のミラー. 契約更新時はここも更新する.
const LIVE_SIGNALS = [
  "lifelog kind='window' per-exe foreground minutes (query_apps_today)",
  "lifelog kind='claude' session ship_paths/topic/project",
  "lifelog kind='claude_turn' prompt_hint (200char)",
  "lifelog kind='order' Amazon/UberEats events",
  'telemetry_report.py COLD hooks (instrumented, 0-fire in N days)',
  'rule_adoption_report.py not-internalized rules (re-firing same lesson)',
  'hook_fires.jsonl raw fire records',
  'skill_gc.py exact-duplicate SKILL.md counts',
  'projects/*/SCOPE.md and ventures/INDEX.md checklist gaps',
  'tbh-tool-watch route diff (new community pages)',
  'arch render --drift (stale diagrams, mtime-based)',
  'app/ directory existence per project (binary)',
  'dashboard task_runs button-press history (shell/kick only)',
]
const DARK_SIGNALS = [
  'dashboard analytics pane.view/dwell/ui.interact (flag OFF, 1 seed row)',
  'dashboard claude-kind button click frequency (no log)',
  'empirical-prompt-tuning ledger rows (0 data rows)',
  'candidate_pipeline adoptions (empty, opt-in default off)',
  'user-experience latency / perf (in-process volatile, no baseline persisted)',
  'cost_ledger as UX-perf proxy (100% background, zero user-initiated)',
  'iPad app state events (extractor is a stub)',
  'Discord voice presence (silent fail, admin-gated)',
  'memory adoption per-session precision (~74% sessions leave no verdict)',
]

// perf は意図的に enum から除外 (観測 oracle が無く, 出せば根拠 fabricate になる).
const TYPES = ['new-feature', 'ux-friction-removal', 'integration', 'defect-fix', 'refactor']

// quota: grounding 勾配を反転. 高価値 weak-grounded(new-feature/ux/integration) を 5 枠, defect-fix は capped 1 枠, perf 0 枠.
const LENSES = [
  {
    key: 'new-feature-shipgap', type: 'new-feature', dark_ok: false, max: '2〜4',
    anchor: '最近の Claude 作業(kind=claude の ship_paths/topic)と CLAUDE.md/PROJECTS.md/SCOPE の目的記述',
    question: 'このプロジェクトの目的が約束しているのに未実装の能力、またはユーザが繰り返し手作業で達成している能力は何か。それを1機能にまとめられないか。',
  },
  {
    key: 'new-feature-unblind', type: 'new-feature', dark_ok: true, max: '1〜2',
    anchor: 'DARK シグナル一覧(現在観測できていない計装)',
    question: 'いま DARK な観測点を LIVE 化する機能は何か。それ自体がユーザ価値であり、将来の改善判断を観測可能にする(bootstrapping)。',
  },
  {
    key: 'ux-friction-behavioral', type: 'ux-friction-removal', dark_ok: false, max: '2〜3',
    anchor: 'lifelog の per-exe foreground 分・window 切替頻度(反復 short burst が friction proxy)',
    question: 'ユーザが時間や手数を費やしている反復操作・アプリ往復はどこか。それを縮める機能や自動化は何か。',
  },
  {
    key: 'ux-friction-cognitive', type: 'ux-friction-removal', dark_ok: false, max: '2〜3',
    anchor: 'rule_adoption の not-internalized 規範・memory の READ-BUT-NEVER-ADOPTED',
    question: 'ユーザ(または Claude)が毎回「思い出して守る」必要がある規範や手順は何か。それは欠けた affordance のサイン。記憶に頼らせず構造で支える機能は何か。',
  },
  {
    key: 'integration', type: 'integration', dark_ok: false, max: '2〜3',
    anchor: 'lifelog の order events・tbh community ツール・app/ ディレクトリ存在・プロジェクト間 gap',
    question: 'ユーザが2つのものを手で橋渡ししている箇所はどこか。繋げば1ステップになる統合は何か。',
  },
  {
    key: 'defect-scavenger', type: 'defect-fix', dark_ok: false, max: '最大1',
    anchor: 'telemetry の COLD hooks・arch render --drift',
    question: 'ユーザを実際に誤導している dead automation / 嘘の表示だけ。test-coverage/lint/命名/型は絶対に出さない。',
  },
]

// defensive args (inline workflow plain-JS contract: args は object / JSON文字列 / bare文字列 / undefined を全て吸収).
// 注意: Workflow ツール経由だと args が JSON 文字列で到着することがある (2026-06-08 verify で project='{"project":"dashboard"}' を観測). JSON-looking なら parse する.
let parsedArgs = args
if (typeof parsedArgs === 'string') {
  const s = parsedArgs.trim()
  if (s.startsWith('{') || s.startsWith('[')) {
    try { parsedArgs = JSON.parse(s) } catch (e) { /* bare string のまま (project 名そのもの) */ }
  }
}
const project =
  parsedArgs && typeof parsedArgs === 'object' && parsedArgs.project ? String(parsedArgs.project)
  : typeof parsedArgs === 'string' && parsedArgs.trim() ? parsedArgs.trim()
  : 'dashboard'
const topN = parsedArgs && typeof parsedArgs === 'object' && parsedArgs.top_n ? parsedArgs.top_n : 5
// ROOT: env 非依存。args.root を優先、無ければ '.' (subagent は repo cwd で実行されるので相対解決可)。
// env 固有の絶対パスは local 層 (.claude/commands/feature-loop.md) から args.root で注入する。
const ROOT = parsedArgs && typeof parsedArgs === 'object' && parsedArgs.root ? String(parsedArgs.root) : '.'

const GATHER_SCHEMA = {
  type: 'object',
  properties: {
    target_project: { type: 'string' },
    purpose: { type: 'string', description: 'CLAUDE.md 役割/目的セクション + PROJECTS.md 1行説明の要約' },
    current_state: { type: 'string', description: 'SCOPE チェックリスト gap / outputs 最新 mtime / app 有無' },
    recent_user_activity: { type: 'array', items: { type: 'string' }, description: '上位 foreground exe+分, 対象 project 関連の最近 Claude topic/ship_paths' },
    live_signal_findings: { type: 'array', items: { type: 'string' }, description: '実値を引用 (例 "COLD hook: block_evidence_jump 0fire/30d")' },
    dark_signal_opportunities: { type: 'array', items: { type: 'string' }, description: '対象 project に該当する DARK シグナル = bootstrapping ネタ' },
    query_notes: { type: 'string', description: '失敗した query や caveat を正直に。捏造禁止' },
  },
  required: ['target_project', 'purpose', 'current_state', 'recent_user_activity', 'live_signal_findings', 'dark_signal_opportunities'],
}

const PROPOSALS_SCHEMA = {
  type: 'object',
  properties: {
    proposals: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          type: { type: 'string', enum: TYPES },
          grounding_signal: {
            type: 'object',
            properties: {
              signal: { type: 'string', description: 'どのシグナルに依拠するか' },
              source: { type: 'string' },
              provenance: { type: 'string', enum: ['observed', 'inferred'], description: 'observed=実値, inferred=目的vs現状の gap 推論' },
              evidence: { type: 'string', description: 'observed なら実値引用, inferred なら gap を1文' },
            },
            required: ['signal', 'provenance', 'evidence'],
          },
          proposal: { type: 'string', description: '何を作る/変えるか' },
          oracle: { type: 'string', description: 'ユーザ体験が良くなったをどう測るか(内部品質でなく)' },
          destructive: { type: 'boolean' },
          revert_plan: { type: 'string' },
        },
        required: ['title', 'type', 'grounding_signal', 'proposal', 'oracle', 'destructive'],
      },
    },
  },
  required: ['proposals'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    grounding_real: { type: 'boolean', description: 'grounding が LIVE 一覧の実シグナルか' },
    is_nitpick: { type: 'boolean', description: 'test-coverage/lint/命名/型/フォーマットの重箱か' },
    destructive_without_net: { type: 'boolean' },
    oracle_measures_user_value: { type: 'boolean' },
    provenance_honest: { type: 'boolean' },
    user_value: { type: 'integer', description: '1-5' },
    grounding_confidence: { type: 'integer', description: '1-5' },
    verdict: { type: 'string', enum: ['keep', 'speculative', 'kill'] },
    reason: { type: 'string' },
  },
  required: ['grounding_real', 'is_nitpick', 'oracle_measures_user_value', 'provenance_honest', 'user_value', 'grounding_confidence', 'verdict', 'reason'],
}

const SYNTH_SCHEMA = {
  type: 'object',
  properties: {
    ranked_markdown: { type: 'string', description: '上位を「根拠シグナル → 改善案 → 想定 oracle」の3点セットで読める markdown' },
    recommended_top: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          rank: { type: 'integer' },
          title: { type: 'string' },
          type: { type: 'string' },
          score: { type: 'number' },
          grounding_signal_summary: { type: 'string' },
          proposal: { type: 'string' },
          oracle: { type: 'string' },
          provenance: { type: 'string', enum: ['observed', 'inferred'] },
          caveat: { type: 'string' },
        },
        required: ['rank', 'title', 'type', 'proposal', 'oracle', 'provenance'],
      },
    },
    suppressed_summary: { type: 'string', description: 'defect-fix/perf/重箱として kill・抑制した内容を正直に1段落' },
    blind_spot_caveat: { type: 'string', description: '観測 oracle が無く inferred に頼った領域(perf 全般・UI click が DARK 等)' },
  },
  required: ['ranked_markdown', 'recommended_top', 'suppressed_summary', 'blind_spot_caveat'],
}

function divergePrompt(lens, proj, signalsJson) {
  return [
    'あなたは feature-loop の発散フェーズの専門 lens 「' + lens.type + ' / ' + lens.key + '」です。対象プロジェクト: ' + ROOT + '/projects/' + proj + '。',
    '単一責務: 下記 anchor のシグナルだけを根拠に、' + lens.type + ' 型の改善案を ' + lens.max + ' 個出す。',
    'anchor: ' + lens.anchor,
    'この lens の問い: ' + lens.question,
    '',
    'gather 済みシグナル(JSON): ' + signalsJson,
    '',
    '出力契約 (PROPOSALS_SCHEMA): 各案は title / type=' + lens.type + ' / grounding_signal{signal,source,provenance,evidence} / proposal / oracle / destructive / revert_plan。',
    '- provenance: 観測データ(lifelog/telemetry の実値)に基づくなら observed、目的と現状の gap を読んだ推論なら inferred。偽装禁止。',
    '- evidence: observed なら実値を引用(例「Chrome.exe 142分/日」)、inferred なら purpose と現状の具体的 gap を1文で。',
    '- oracle: 「ユーザ体験が良くなった」をどう測るか。内部品質(test/lint/カバレッジ)ではなくユーザ価値の観測手段を書く。',
    '- destructive=true なら revert_plan 必須(branch/worktree + 戻し手順)。',
    '',
    '禁止事項:',
    '- test-coverage / lint / 命名 / 型注釈 / フォーマットの重箱を「改善案」にしない(本 loop が打倒する対象そのもの)。',
    '- 根拠シグナル無しの「あったら良さそう」を出さない。',
    '- perf(性能)は scope 外(観測 oracle が無い)。出さない。',
    lens.dark_ok
      ? '- DARK シグナル(未観測)を根拠にする場合は、その DARK シグナル自体を LIVE 化する bootstrapping 提案としてのみ可。provenance=inferred、evidence に「現在 DARK: <理由>」と明記。'
      : '- DARK / 未観測シグナルを観測済みのように扱わない。観測値が無い領域は出さない。',
  ].join('\n')
}

function judgePrompt(p, proj) {
  return [
    'あなたは feature-loop の判定フェーズ。対象: ' + ROOT + '/projects/' + proj + '。下記の改善案 1 件だけを敵対的に検証する。',
    '改善案(JSON): ' + JSON.stringify(p),
    '',
    'LIVE シグナル一覧(実データあり): ' + JSON.stringify(LIVE_SIGNALS),
    'DARK シグナル一覧(実データ無し): ' + JSON.stringify(DARK_SIGNALS),
    '',
    '検証項目(全て埋める):',
    '- grounding_real: 案の grounding_signal が LIVE 一覧に該当する実シグナルなら true。DARK 一覧該当 or 一覧外の捏造なら false。',
    '- is_nitpick: 実体が test-coverage/lint/命名/型/フォーマットの重箱なら true。',
    '- destructive_without_net: destructive=true なのに revert_plan が無い/不十分なら true。',
    '- oracle_measures_user_value: oracle がユーザ体験の改善を測るなら true。内部品質しか測らないなら false。',
    '- provenance_honest: observed と称しつつ実値 evidence が無い、または DARK を observed と偽装していれば false。',
    '- user_value: 1-5。ユーザの明日がどれだけ良くなるか。',
    '- grounding_confidence: 1-5。observed live=高 / inferred=中 / DARK 単独=低。',
    '- verdict と reason(1-2文)。',
    '',
    '判定規則(順に適用):',
    '- is_nitpick=true → 必ず kill。',
    '- provenance_honest=false → kill。',
    '- grounding_real=false かつ bootstrapping(DARK を LIVE 化する提案)でない → kill。',
    '- grounding_real=false だが bootstrapping 提案で provenance_honest=true → speculative(kill しない。最弱観測点を un-blind する価値)。',
    '- oracle_measures_user_value=false → 原則 kill(内部品質 oracle は本 loop の対象外)。',
    '- 上記に当たらず user_value>=3 → keep。それ未満は kill。',
  ].join('\n')
}

function synthPrompt(proj, survivors, killed, n) {
  const killSummary = killed.map(k => ({ title: k.title, type: k.type, reason: k.verdict && k.verdict.reason }))
  return [
    'あなたは feature-loop の統合フェーズ(convergent)。対象: ' + ROOT + '/projects/' + proj + '。',
    '生存した改善案(judge keep/speculative, JSON): ' + JSON.stringify(survivors),
    'kill された案(summary 用, JSON): ' + JSON.stringify(killSummary),
    '',
    'タスク:',
    '1. 生存案を user_value × grounding_confidence で降順ランク。speculative は同点なら keep より下。',
    '2. 上位 ' + n + ' 件を recommended_top に { rank, title, type, score, grounding_signal_summary, proposal, oracle, provenance, caveat } で。',
    '3. suppressed_summary: defect-fix/perf/重箱として kill・抑制した内容を1段落で正直に要約(隠さない)。',
    '4. blind_spot_caveat: この対象で観測 oracle が無く inferred に頼った領域を明示(perf 全般、UI click/dwell が DARK 等)。',
    '5. ranked_markdown: 上位を「根拠シグナル(provenance付き) → 改善案 → 想定 oracle」の3点セットで読める markdown に。',
    '',
    '禁止: 弱い根拠を強く見せる格上げ / inferred を observed と書く / 重箱を上位に混ぜる。survivors が 0 件なら正直にそう書き、なぜ出せなかったかを blind_spot_caveat に。',
  ].join('\n')
}

const gatherPrompt = [
  'あなたは feature-loop の収集フェーズ。対象プロジェクト: ' + ROOT + '/projects/' + project + '。',
  '単一責務: 下記の deterministic な実使用シグナルを実際に query して構造化して返す。ideation はしない(改善案を出さない)。read-only。',
  '',
  '実行すること(パスは全て ' + ROOT + ' 基準。ROOT="." なら repo ルートからの相対):',
  '1. 目的: ' + ROOT + '/projects/' + project + '/CLAUDE.md の役割/目的セクション、' + ROOT + '/PROJECTS.md の該当1行。',
  '2. 現状: 同 project の SCOPE.md(あれば)の "- [ ]"/"- [x]"、outputs/ の最新 mtime、app/ の有無を ls で。',
  '3. ユーザ実活動: lifelog SQLite を query。',
  "   sqlite3 " + ROOT + "/projects/lifelog/.local/index.sqlite \"SELECT ts,payload FROM events WHERE kind='claude' ORDER BY ts DESC LIMIT 30\" で最近の Claude 作業(project/topic/ship_paths)を見て対象 project 関連を抽出。",
  '   foreground 上位 exe と概算分も取れれば(window kind を exe 集計、または lifelog の query_apps_today 相当)。',
  '4. LIVE シグナル: python3 ' + ROOT + '/heaven/tools/telemetry_report.py --days 30 (COLD hooks) と python3 ' + ROOT + '/heaven/tools/rule_adoption_report.py (not-internalized rules) を実行し、対象 project に関係する dead automation / 反復失敗を抽出。',
  '5. DARK 機会: 対象 project に該当する DARK シグナルを列挙(bootstrapping ネタ)。DARK 一覧: ' + JSON.stringify(DARK_SIGNALS),
  '',
  '出力契約 (GATHER_SCHEMA): live_signal_findings は実値を引用。query が失敗したら query_notes に正直に記す。取れなかったシグナルを「ある」と書かない。',
].join('\n')

phase('Gather')
const signals = await agent(gatherPrompt, { label: 'gather:' + project, phase: 'Gather', schema: GATHER_SCHEMA, model: 'sonnet' })
log(
  'gathered ' + project + ': ' +
  (signals && signals.live_signal_findings ? signals.live_signal_findings.length : 0) + ' live findings, ' +
  (signals && signals.dark_signal_opportunities ? signals.dark_signal_opportunities.length : 0) + ' dark opportunities'
)
const signalsJson = JSON.stringify(signals)

phase('Diverge')
const judgedByLens = await pipeline(
  LENSES,
  (lens) => agent(divergePrompt(lens, project, signalsJson), { label: 'diverge:' + lens.key, phase: 'Diverge', schema: PROPOSALS_SCHEMA }),
  (review, lens) =>
    parallel(((review && review.proposals) ? review.proposals : []).map(p => () =>
      agent(judgePrompt(p, project), { label: 'judge:' + lens.type, phase: 'Judge', schema: VERDICT_SCHEMA, model: 'sonnet' })
        .then(v => ({ ...p, lens_key: lens.key, verdict: v }))
    ))
)
const judged = judgedByLens.flat().filter(Boolean)
const survivors = judged.filter(p => p.verdict && p.verdict.verdict !== 'kill')
const killed = judged.filter(p => p.verdict && p.verdict.verdict === 'kill')
log('diverged ' + judged.length + ' proposals; ' + survivors.length + ' survived, ' + killed.length + ' killed (nitpick / fabricated-grounding)')

phase('Synthesize')
const synthesis = await agent(synthPrompt(project, survivors, killed, topN), { label: 'synthesize', phase: 'Synthesize', schema: SYNTH_SCHEMA, model: 'opus' })

return {
  project,
  counts: { diverged: judged.length, survived: survivors.length, killed: killed.length },
  survivors,
  synthesis,
}
