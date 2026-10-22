#!/usr/bin/env python3
"""fetch_any — URL を 1 本、種類に合った経路で取りに行き、原文として保存する（共通の取得口）。

Failure it prevents
-------------------
取得の経路が skill・workflow・検索役ごとに別々で、それぞれが自分の手段で 1 回失敗すると
「取れない」と結論していた。2026-10-22 に 1 日で 3 回: web-research が X の投稿を未取得のまま
未確定扱い、検索役が x.com の HTTP 402 で諦め、imozuru の fetch.py が X を一律に FAIL（「ログインの
壁」）。X の投稿は fxtwitter の API で取れる（skill fetch-xcom と同じ経路）のに、判断の場所から
呼ばれていなかった。「取れない」と書く前にこの 1 本を当てる。

Observable signal
-----------------
標準出力の 1 行（imozuru の fetch.py と同じ形）: OK<TAB>name<TAB>path<TAB>文字数<TAB>冒頭 / FAIL<TAB>name<TAB>理由。
FAIL の理由には、試した経路を全部書く。

使い方
------
  python3 heaven/tools/fetch_any.py <sources_dir> <name> <url>

経路
----
- x.com / twitter.com の投稿（/status/<id>）→ api.fxtwitter.com の JSON から本文・作者・日時・引用元・
  返信先・添付を .md にする
- x.com のプロフィール等（status が無い）→ FAIL（skill fetch-xcom の nitter 経路を案内）
- それ以外 → imozuru の scripts/fetch.py にそのまま渡す（PDF・HTML・YouTube 字幕・検査つき）。
  imozuru は外部から無改変で取り込んだものなので、本体は書き換えずにここから呼ぶ
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

X_STATUS = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter|vxtwitter|fxtwitter)\.com/([^/?#]+)/status/(\d+)")
X_ANY = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/")
IMOZURU_CANDIDATES = [
    Path.home() / ".claude/skills/imozuru/scripts/fetch.py",
    Path(__file__).resolve().parents[2] / ".claude/skills/imozuru/scripts/fetch.py",
]


def x_status_to_md(user: str, sid: str) -> str:
    req = urllib.request.Request(f"https://api.fxtwitter.com/{user}/status/{sid}",
                                 headers={"User-Agent": "Mozilla/5.0 (fetch_any)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        t = json.load(r)["tweet"]
    a = t.get("author", {})
    lines = [f"# X: @{a.get('screen_name', user)} ({a.get('name', '')})",
             f"source: https://x.com/{a.get('screen_name', user)}/status/{sid}",
             f"created_at: {t.get('created_at', '')}",
             f"via: api.fxtwitter.com",
             ""]
    if t.get("replying_to"):
        lines += [f"replying_to: @{t['replying_to']}", ""]
    lines += [t.get("text", "")]
    q = t.get("quote")
    if q:
        qa = q.get("author", {})
        lines += ["", "## 引用元", f"@{qa.get('screen_name', '')} {q.get('created_at', '')}",
                  f"source: {q.get('url', '')}", "", q.get("text", "")]
    media = (t.get("media") or {}).get("all") or []
    if media:
        lines += ["", "## 添付"] + [f"- {m.get('type', '')}: {m.get('url', '')}" for m in media]
    return "\n".join(lines) + "\n"


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) > 8 and not line.startswith(("#", "source:", "created_at:", "via:")):
            return line[:100]
    return ""


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__.split("使い方")[1].split("経路")[0].strip())
        return 2
    sources, name, url = Path(sys.argv[1]).expanduser(), sys.argv[2], sys.argv[3]
    if "/" in name or "\\" in name:
        print(f"FAIL\t{name!r}\t原文の名前は単一のファイル名にする")
        return 1
    m = X_STATUS.match(url)
    if m:
        try:
            body = x_status_to_md(m.group(1), m.group(2))
        except Exception as e:
            print(f"FAIL\t{name}\tX の投稿: api.fxtwitter.com で {type(e).__name__}: {e}"
                  "（残りの経路は skill fetch-xcom の fxtwitter.com の WebFetch → Playwright）")
            return 1
        sources.mkdir(parents=True, exist_ok=True)
        out = sources / f"{name}.md"
        out.write_text(body, encoding="utf-8")
        print(f"OK\t{name}\t{out}\t{len(body)}\t{first_line(body)}")
        return 0
    if X_ANY.match(url):
        print(f"FAIL\t{name}\tX の投稿以外（プロフィール等）は skill fetch-xcom の nitter 経路で取る")
        return 1
    imozuru = next((p for p in IMOZURU_CANDIDATES if p.exists()), None)
    if imozuru is None:
        print(f"FAIL\t{name}\timozuru の fetch.py が見つからない（探した: "
              + ", ".join(str(p) for p in IMOZURU_CANDIDATES) + "）")
        return 1
    return subprocess.run([sys.executable, str(imozuru), str(sources), name, url]).returncode


if __name__ == "__main__":
    sys.exit(main())
