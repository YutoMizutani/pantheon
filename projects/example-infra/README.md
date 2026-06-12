# Example Agent (Template)

新規プロジェクトエージェントの**雛形**。骨格だけを持つ。
新しいプロジェクトを作るときは、このディレクトリごとコピーして `projects/<name>/` にリネームし、
`CLAUDE.md` をドメインに合わせて書き換える（手順: [docs/new-project-setup.md](../../docs/new-project-setup.md)）。

```
projects/<name>/
├── CLAUDE.md        # エージェントの人格・役割・禁止事項（※必ず書き換える）
├── .claude/         # Claude Code 拡張の置き場（settings.json の骨格のみ）
├── context/         # ドメイン知識・前提条件・用語集など
├── tools/           # スクリプト・ツール類
├── logs/            # 思考過程・検討メモ（yyyyMMdd 形式のサブディレクトリに配置）
├── outputs/         # 最終成果物
├── reference/       # 再利用する根拠データ・参考文献
└── tmp/             # 一時ファイル（削除前提）
```

- 拡張（agents / commands / skills / rules / hooks）は最初から同梱しない。
  **必要になったものだけ**自作して育てる（仕様: [docs/new-project-setup.md](../../docs/new-project-setup.md)）。
- `.claude/settings.json` が harness に読まれるのは、このプロジェクトを**単独のセッションルート
  として開いたとき**のみ。フレームルートで運用する通常モードでは、hook 等はルートの
  `.claude/settings.json` に登録する（[docs/new-project-setup.md](../../docs/new-project-setup.md)「プロジェクト hook の書き方」）。
- この README はテンプレートの説明なので、コピー後は削除してよい。
