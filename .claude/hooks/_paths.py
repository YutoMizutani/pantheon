"""Shared path resolution for frame hooks (environment-derived, no hardcoding).

Claude Code はプロジェクトごとの状態 (memory / telemetry / transcripts) を
``~/.claude/projects/<slug>/`` に置く。slug はプロジェクト絶対パスの非英数字を
'-' に置換したもの (例: ``/Users/you/dev/llm`` → ``-Users-you-dev-llm``)。
hook 実行時は ``CLAUDE_PROJECT_DIR`` 環境変数がプロジェクトルートを指す。
"""
import os
import re
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))


def _slug(p) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


STATE_DIR = Path.home() / ".claude" / "projects" / _slug(PROJECT_DIR)
MEMORY_DIR = STATE_DIR / "memory"
TELEMETRY_DIR = STATE_DIR / "telemetry"
RUNTIME_DIR = PROJECT_DIR / ".claude" / "runtime"

# 生のホームパス (例: /Users/you) が user-facing テキストに漏れたことを検出する正規表現
HOME_HIT_RE = re.compile(re.escape(str(Path.home())) + r"(?!\w)")
