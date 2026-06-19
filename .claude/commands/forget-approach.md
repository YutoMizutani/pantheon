---
description: 却下した手法/ツールの参照を repo-local（memory・gitignore済みの局所層を含む）から残らず除去し、再提案の再seedを断つ。stateless — 却下語を記録に書き戻さない
argument-hint: "<却下した手法> と別名を列挙 (例: maplestory.io io msio)"
---

> v0 (2026-06-18)。設計は本環境の **maplestory.io 削除トレース**（session 916abb0 / docs/incidents/2026-06-17-correction-queue-cross-session-drift.md / projects/lifelog/outputs/2026-06-17.md）に接地。
> **未検証**: 実走 N=0。初回は必ず削除前に候補リストを user に提示してから消す。

# forget-approach — 却下した手法を repo-local から残らず忘れる

却下した手法/ツール（例: maplestory.io）の参照が repo に残ると、次の session で Claude がその語に飛びつき**検証を経ずに再提案**する。このコマンドは再seedの源（= context に載る repo-local の残骸）を残らず除去する。良い手法なら web-research の確度ゲートを通って再登場すればよい、という前提。

## これは何で、何でないか

- **これ**: repo-local（tracked + **gitignore済みの局所層**: `heaven/memory/` / `CLAUDE.local.md` / `settings.local.json` / `.claude/hooks/local/`）から却下語の参照を**残らず**除去し、残存0を検証する。
- **これでない**: 却下語を「却下リスト/memory/blocklist」に**記録すること**。それ自体が residue になり、まさに避けたい再seedを作る（→ 大原則1）。
- **これでない**: 一般知識からの再提案や cross-session 注入の防止。前者は web-research ゲートの仕事、後者は既に構造退役済み（commit 29ccbf4）。本コマンドの守備範囲外（→「到達できない残骸」）。

## 大原則（破ったら設計が崩れる）

1. **却下語を新しい記録に書かない**（memory / blocklist / correction / 説明コメント）。書けば residue を自分で作る。io 削除直後に memory へ却下語を書き戻した silent 反復（lifelog 2026-06-17 L79「.io を消した直後にメモリに書き戻す」）の再演を禁じる。**このコマンドは stateless** — 状態ファイルを一切残さない。出力はチャットの ephemeral 報告と削除そのものだけ。
2. **巻き添え削除をしない**。gitignore 配下は削除が不可逆。却下語を**併用/言及するだけで別ソースで稼働し続けるツール**を、名前一致だけで消さない（→ 手順3の分類）。io で maplestorywiki.net / maplemaps.net スクレイパを誤削除しかけた実害がある。
3. **再seedの面だけを掃く**。context に載らない append-only/scratch（transcripts / `logs/` / `tmp/`）は対象外（→「到達できない残骸」）。

## 引数

`$ARGUMENTS` から **却下対象とその別名を全て**取る（例: `maplestory.io io msio`）。
**別名の自動推測はしない** — 不確実。`$ARGUMENTS` が空、または別名が網羅されているか不明なら、**先に user に別名を列挙してもらう**（「`1Password` なら `1pw` / `op` も対象か?」のように確認）。確定した別名を `.` を `\.` にエスケープして `|` 連結し、走査用 regex を作る。

## 手順

### 1. 走査 regex を確定
```bash
# user が列挙した別名を | で連結。リテラルの . は \. にする
APPROACH_RE='maplestory\.?io|msio'
```

### 2. 全層を走査（このコマンドを verbatim で実行する — flag を再構成しない）
gitignore済みの局所層を見るため `--no-ignore-vcs`、隠しディレクトリ（`.claude/` 等）のため `--hidden`。`.git`・依存物・transcript(`*.jsonl`) は除外。`.ignore` は有効のまま（logs/tmp/build の churn だけ抑制し、局所層は隠さない）。
```bash
rg -i --no-ignore-vcs --hidden -n \
  -g '!**/.git/**' -g '!**/node_modules/**' -g '!**/target/**' -g '!**/*.jsonl' \
  "$APPROACH_RE" .
```
ヒット0でも「無い」と即断しない — regex の別名漏れを疑い、1で列挙した別名が全て入っているか見直す。

### 3. 各ヒットを分類（ここは判断 — 機械削除しない）

| 分類 | 定義 | 対応 |
|---|---|---|
| **純言及** | doc/コメントが却下語を名指すだけ | 該当箇所を削除/編集で除去 |
| **死依存** | スクリプトの**唯一の**機能源が却下対象で、除去すると完全に機能不全 | 「唯一の源か」を確認（他ドメイン併用がないか grep）した上でファイルごと削除 |
| **稼働継続（巻き添え注意）** | 却下対象を併用/言及するが、**別ソースで稼働し続ける** | 削除しない。却下語への参照行だけ除去するか、判断を user に提示 |

死依存と稼働継続の判別を誤ると稼働ツールを壊す。**削除前に「このファイルのネットワーク取得先/データ源は却下対象だけか」を必ず確認**する。

### 4. 除去
- **純言及・確認済みの死依存**を削除する。
- 却下対象**専用の memory ファイル**があれば、ファイル本体 + `heaven/memory/MEMORY.md`（および分割 index）の該当ポインタ行**両方**を削除する（孤立 index 行を残さない）。
- **稼働継続ツールは温存**し、何をなぜ残したかを報告に明記する。

### 5. 残存0を検証
2 と同一の regex・同一 flag で再走査し、**0 件**を確認する（rg は 0ヒットで exit 1）。
```bash
rg -i --no-ignore-vcs --hidden \
  -g '!**/.git/**' -g '!**/node_modules/**' -g '!**/target/**' -g '!**/*.jsonl' \
  -c "$APPROACH_RE" . ; echo "rg_exit=$?  (1 = 残存0 = OK / 0 = まだ残ってる)"
```
編集した YAML/コード が壊れていないか（parse/compile）も spot-check する。

### 6. 報告（ephemeral・repo に書かない）
user に日本語で簡潔に: **削除した件数/ファイル** ・ **温存したもの と its 理由**（巻き添え回避の透明化）・ **到達できない残骸の明示**。記録ファイルは作らない（大原則1）。

## 到達できない残骸（正直に報告する）

- **transcripts**（`~/.claude/projects/<slug>/*.jsonl`、repo外・追記専用、数百ファイル）は scrub 不能。ただし auto-load されないので再提案 vector は弱い。
- **`logs/` / `tmp/`** も append-only/scratch で context に載らないため既定スコープ外（`.ignore` が抑制）。
- **correction queue 経由の cross-session 注入**は既に構造退役済み（commit 29ccbf4 / docs/incidents/2026-06-17-correction-queue-cross-session-drift.md）。本コマンドは触れない。

## 崩落モード（このセッションで実際に踏んだもの）

1. **却下語を memory/記録に書き戻す** — 最重大。自分で residue を作る。やらない（大原則1）。
2. **名前一致で稼働ツールを巻き添え削除** — io で maplestorywiki.net/maplemaps.net を誤削除しかけた。「唯一の源か」確認を飛ばさない。
3. **under-scope** — 単一 app に絞って repo 横断を怠る。io で user が2回差し戻した（「リポジトリ以下から削除しろ」）。既定は repo 横断。
4. **gitignore-blind な grep** — 素の Grep ツール / `grep -r` は局所層（gitignore済み）に盲目で、消し残して「再発」を生む。必ず手順2の `rg --no-ignore-vcs --hidden`（[[feedback_grep_residual_refs_blind_to_gitignored_config]]）。
