#!/usr/bin/env python3
"""commit_status_ok — commit / tracking の状態に触れる正当な理由を、応答に合図を書かずに記録する。

  python3 heaven/tools/commit_status_ok.py "<根拠>"

.claude/hooks/block_unverified_commit_status.py は、同じターンにこのコマンドが打たれていれば通す。
根拠は .local/state/commit_status_ok.jsonl に残る（監査用）。応答の本文に合図を書かせると、
行頭の # が画面で見出しになる・user に要らない文字が出るので、記録の側へ移した。
"""
import json, os, sys, time
if len(sys.argv) != 2 or not sys.argv[1].strip():
    sys.exit(__doc__)
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".local", "state", "commit_status_ok.jsonl")
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, "a", encoding="utf-8") as f:
    f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "reason": sys.argv[1]}, ensure_ascii=False) + "\n")
print("記録した:", sys.argv[1])
