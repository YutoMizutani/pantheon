#!/usr/bin/env python3
"""source_audit.py のテスト（札なしの言い切りを止め、札付き・表の注で受け継ぐ行・md を正しく扱うか）。

実行:
    python3 heaven/tools/test_source_audit.py
    python3 -m pytest heaven/tools/test_source_audit.py -q
"""
import importlib.util
import os
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("source_audit", os.path.join(_HERE, "source_audit.py"))
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)


def _run(body, ext=".html", tokens=None):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "page" + ext)
        open(p, "w", encoding="utf-8").write(body)
        tf = None
        if tokens:
            tf = os.path.join(d, "tokens.txt")
            open(tf, "w", encoding="utf-8").write("\n".join(tokens))
        return sa.audit(p, sa.load_src(tf))


def test_unlabeled_claims_are_flagged():
    bad = _run("<p>この設定は確定で効きます。</p><p>ほかの柱の価値を上げます。</p>")
    assert len(bad) == 2


def test_labeled_claim_passes():
    assert _run("<p>この設定は確定で効きます（公式のパッチノート）。</p><p>確定で効くとされる（記事の記述）。</p>") == []


def test_table_rows_inherit_note_source():
    body = "<table><tr><th>物</th><th>効果</th></tr><tr><td>A</td><td>確定で出る</td></tr></table><p class=\"note\">出典: 仕様書 3 章</p>"
    assert _run(body) == []


def test_table_without_source_is_flagged():
    body = "<table><tr><th>物</th><th>効果</th></tr><tr><td>A</td><td>確定で効く設定がある</td></tr></table>"
    assert len(_run(body)) == 1


def test_project_tokens_extend_sources():
    body = "<p>この設定は確定で効きます（example-db）。</p>"
    assert len(_run(body)) == 1
    assert _run(body, tokens=["example-db"]) == []


def test_markdown_bullets_and_paragraphs():
    body = "# 見出し\n\n- 申請は必ず月末までに出す必要がある\n- 申請は月末まで（社内規程 4 条の引用）\n\n手数料は 3% 増加する。\n"
    bad = _run(body, ext=".md")
    assert len(bad) == 2


def test_short_or_non_claim_text_passes():
    assert _run("<p>次へ進む。</p><p>設定画面で項目を選んで開く。</p>") == []


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f(); n += 1
    print(f"OK {n} tests")
