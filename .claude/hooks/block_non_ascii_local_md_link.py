#!/usr/bin/env python3
"""Stop hook — 最終回答に、日本語（非 ASCII）を含むローカルパスへの markdown リンクがあれば書き直させる。

VSCode 拡張のリンクは非 ASCII を含むパスを開けない（生でも percent エンコードでも）。常時層の
配送面確認原則と memory feedback_vscode_link_non_ascii_path_use_code_r があるのに、直近 20 日で
47 セッション・170 件起きていた（2026-10-19 遡り振り返り sid 21e63be2・653d9bc5。後者は memory を
読んだ直後でも出した）。検出できる形が決まっているので、指示文でなく Stop で強制する。

判定: 最後の user 発話以降で、最後の tool_use より後の assistant テキスト（＝最終回答）から
`](<target>)` を取り出し、http(s):// / mailto: / # で始まらず、非 ASCII 文字か非 ASCII の
percent エンコード（%80〜%FF）を含むものがあれば exit 2。stop_hook_active なら通す（無限ループ防止）。
「止まります。指示ください」等の停止出力はリンクを含まないので通る。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover
    def record_fire(*_a, **_k):  # type: ignore
        pass

LINK = re.compile(r"\]\(([^)\s]+)\)")
PCT_NON_ASCII = re.compile(r"%[89A-Fa-f][0-9A-Fa-f]")


def final_texts(transcript: Path) -> list[str]:
    texts: list[str] = []
    for line in transcript.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        t = d.get("type")
        c = (d.get("message") or {}).get("content")
        if t == "user" and (isinstance(c, str) or (isinstance(c, list) and any(
                isinstance(b, dict) and b.get("type") == "text" for b in c))):
            texts = []
        elif t == "assistant" and isinstance(c, list):
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    texts = []
                elif b.get("type") == "text":
                    texts.append(b.get("text", ""))
    return texts


def bad_links(text: str) -> list[str]:
    out = []
    for m in LINK.finditer(text):
        tgt = m.group(1)
        if tgt.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if any(ord(ch) > 127 for ch in tgt) or PCT_NON_ASCII.search(tgt):
            out.append(tgt)
    return out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("stop_hook_active"):
        return 0
    tp = payload.get("transcript_path")
    if not tp or not Path(tp).exists():
        return 0
    hits = [b for t in final_texts(Path(tp)) for b in bad_links(t)]
    if not hits:
        return 0
    record_fire("feedback_vscode_link_non_ascii_path_use_code_r", "block",
                hook_name="block_non_ascii_local_md_link", count=len(hits))
    sys.stderr.write(
        "日本語（非 ASCII）を含むローカルパスへのリンクは VSCode では開かない: "
        + ", ".join(h[:80] for h in hits[:3])
        + "\nリンクを外して書き直す。別の面（チャットなど）へ送ったなら、そこに送ったものと同じだと書く。"
        "プロジェクトが視聴面を定めていればその直リンク（poe2 ならツリービューア）に置き換える。"
        "開かせたいテキストが 1 つなら `code -r <パス>` で開く。\n"
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
