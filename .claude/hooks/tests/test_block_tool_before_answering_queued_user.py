"""block_tool_before_answering_queued_user.py のテスト（2026-10-21 sid 88fca185 の失敗の並びを再現）。

  python3 .claude/hooks/tests/test_block_tool_before_answering_queued_user.py
"""
import json, os, subprocess, sys, tempfile, uuid
from pathlib import Path

HOOK = Path(os.environ.get("HOOK_UNDER_TEST") or Path(__file__).resolve().parents[1] / "block_tool_before_answering_queued_user.py")


def line(**d):
    return json.dumps(d, ensure_ascii=False)


def user_prompt(t):
    return line(type="user", message={"role": "user", "content": t})


def asst(*blocks):
    return line(type="assistant", message={"role": "assistant", "id": str(uuid.uuid4()), "content": list(blocks)})


def text(t):
    return {"type": "text", "text": t}


def think(t):
    return {"type": "thinking", "thinking": t}


# 実記録（sid dcd953b3 行 89 / 63）の署名の先頭。種類の印（narration / thinking）はこの範囲に入っている
NARRATION_SIG = "CAQSgwcKEQgTGAI4AUIJbmFycmF0aW9uEgwwnGbyFn3VAPHwJOkaDFIGFq+G"
THINKING_SIG = "CAQSmQYKEAgTGAI4AUIIdGhpbmtpbmcSDEvtruGcAI7tqc7WFxoMcdwwc8Pm"
REAL = Path.home() / ".claude/projects" / __import__("re").sub(r"[^A-Za-z0-9]", "-", __import__("os").environ.get("CLAUDE_PROJECT_DIR") or __import__("os").getcwd()) / "dcd953b3-13a9-4946-af7f-2b399a48c861.jsonl"


def narration(t):
    return {"type": "thinking", "thinking": t, "signature": NARRATION_SIG}


def real_think(t):
    return {"type": "thinking", "thinking": t, "signature": THINKING_SIG}


def tool():
    return {"type": "tool_use", "id": str(uuid.uuid4()), "name": "Bash", "input": {}}


def result():
    return line(type="user", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]})


def queued(t):
    return line(type="attachment", attachment={"type": "queued_command", "prompt": [{"type": "text", "text": t}], "origin": {"kind": "human"}})


class T:
    def __init__(self):
        self.d = tempfile.mkdtemp(); self.p = Path(self.d) / "t.jsonl"; self.p.write_text(""); self.sid = "test-" + uuid.uuid4().hex

    def add(self, *ls):
        with self.p.open("a") as f:
            for l in ls: f.write(l + "\n")

    def run(self, event="PreToolUse", **extra):
        payload = {"hook_event_name": event, "transcript_path": str(self.p), "session_id": self.sid, **extra}
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True,
                           env={**os.environ, "CLAUDE_PROJECT_DIR": str(Path(__file__).resolve().parents[3])})
        return r.returncode, r.stderr


def check(name, got, want):
    ok = got == want
    print(("OK  " if ok else "NG  ") + name, got if not ok else "")
    return ok


def main():
    res = []
    # 1. 答えを thinking にだけ書いて呼び直す（88fca185 行 2457→2458→2460 の型）→ 呼び直しの次で止め直す
    t = T(); t.add(user_prompt("依頼"), asst(text("始めます"), tool()), result(), queued("今すぐ全キャラやれ"))
    res.append(check("1a 届いた直後は止める", t.run()[0], 2))
    t.add(asst(think("手落ちでした。全キャラ保存します"), tool()), result())
    res.append(check("1b 呼び直しの 1 回は通す", t.run()[0], 0))
    t.add(asst(think("続けます"), tool()), result())
    code, err = t.run()
    res.append(check("1c thinking だけなら止め直す", code, 2))
    res.append(check("1d 止め直しの文に『画面に出る文が無い』", "画面に出る文" in err, True))
    res.append(check("1e 誤った診断『thinking の中にあり』を出さない", "thinking の中にあり" in err, False))
    # 2. text で答えて呼び直す（4c932094 行 4670 の型）→ 次で通り、以後も通る
    t = T(); t.add(user_prompt("依頼"), asst(text("始めます"), tool()), result(), queued("どっちだよ？"))
    res.append(check("2a 止める", t.run()[0], 2))
    t.add(asst(text("間違っていました。"), tool()), result())
    res.append(check("2b 呼び直しは通す", t.run()[0], 0))
    res.append(check("2c text で答えた後は通り続ける", t.run()[0], 0))
    # 3. Stop: 未回答の割り込みが残っていれば 1 回止める・stop_hook_active なら通す
    t = T(); t.add(user_prompt("依頼"), asst(text("始めます"), tool()), result(), queued("インシデント"), asst(think("考え中")))
    res.append(check("3a Stop で未回答なら止める", t.run("Stop")[0], 2))
    res.append(check("3b stop_hook_active なら通す", t.run("Stop", stop_hook_active=True)[0], 0))
    # 4. 既存ガードの正当な停止文（text）→ Stop は通す（guard-conflict）
    t = T(); t.add(user_prompt("依頼"), asst(text("始めます"), tool()), result(), queued("なんでだよ"), asst(text("止まります。指示ください")))
    res.append(check("4 正当な停止文は止めない", t.run("Stop")[0], 0))
    # 5. 短い了承（ok / lok）は答えを要さない
    t = T(); t.add(user_prompt("依頼"), asst(text("始めます"), tool()), result(), queued("lok"))
    res.append(check("5 ok/lok では止めない", t.run()[0], 0))
    # 6. 止め直しは上限 2 回まで（その先は Stop に任せる）
    t = T(); t.add(user_prompt("依頼"), asst(text("始めます"), tool()), result(), queued("全部やれよ"))
    codes = []
    for _ in range(8):
        codes.append(t.run()[0]); t.add(asst(think("..."), tool()), result())
    res.append(check("6 止めるのは最初＋止め直し 2 回の計 3 回まで", codes.count(2), 3))
    # 7. ツール呼び出し前の状況報告（narration。VSCode の画面に表示される）で答えた → 答えとして数える
    t = T(); t.add(user_prompt("依頼"), asst(text("始めます"), tool()), result(), queued("サブスク枠でやって"))
    res.append(check("7a 止める", t.run()[0], 2))
    t.add(asst(narration("サブスク枠で進めます。"), tool()), result())
    res.append(check("7b narration で答えた後の呼び直しは通す", t.run()[0], 0))
    res.append(check("7c 以後も通り続ける", t.run()[0], 0))
    res.append(check("7d Stop も止めない", t.run("Stop")[0], 0))
    # 8. 本物の推論（署名が thinking）は表示されないので、答えに数えない
    t = T(); t.add(user_prompt("依頼"), asst(text("始めます"), tool()), result(), queued("サブスク枠でやって"))
    t.run(); t.add(asst(real_think("答えを考える"), tool()), result()); t.run()
    t.add(asst(real_think("まだ考える"), tool()), result())
    res.append(check("8 推論だけなら止め直す", t.run()[0], 2))
    # 9. 実記録の再生: sid dcd953b3 で queued 発話（行 66）に行 89 の narration で答えた時点
    if REAL.exists():
        t = T(); t.add(*REAL.read_text(encoding="utf-8").splitlines()[:90])
        res.append(check("9 実記録: narration の答えの後は通す", t.run()[0], 0))
    print(f"{sum(res)}/{len(res)} OK")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
