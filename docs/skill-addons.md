# skill addon — 共有の skill を書き換えずに、環境・プロジェクトごとの手順を足す

frame 層の skill やプラグインの skill は、どの環境でも同じ本文で配る。
「この環境ではこの道具も使う」「このプロジェクトではこの書式で積む」といった手順は本文に書かず、
skill addon として別ファイルに置く。skill が呼ばれた直後に hook `.claude/hooks/inject_skill_addons.py` が差し込む。
skill 側に受け口は要らない。addon が 1 つも無ければ何も起きない（opt-in）。

## 置き場所と重なり方

広い方から順に差し込み、食い違うときは後＝深い方を優先する。

| 層 | パス | 例 |
|---|---|---|
| 環境（ローカル層） | `.local/skill-addons/<名前>.md` | どのプロジェクトでも足す手順 |
| プロジェクト | `<dir>/.claude/skill-addons/<名前>.md` | `projects/foo/.claude/skill-addons/lens-next.md` |
| 入れ子のプロジェクト | 深い `<dir>` ごとに同じ形 | `projects/foo/projects/bar/…` や `projects/foo/bar/…`（foo の分に bar の分が重なる） |

- `<名前>` は skill 名。プラグインの skill（`plugin:skill`）は `:` の後ろ（例: `decision-tool-selection.md`）。
- どの階層を読むかは、skill に渡した引数と作業ディレクトリに出てくる repo 内のパスで決まる。決まらないときは環境の分だけを差し込み、「対象のプロジェクトの階層の分も読む」と添える。
- プロジェクトの addon は `.claude/` の下に置く（プロジェクトの `.local/` は実行時データの置き場で、移植しないため）。

## 書き方

- skill 本体の工程名・番号を見出しにして、「何をどこに足すか」を書く（例: 「## 7.『TODO に積んで』に足す」）。
- 由来（いつ誰の指示か）を 1 行残す。消すときにその指示を確かめられるように。
- 本体を打ち消す指示は、深い階層ほど優先されるので、どの層で打ち消すかを決めて書く。
