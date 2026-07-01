# git ワークフロー — Pantheon は main 直 commit (feature ブランチ不要)

> 背景: Pantheon は単一運用者の作業リポで、PR レビューを経由する開発フローではない。
> harness 既定の「default branch にいたら branch first」をこのリポでは override する
> (user 裁定 2026-06-15)。

<important if="このリポジトリで commit / branch しようとしている、または編集先が tracked か・gitignored か・「commit 対象」かを断定しようとしている">

- **commit / git の語は、user が頼んだ・訊いたとき以外は自分から出さない。** user 発話に「commit / push / git」が無く、commit すべき対象も無いなら、完了報告・脚注のどこにも `commit` ／『未コミット』を書かない。**触れる必要があるとき（user が訊いた等）だけ**、推測で言わず確認してから事実だけ返す — ignored かは `git check-ignore -v <path>`（マッチ行が出れば ignored）、tracked かは `git ls-files --error-unmatch <path>`（exit 0 なら tracked）。『gitignored の可能性が高い』『未コミットかも』のように、確認すれば分かるのに推測で言うのは禁止。
- **main に直接 commit する。feature ブランチを切らない。** harness 既定の
  「commit 前に branch first」はこのリポジトリには適用しない。
- **ただし commit / push は user が頼んだときだけ** — これは不変 (harness 既定を維持)。
  「ok」「続けて」等の曖昧シグナルを commit 承認と読まない ([[feedback_no_expansion_on_vague_signals]])。
- push も明示の合図 (例:「push して」「PR にして」) があるまでしない。
- **`git init` / 新規リポ作成も同じ — user が頼んだときだけ。** 「commit/push は user が頼んだとき」は実行だけでなく **intent 形成（自発的に commit/init を計画に据えること）にも効く**。user が一度も「commit」「git 化」「リポにして」と言っていないのに、自分のタスク遂行（実装完了・bug fix）の延長で「ついでに commit まで進める」「version 管理されていないから git init するのが安全」と**自己生成した git 目標を既定プランに格上げしてはならない**。version 管理外の成果物は version 管理外のまま残すのが既定で、それは欠陥ではない。
- **「commit」という語を自分で持ち出したら、それは既に逸脱の入口。** user 発話に commit/init/push/リポ/版管理 のいずれも無いなら、git は話題にしない。「データ保護のため git init が唯一安全な解釈」のような**自分で作った前提から git init を導く推論連鎖は禁止** — 高機微データの保護は `.gitignore` 編集や配置で達成でき、新規リポ作成を要求しない（2026-06-17 sid 8ef2f6eb: user は「カード明細を置いた」としか言っていないのに 03:11『commit まで進めます』→03:15『独立リポとして git init し…が唯一安全な解釈。まず実装します』と self-escalate。実際に init しなかったのは理由ログの無い偶発的 self-reversal で、規範が確実に効いたのではない）。
- **tracked / 管理外 / IGNORED / 「commit 対象」を口に出す前に `git ls-files <編集先>` を踏み、その結果を待ってから言う。** 空（= ローカル層・gitignored、例: `projects/llm`・event-sentinel テンプレ由来）なら commit を脚注ですら言及しない。`.gitignore` や本 rule の存在は scaffolding（体裁）であって tracking（実態）の証拠ではない（むしろ ignore = 管理外の証拠）。**6 回再発の核は「確認手順を踏んでもその結果に反して断定する」出力癖** — `git check-ignore` を実行してもなお『git 管理外です』と断定した（sid 4c69ae7f）。手順を踏むだけでなく**結果を待って、結果どおりに言う**。向きは 2 つ — **tracked か**は `git ls-files --error-unmatch <path>`（exit 0 = tracked）、**ignored か**は `git check-ignore -v <path>`（マッチ行が出る = ignored）で、断定したい向きの方を実際に踏む。完了報告・脚注・一覧で**複数ファイルの commit 可否や tracked 状態に触れるときも、各パスを個別に確認してから**「これは入る／これは gitignored で入らない」と書く（「両方 tracked」式の一括断定が再発の典型）。[[feedback_no_assumed_commit_for_projects_metadir]]
- enforce は instruction 層のみ（hook 不在は意図的）: `git init`/`git commit` の Bash 一律 block は tmp テスト・frame 化承認作業など正当な実行を大量 false-positive にする（過去 transcript の git init 13 件中 11 件が legitimate）。loud×rare な事象なので measure-first により hook は作らず、本 rule と [[feedback_no_assumed_commit_for_projects_metadir]] / [[feedback_no_expansion_on_vague_signals]] で縛る。
- **escalation（audit→block 昇格の接地・2026-06-20 追加）**: 本 instruction が今後も破られたら、その実例を必ず 2 類型で分類して記録する — **(i) command 未実行型**（ls-files / check-ignore を踏まずに tracked/commit を断定）と **(ii) misread 型**（踏んだのに出力に反して断定）。**(i) が出たときだけ**、「その turn の tool 履歴に検証コマンドが無ければ tracked/commit の断定出力を Stop する」narrow gate を新設してよい — ただし live 化前に、既存の最上位安全ガードの正当停止出力（[[feedback_never_anger_user_absolute]] の「止まります。指示ください」・gate 拒否後の停止・役割逆転回避）を RED ケースに入れ、新 gate がそれを pass することを確認する（[[feedback_new_gate_must_not_block_existing_safety_guard]]）。**(ii) は意味照合で機械検出が不能**（コマンドの有無を見る gate は素通りする）なので mechanism を足さず instruction 強化に留める。**分類根拠なしに block を配線しない** — loud×recurring な failure に measure-first を通さず gate を積むのを防ぐ。（個別 incident の回数・日付・分類の forensic 記録はこのルール本体に書かない＝毎セッション露出すると意味不明なノイズになるため。残すなら memory 側に。）
- **不可逆な外部作用（push / 送信 / 公開）は、ローカル準備（commit / stage / build）と同一の Bash 複合コマンドに `&&` / `;` / 改行で束ねず、必ず最後の単独 tool 呼び出しに分離する。** user の queued 入力は実行中の 1 tool 呼び出しの境界でしか効かないため、「ゲート→commit→push」を 1 スクリプトで走らせると、user が途中で止めたくても物理的に間に合わず push まで走り切る（2026-06-29 sid fa9d2f79 の実害: 「まだcommitするな」が届く前に commit→push 完走、しかも架空ハードコードパス含有版を公開リポへ。force-push で原状回復）。push の直前に `git log` / 最終ゲート test を**別呼び出し**で走らせ、結果を見てから push を**別呼び出し**で打つ — ゲートと push の間に割り込み点（tool 境界）を必ず 1 つ残す。これは「commit/push は user が頼んだときだけ」（承認の有無）とは別軸の「承認済みでも user の中断権を物理的に保つ」原則。[[feedback_separate_irreversible_network_step_from_local_prep]]

</important>
