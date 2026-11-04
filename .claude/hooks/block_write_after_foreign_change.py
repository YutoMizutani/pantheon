#!/usr/bin/env python3
"""block_write_after_foreign_change — 別のセッションが変えた後のファイルを、読み直さずに書き換える
Write/Edit を止める PreToolUse フック。

Failure it prevents
-------------------
fork したセッションは元の会話の読み書きの記録を引き継ぐため、harness の「読まずに上書き」検査が
効かない。分岐後に元の会話がファイルを書き直していても、fork 側は古い理解のまま上書きできてしまう。
実害: fork したセッションが、元の会話の作業中のソースを Write で上書きし、そのまま実機にも書き込んだ。並行する別セッションが同じファイルを触る場合も同じ。

Observable signal
-----------------
対象ファイルが存在し、この会話の transcript にそのパスへの Read/Write/Edit/NotebookEdit か、
パスを含む Bash があり、その最後の時刻（tool_result の時刻を含む）よりファイルの更新日時が
SLACK_S 秒以上新しいとき exit 2。一度も触れていないファイルは、会話の最初の時刻より新しければ exit 2。

1 つの返答に並べた同じファイルへの Edit は、1 件目の実行時点ではまだ記録に書かれていない
（実例: 2〜4 件目が、1 件目の更新を別のセッションの変更と取り違えて止まった）。
そこで、この hook が書き込みを通した時刻をセッションごとに残し、記録の時刻と並べて「最後に触れた時刻」に数える。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _fire_counter import record_fire  # type: ignore
except Exception:  # 計器が無くても本体は動かす
    def record_fire(*a, **k):
        return None

try:
    from _paths import STATE_DIR  # type: ignore
except Exception:
    STATE_DIR = None

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
TOUCH_TOOLS = WRITE_TOOLS | {"Read"}
SLACK_S = 5.0  # 自分の書き込みの後に走る整形 hook 等で更新日時が少し後ろへずれる分


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def last_touch(transcript: str, path: str) -> tuple[float | None, float | None]:
    """(この会話で最後に path に触れた時刻, 会話の最初の時刻)。

    触れた時刻は tool_use と、その tool_result の遅い方。
    """
    touched_ids: set[str] = set()
    last: float | None = None
    first: float | None = None
    try:
        f = open(transcript, encoding="utf-8", errors="ignore")
    except OSError:
        return None, None
    with f:
        for line in f:
            if first is None and '"timestamp"' in line:
                try:
                    first = _ts(json.loads(line).get("timestamp"))
                except json.JSONDecodeError:
                    pass
            if path not in line and not touched_ids:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (d.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            t = _ts(d.get("timestamp"))
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    name = b.get("name")
                    hit = (name in TOUCH_TOOLS and inp.get("file_path") == path) or (
                        name == "Bash" and path in str(inp.get("command", "")))
                    if hit:
                        touched_ids.add(b.get("id", ""))
                        if t is not None:
                            last = t if last is None else max(last, t)
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in touched_ids:
                    if t is not None:
                        last = t if last is None else max(last, t)
    return last, first


def _passes_file(sid: str) -> Path | None:
    if STATE_DIR is None or not sid or "/" in sid:
        return None
    return STATE_DIR / "write_passes" / f"{sid}.json"


def own_pass(sid: str, path: str) -> float | None:
    """このセッションでこの hook が path への書き込みを最後に通した時刻。"""
    f = _passes_file(sid)
    try:
        return float(json.loads(f.read_text()).get(path)) if f else None
    except Exception:
        return None


def record_pass(sid: str, path: str) -> None:
    f = _passes_file(sid)
    if f is None:
        return
    try:
        d = json.loads(f.read_text()) if f.exists() else {}
        d[path] = __import__("time").time()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d))
    except Exception:
        pass


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if data.get("tool_name") not in WRITE_TOOLS:
        return 0
    inp = data.get("tool_input") or {}
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    sid = str(data.get("session_id") or "")
    if not path:
        return 0
    if not os.path.isfile(path):
        record_pass(sid, path)  # 新規作成の直後に並べた Edit も、自分の書き込みとして通す
        return 0
    touched, first = last_touch(data.get("transcript_path") or "", path)
    mine = own_pass(sid, path)
    if mine is not None:
        touched = mine if touched is None else max(touched, mine)
    # 一度も触れていないファイルは、会話の開始後に更新されていれば止める。パスが相対パスや
    # 変数の入った Bash でしか現れず、記録の上では「未接触」に見える場面が実害の元だった。
    since = touched if touched is not None else first
    if since is None:
        record_pass(sid, path)
        return 0
    mtime = os.path.getmtime(path)
    if mtime - since < SLACK_S:
        record_pass(sid, path)
        return 0
    try:
        record_fire("block_write_after_foreign_change", "block", context=path)
    except Exception:
        pass
    when = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
    mine = datetime.fromtimestamp(since).strftime("%m-%d %H:%M")
    seen = "この会話で最後に触れたのは" if touched is not None else "この会話では Read していない（会話の開始は"
    tail = "" if touched is not None else "）"
    print(f"{path} は {when} に更新されているが、{seen} {mine}{tail}。"
          f"その間に別のセッション（fork 元の会話・並行する会話）・user・自分の Bash（相対パスや変数で書いた分）のどれかが変えている。"
          f"`python3 heaven/tools/peers.py --grep <ファイル名>` で誰が触っているかを確かめ、"
          f"Read で今の中身を読み直してから、同じ呼び出しを出し直す。"
          f"相手が作業中なら、書く前に SendMessage で一声かける。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
