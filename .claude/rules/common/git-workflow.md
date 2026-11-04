# git — 頼まれていない git の話を出さない・状態は確かめてから言う

> 書く瞬間に効く 3 点だけをここ（常時読み込み）に置く。commit / push / init / branch の手順の本体は
> `.claude/rules-ondemand/git-workflow.md` で、`inject_rule_on_tool.py` が git を叩く瞬間に渡す。

- **commit / git の語は、user が頼んだ・訊いたとき以外は自分から出さない。** commit・push・`git init` は user が頼んだときだけで、自分のタスクの延長で計画に入れない（「ok」「続けて」を commit 承認と読まない）。
- **tracked / gitignored / 「commit 対象」を口に出す前に、各パスを個別に確かめ、結果を待って結果どおりに言う** — tracked かは `git ls-files --error-unmatch <path>`（exit 0 = tracked）、ignored かは `git check-ignore -v <path>`（マッチ行 = ignored）。確かめれば分かることを推測で言わない。
- **tracked のファイル（コード・コメント・test の値・docs）と commit メッセージには、作業番号・出来事の日付・セッション ID・user の発言・ローカル層のプロジェクトの語を最初から書かない。** 書くのは今の仕様と理由だけ。公開前の検査（`precommit_privacy.py`）に止められてから消す順番にしない。
