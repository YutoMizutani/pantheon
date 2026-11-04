#!/usr/bin/env python3
"""tool_use_weekly の回帰テスト（偽 jsonl 3 本・2 週にまたがる）。

実行:
    python3 -m pytest heaven/tools/test_tool_use_weekly.py -q
    python3 heaven/tools/test_tool_use_weekly.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("tool_use_weekly", os.path.join(_HERE, "tool_use_weekly.py"))
tuw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tuw)


def _assistant(ts, *tools):
    content = [{"type": "tool_use", "id": tid, "name": name, "input": {}} for tid, name in tools]
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": content}}


def _write(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def _fixture(d):
    # 2026-10-18（日）= 2026-W42、2026-10-19（月）= 2026-W43
    _write(os.path.join(d, "a.jsonl"), [
        _assistant("2026-10-18T23:59:00Z", ("t1", "Bash"), ("t2", "Read")),
        _assistant("2026-10-19T00:01:00Z", ("t3", "Bash")),
        _assistant("2026-10-19T00:01:00Z", ("t3", "Bash")),  # 同じ id の重複は 1 回
        {"type": "user", "timestamp": "2026-10-19T00:02:00Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": "tool_use"}]}},
    ])
    _write(os.path.join(d, "b.jsonl"), [
        _assistant("2026-10-20T10:00:00Z", ("u1", "Bash"), ("u2", "Edit")),
        "{not json tool_use",  # 壊れた行は飛ばす
    ])
    _write(os.path.join(d, "c.jsonl"), [
        _assistant("2026-10-17T10:00:00Z", ("v1", "Grep")),
        {"type": "summary", "summary": "no tools"},
    ])


def test_counts_across_two_weeks():
    with tempfile.TemporaryDirectory() as d:
        _fixture(d)
        weekly, stats = tuw.collect(d, os.path.join(d, "cache", "c.json"))
        assert weekly == {
            "2026-W42": {"Bash": 1, "Grep": 1, "Read": 1},
            "2026-W43": {"Bash": 2, "Edit": 1},
        }
        assert stats == {"files": 3, "reread": 3, "reused": 0}


def test_cache_reused_and_only_changed_file_reread():
    with tempfile.TemporaryDirectory() as d:
        _fixture(d)
        cache = os.path.join(d, "cache", "c.json")
        first, _ = tuw.collect(d, cache)
        second, stats = tuw.collect(d, cache)
        assert second == first
        assert stats == {"files": 3, "reread": 0, "reused": 3}

        # b.jsonl に 1 件足す → b だけ読み直す
        with open(os.path.join(d, "b.jsonl"), "a") as f:
            f.write(json.dumps(_assistant("2026-10-21T10:00:00Z", ("u3", "Bash"))) + "\n")
        third, stats = tuw.collect(d, cache)
        assert stats == {"files": 3, "reread": 1, "reused": 2}
        assert third["2026-W43"]["Bash"] == 3

        # c.jsonl を消す → その分が外れる
        os.remove(os.path.join(d, "c.jsonl"))
        fourth, stats = tuw.collect(d, cache)
        assert stats == {"files": 2, "reread": 0, "reused": 2}
        assert "Grep" not in fourth["2026-W42"]


def test_same_id_across_files_counted_once():
    # resume で前の履歴が別ファイルへ写された形
    with tempfile.TemporaryDirectory() as d:
        _fixture(d)
        _write(os.path.join(d, "d.jsonl"), [
            _assistant("2026-10-19T00:01:00Z", ("t3", "Bash")),  # a.jsonl と同じ id
            _assistant("2026-10-22T00:00:00Z", ("w1", "Bash")),
        ])
        weekly, _ = tuw.collect(d, os.path.join(d, "c.json"))
        assert weekly["2026-W43"]["Bash"] == 3  # t3・u1・w1


def test_iso_week_uses_utc():
    assert tuw.iso_week("2026-10-19T08:00:00+09:00") == "2026-W42"  # UTC では 10-18 23:00
    assert tuw.iso_week("2026-10-19T00:00:00Z") == "2026-W43"
    assert tuw.iso_week("") is None


def test_table_has_totals():
    out = tuw.render_table({"2026-W43": {"Bash": 2, "Edit": 1}}, weeks=8, top=1)
    lines = out.splitlines()
    assert lines[0].split() == ["週", "合計", "Bash", "その他"]
    assert lines[1].split() == ["2026-W43", "3", "2", "1"]


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e!r}")
    sys.exit(1 if fails else 0)
