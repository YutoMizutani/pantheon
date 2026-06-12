# 組み込み機構 — 何が入っているか（人間向けの機能説明）

核（ルーティング / heaven / 自己改善ループ）は [design-rationale.md](design-rationale.md)、ループの流れは [self-improvement-loop.md](self-improvement-loop.md) を参照。
ここは**核ではないが配線済みで組み込まれている機構**の早見表。

## 退役機構

規範の獲得（reflection）だけでは規範は単調増加して劣化する。その対として、全 hook の発火 (`_fire_counter.py` → `telemetry/hook_fires.jsonl`) と memory 参照 (`touch_memory_on_read.py` → `telemetry/memory_touches.jsonl`) を常時記録し、棚卸しツール群が「効いていない規範」を可視化する:

| ツール | 出すもの |
|---|---|
| `heaven/tools/telemetry_report.py` | 0 発火の cold hook 一覧 |
| `heaven/tools/rule_adoption_report.py` | enforce 後に再発した規範（定着していない lesson） |
| `heaven/tools/memory_adoption_report.py` | 読まれていない memory 一覧 |
| `heaven/tools/memory_lint.py` | index 漏れ（orphan）・参照切れ・index 肥大の検査 |
| `heaven/tools/skill_gc.py` | 使われない skill の可逆 archive |
| `.claude/agents/rule-auditor.md` | 上記を統合した退役・統合の提案 |

## 条件付きルール機構

ルールに適用条件を付け、効く場面でだけ context を消費させる 2 段階の仕組み。
ファイル単位 = frontmatter `paths:`、セクション単位 = `<important if="...">`。
プロジェクトが増えても常時ロードが肥大しない（ルーティングのスケールを支える）。
仕様: [new-project-setup.md](new-project-setup.md)。

## セットアップ自己修復

「セットアップして」の一言で、エージェント自身が memory symlink（auto-memory 規約パス `~/.claude/projects/<slug>/memory` → `heaven/memory/`）を検査・既存 memory の移行・張り直し・verify まで行う。セッション中に symlink 破損へ気づいた場合も同様（リポジトリ移動などで memory が silent に分岐するのを防ぐ）。実体: ルート [CLAUDE.md](../CLAUDE.md)「初回セットアップ」節。

## memory 衛生 2 hook

- **重複ゲート** `block_memory_duplicate.py` — 既存 memory と類似しすぎる新規ファイル追加を PreToolUse で block し、既存エントリの拡張へ誘導（類義 memory の分裂を防ぐ）。閾値は `FRAME_DUP_THRESHOLD` で上書き可、再導出手順は docstring。
- **index 肥大ゲート** `block_memory_index_bloat.py` — 毎セッション auto-load される index（MEMORY.md）への肥大書き込みを block。auto-load には上限（実測 ~24.4KB）があり、超えると index ごと読まれなくなるため。

index が上限に近づいたら、セクション単位で on-demand の副 index（`MEMORY-<topic>.md`）に分割し、本 index にはトリガ行（「〜に触れるときに読む」）だけを残す — 原環境で実証済みの運用。肥大の定期検査は `memory_lint.py` が担う。
