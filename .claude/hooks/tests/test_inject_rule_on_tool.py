#!/usr/bin/env python3
"""Contract test for inject_rule_on_tool.py (PreToolUse で、その道具に関わる規則を 1 回だけ渡す hook).

仮の repo を tmp に作り、CLAUDE_PROJECT_DIR をそこへ向けて hook を走らせる。Run:
  python3 .claude/hooks/tests/test_inject_rule_on_tool.py
"""
import _hermetic  # noqa: F401 — hermetic telemetry
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "inject_rule_on_tool.py"
FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def run(root: Path, tool: str, tool_input: dict, sid: str, agent_id: str = "", raw: str | None = None):
    payload = {"tool_name": tool, "tool_input": tool_input, "session_id": sid}
    if agent_id:
        payload["agent_id"] = agent_id
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    p = subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(payload),
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stdout, p.stderr


def put(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        put(root / ".claude/rules-ondemand/wf.md", "WF-RULE-TEXT")
        put(root / ".claude/rules-ondemand/git.md", "GIT-RULE-TEXT")
        put(root / ".claude/rules-ondemand/skill.md", "SKILL-RULE-TEXT")
        put(root / ".claude/rules-ondemand/triggers.json", json.dumps([
            {"rule": "wf.md", "tools": ["Workflow"], "mode": "block"},
            {"rule": "wf.md", "tools": ["Write", "Edit"], "match": {"file_path": r"\.claude/workflows/.*\.js$"}, "mode": "block"},
            {"rule": "git.md", "tools": ["Bash"], "match": {"command": r"\bgit\s+(commit|push)\b"}, "mode": "block"},
            {"rule": "skill.md", "tools": ["Skill"], "mode": "context"},
        ]))
        put(root / ".local/rules-ondemand/local.md", "LOCAL-RULE-TEXT")
        put(root / ".local/rules-ondemand/triggers.json", json.dumps([
            {"rule": "local.md", "tools": ["Agent"], "mode": "block"},
        ]))

        sid = str(uuid.uuid4())
        rc, out, err = run(root, "Workflow", {"script": "x"}, sid)
        check("初回の Workflow は止めて規則の全文を渡す", rc == 2 and "WF-RULE-TEXT" in err)
        rc, out, err = run(root, "Workflow", {"script": "x"}, sid)
        check("同じセッションの 2 回目は通す", rc == 0 and "WF-RULE-TEXT" not in err + out)
        rc, out, err = run(root, "Write", {"file_path": str(root / ".claude/workflows/a.js")}, sid)
        check("同じ規則は別の引き金でも 2 回渡さない", rc == 0 and not err)

        rc, out, err = run(root, "Bash", {"command": "ls -la"}, sid)
        check("関係ない Bash は通す", rc == 0 and not err and not out)
        rc, out, err = run(root, "Bash", {"command": "git add a && git commit -m x"}, sid)
        check("git commit は止めて git の規則を渡す", rc == 2 and "GIT-RULE-TEXT" in err)

        rc, out, err = run(root, "Skill", {"skill": "fix"}, sid)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""
        check("context の規則は止めずに文を差し込む", rc == 0 and "SKILL-RULE-TEXT" in ctx)

        rc, out, err = run(root, "Workflow", {"script": "x"}, sid, agent_id="sub1")
        check("サブエージェントには別に渡す", rc == 2 and "WF-RULE-TEXT" in err)

        rc, out, err = run(root, "Agent", {"prompt": "x"}, sid)
        check("ローカル層の引き金も読む", rc == 2 and "LOCAL-RULE-TEXT" in err)

        rc, out, err = run(root, "Workflow", {}, sid, raw="not json")
        check("壊れた入力では通す", rc == 0)

    with tempfile.TemporaryDirectory() as td:
        rc, out, err = run(Path(td), "Workflow", {"script": "x"}, str(uuid.uuid4()))
        check("引き金の一覧が無ければ何もしない", rc == 0 and not err and not out)

    # 本物の引き金の一覧: 当たるべき呼び出しと、当たってはいけない呼び出し（文字列の中の git 等）
    import re
    real = json.loads((HOOK.parent.parent / "rules-ondemand/triggers.json").read_text(encoding="utf-8"))

    def fires(rule, tool, tool_input):
        for t in real:
            if t["rule"] != rule or tool not in t["tools"]:
                continue
            m = t.get("match")
            if not m or any(isinstance(tool_input.get(k), str) and re.search(p, tool_input[k]) for k, p in m.items()):
                return True
        return False

    for cmd in ["git commit -m x", "cd a && git push", "git -C foo commit", "x; git checkout -b f",
                "if y; then git init; fi"]:
        check(f"git の規則が当たる: {cmd}", fires("git-workflow.md", "Bash", {"command": cmd}))
    for cmd in ["git status", "git log --oneline", "git diff", "git checkout main",
                "python3 - <<'EOF'\nfor c in [\"git commit -m x\"]: pass\nEOF", "grep -n 'git push' a.md"]:
        check(f"git の規則が当たらない: {cmd[:30]!r}", not fires("git-workflow.md", "Bash", {"command": cmd}))
    check("launchctl load で定期実行の規則", fires("scheduled-job-fallbacks.md", "Bash",
                                                  {"command": "launchctl load ~/Library/LaunchAgents/a.plist"}))
    check("launchctl list では当たらない", not fires("scheduled-job-fallbacks.md", "Bash", {"command": "launchctl list"}))
    check("ffmpeg で動画の規則", fires("video-game-state-extraction.md", "Bash", {"command": "ffmpeg -i a.mp4 x.png"}))
    check("mp4 の Read で動画の規則", fires("video-game-state-extraction.md", "Read", {"file_path": "/a/b.mp4"}))
    check("workflow の js を書くと I/O 契約", fires("workflow-io-contract.md", "Write",
                                                {"file_path": "/r/.claude/workflows/x.js"}))

    print("ALL PASS" if not FAILS else f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
