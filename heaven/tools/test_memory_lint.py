#!/usr/bin/env python3
"""memory_lint の判定ロジック回帰テスト (pure 関数のみ)。

実行: cd heaven/tools && python3 -m pytest test_memory_lint.py -q
"""

from memory_lint import (
    extract_index_refs,
    extract_wikilinks,
    name_matches_filename,
    normalize_slug,
    parse_frontmatter,
)


def test_normalize_slug_unifies_kebab_and_snake():
    assert normalize_slug("foo-bar-baz") == normalize_slug("foo_bar_baz")


def test_name_matches_filename_exact():
    assert name_matches_filename("project_skill_dedup_rejected", "project_skill_dedup_rejected")


def test_name_matches_filename_type_prefix_convention():
    # 既存慣習: filename は type プレフィックス付き snake、name はプレフィックス無し kebab
    assert name_matches_filename("verify-before-negating", "feedback_verify_before_negating")
    assert name_matches_filename("heartbeat-and-responsiveness", "feedback_heartbeat_and_responsiveness")


def test_name_matches_filename_rejects_dual_name():
    # 完全別系統の name は二重名 (実在例: verify-before-claiming)
    assert not name_matches_filename("verify-before-claiming", "feedback_verify_before_negating")


def test_name_matches_filename_rejects_partial_word_overlap():
    # suffix 一致は語境界 (_) 必須: "bar" は "foobar" にマッチしない
    assert not name_matches_filename("bar", "foobar")
    assert name_matches_filename("bar", "foo_bar")


def test_parse_frontmatter_quoted_description_and_nested_type():
    text = (
        "---\n"
        'name: project_skill_dedup_rejected\n'
        'description: "cross-project の skill exact-dup は dedup しない判断"\n'
        "metadata: \n"
        "  node_type: memory\n"
        "  type: project\n"
        "---\n\nbody\n"
    )
    fm = parse_frontmatter(text)
    assert fm["name"] == "project_skill_dedup_rejected"
    assert fm["description"].startswith('"cross-project')
    assert fm["type"] == "project"


def test_parse_frontmatter_missing_block():
    assert parse_frontmatter("no frontmatter here") == {
        "name": None,
        "name_raw": None,
        "description": None,
        "type": None,
    }


def test_parse_frontmatter_title_style_name_is_raw_not_slug():
    # 実在した誤報 (原環境): タイトル文 name を「欠落」と報告していた
    text = "---\nname: Do not address the user by name\n---\nbody"
    fm = parse_frontmatter(text)
    assert fm["name"] is None
    assert fm["name_raw"] == "Do not address the user by name"


def test_extract_index_refs_ignores_external_links():
    text = "- [A](foo_bar.md) — x\n- [B](https://example.com/c.md)\n- [C](dir/d.md)\n"
    # 絶対 URL / サブディレクトリパスは memory 参照ではない
    assert extract_index_refs(text) == {"foo_bar.md"}


def test_extract_wikilinks():
    text = "see [[foo-bar]] and [[baz_qux]] but not [single]"
    assert extract_wikilinks(text) == {"foo-bar", "baz_qux"}
