"""PreToolUse の注意型 hook の注意が、Claude に届く形（stdout の additionalContext か、exit 2 の拒否）で出るか。
exit 0 で stderr に書くだけでは Claude に届かない（2026-10-19 12:46:42Z: timeout の検査は発火したが結果に注意が無かった）。"""
import json, subprocess, sys
from pathlib import Path
H = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == "tests" else Path.cwd() / ".claude" / "hooks"
cases = [
 ("block_timeout_binary_on_macos", {"tool_name":"Bash","tool_input":{"command":"cd /x && timeout 90 ls"}}, "deny"),
 ("block_unquoted_glob_in_grep", {"tool_name":"Bash","tool_input":{"command":"grep -rn foo --include=*.py ."}}, "ctx"),
 ("block_redirect_inside_for_in", {"tool_name":"Bash","tool_input":{"command":"for f in a b 2>/dev/null; do echo $f; done"}}, "ctx"),
 ("block_sed_zero_addr_range", {"tool_name":"Bash","tool_input":{"command":"sed '0,/re/s/a/b/' f.txt"}}, "ctx"),
 ("block_inline_workflow_ts_syntax", {"tool_name":"Workflow","tool_input":{"script":"export const meta = {name:'x', description:'y'}\nconst a: string[] = []\n"}}, "ctx"),
]
bad = 0
for name, payload, want in cases:
    r = subprocess.run([sys.executable, str(H / f"{name}.py")], input=json.dumps(payload), capture_output=True, text=True)
    fired = bool(r.stderr.strip()) or r.returncode == 2 or r.stdout.strip()
    got = "none"
    if r.returncode == 2: got = "deny"
    elif r.stdout.strip():
        try:
            if json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"].strip(): got = "ctx"
        except Exception: got = "bad-stdout"
    ok = got == want and fired
    bad += not ok
    print("ok  " if ok else "FAIL", name, "want", want, "got", got, "(発火)" if fired else "(未発火)")
sys.exit(1 if bad else 0)
