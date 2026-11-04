"""会話記録 (JSONL) を、丸ごとメモリに載せずに読む共通部品。

hook は応答やツール呼び出しのたびに同時に走る。全体を読んで行のリストにすると、記録の約 9 倍の
メモリを使うので、長いセッションでは応答の終わりに重なってマシンが固まる。
読む範囲は変えず、1 行ずつ流す。末尾だけを読む方式にしないのは、1 ターンが 100MB を超える記録が
実在し、ターンの先頭を取りこぼすと判定が変わるため。

行を split するのは改行 (LF) のみ。splitlines() は JSON 文字列内の U+2028 などでも行を割って
その行を読めなくしていた。壊れた UTF-8 は置換して読み進める（書き込み途中の末尾行で落ちない）。
"""
from __future__ import annotations

from collections import deque
from typing import Iterator


def iter_lines(path) -> Iterator[str]:
    """先頭から 1 行ずつ、改行を除いて返す。ファイルが無ければ OSError。"""
    with open(path, encoding="utf-8", errors="replace", newline="\n") as f:
        for line in f:
            yield line[:-1] if line.endswith("\n") else line


def tail_lines(path, n: int) -> list[str]:
    """末尾 n 行を順番どおりに返す。メモリに持つのは n 行だけ。"""
    return list(deque(iter_lines(path), maxlen=n))
