#!/usr/bin/env python3
"""block_tool_before_answering_queued_user の回帰試験（2026-10-23）: 作業中の発話のあと、同じ応答のツールの前の文（narration）が少し遅れて記録に書き出される状況を再現する。"""
import base64, json, os, subprocess, sys, tempfile, threading, time
from pathlib import Path
HOOK = str(Path(__file__).resolve().parent.parent / "block_tool_before_answering_queued_user.py")
def run(delay, append_text):
    d = Path(tempfile.mkdtemp()); tp = d / "t.jsonl"
    rows = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "前の返答"}]}},
        {"type": "attachment", "attachment": {"type": "queued_command", "origin": {"kind": "human"}, "prompt": "公開済みの 3 本も直して"}},
    ]
    tp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    narr = {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "受け取りました。3 本も直します。",
            "signature": base64.b64encode(b"xx narration yy").decode()}]}}
    def later():
        time.sleep(delay)
        if append_text:
            with open(tp, "a", encoding="utf-8") as f: f.write(json.dumps(narr, ensure_ascii=False) + "\n")
    th = threading.Thread(target=later); th.start()
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(d))
    p = subprocess.run([sys.executable, HOOK], input=json.dumps({"transcript_path": str(tp), "session_id": f"lag-{time.time()}", "hook_event_name": "PreToolUse"}),
                       capture_output=True, text=True, env=env)
    th.join(); return p.returncode
ok = True
for label, delay, txt, want in [("文が 0.3 秒遅れて書き出される（答えている）", 0.3, True, 0),
                                ("文が最初から書き出し済み（答えている）", 0.0, True, 0),
                                ("文が無い（答えていない）", 0.3, False, 2)]:
    rc = run(delay, txt)
    res = "OK" if rc == want else "NG"
    ok &= rc == want
    print(f"{res} {label}: exit={rc}（期待 {want}）")
sys.exit(0 if ok else 1)
