"""block_write_after_foreign_change: 別のセッションが変えた後のファイルを、読み直さずに書き換える呼び出しを止める。

RED の元の場面: fork したセッションは元の会話の読み書きの記録を引き継ぐ。元の会話が分岐後に
src/main.cpp を書き直していたのに、fork 側は harness の「未読」検査に引っかからず、
古い理解のまま Write で上書きした。ファイルの更新日時が、この会話で最後にそのファイルに
触れた時刻より新しいことで判定できる。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "block_write_after_foreign_change.py"
fails = []


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def transcript(entries):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write(json.dumps({"type": "user", "timestamp": iso(time.time() - 9000),
                        "message": {"content": "進めて"}}) + "\n")
    for i, (t, name, inp) in enumerate(entries):
        tid = f"toolu_{i}"
        f.write(json.dumps({"type": "assistant", "timestamp": iso(t), "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}) + "\n")
        f.write(json.dumps({"type": "user", "timestamp": iso(t + 0.5), "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}) + "\n")
    f.close()
    return f.name


HOME = tempfile.mkdtemp()  # 通した時刻の記録（~/.claude/projects/…）を本物の home に書かない


def run(tool, inp, entries, sid=None, tp=None):
    # 名前を渡さない件は毎回別のセッション（前の件で通した記録を持ち越さない）
    payload = {"tool_name": tool, "tool_input": inp, "transcript_path": tp or transcript(entries),
               "session_id": sid or f"test-{time.time_ns()}"}
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=20, env={**os.environ, "HOME": HOME})
    return r.returncode, r.stderr


def check(name, ok, detail=""):
    if not ok:
        fails.append(f"{name}: {detail}")


d = Path(tempfile.mkdtemp())
target = d / "main.cpp"
target.write_text("v2 by the other session\n")
now = time.time()

# 1. 元の場面: 最後に触れたのが 1 時間前、ファイルは今変わった → 止める
os.utime(target, (now, now))
rc, err = run("Write", {"file_path": str(target), "content": "mine"},
              [(now - 3600, "Write", {"file_path": str(target), "content": "v1"})])
check("foreign-change-blocks", rc == 2 and "別のセッション" in err, f"rc={rc} err={err[:200]}")

# 2. Edit も同じ
rc, err = run("Edit", {"file_path": str(target), "old_string": "a", "new_string": "b"},
              [(now - 3600, "Read", {"file_path": str(target)})])
check("edit-foreign-change-blocks", rc == 2, f"rc={rc}")

# 3. 読み直した後なら通す
rc, err = run("Write", {"file_path": str(target), "content": "mine"},
              [(now - 3600, "Write", {"file_path": str(target), "content": "v1"}),
               (now + 1, "Read", {"file_path": str(target)})])
check("reread-passes", rc == 0, f"rc={rc} err={err[:200]}")

# 4. 自分が Bash で書いた直後（更新日時が tool_use より少し後）は通す
os.utime(target, (now + 0.3, now + 0.3))
rc, err = run("Edit", {"file_path": str(target), "old_string": "a", "new_string": "b"},
              [(now, "Bash", {"command": f"echo x >| {target}"})])
check("own-bash-write-passes", rc == 0, f"rc={rc} err={err[:200]}")

# 5. 元の場面そのもの: パスが記録に一度も出ない（相対パス・変数の Bash でしか触れていない）が、
#    会話の開始後に更新されている → 止める
os.utime(target, (now, now))
rc, err = run("Write", {"file_path": str(target), "content": "x"},
              [(now - 3600, "Bash", {"command": "cd tmp && cat >| src/main.cpp"})])
check("untouched-changed-blocks", rc == 2, f"rc={rc}")

# 5b. 一度も触れておらず、会話の開始より前から変わっていないファイルは通す
os.utime(target, (now - 20000, now - 20000))
rc, err = run("Write", {"file_path": str(target), "content": "x"}, [])
check("untouched-old-passes", rc == 0, f"rc={rc} err={err[:200]}")

# 6. 新規ファイルは通す
rc, err = run("Write", {"file_path": str(d / "new.txt"), "content": "x"},
              [(now - 3600, "Read", {"file_path": str(d / "new.txt")})])
check("new-file-passes", rc == 0, f"rc={rc}")

# 8. 同じファイルへの Edit を 1 つの返答に並べた: 1 件目の実行時点では、記録に並べた Edit がまだ
#    書かれておらず最後に触れたのは 35 秒前の Read。1 件目が更新した直後の 2 件目は自分の更新なので通す
#    （実際に 2〜4 件目が止まった）
tp_par = transcript([(now - 35, "Read", {"file_path": str(target)})])
os.utime(target, (now - 35, now - 35))
rc1, _ = run("Edit", {"file_path": str(target), "old_string": "a", "new_string": "b"}, None, sid="par", tp=tp_par)
t1 = time.time() + 0.2
os.utime(target, (t1, t1))  # 1 件目の Edit が書いた
rc2, err = run("Edit", {"file_path": str(target), "old_string": "c", "new_string": "d"}, None, sid="par", tp=tp_par)
check("parallel-own-edits-pass", rc1 == 0 and rc2 == 0, f"rc1={rc1} rc2={rc2} err={err[:200]}")

# 9. 自分が通した後でも、別のセッションがそれより後に書き換えたら止める
t2 = time.time() + 60
os.utime(target, (t2, t2))
rc, err = run("Edit", {"file_path": str(target), "old_string": "e", "new_string": "f"}, None, sid="par", tp=tp_par)
check("foreign-after-own-pass-blocks", rc == 2, f"rc={rc}")

# 10. 別のセッションが通した記録は、このセッションの「触れた」に数えない
rc, err = run("Edit", {"file_path": str(target), "old_string": "e", "new_string": "f"}, None, sid="other", tp=tp_par)
check("other-session-pass-not-counted", rc == 2, f"rc={rc}")

# 7. 書き込み以外の道具は通す
rc, err = run("Read", {"file_path": str(target)}, [(now - 3600, "Read", {"file_path": str(target)})])
check("read-tool-passes", rc == 0, f"rc={rc}")

if fails:
    print("FAIL\n" + "\n".join(fails))
    sys.exit(1)
print("ok")
