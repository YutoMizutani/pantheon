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

# PROJECT_DIR を symlink 解決した正準形 (リポジトリ名 = pantheon に揃えた名前)。rm 系
# ガードが実パスを relative_to で相対化するのに使う — 各 hook で再宣言せずここから import
# する (no-hardcoded-repo-path 規約: ルートを各ファイルで再定義しない)。
PANTHEON_ROOT = PROJECT_DIR.resolve()

# この repo（hook ファイルが置かれた Pantheon）のルート。起動フォルダに依らない。PROJECT_DIR /
# PANTHEON_ROOT は起動フォルダなので、`cd projects/<name> && claude` で起動したセッションでは
# projects/<name> になる。「repo のルート直下」「repo 全体が対象」を判定する hook はこちらを使う
# （2026-10-23: PROJECT_DIR で判定した hook が projects/<name>/TODO.md を「ルート直下」と誤って止めた）。
REPO_ROOT = Path(__file__).resolve().parents[2]


def _slug(p) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


STATE_DIR = Path.home() / ".claude" / "projects" / _slug(PROJECT_DIR)
MEMORY_DIR = STATE_DIR / "memory"
# Telemetry root. ``FRAME_TELEMETRY_DIR`` is a hermetic-test seam (同 FRAME_SIGNALS_FILE):
# when set, ALL frame-hook telemetry writes (reflection_gate / hook_fires / ...) go there
# instead of the operator's real ``~/.claude/projects/<slug>/telemetry/``. Root cause of the
# 2026-06 pollution was that this path derived only from CLAUDE_PROJECT_DIR (= cwd when unset),
# so a manual test run wrote test-gate-*/sigv-* records into the production logs that
# review:pantheon / rule-auditor then read as real activity. Tests redirect via tests/_hermetic.py.
TELEMETRY_DIR = (
    Path(os.environ["FRAME_TELEMETRY_DIR"]).expanduser()
    if os.environ.get("FRAME_TELEMETRY_DIR")
    else STATE_DIR / "telemetry"
)
RUNTIME_DIR = PROJECT_DIR / ".claude" / "runtime"

# 生のホームパス (例: /Users/you) が user-facing テキストに漏れたことを検出する正規表現
HOME_HIT_RE = re.compile(re.escape(str(Path.home())) + r"(?!\w)")
