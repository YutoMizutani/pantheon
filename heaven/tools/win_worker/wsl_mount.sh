#!/bin/bash
# win_worker の WSL 側の下準備。Mac の worker.sh が scp で Windows に置き、wsl で実行する。
#   bash wsl_mount.sh <Mac の user@host> <リポジトリの絶対パス>
# Mac のリポジトリを WSL の同じパスに sshfs でつなぐ（つながっていれば何もしない）。
# sshfs は常駐するので、setsid で別セッションにし標準入出力も切り離す（しないと wsl の呼び出しが閉じない）。
set -u
MAC="${1:?Mac の user@host}"; REPO="${2:?リポジトリの絶対パス}"
ok() { mountpoint -q "$REPO" && [ -f "$REPO/CLAUDE.md" ]; }
ok && { echo MOUNT-OK; exit 0; }
fusermount -uz "$REPO" 2>/dev/null
mkdir -p "$REPO"
setsid -f sshfs "$MAC:$REPO" "$REPO" -f \
  -o BatchMode=yes,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,idmap=user,IdentityFile="$HOME/.ssh/id_ed25519" \
  </dev/null >/dev/null 2>&1
for _ in 1 2 3 4 5 6 7 8 9 10; do ok && { echo MOUNT-OK; exit 0; }; sleep 1; done
echo MOUNT-FAIL; exit 90
