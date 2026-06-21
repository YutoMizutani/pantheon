# `.local/` — この環境のローカル層（あなた固有の設定の集約先）

このフォルダは **gitignored**。clone してもこの README 以外は付いてこない。
ここには Pantheon の「自分のルール」＝環境固有・ユーザー固有の設定だけを集める。
フレーム層（git 管理の汎用機構）と対になる、移植の 1 単位。

## ここに入るもの（移植したい config のみ）

| 実体 | symlink される harness 期待パス | 中身 |
|---|---|---|
| `.local/CLAUDE.local.md` | `/CLAUDE.local.md` | あなた固有の原則・全体方針 |
| `.local/PROJECTS.md` | `/PROJECTS.md` | プロジェクト索引 |
| `.local/settings.local.json` | `/.claude/settings.local.json` | harness 設定の上書き |
| `.local/hooks/` | `/.claude/hooks/local/` | ユーザー固有語彙を encode した local hook |
| `.local/memory/` | `~/.claude/projects/<slug>/memory` + `/heaven/memory` | memory 実体 |

**入れないもの**: `projects/*/.local/` の実行時データ（sqlite・thumbs・pid 等）。
これらは env 固有で移植しない。`.local/` ＝「この階層の gitignored ローカル層」という
規則は同じだが、root は config、各 project は runtime と役割が分かれる。

## 別マシンへ移植するとき

1. このリポジトリを clone（フレーム層が入る）
2. 旧環境の `.local/` をまるごとコピーして配置（中身は gitignored なので手で運ぶ）
3. `bash heaven/tools/relink-local.sh` を実行 → harness 期待パスへの symlink を全て再生成
   （symlink はパス文字列を保持するだけなので移植先で張り直しが要る。
   root の `CLAUDE.local.md` 等に加え、memory の `~/.claude` 側 symlink もこのスクリプトが張る）

> 背景: 以前はローカル層が `CLAUDE.local.md`(root) / `PROJECTS.md`(root) /
> `settings.local.json`(.claude) / `signals.json`(.claude/hooks/local) / `heaven/memory`
> と 5 箇所に散在し、移植時に「何を持っていくか」を取りこぼしやすかった。
> この 1 フォルダに集約して「掴むべきもの」を 1 つにした。
