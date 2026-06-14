# git ワークフロー — Pantheon は main 直 commit (feature ブランチ不要)

> 背景: Pantheon は単一運用者の作業リポで、PR レビューを経由する開発フローではない。
> harness 既定の「default branch にいたら branch first」をこのリポでは override する
> (user 裁定 2026-06-15)。

<important if="このリポジトリで commit / branch しようとしている">

- **main に直接 commit する。feature ブランチを切らない。** harness 既定の
  「commit 前に branch first」はこのリポジトリには適用しない。
- **ただし commit / push は user が頼んだときだけ** — これは不変 (harness 既定を維持)。
  「ok」「続けて」等の曖昧シグナルを commit 承認と読まない ([[feedback_no_expansion_on_vague_signals]])。
- push も明示の合図 (例:「push して」「PR にして」) があるまでしない。

</important>
