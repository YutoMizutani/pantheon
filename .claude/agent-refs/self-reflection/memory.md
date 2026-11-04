# memory の書き方（self-reflection 係が memory を書く・MEMORY.md を編集する前に Read する）

- transcript_path と同階層の `memory/` の既存 memory を検索し、拡張か新規作成かを決める。
- memory ファイルを frontmatter + Why（本セッションの具体的証拠）+ How to apply + 関連メモリ links で書く。
- user が止めろ・やめろ・忘れろと言った対象は、名前も「戻さない」旨も memory に書かない（記録そのものが再起動の種に
  なる — forget-approach 大原則1）。その学びは、止める手順を引ける道具・skill の起動語の書き換えとして `PROPOSAL:` にする。

## MEMORY.md の索引行

- **一発で書く**。形式は `- [Title](file.md) — hook` で **相対ファイル名のみ**（絶対パス禁止）。
- Edit する前に候補行の `len()` を自分で計算し **≤200 を確認してから** Edit する（`len` は code point 数で CJK も 1 字）。
- 現 hook（`block_memory_index_bloat`）は 200 字超を deny せず末尾を語境界で auto-truncate して allow する — 長い行は
  黙って末尾が落ちて通る。よって **行の意味の核（他 memory との区別を生む語・How の要）を必ず先頭側に置き、装飾・出典・
  日付・括弧注は末尾に回す**。
- auto-truncate の通知を受けたら、対象ファイルを Read し直さず、自分が書いた new_string の末尾 ~40 字が核だったかだけ
  自己照合し、核が落ちていたら 200 字以内へ言い換えて 1 回だけ再 Edit する。
