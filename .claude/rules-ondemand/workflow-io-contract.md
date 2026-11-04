# Workflow ツール I/O 契約 — enforce projection

> SSoT は各 memory (下記 source)。本ファイルは Workflow 起動/authoring/消費時の operational 形のみ。
> 背景: args=string 起因の launch fail / silent 誤展開 / output shape 仮定ミスが 4 セッションで再発
> (e926ff7d, 10412678, scope 誤展開 smoke 1→6 dom, output shape)。memory 単体では起動時に recall されず
> 都度 round-trip を焼いたため、rule へ昇格。

<important if="Workflow を起動する・workflow script (inline / .claude/workflows/*.js) を書く・workflow output を消費しようとしている">

1. **args は常に string で届く** (inline でも scriptPath でも)。script 側は先頭で防御的に正規化する —
   JSON parse 失敗または非 object なら bare string を必須フィールドに詰める graceful 契約
   (`{report: raw}` / `{question: raw}`; feature-loop.js / bug-fix.js / web-research.js が実装例)。
   throw すると Skill 経由初回起動が必ず silent fail → re-launch になる。
2. **inline script は plain JS**: 型注釈ゼロ / テンプレートリテラル入れ子は最外 1 段 /
   `Unexpected token (N:0)` は TS でなく N 行目より手前の括弧閉じ漏れを疑う。
3. **新 workflow の meta.whenToUse 末尾に args 形式を 1 行書く** — orchestrator から見える唯一の surface。
4. **起動直後に scope を 1 行確認する** — args の obj 前提が silent に全展開していないか
   (smoke 1 件のつもりが 6 domain 展開した実害)。
5. **output 消費は keys() を 1 回確認してから** — `.output` は `{"result": <str|obj>}` ラップ。
6. **script 内 agent() は null を返しうる** (session limit / API error) — 未ガード参照は phase ごと落とす。

</important>

source_memories: feedback_inline_workflow_script_plain_js_contract /
feedback_verify_workflow_scope_after_launch / feedback_inspect_workflow_output_shape_before_consuming /
feedback_guard_agent_null_in_workflow_scripts
