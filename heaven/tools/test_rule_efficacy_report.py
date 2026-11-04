#!/usr/bin/env python3
"""rule_efficacy_report.py のテスト（偽の hook_fires・transcript・settings で率と判定を確かめる）。

実行:
    python3 heaven/tools/test_rule_efficacy_report.py
    python3 -m pytest heaven/tools/test_rule_efficacy_report.py -q
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("rule_efficacy_report", os.path.join(_HERE, "rule_efficacy_report.py"))
rer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rer)

WEEKS = list(range(10, 21))  # 2026-W10 .. W20


def _ts(week: int, i: int = 0) -> str:
    t = datetime.fromisocalendar(2026, week, 2).replace(tzinfo=timezone.utc) + timedelta(minutes=i)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _world(td: str) -> dict:
    """偽の repo（settings）・transcript・hook_fires を作り、main に渡す引数を返す。

    毎週 Bash 100 回・Read 100 回。
      rule_down: Bash の hook。W10-13 は週 10 発火、W17-20 は週 2 発火 → 100 → 20 /1000
      rule_flat: Stop の hook。毎週 4 発火、分母は全 tool 200 → 20 /1000 のまま
      rule_up:   Read の hook。W10-13 は週 1、W17-20 は週 5 → 10 → 50 /1000
      rule_new:  W19 に生まれた → 誕生後 4 週と直近 4 週が重なる
    """
    repo = os.path.join(td, "repo")
    os.makedirs(os.path.join(repo, ".claude"))
    os.makedirs(os.path.join(repo, ".local"))
    settings = {"hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/h_down.py"'},
                                          {"command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/h_new.py"'}]},
            {"matcher": "Read", "hooks": [{"command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/h_up.py"'}]},
        ],
    }}
    local = {"hooks": {"Stop": [{"hooks": [{"command": 'f="$CLAUDE_PROJECT_DIR/.claude/hooks/local/h_flat.py"; python3 "$f"'}]}]}}
    with open(os.path.join(repo, ".claude", "settings.json"), "w") as f:
        json.dump(settings, f)
    with open(os.path.join(repo, ".local", "settings.local.json"), "w") as f:
        json.dump(local, f)

    tdir = os.path.join(td, "transcripts")
    os.makedirs(tdir)
    n = 0
    with open(os.path.join(tdir, "a.jsonl"), "w") as f:
        for w in WEEKS:
            for name in ("Bash", "Read"):
                for i in range(100):
                    n += 1
                    f.write(json.dumps({"timestamp": _ts(w, i), "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": f"t{n}", "name": name, "input": {}}]}}) + "\n")

    fires = []

    def add(rule, hook, week, k):
        for i in range(k):
            fires.append({"ts": _ts(week, i), "rule_id": rule, "hook": hook, "outcome": "warn", "count": 3})

    for w in WEEKS:
        add("rule_flat", "h_flat", w, 4)
        if w <= 13:
            add("rule_down", "h_down", w, 10)
            add("rule_up", "h_up:reblock", w, 1)
        if w >= 17:
            add("rule_down", "h_down", w, 2)
            add("rule_up", "h_up", w, 5)
        if w >= 19:
            add("rule_new", "h_new", w, 3)
    fp = os.path.join(td, "hook_fires.jsonl")
    with open(fp, "w") as f:
        for r in fires:
            f.write(json.dumps(r) + "\n")
        f.write("not json\n")

    mem = os.path.join(td, "memory")
    os.makedirs(mem)
    return {"argv": ["--fires", fp, "--memory-dir", mem, "--repo", repo, "--dir", tdir,
                     "--cache", os.path.join(td, "cache.json")], "mem": mem}


def _run(argv: list[str]) -> list[dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert rer.main(argv + ["--json"]) == 0
    return {r["rule_id"]: r for r in json.loads(buf.getvalue())}


def test_rates_and_verdicts():
    with tempfile.TemporaryDirectory() as td:
        rows = _run(_world(td)["argv"] + ["--all"])
    d = rows["rule_down"]
    assert d["denominator"] == "Bash"
    assert d["early_weeks"] == ["2026-W10", "2026-W11", "2026-W12", "2026-W13"]
    assert d["recent_weeks"] == ["2026-W17", "2026-W18", "2026-W19", "2026-W20"]
    assert (d["early_fires"], d["early_den"], d["recent_fires"], d["recent_den"]) == (40, 400, 8, 400)
    assert abs(d["early_rate"] - 100.0) < 1e-9 and abs(d["recent_rate"] - 20.0) < 1e-9
    assert d["verdict"] == "下がった"

    f = rows["rule_flat"]
    assert f["denominator"] == "全 tool"
    assert abs(f["early_rate"] - 20.0) < 1e-9 and abs(f["recent_rate"] - 20.0) < 1e-9
    assert f["verdict"] == "横ばい"

    u = rows["rule_up"]
    assert u["denominator"] == "Read"          # ':reblock' の枝の印を外して配線を引く
    assert u["hooks"] == ["h_up", "h_up:reblock"]
    assert abs(u["early_rate"] - 10.0) < 1e-9 and abs(u["recent_rate"] - 50.0) < 1e-9
    assert u["verdict"] == "上がった"

    assert rows["rule_new"]["verdict"] == "判定不能"


def test_new_rule_overlaps():
    with tempfile.TemporaryDirectory() as td:
        rows = _run(_world(td)["argv"] + ["--rule", "rule_new"])
    n = rows["rule_new"]
    assert n["birth"] == datetime.fromisocalendar(2026, 19, 2).date().isoformat()
    assert n["early_weeks"] == ["2026-W19", "2026-W20"]
    assert n["verdict"] == "判定不能" and "重なる" in n["reason"]


def test_memory_birth_earlier_than_first_fire():
    with tempfile.TemporaryDirectory() as td:
        w = _world(td)
        p = os.path.join(w["mem"], "rule_new.md")
        open(p, "w").close()
        t = datetime.fromisocalendar(2026, 10, 2).replace(tzinfo=timezone.utc).timestamp()
        os.utime(p, (t, t))
        born, _ = rer.memory_birth("rule_new", w["mem"])
        rows = _run(w["argv"] + ["--rule", "rule_new"])
    # 作成時刻を持つ OS でも、それより前の mtime を付けると作成時刻が引き下がる（APFS）。
    # どちらの時刻が返っても、誕生日は memory の日付と初発火（W19）の早い方。
    first_fire = datetime.fromisocalendar(2026, 19, 2).replace(tzinfo=timezone.utc)
    n = rows["rule_new"]
    assert n["birth"] == min(born, first_fire).date().isoformat()
    if born < first_fire:
        assert n["birth"] == "2026-03-03" and n["early_weeks"][0] == "2026-W10"
        assert n["verdict"] == "上がった"      # 誕生後 4 週は 0 発火、直近は発火あり


def test_verdict_boundaries():
    v = rer.verdict
    assert v(100, 1000, 75, 1000, False, 0.25) == "横ばい"     # ちょうど 0.75
    assert v(100, 1000, 74, 1000, False, 0.25) == "下がった"
    assert v(100, 1000, 125, 1000, False, 0.25) == "横ばい"    # ちょうど 1.25
    assert v(100, 1000, 126, 1000, False, 0.25) == "上がった"
    assert v(0, 1000, 0, 1000, False, 0.25) == "横ばい"
    assert v(0, 1000, 1, 1000, False, 0.25) == "上がった"
    assert v(10, 0, 1, 1000, False, 0.25) == "判定不能"
    assert v(10, 100, 1, 100, True, 0.25) == "判定不能"


def test_denominator_key():
    m = {"a": {"Bash"}, "b": {"Read", "Edit|Write"}, "s": {rer.ALL}}
    assert rer.denominator_key({"a"}, m) == frozenset({"Bash"})
    assert rer.denominator_key({"a", "b:x"}, m) == frozenset({"Bash", "Read", "Edit|Write"})
    assert rer.denominator_key({"a", "s"}, m) == frozenset({rer.ALL})
    assert rer.denominator_key({"unknown"}, m) == frozenset({rer.ALL})
    weekly = {"W1": {"Bash": 3, "Edit": 2, "Write": 1, "Read": 4, "Grep": 7}}
    assert rer.weekly_denominator(weekly, frozenset({"Edit|Write"})) == {"W1": 3}
    assert rer.weekly_denominator(weekly, frozenset({rer.ALL})) == {"W1": 17}


def test_not_internalized_matches_adoption_rule():
    now = datetime(2026, 5, 20, tzinfo=timezone.utc)
    ev = lambda days, outcome="warn": [(now - timedelta(days=d), outcome) for d in days]
    per = {
        "recurring": {"events": ev([1, 2, 3])},             # 3 日・直近 1 日 → not-internalized
        "quiet": {"events": ev([20, 21, 22])},               # 直近 20 日 → 対象外
        "audit": {"events": ev([1, 2, 3], "audit")},         # audit のみ → 対象外
        "old": {"events": ev([40, 41, 42])},                 # 30 日の窓の外
    }
    assert rer.not_internalized(per, now) == ["recurring"]


def test_table_output():
    with tempfile.TemporaryDirectory() as td:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert rer.main(_world(td)["argv"] + ["--all", "--series"]) == 0
    out = buf.getvalue()
    assert "RULE_EFFICACY_SUMMARY rules=4 up=1 flat=1 down=1 undetermined=1" in out
    assert "-- rule_down" in out and "2026-W10" in out


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e!r}")
    print("all passed" if not fails else f"{fails} failed")
    sys.exit(1 if fails else 0)
