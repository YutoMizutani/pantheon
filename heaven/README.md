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
└── tools/    # エージェントが調査・運用のために自作したツール
```

heaven/ 直下はエージェントの自由領域ゆえ**ローカル層が既定**（.gitignore の `/heaven/*`）。
フレーム層として track されるのは、出荷された計器 `tools/`・この README・`memory/.gitkeep` だけ。
新しく作ったツールが汎用（環境固有のパス・固有名詞を含まない）なら、commit してフレームを育ててよい。

## 運用ノート（エージェント向け）

- symlink のセットアップと self-healing はルート [CLAUDE.md](../CLAUDE.md) の「初回セットアップ（エージェントの仕事）」節が契約。人間向けの Install 導線はリポジトリ直下の [README.md](../README.md) にある — **このファイルに人間向け手順を書かない**（ユーザーは heaven/ に到達しない前提）。

- リポジトリの移動・rename 時は symlink の張り直しが必須（壊れると memory が silent に分岐する）。
- サブディレクトリはエージェントの判断で自由に追加してよい（破壊的操作・外部送信を伴うものは除く）。
