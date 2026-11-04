#!/usr/bin/env python3
"""transcript_digest.py のテスト（人の発話を全部逐語で残し、自動の通知を人の発話と混ぜないか）。

実行:
    python3 heaven/tools/test_transcript_digest.py
    python3 -m pytest heaven/tools/test_transcript_digest.py -q
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("transcript_digest", os.path.join(_HERE, "transcript_digest.py"))
td = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(td)


def _user(text):
    return {"type": "user", "timestamp": "2026-10-27T10:00:00Z", "message": {"role": "user", "content": text}}


def _queued(text, kind):
    return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": [{"type": "text", "text": text}],
                                                 "origin": {"kind": kind}}}


def _tool(i, cmd, err=False):
    return [
        {"type": "assistant", "message": {"role": "assistant", "id": f"m{i}", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": cmd}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "x" * 50, "is_error": err}]}},
    ]


def _digest(records, **kw):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
        return td.digest(p, 0, **kw)


BASE = [_user("最初の依頼: 個人ファームのメモを作って"),
        *_tool(1, "ls"), *_tool(2, "ls"), *_tool(3, "ls"), *_tool(4, "cat a", err=True),
        _queued("でも、stop系hookが多いとトークンが増えるよね", "human"),
        _queued("<task-notification>\n<status>completed</status>\n</task-notification>", "task-notification"),
        {"type": "assistant", "message": {"role": "assistant", "id": "m9", "content": [
            {"type": "text", "text": "直しました。"}]}}]


def test_every_human_utterance_is_verbatim_including_queued():
    d = _digest(BASE)
    assert "最初の依頼: 個人ファームのメモを作って" in d
    assert "割り込み: でも、stop系hookが多いとトークンが増えるよね" in d


def test_task_notification_queued_is_not_a_human_utterance():
    d = _digest(BASE)
    assert "割り込み: <task-notification>" not in d
    assert "自動: <task-notification>" in d
    assert "user 発話 2 件" in d


def test_repeats_collapsed_and_errors_listed():
    d = _digest(BASE)
    assert "（同じ呼び出し ×3）" in d
    assert "エラーになった呼び出し" in d and "cat a" in d.split("エラーになった呼び出し")[1]


def test_compact_mode_keeps_user_verbatim_and_folds_tools():
    d = _digest(BASE, budget=10)
    assert "畳んだ形" in d
    assert "割り込み: でも、stop系hookが多いとトークンが増えるよね" in d
    assert "→ Bash" not in d.split("## ③")[0]
    assert "道具 Bash" in d and "エラー 1" in d


def test_large_transcript_is_not_held_in_memory():
    """1MB の行が 64 本の記録で、丸ごと持てば 64MB を超える。行そのものを持たないこと。"""
    import resource
    import subprocess
    import sys
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "big.jsonl"
        line = json.dumps(_user("あ" * (1024 * 1024)), ensure_ascii=False) + "\n"
        with open(p, "w", encoding="utf-8") as f:
            for _ in range(64):
                f.write(line)
        code = (
            "import resource, runpy, sys\n"
            f"sys.argv = ['transcript_digest.py', {str(p)!r}, '--start', '0']\n"
            "try:\n"
            f"    runpy.run_path({os.path.join(_HERE, 'transcript_digest.py')!r}, run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "sys.stderr.write(str(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))\n"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        rss = int(r.stderr.strip().splitlines()[-1])
        if sys.platform != "darwin":
            rss *= 1024
        assert rss < 150 * 2**20, f"peak {rss / 2**20:.0f}MB"


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f(); n += 1
    print(f"OK {n} tests")


def test_memory_reads_are_listed_for_adoption_log():
    recs = BASE + [
        {"type": "assistant", "message": {"role": "assistant", "id": "mr", "content": [
            {"type": "tool_use", "id": "tr", "name": "Read",
             "input": {"file_path": "/x/heaven/memory/feedback_sample_rule.md"}}]}},
        {"type": "assistant", "message": {"role": "assistant", "id": "mo", "content": [
            {"type": "tool_use", "id": "to", "name": "Read", "input": {"file_path": "/x/docs/other.md"}}]}},
    ]
    d = _digest(recs)
    assert "Read で開いた memory" in d
    assert "feedback_sample_rule [" in d
    assert "other [" not in d
