---
description: tmp/ の後片付け規約 — 「削除前提」に消す主体・タイミング・例外を定義する（全セッション適用）
---

# tmp retention — 消す主体はタスクを完了したセッション自身

> 背景: 「tmp は削除前提」と各所に書かれていたが、削除を実行する主体・トリガが
> どこにも定義されておらず、2026-06-11 に 111GB 蓄積（残容量 52GB）で発覚した欠陥への手当て。

<important if="tmp/ 配下に 100MB 超の生成物 (フレーム/crops/dump/dataset/フルコピー) を作ったタスクを完了報告しようとしている、または 🧹 [tmp-cleanup] を観測した">

1. **消す主体 = 作ったセッション自身**。タスクの完了報告（GREEN / accepted）を出す前に、
   自分が tmp に作った heavy 中間物を削除する。残してよいのは小さい証跡
   （score.json / summary / manifest、目安 1MB 未満）のみ。
2. **削除前に live process 確認（必須）**: `ps aux | grep <project>` と `lsof +D <tmp>` で
   当該 tmp に書き込み中のプロセスが無いことを確認してから消す。
   並行セッションの eval/training が同じ tmp を使っていることがある（2026-06-11 実例）。
3. **証跡を tmp に置いたまま PROGRESS / レポートから参照しない**。後から参照する証跡は
   `logs/claude/outputs/` か永続ディレクトリに置く（tmp 参照は「削除前提」と矛盾し、
   消せない心理を生む）。既存の tmp 参照証跡は、小さければそのファイルだけ温存してよい。
4. **例外 (allowlist)**: user が「元データ参照として保持」と明示したパスのみ除外する。
   **allowlist は各プロジェクトが own する** — `projects/<name>/.claude/rules/tmp-retention.md` の
   `RETAIN-ALLOWLIST` ブロックに宣言し、`heaven/tools/tmp_bloat_report.py` が全プロジェクト分を集約する。
   共通規約・heaven のツールにプロジェクト固有パスは書かない（1 パス 1 オーナー）。
5. **backstop**: morning-check の `heaven/tools/tmp_bloat_report.py` が閾値超過時に
   `🧹 [tmp-cleanup]` を出す。観測したセッションは 1–2 を踏んで片付ける
   （allowlist 以外でも削除に迷うものは user に確認してよい）。

</important>
