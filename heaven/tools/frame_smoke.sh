#!/usr/bin/env bash
# frame_smoke.sh — tracked ファイル（フレーム層）だけを展開した複製で、hook・層検査・relink が通るかを確かめる。
#
# Failure it prevents
# -------------------
# 手元ではローカル層（.local/・settings.local.json・memory）があるから動く物が、clone しただけの環境では
# hook の異常終了・relink の失敗で動かない。手元のセッションでは気づけない。
#
# Observable signal
# -----------------
# 最終行 `frame_smoke: OK hooks=<N> layer=<違反数> relink=<終了コード>`（exit 0）か
# `frame_smoke: NG <何が>`（exit 1）。
#
# 使い方
# ------
#   bash heaven/tools/frame_smoke.sh                      # HEAD を一時フォルダに展開して検査し、消す
#   FRAME_SMOKE_DIR=<展開済みのフォルダ> bash heaven/tools/frame_smoke.sh   # そのフォルダを検査する（消さない）
#
# 展開先と HOME は一時フォルダにする（relink の `git config`・`~/.claude` への symlink、hook の記録を
# 本物の repo と ~/.claude に書かないため）。展開先が git 管理下なら検査しない。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # heaven/tools/ → repo root
WORK="$(mktemp -d "${TMPDIR:-/tmp}/frame_smoke.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT   # 消すのは自分が作った一時フォルダだけ（FRAME_SMOKE_DIR は消さない）

ng() { echo "frame_smoke: NG $*"; exit 1; }

if [[ -n "${FRAME_SMOKE_DIR:-}" ]]; then
  D="$(cd "$FRAME_SMOKE_DIR" 2>/dev/null && pwd)" || ng "FRAME_SMOKE_DIR が無い: $FRAME_SMOKE_DIR"
else
  D="$WORK/frame"
  mkdir -p "$D"
  git -C "$ROOT" archive HEAD | tar -x -C "$D" || ng "git archive HEAD の展開に失敗"
fi
git -C "$D" rev-parse --git-dir >/dev/null 2>&1 && ng "展開先が git 管理下（relink が本物の repo の設定を書き換える）: $D"
[[ -f "$D/.claude/settings.json" ]] || ng "展開先に .claude/settings.json が無い"

FAKE_HOME="$WORK/home"
mkdir -p "$FAKE_HOME"
: > "$WORK/transcript.jsonl"

# --- 1. settings.json の全 hook を、イベントごとの最小 payload で起動 ---
hooks_out="$(HOME="$FAKE_HOME" python3 - "$D" "$WORK/transcript.jsonl" <<'PY'
import json, os, subprocess, sys
d, transcript = sys.argv[1], sys.argv[2]
settings = json.load(open(os.path.join(d, ".claude/settings.json"), encoding="utf-8"))

def payload(event, matcher):
    tool = (matcher or "Bash").split("|")[0]
    base = {"session_id": "frame-smoke", "transcript_path": transcript, "cwd": d, "hook_event_name": event}
    if event == "UserPromptSubmit":
        return {**base, "prompt": "frame_smoke"}
    inputs = {
        "Bash": {"command": "true", "description": "frame_smoke"},
        "Edit": {"file_path": os.path.join(d, "frame_smoke_probe.md"), "old_string": "a", "new_string": "b"},
        "Write": {"file_path": os.path.join(d, "frame_smoke_probe.md"), "content": "frame_smoke"},
        "Read": {"file_path": os.path.join(d, "README.md")},
        "Skill": {"skill": "frame-smoke"},
    }
    p = {**base, "tool_name": tool, "tool_input": inputs.get(tool, {})}
    if event == "PostToolUse":
        p["tool_response"] = {}
    return p

env = {**os.environ, "CLAUDE_PROJECT_DIR": d}
n, bad = 0, []
for event, groups in settings.get("hooks", {}).items():
    for g in groups:
        for h in g.get("hooks", []):
            if h.get("type") != "command":
                continue
            n += 1
            cmd = h["command"]
            name = cmd.split("/.claude/hooks/")[-1].split('"')[0] if "/.claude/hooks/" in cmd else cmd[:60]
            try:
                r = subprocess.run(["bash", "-c", cmd], cwd=d, env=env, input=json.dumps(payload(event, g.get("matcher"))),
                                   capture_output=True, text=True, timeout=60)
                rc = r.returncode
            except subprocess.TimeoutExpired:
                rc = "timeout"
            if rc != 0:
                bad.append(f"{event}:{name}(exit={rc})")
print(n)
print(" ".join(bad))
PY
)" || ng "hook の起動器が落ちた"
hooks_n="$(sed -n 1p <<<"$hooks_out")"
hooks_bad="$(sed -n 2p <<<"$hooks_out")"
[[ -z "$hooks_bad" ]] || ng "hook $hooks_bad"

# --- 2. 置き場と層の検査 ---
layer_out="$(HOME="$FAKE_HOME" python3 "$D/heaven/tools/layer_check.py" 2>&1)"; layer_rc=$?
layer_n="$(sed -n 's/^layer_check: 違反 \([0-9][0-9]*\) 件$/\1/p' <<<"$layer_out" | tail -1)"
[[ $layer_rc -eq 0 && "$layer_n" == 0 ]] || ng "layer_check exit=$layer_rc 違反=${layer_n:-?}: $(head -3 <<<"$layer_out" | tr '\n' ' ')"

# --- 3. relink（ローカル層が README だけの状態から） ---
relink_out="$(cd "$D" && HOME="$FAKE_HOME" bash "$D/heaven/tools/relink-local.sh" 2>&1)"; relink_rc=$?
[[ $relink_rc -eq 0 ]] || ng "relink-local.sh exit=$relink_rc: $(tail -3 <<<"$relink_out" | tr '\n' ' ')"

echo "frame_smoke: OK hooks=$hooks_n layer=$layer_n relink=$relink_rc"
