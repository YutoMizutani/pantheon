#!/usr/bin/env python3
"""claude_code_changelog の回帰テスト（偽 CHANGELOG 3 版・hook の行 2 つ）。

HOME を一時ディレクトリに向けるので、本番の ~/.claude/runtime には書かない。
取得は file:// の URL で行う（ネットワークに出ない）。

実行:
    python3 -m pytest heaven/tools/test_claude_code_changelog.py -q
    python3 heaven/tools/test_claude_code_changelog.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("claude_code_changelog",
                                               os.path.join(_HERE, "claude_code_changelog.py"))
ccc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccc)

FAKE = """# Changelog

## 1.2.10

- Added `PostToolBatch` hook event
- Fixed scrolling in the transcript view

## 1.2.9

- Fixed PreToolUse hooks ignoring `updatedInput` on Windows
- Added a `skills` field to settings.json
- Improved startup time

## 1.2.8

- Fixed a crash when the plugin directory is missing
"""


def _run(*args, home=None, version="1.2.10"):
    """main() を HOME と `claude --version` を差し替えて呼ぶ。(rc, stdout) を返す。"""
    home = home or tempfile.mkdtemp()
    src = Path(home) / "CHANGELOG.md"
    if not src.exists():
        src.write_text(FAKE, encoding="utf-8")
    old_home, old_ver = os.environ.get("HOME"), ccc.installed_version
    os.environ["HOME"] = home
    ccc.installed_version = lambda: version
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = ccc.main(["--url", src.as_uri(), *args])
    finally:
        ccc.installed_version = old_ver
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
    return rc, out.getvalue(), home


def test_range_is_since_exclusive_until_inclusive():
    rc, out, _ = _run("--since", "1.2.8", "--until", "1.2.10")
    assert rc == 0, out
    first = out.splitlines()[0]
    assert first.startswith("claude_code_changelog: OK range=1.2.8..1.2.10 versions=2 matched="), first
    assert "## 1.2.10" in out and "## 1.2.9" in out and "## 1.2.8" not in out, out
    assert "plugin directory" not in out, out  # 1.2.8 は範囲外


def test_hook_lines_are_picked_and_others_dropped():
    rc, out, _ = _run("--since", "1.2.8", "--keywords", "hook")
    assert rc == 0, out
    assert "matched=2" in out.splitlines()[0], out
    assert "- Added `PostToolBatch` hook event" in out, out
    assert "- Fixed PreToolUse hooks ignoring `updatedInput` on Windows" in out, out
    assert "scrolling" not in out and "startup time" not in out, out


def test_default_keywords_include_settings_and_plugin():
    rc, out, _ = _run("--since", "1.2.7")
    assert rc == 0, out
    assert "matched=4" in out.splitlines()[0], out  # hook 2 + settings 1 + plugin 1
    assert "settings.json" in out and "plugin directory" in out, out


def test_keyword_needs_word_start():
    # "hook" は "hooks" に当たるが、語の途中（"webhooks"）には当たらない
    home = tempfile.mkdtemp()
    (Path(home) / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 1.0.1\n\n- Fixed webhooks retry\n- Fixed hooks timeout\n", encoding="utf-8")
    rc, out, _ = _run("--since", "1.0.0", "--keywords", "hook", home=home, version="1.0.1")
    assert rc == 0, out
    assert "matched=1" in out.splitlines()[0], out
    assert "webhooks" not in out, out


def test_camel_case_setting_key_counts_as_word_start():
    home = tempfile.mkdtemp()
    (Path(home) / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 1.0.1\n\n- Fixed `disableAllHooks` being ignored\n"
        "- Fixed `allowedHttpHookUrls` admitting everything\n- Fixed webhook payloads\n", encoding="utf-8")
    rc, out, _ = _run("--since", "1.0.0", "--keywords", "hook", home=home, version="1.0.1")
    assert rc == 0, out
    assert "matched=2" in out.splitlines()[0], out
    assert "webhook payloads" not in out, out


def test_mark_checked_moves_default_since():
    rc, out, home = _run("--since", "1.2.8", "--until", "1.2.9", "--mark-checked")
    assert rc == 0, out
    saved = json.loads((Path(home) / ".claude/runtime/claude_code_changelog/last_checked.json").read_text())
    assert saved["version"] == "1.2.9", saved
    rc, out, _ = _run(home=home)  # since 省略 → last_checked の 1.2.9、until 省略 → claude --version の 1.2.10
    assert rc == 0, out
    assert out.splitlines()[0].startswith("claude_code_changelog: OK range=1.2.9..1.2.10 versions=1"), out
    assert "## 1.2.9" not in out, out


def test_default_since_without_last_checked_is_installed_version():
    rc, out, _ = _run(version="1.2.10")
    assert rc == 0, out
    assert out.splitlines()[0].startswith("claude_code_changelog: OK range=1.2.10..1.2.10 versions=0 matched=0"), out


def test_numeric_version_order_not_string_order():
    home = tempfile.mkdtemp()
    (Path(home) / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 1.0.10\n\n- hook ten\n\n## 1.0.9\n\n- hook nine\n\n## 1.0.2\n\n- hook two\n",
        encoding="utf-8")
    rc, out, _ = _run("--since", "1.0.2", "--until", "1.0.10", home=home, version="1.0.10")
    assert "versions=2" in out.splitlines()[0], out
    assert "hook ten" in out and "hook nine" in out and "hook two" not in out, out


def test_saved_copy_and_meta_written():
    rc, out, home = _run("--since", "1.2.8")
    rt = Path(home) / ".claude/runtime/claude_code_changelog"
    assert (rt / "CHANGELOG.md").read_text(encoding="utf-8") == FAKE
    meta = json.loads((rt / "CHANGELOG.md.json").read_text())
    assert {"url", "sha256", "size", "fetched_at"} <= set(meta), meta


def test_fetch_failure_uses_saved_copy():
    rc, out, home = _run("--since", "1.2.8")  # 1 回目で保存させる
    old = os.environ.get("HOME")
    os.environ["HOME"] = home
    ccc_out = io.StringIO()
    try:
        with contextlib.redirect_stdout(ccc_out):
            rc = ccc.main(["--url", "file:///nonexistent/CHANGELOG.md", "--since", "1.2.8", "--until", "1.2.10"])
    finally:
        os.environ["HOME"] = old
    text = ccc_out.getvalue()
    assert rc == 0, text
    assert "WARN fetch failed, using saved copy" in text, text
    assert "PostToolBatch" in text, text


def test_fetch_failure_without_saved_copy_is_unavailable():
    home = tempfile.mkdtemp()
    old = os.environ.get("HOME")
    os.environ["HOME"] = home
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = ccc.main(["--url", "file:///nonexistent/CHANGELOG.md", "--since", "1.2.8", "--until", "1.2.10"])
    finally:
        os.environ["HOME"] = old
    assert rc == 2, out.getvalue()
    assert out.getvalue().startswith("claude_code_changelog: UNAVAILABLE fetch failed"), out.getvalue()


def test_offline_reads_file_and_writes_nothing():
    home = tempfile.mkdtemp()
    src = Path(home) / "x.md"
    src.write_text(FAKE, encoding="utf-8")
    old = os.environ.get("HOME")
    os.environ["HOME"] = home
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = ccc.main(["--offline", str(src), "--since", "1.2.8", "--until", "1.2.10"])
    finally:
        os.environ["HOME"] = old
    assert rc == 0 and "versions=2" in out.getvalue(), out.getvalue()
    assert not (Path(home) / ".claude").exists()


def test_bad_version_is_unavailable():
    rc, out, _ = _run("--since", "latest")
    assert rc == 2 and "UNAVAILABLE bad version" in out, out


def test_json_output():
    rc, out, _ = _run("--since", "1.2.8", "--keywords", "hook", "--json")
    data = json.loads(out.split("\n", 1)[1])
    assert data["range"] == ["1.2.8", "1.2.10"], data
    assert [v["version"] for v in data["versions"]] == ["1.2.10", "1.2.9"], data
    assert data["versions"][0]["lines"] == ["Added `PostToolBatch` hook event"], data


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e!r}")
    print(f"{'FAILED' if fails else 'passed'}: {fails} failed")
    sys.exit(1 if fails else 0)
