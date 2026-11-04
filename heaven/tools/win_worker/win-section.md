## この回は Windows 機の WSL（Linux）で動いている — 下の指示書より優先する

- リポジトリは Mac のリポジトリを、同じパスに sshfs でつないだもの。書いたファイルはそのまま Mac に残る（成果物は Mac に置かれる）。
- **見出し行の状態は、始めた時点で `doing-win`。指示書の「状態を `doing` に書き換える」はしない**（`doing` にすると Mac の runner が同じストーリーを同時に始める）。終わるときに `done` / `doing` / `blocked` / `awaiting-approval` のどれかにする。
- OS は Linux。macOS 専用のもの（`launchctl`・`osascript`・`open`・`pbcopy`・`security`・Keychain・BSD の `date -j`・`stat -f`・Homebrew の道具・各プロジェクトの `.venv/bin/python`（Mac の実行形式））は使えない。`python3` は 3.12、`rg`・`jq`・`curl` はある。
- 次のどれかが要ると分かったら、**作業に入る前に**止め、見出しの状態を `todo` に戻し、引き継ぎ 欄の先頭に `win-unsuitable: <理由 1 行>` と書いて終わる（`blocked` にしない。Mac の runner が後で拾う）:
  - macOS 専用のもの・Mac にしか無い道具
  - `git commit`（このリポジトリの git は Mac の runner と共有。同時に触ると壊れる）
  - `cargo build` / `cargo test` など、Mac と共有のビルド成果物（`target/`）を書くもの（ストーリーが `CARGO_TARGET_DIR=/tmp/…` で組み立て先を Windows の中に分けると書いていれば、その通りにして使ってよい。出力もリポジトリの外の /tmp に置き、要約だけをリポジトリに書く）
  - Windows 機そのものの操作など、Mac からの経路を前提にしたもの
- 一時ファイルは `/tmp`（Windows 機の中）に置く。リポジトリの `tmp/` には置かない。
- ファイルの読み書きはネットワーク越しで遅い。リポジトリ全体を `rg` しない（対象のフォルダを指定する）。
