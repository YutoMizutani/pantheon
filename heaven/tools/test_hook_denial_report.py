#!/usr/bin/env python3
"""hook_denial_report.py のテスト（合成した transcript で、束ね方と空回りの数え方を確かめる）。

実行:
    python3 heaven/tools/test_hook_denial_report.py
    python3 -m pytest heaven/tools/test_hook_denial_report.py -q
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("hook_denial_report", os.path.join(_HERE, "hook_denial_report.py"))
hdr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hdr)

NEW = 'PreToolUse:Bash hook error: [f="$CLAUDE_PROJECT_DIR/.claude/hooks/block_a.py"; [ -f "$f" ] || exit 0; python3 "$f"]: A で止めました'
OLD = "PreToolUse:Edit hook error: [python3 <repo>/.claude/hooks/block_b.py]: B で止めました"
BARE = "PreToolUse:Bash hook error: 名前の無い拒否です。理由はこう"


def _use(i, name, inp, ts="2026-11-01T00:00:00Z"):
    return {"timestamp": ts, "message": {"content": [{"type": "tool_use", "id": f"u{i}", "name": name, "input": inp}]}}


def _res(i, text, ts="2026-11-01T00:00:01Z"):
    return {"timestamp": ts, "message": {"content": [{"type": "tool_result", "tool_use_id": f"u{i}", "content": text}]}}


def _write(td, name, entries):
    p = os.path.join(td, name)
    with open(p, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return p


def _world(td):
    """会話 s1: A が同じ入力を 3 回止める（空回り 2、最大連続 3）＋ 別の入力を 1 回止めて書き直しで通る。
    B は 1 回止めて、同じ入力の呼び直しで通る（正しい使い方 — 空回りに数えない）。
    会話 s2: 名前の無い拒否 1 回、A を 2 回同じ入力で（空回り 1）。期間外の古い拒否 1 回。"""
    x = {"command": "ls x"}
    s1 = [
        _use(1, "Bash", x), _res(1, NEW),
        _use(2, "Bash", x), _res(2, NEW),
        _use(3, "Bash", x), _res(3, NEW),
        _use(4, "Bash", {"command": "ls y"}), _res(4, NEW),
        _use(5, "Bash", {"command": "ls y2"}), _res(5, "ok"),
        _use(6, "Edit", {"file_path": "f"}), _res(6, OLD),
        _use(7, "Edit", {"file_path": "f"}), _res(7, "ok"),
    ]
    s2 = [
        _use(1, "Bash", {"command": "old"}, ts="2026-09-01T00:00:00Z"), _res(1, NEW, ts="2026-09-01T00:00:01Z"),
        _use(2, "Bash", {"command": "z"}), _res(2, BARE),
        _use(3, "Bash", x), _res(3, [{"type": "text", "text": NEW}]),
        _use(4, "Bash", x), _res(4, NEW),
    ]
    return [_write(td, "s1.jsonl", s1), _write(td, "s2.jsonl", s2)]


def test_sig_reads_both_registration_forms_and_bare():
    assert hdr.sig(NEW) == "block_a.py"
    assert hdr.sig(OLD) == "block_b.py"
    assert hdr.sig(BARE) == "名前の無い拒否です"


def test_spin_counts_only_repeated_identical_denials():
    with tempfile.TemporaryDirectory() as td:
        table = {r["hook"]: r for r in hdr.rows(hdr.scan(_world(td), "2026-10-20"))}
    a = table["block_a.py"]
    assert a["denials"] == 6
    assert a["spins"] == 3  # s1 で 2、s2 で 1（会話をまたいでは数えない）
    assert a["max_run"] == 3
    assert (a["sessions"], a["spin_sessions"]) == (2, 2)
    b = table["block_b.py"]
    assert (b["denials"], b["spins"], b["max_run"]) == (1, 0, 1)
    assert table["名前の無い拒否です"]["denials"] == 1


def test_since_drops_old_rows():
    with tempfile.TemporaryDirectory() as td:
        files = _world(td)
        assert hdr.rows(hdr.scan(files, "2026-10-20"))[0]["first"] == "2026-11-01"
        assert {r["hook"]: r for r in hdr.rows(hdr.scan(files, ""))}["block_a.py"]["denials"] == 7


def test_main_renders_table_and_json():
    with tempfile.TemporaryDirectory() as td:
        _world(td)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert hdr.main(["--root", td, "--since", "2026-10-20"]) == 0
        text = out.getvalue()
        assert "拒否の合計 8 件" in text
        assert "| `block_a.py` | 見つからない | 6 | 3（50%） | 2 / 2 | 3 |" in text  # 既定の repo に block_a.py は無い
        assert "A で止めました" in text and "hook error" not in text
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert hdr.main(["--root", td, "--since", "2026-10-20", "--json", "--top", "1"]) == 0
        data = json.loads(out.getvalue())
        assert data["total"] == 8 and len(data["rows"]) == 1


def _repo(td, hooks: dict, local: dict | None = None):
    for sub, files in ((".claude/hooks", hooks), (".local/hooks", local or {})):
        os.makedirs(os.path.join(td, sub), exist_ok=True)
        for name, body in files.items():
            with open(os.path.join(td, sub, name), "w", encoding="utf-8") as fh:
                fh.write(body)
    return td


def test_owner_names_present_hook_and_flags_removed_one():
    with tempfile.TemporaryDirectory() as td:
        hooks = hdr.load_hooks(_repo(td, {"block_a.py": "x"}, {"lock.py": 'msg = f"言語ロック: この報告が英語で書かれています（{n} 本）"'}))
    assert hdr.owner("block_a.py", "", hooks) == "block_a.py"
    assert hdr.owner("block_gone.py", "", hooks) == hdr.MISSING
    # 名前の無い束は、例文の固定の文言で持ち主を引く（埋め込まれた値の部分は違ってよい）
    assert hdr.owner("言語ロック", "言語ロック: この報告が英語で書かれています（3 本）", hooks) == "lock.py"
    # 先頭の句だけ同じで本文が今の hook に無い行（撤去済みの hook の文面）は持ち主にしない
    assert hdr.owner("言語ロック", "言語ロック: このターンで一言を英語で書いた。その一言を日本語で書き直して出してから呼ぶ", hooks) == hdr.MISSING


def test_main_reports_missing_root():
    with tempfile.TemporaryDirectory() as td:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert hdr.main(["--root", os.path.join(td, "none")]) == 1
        assert "見つかりません" in err.getvalue()


if __name__ == "__main__":
    fails = 0
    for k, v in list(globals().items()):
        if k.startswith("test_") and callable(v):
            try:
                v()
                print("ok  ", k)
            except AssertionError as e:
                fails += 1
                print("FAIL", k, e)
    sys.exit(1 if fails else 0)
