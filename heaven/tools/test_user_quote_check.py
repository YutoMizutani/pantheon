#!/usr/bin/env python3
"""user_quote_check.py のテスト（user の発言の逐語一致と、user を出典にした書き方で止まるか）。

実行:
    python3 heaven/tools/test_user_quote_check.py
    python3 -m pytest heaven/tools/test_user_quote_check.py -q
"""
import importlib.util
import json
import os
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("user_quote_check", os.path.join(_HERE, "user_quote_check.py"))
uq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uq)

USER = [
    "この設定ファイルは新しい版の起動では読まれないんだけど。嘘付いた？",
    "Stop hook feedback:\n謝罪だけで閉じない、直したファイルと効き方を書くこと",
    "> 自分の回答から貼った行は照合から外して数えない\nこれは本当？",
]


def _run(page_body):
    with tempfile.TemporaryDirectory() as d:
        tr = os.path.join(d, "t.jsonl")
        with open(tr, "w", encoding="utf-8") as f:
            for u in USER:
                f.write(json.dumps({"type": "user", "message": {"content": u}}, ensure_ascii=False) + "\n")
        p = os.path.join(d, "page.html")
        open(p, "w", encoding="utf-8").write(page_body)
        return uq.check(p, uq.user_texts(tr))


def test_verbatim_quote_is_flagged():
    assert any("一致" in b for b in _run("<p>根拠: この設定ファイルは新しい版の起動では読まれない</p>"))


def test_user_attribution_is_flagged():
    assert any("主語" in b for b in _run("<p>user の実機観測によると設定は読まれない。</p>"))
    assert any("主語" in b for b in _run("<p>ユーザー「読まれない」との申告。</p>"))


def test_primary_source_wording_passes():
    assert _run("<p>この設定は起動時に一度だけ読まれる（公式のリリースノート 0.5.0）。</p>") == []


def test_stop_hook_feedback_and_pasted_lines_are_not_user_words():
    assert _run("<p>謝罪だけで閉じない、直したファイルと効き方を書くこと</p>") == []
    assert _run("<p>自分の回答から貼った行は照合から外して数えない</p>") == []


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f(); n += 1
    print(f"OK {n} tests")
