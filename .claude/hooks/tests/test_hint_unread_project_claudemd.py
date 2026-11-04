"""hint_unread_project_claudemd: 案件の CLAUDE.md が未読のまま、その配下へ書く呼び出しを止める。

RED の元の場面: Bash で入れ子の案件を見た時点で知らせは出たが、読まずに、その案件の CLAUDE.md が
書き込み禁止と定める場所へ Write と `sed -i` で書いた。知らせは 1 案件 1 回で、Write/Edit には
配線されていなかったため、書く瞬間には何も効かなかった。

hook は repo の projects/ を見るので、ignore される projects/ の下に入れ子の架空の案件を一時的に作り、
終わったら消す（どの環境でも走る）。
"""
import atexit
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hint_unread_project_claudemd.py"
REPO = Path(__file__).resolve().parents[3]
PARENT = REPO / "projects" / f"zz-hint-test-{uuid.uuid4().hex[:8]}"
CHILD = PARENT / "child"
TARGET = CHILD / "locked" / "src" / "main.cpp"
CLAUDEMDS = [PARENT / "CLAUDE.md", CHILD / "CLAUDE.md"]

TARGET.parent.mkdir(parents=True)
atexit.register(shutil.rmtree, PARENT, True)
for md in CLAUDEMDS:
    md.write_text("# 試験用の案件\n\nlocked/ には書かない。\n")


def transcript(read_paths):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write(json.dumps({"type": "user", "message": {"content": "作業を進めて"}}) + "\n")
    for p in read_paths:
        f.write(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(p)}}]}}) + "\n")
    f.close()
    return f.name


def run(tool, tool_input, read_paths):
    payload = {"session_id": "test-" + uuid.uuid4().hex, "tool_name": tool, "tool_input": tool_input,
               "transcript_path": transcript(read_paths)}
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


fails = []


def check(name, cond, detail=""):
    if not cond:
        fails.append(f"{name}: {detail}")


# 1. 未読のまま Write → 止める
rc, out, err = run("Write", {"file_path": str(TARGET), "content": "x"}, [])
check("write-unread-blocks", rc == 2 and "child/CLAUDE.md" in err, f"rc={rc} err={err[:200]}")

# 2. 未読のまま Edit → 止める
rc, out, err = run("Edit", {"file_path": str(TARGET), "old_string": "a", "new_string": "b"}, [])
check("edit-unread-blocks", rc == 2, f"rc={rc}")

# 3. 未読のまま書き込みを伴う Bash（sed -i）→ 止める
cmd = f"cd {TARGET.parent.parent.parent} && sed -i '' 's/a/b/' README.md"
rc, out, err = run("Bash", {"command": cmd}, [])
check("bash-write-unread-blocks", rc == 2, f"rc={rc}")

# 4. 両方読んだ後の Write → 通す
rc, out, err = run("Write", {"file_path": str(TARGET), "content": "x"}, CLAUDEMDS)
check("write-after-read-passes", rc == 0, f"rc={rc} err={err[:200]}")

# 5. 親だけ読んで子が未読 → 止める（入れ子の案件）
rc, out, err = run("Write", {"file_path": str(TARGET), "content": "x"}, CLAUDEMDS[:1])
check("nested-unread-blocks", rc == 2 and "child/CLAUDE.md" in err, f"rc={rc}")

# 6. 読むだけの Bash は止めない（知らせのみ）
rc, out, err = run("Bash", {"command": f"rg -n pattern {TARGET.parent}"}, [])
check("bash-read-only-hints", rc == 0 and "additionalContext" in out, f"rc={rc} out={out[:200]}")

# 6b. 読むだけで stderr を捨てる Bash（2>/dev/null）は書き込みと見なさない
rc, out, err = run("Bash", {"command": f"rg -n pattern {TARGET.parent} 2>/dev/null | head"}, [])
check("bash-devnull-not-write", rc == 0, f"rc={rc}")

# 6c. リダイレクトで案件配下に書く Bash は止める
rc, out, err = run("Bash", {"command": f"echo x > {TARGET}"}, [])
check("bash-redirect-blocks", rc == 2, f"rc={rc}")

# 7. 案件外の Write は素通り
rc, out, err = run("Write", {"file_path": "/tmp/x.txt", "content": "x"}, [])
check("outside-project-passes", rc == 0 and not out.strip(), f"rc={rc}")

# 8. 案件外への Write の本文に案件のパスが書かれているだけなら止めない
rc, out, err = run("Write", {"file_path": "/tmp/x.md", "content": f"見た: {TARGET}"}, [])
check("write-content-path-not-target", rc == 0, f"rc={rc} err={err[:200]}")

# 9〜14. heredoc の本文: スクリプトの文字列や文書に案件のパスが出てくるだけなら止めない。書き先なら止める
#        （一時フォルダの写しを書き換える python の本文で、試験の文章に出てくる projects/<X> を理由に止めていた）
py_doc = "cd /tmp && python3 - <<'EOF'\nopen('/tmp/t.py','w').write(\"body = '操作: rm -rf " + str(TARGET) + "'\")\nEOF"
rc, out, err = run("Bash", {"command": py_doc}, [])
check("py-heredoc-string-not-target", rc == 0, f"rc={rc} err={err[:200]}")

cat_doc = f"cat > /tmp/note.md <<'EOF'\n見た場所: {TARGET} の 3 行目\nEOF"
rc, out, err = run("Bash", {"command": cat_doc}, [])
check("cat-heredoc-prose-not-target", rc == 0, f"rc={rc} err={err[:200]}")

py_assign = f"python3 - <<'EOF'\np='{TARGET}'\nopen(p,'w').write('x')\nEOF"
rc, out, err = run("Bash", {"command": py_assign}, [])
check("py-heredoc-assigned-target-blocks", rc == 2, f"rc={rc}")

py_open = f"python3 - <<'EOF'\nopen('{TARGET}', 'w').write('x')\nEOF"
rc, out, err = run("Bash", {"command": py_open}, [])
check("py-heredoc-open-target-blocks", rc == 2, f"rc={rc}")

sh_cp = f"bash <<'EOF'\ncp /tmp/a {TARGET}\nEOF"
rc, out, err = run("Bash", {"command": sh_cp}, [])
check("bash-heredoc-cp-target-blocks", rc == 2, f"rc={rc}")

cat_target = f"cat > {TARGET} <<'EOF'\nx\nEOF"
rc, out, err = run("Bash", {"command": cat_target}, [])
check("cat-heredoc-to-target-blocks", rc == 2, f"rc={rc}")

if fails:
    print("FAIL\n" + "\n".join(fails))
    sys.exit(1)
print("ok")
