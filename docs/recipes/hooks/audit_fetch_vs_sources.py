#!/usr/bin/env python3
# RECIPE (非配線): 原環境の自己改善ループが育てた規範の実体。
# 採用条件 = 出典を実取得せず外部事実を書く失敗を自環境で観測したこと。
# 採用手順と再校正の注意は ../README.md (docs/recipes/README.md)。
# コード/メッセージ内の .claude/workflows/web-research.js 参照は「採用後のパス」として正しいので変更しない。
"""audit_fetch_vs_sources — observe-mode Stop hook.

最新ターンの回答が「Sources:/出典:/参考:」節に URL を記名しているのに、その URL を
**セッション内で一度も WebFetch / curl で実取得していない** 場合を audit する。
web-research workflow (.claude/workflows/web-research.js) の「一次ソース先行」規約の
決定論側の対。

対応する失敗パターン:
  * fetch 失敗 (403/404/405) の沈黙吸収 — 取れなかった URL を「Sources」に記名
  * WebSearch のサマリを一次ソース扱い — search しただけの URL を出典記名

設計:
  * Stop event hook. payload の transcript を読み、(a) セッション全体で WebFetch /
    Bash(curl/wget) が実取得した URL 集合 と、(b) current turn (最新 user msg 以降) の
    assistant text の「Sources 節」に記名された URL を突合する。
  * 記名されたが実取得されていない URL があれば audit log + stderr warn。
  * **WebSearch しただけの URL は fetched に含めない** (= search サマリ依存も検出対象)。
  * observe モード: 常に exit 0、enforce しない。Stop は post-display で block 不可
    なのもあり、価値は「post-turn の決定論的シグナルで自己想起ギャップを閉じる」こと。
    新 hook は observe モードで誤検知率を測ってから enforce 化を判断する、という
    フレームの設計ゲート (docs/self-improvement-loop.md) に従う
    (search snippet 由来 / 読み取り proxy 経由 fetch 等の false positive 想定)。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# 同ディレクトリの _paths / _fire_counter を import 可能にする
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _paths import TELEMETRY_DIR  # noqa: E402
except Exception:
    import os

    TELEMETRY_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())) / ".claude" / "runtime"
try:
    from _fire_counter import record_fire  # type: ignore # noqa: E402
except Exception:  # pragma: no cover - telemetry は best-effort
    def record_fire(*_a, **_k) -> None:  # type: ignore
        return None

_AUDIT_LOG = TELEMETRY_DIR / "audit_fetch_vs_sources.log"

# 「Sources / 出典 / 参考」見出し. マッチ位置以降を出典範囲とみなす.
_SOURCE_HEADER = re.compile(
    r"(?:^|\n)\s*[*\-#>　]*\s*"
    r"(Sources?|出典(?:・鮮度)?|参考文献|参考|引用元|引用|一次ソース|出所|根拠ソース|References?)"
    r"\s*[:：]",
    re.IGNORECASE,
)

# URL 抽出 (末尾の句読点 / 閉じ括弧 / 全角記号を除く)
_URL_RE = re.compile(r"https?://[^\s)>\]」』、，,。　]+")

# Bash command 内に curl/wget があるかの判定
_CURL_RE = re.compile(r"\b(?:curl|wget|http|https)\b")


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _norm_url(u: str) -> str:
    """scheme / www / 末尾記号を落として比較用に正規化."""
    u = u.strip().rstrip("/.,)>]」』。、")
    u = re.sub(r"^https?://", "", u, flags=re.IGNORECASE)
    u = re.sub(r"^www\.", "", u, flags=re.IGNORECASE)
    return u.lower()


def _iter_records(transcript_path: str):
    p = Path(transcript_path)
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj
    except OSError:
        return


def _collect(transcript_path: str) -> tuple[set, str]:
    """(セッション全体で実取得した URL 集合, current turn の assistant text) を返す.

    実取得 = WebFetch(url) または Bash の curl/wget コマンド引数の URL.
    WebSearch (query のみ / 結果 URL は fetch でない) は **含めない**.
    current turn = 最新 user message 以降の assistant text 連結.
    """
    fetched: set = set()
    turn_parts: list = []

    for obj in _iter_records(transcript_path):
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        if role == "user":
            # 新しい user turn の開始 → current turn テキストをリセット.
            # ただし tool_result (role=user) は turn 境界ではないので除外.
            is_tool_result = isinstance(content, list) and any(
                isinstance(c, dict) and c.get("type") == "tool_result" for c in content
            )
            if not is_tool_result:
                turn_parts = []
            continue

        if role != "assistant":
            continue

        if isinstance(content, str):
            turn_parts.append(content)
        elif isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                ctype = c.get("type")
                if ctype == "text":
                    turn_parts.append(str(c.get("text", "")))
                elif ctype == "tool_use":
                    name = c.get("name") or ""
                    inp = c.get("input") or {}
                    if not isinstance(inp, dict):
                        continue
                    if name == "WebFetch":
                        url = inp.get("url")
                        if isinstance(url, str) and url:
                            fetched.add(_norm_url(url))
                    elif name == "Bash":
                        cmd = inp.get("command") or ""
                        if isinstance(cmd, str) and _CURL_RE.search(cmd):
                            for m in _URL_RE.finditer(cmd):
                                fetched.add(_norm_url(m.group(0)))

    return fetched, "\n".join(turn_parts).strip()


def _source_urls(text: str) -> list:
    """current turn text の Sources 節に記名された URL を抽出."""
    if not text:
        return []
    headers = list(_SOURCE_HEADER.finditer(text))
    if not headers:
        return []
    urls: list = []
    for h in headers:
        # 見出し末尾から次の見出し開始 (or 文末) までを出典範囲とする
        start = h.end()
        nxt = _SOURCE_HEADER.search(text, start)
        end = nxt.start() if nxt else len(text)
        segment = text[start:end]
        for m in _URL_RE.finditer(segment):
            urls.append(m.group(0))
    return urls


def _is_fetched(src_url: str, fetched: set) -> bool:
    n = _norm_url(src_url)
    if not n:
        return True  # 空は判定不能 → 黙認
    for f in fetched:
        # どちらかが他方を包含すれば取得済みとみなす (path 末尾 / クエリの揺れ吸収)
        if n == f or n in f or f in n:
            return True
    return False


def _log_hit(sid: str, unfetched: list, total_src: int, text: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "ts": ts,
            "session_id": sid,
            "category": "fetch_vs_sources",
            "unfetched_sources": unfetched[:10],
            "source_count": total_src,
            "text_tail": text[-400:],
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    data = _read_payload()
    sid = data.get("session_id") or ""
    transcript = data.get("transcript_path") or ""
    if not transcript:
        return 0

    fetched, text = _collect(transcript)
    src_urls = _source_urls(text)
    if not src_urls:
        return 0

    unfetched = [u for u in src_urls if not _is_fetched(u, fetched)]
    if not unfetched:
        return 0

    _log_hit(sid, unfetched, len(src_urls), text)
    record_fire(
        "web_research_fetch_vs_sources",
        "audit",
        hook_name="audit_fetch_vs_sources",
        count=len(unfetched),
        context=",".join(unfetched[:3]),
    )
    shown = ", ".join(sorted(set(unfetched))[:5])
    sys.stderr.write(
        f"[audit_fetch_vs_sources] 出典に記名した {len(unfetched)}/{len(src_urls)} 件の URL を "
        f"このセッションで WebFetch/curl していません ({shown})。"
        "一次ソース先行原則: WebSearch のサマリや 403/404 を Sources に記名しない。"
        "実取得した一次ソースだけを出典に。"
        "事実確定が必要なら web-research workflow (.claude/workflows/web-research.js) を通す。\n"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # hook は harness を壊さない
        sys.stderr.write(f"[audit_fetch_vs_sources] error: {exc}\n")
        sys.exit(0)
