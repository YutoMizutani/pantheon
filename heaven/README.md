# heaven/ — エージェントの自由領域

`projects/` と同階層に置かれた、メタエージェントが自由に配置・蓄積・自己改善してよい領域。
`projects/` 以下はユーザーの持ち物であり、エージェント起源の成果物（memory・自作ツール・実験）はここに置く。

```
heaven/
├── memory/   # セッション横断 auto-memory の実体（ローカル層 — git 管理外）
│             #   ~/.claude/projects/<project-slug>/memory → ここへの symlink を張る
│             #   リポジトリディレクトリ内に置くことで持ち運び・バックアップの単位が
│             #   リポジトリに揃う。ただし内容はユーザー固有なので commit しない
│             #   （.gitignore が /heaven/memory/* を ignore。track は .gitkeep のみ）
├── tools/    # Pantheon の仕組みそのもの（自己改善ループの計器・セッション管理・共通の取得口など）。どの環境にも配る
└── lib/      # どのプロジェクトの知識も含まない、手元の部品（heaven/ の中から使う。ローカル層 — git 管理外）
```

heaven/ は人間の介入やコードレビューを前提にしない、エージェントの領域。Pantheon が担う仕事は、ルーティングや
自己改善ループに限らずここに置く。特定のプロジェクトの知識（名前・パス・データの形）を含む物は、
エージェントが作った物でも `projects/<name>/` に置く。
依存はどちら向きにも張らない — プロジェクトは heaven/ を実行時に当てにせず、単体で `claude` を起動して完結する。
2 つ目のプロジェクトが同じ部品を使い始めたら、各プロジェクトに複製するか、opt-in の plugin（`plugins/`）として配る。
`heaven/tools/layer_check.py` が両方の向きを検査する。

heaven/ 直下は**ローカル層が既定**（.gitignore の `/heaven/*`）。フレーム層として track されるのは `tools/`・この README・
`memory/.gitkeep` だけ。層はディレクトリで決まる — `tools/` に置く物は配る前提（環境固有のパス・固有名詞を含めない）、
手元だけの物は `lib/` に置く。`tools/` に置いて exclude で 1 件ずつ隠す運用はしない。

## 運用ノート（エージェント向け）

- symlink のセットアップと self-healing はルート [CLAUDE.md](../CLAUDE.md) の「初回セットアップ（エージェントの仕事）」節が契約。人間向けの Install 導線はリポジトリ直下の [README.md](../README.md) にある — **このファイルに人間向け手順を書かない**（ユーザーは heaven/ に到達しない前提）。

- リポジトリの移動・rename 時は symlink の張り直しが必須（壊れると memory が silent に分岐する）。
- サブディレクトリはエージェントの判断で自由に追加してよい（破壊的操作・外部送信を伴うものは除く）。
