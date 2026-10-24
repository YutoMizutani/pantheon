#!/usr/bin/env bash
# relink-local.sh — `.local/` に集約したローカル層を harness 期待パスへ symlink し直す。
#
# 用途: 別マシンへ移植したとき、リポジトリを clone → 旧環境の `.local/` を配置 →
#       本スクリプトを 1 回実行すると、harness が期待する固定パス
#       (root の CLAUDE.local.md 等) への symlink を全て再生成する。
#       symlink はパス文字列を保持するだけなので、移植先では必ず張り直しが要る。
#
# 冪等: 既存 symlink は ln -sfn で原子的に張り直す。期待パスに「実体ファイル」が
#       居る場合は壊さず SKIP して警告する (先に .local/ へ退避してから再実行)。
#
# frame 層: 特定の hook ファイル名はハードコードせず `.local/hooks/*` を走査する。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # heaven/tools/ → repo root
cd "$ROOT"

[[ -d .local ]] || { echo "ERROR: $ROOT/.local が無い。移植元の .local/ を配置してから実行"; exit 1; }

# link <symlink を置くパス> <symlink の張り先 (symlink のあるディレクトリからの相対)>
link() {
  local lp="$1" tgt="$2"
  if [[ -e "$lp" && ! -L "$lp" ]]; then
    echo "SKIP (実体が存在): $lp — 先に .local/ へ退避してから再実行"
    return
  fi
  mkdir -p "$(dirname "$lp")"
  ln -sfn "$tgt" "$lp"
  echo "linked: $lp -> $tgt"
}

# --- Pantheon 固定構造 (frame が定める期待パス) ---
[[ -e .local/CLAUDE.local.md     ]] && link CLAUDE.local.md             .local/CLAUDE.local.md
[[ -e .local/PROJECTS.md         ]] && link PROJECTS.md                 .local/PROJECTS.md
[[ -e .local/settings.local.json ]] && link .claude/settings.local.json ../.local/settings.local.json

# --- git の hook (.githooks/: 個人情報・ローカル層の中身を公開側へ入れない pre-commit 等) ---
git config core.hooksPath .githooks && echo "git: core.hooksPath = .githooks"

# --- local hook 群 (ユーザー固有・汎用走査。frame の *.example / README は触らない) ---
if [[ -d .local/hooks ]]; then
  for f in .local/hooks/*; do
    [[ -e "$f" ]] || continue
    link ".claude/hooks/local/$(basename "$f")" "../../../.local/hooks/$(basename "$f")"
  done
fi
# 実体を移した・消した hook の symlink は張り先が無いまま残るので外す
for lp in .claude/hooks/local/*; do
  if [[ -L "$lp" && ! -e "$lp" && "$(readlink "$lp")" == ../../../.local/hooks/* ]]; then
    rm "$lp"; echo "removed (張り先なし): $lp"
  fi
done

# --- local rule 群: .local/rules/* → .claude/rules/local/ (harness は rules/ の下を再帰で読む) ---
if [[ -d .local/rules ]]; then
  for f in .local/rules/*.md; do
    [[ -e "$f" ]] || continue
    link ".claude/rules/local/$(basename "$f")" "../../../.local/rules/$(basename "$f")"
  done
fi

# --- local skill / command: ユーザー単位の置き場 (~/.claude/skills・~/.claude/commands) へ ---
#     .claude/skills/<名前> は 1 階層しか読まれず、フォルダで層を分けられない。plugin にすると呼び名が
#     <plugin>:<名前> に変わる。手元だけの skill / command は名前を変えずに使えるよう、ユーザー単位の
#     置き場から .local/ の実体へ symlink する (このマシンの他のリポジトリでも見える点は承知の上)。
link_user() {   # link_user <~/.claude 側のパス> <実体の絶対パス>
  local lp="$1" tgt="$2"
  if [[ -e "$lp" && ! -L "$lp" ]]; then echo "SKIP (実体が存在): $lp"; return; fi
  mkdir -p "$(dirname "$lp")"; ln -sfn "$tgt" "$lp"; echo "linked: $lp -> $tgt"
}
if [[ -d .local/skills ]]; then
  for d in .local/skills/*/; do
    [[ -f "${d}SKILL.md" ]] && link_user "$HOME/.claude/skills/$(basename "$d")" "$ROOT/${d%/}"
  done
fi
if [[ -d .local/commands ]]; then
  for f in .local/commands/*.md; do
    [[ -e "$f" ]] && link_user "$HOME/.claude/commands/$(basename "$f")" "$ROOT/$f"
  done
fi

# --- プロジェクトの skill / agent / hook をルートへ (opt-in・plugin として登録) ---
#     ルートから起動したセッションには projects/<name>/.claude/skills|agents が読み込まれない
#     (実測: ルート起動の Skill 一覧に projects/<name> の skill が載らず、ファイルを読んでも遅れて読み込まれない)。
#     projects/.claude-plugin/marketplace.json に載せたプロジェクトの .claude/ を、手元の範囲 (--scope local) の
#     plugin として登録する。名前は <plugin>:<名前> になる。
#     Skill として呼んだときに読まれる本文は ~/.claude/plugins/cache/ の写しで、install しただけでは取り直されない
#     (写しが古いまま、元の場所での Skill の直しがどのセッションにも届かなくなる)。
#     install の後に update で写しを元の場所から取り直す (反映は次のセッション開始時)。
#     ルートの .claude/ に symlink を張ってファイル単位で exclude する方式はやめた (層をディレクトリで分けるため)。
mk="projects/.claude-plugin/marketplace.json"
if [[ -f "$mk" ]] && command -v claude >/dev/null; then
  mk_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$mk")"
  sl=".claude/settings.local.json"
  # CLI は symlink の settings へ書かない (SymlinkWriteRefusedError)。書く間だけ実ファイルにし、.local へ戻す
  restore=""
  if [[ -L "$sl" && -f .local/settings.local.json ]]; then rm "$sl"; cp .local/settings.local.json "$sl"; restore=1; fi
  claude plugin marketplace add "$ROOT/projects" --scope local >/dev/null 2>&1 || true
  for p in $(python3 -c 'import json,sys; print(" ".join(x["name"] for x in json.load(open(sys.argv[1]))["plugins"]))' "$mk"); do
    claude plugin install "$p@$mk_name" --scope local >/dev/null 2>&1 && echo "plugin: $p@$mk_name (local)" || echo "WARN: plugin $p@$mk_name を登録できない"
    claude plugin update "$p@$mk_name" --scope local >/dev/null 2>&1 || echo "WARN: plugin $p@$mk_name の写しを取り直せない"
  done
  if [[ -n "$restore" ]]; then cp "$sl" .local/settings.local.json; rm "$sl"; link "$sl" ../.local/settings.local.json; fi
fi

# --- memory: SSoT は heaven/memory。.local からの可視 symlink を張り直す ---
[[ -e heaven/memory ]] && link .local/memory ../heaven/memory

# --- memory の ~/.claude 側 symlink (slug 依存・cross-boundary) ---
slug="$(printf '%s' "$ROOT" | sed 's/[^a-zA-Z0-9]/-/g')"
cdir="$HOME/.claude/projects/$slug"
if [[ -d "$cdir" ]]; then
  ln -sfn "$ROOT/heaven/memory" "$cdir/memory"
  echo "linked: $cdir/memory -> $ROOT/heaven/memory"
else
  echo "NOTE: $cdir が無い — ~/.claude の memory link は初回 setup 完了後に再実行のこと"
fi

# --- 置き場と層の検査: 配る層のディレクトリをファイル単位で隠す行・heaven のコードがプロジェクトを指す行 ---
python3 heaven/tools/layer_check.py | tail -1 || true

# --- hook の行の検査: ファイルが無い行・「無ければ通す」形になっていない行 ---
#     python3 <無いファイル> は exit 2 ＝「ツールを止める」になり、全セッションの全ツールが止まる
python3 heaven/tools/hook_guard.py --check || echo "WARN: 上の行を直すこと（python3 heaven/tools/hook_guard.py で一括対応）"

echo "done."
