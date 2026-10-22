#!/usr/bin/env python3
"""PreToolUse（全ツール）・Stop hook — 作業中に届いた user の発話（queued_command）に、
user に見える text で答えないまま作業を続けるのを止める。

背景:
  2026-10-16 sid 375c1ccf: queued の問い・命令に 4 回答えないまま grep / curl / Edit を続けた → 新設。
  2026-10-19 sid 4c932094: PreToolUse の時点では同じ応答の text がまだ transcript に無いので、
    答えても止まり続けた → 「同じ発話は 2 回目に通す」に変更。
  2026-10-21 sid 88fca185: その「2 回目に通す」は答えの有無を見ていなかった。答えを thinking に書いて
    （user には見えない）同じツールを呼び直すだけで 8 回とも素通りし、user に見える返事が約 4 分 0 件だった。
    root-cause-auditor 判定 wrong-impl。通す条件を「見える text が実際に記録されたか」に変えた。
  2026-10-22 sid dcd953b3: ツール呼び出しと同じ応答に書いた文は、type=thinking・署名の種類 narration の
    ブロックとして記録される（VSCode の画面には表示される — user が画面を貼って確認）。それを答えに数えず、
    「thinking に書いた」と誤った診断で 33 回止め直していた（root-cause-auditor 判定 wrong-impl）。
    narration も見える答えとして数え、止める文から誤った診断を消した。

判定（未回答 = 最後の「本文のある type=text」より後にある human の queued_command。3 文字以下の了承は除く）:
  PreToolUse（「本文のある type=text」は narration＝ツール呼び出し前の状況報告も含む）:
    未回答なし → 状態を消して通す。
    未回答あり・その発話でまだ止めていない → 止める（phase=blocked）。
    phase=blocked → 呼び直しの 1 回は通す（その応答はまだ transcript に無いので確かめられない）。phase=await。
    phase=await → ここで未回答が残っていれば、呼び直しの応答は text を出していない（thinking だけ等）。
      「user に見えていない」と明示して止め直す（その発話につき 2 回まで。超えたら Stop に任せて通す）。
  Stop:
    未回答があり、stop_hook_active でなければ 1 回止める（最後に届いた割り込みへの答えの取りこぼし）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover
    def record_fire(*_a, **_k):  # type: ignore
        pass

TAIL_LINES = 400
MAX_REBLOCK = 2


def _is_visible(c) -> bool:
    """user の画面に出る文か: 本文のある text、または narration（ツール呼び出し前の状況報告）。
    narration は type=thinking だが、署名（base64）の先頭に種類 "narration" が入る。本物の推論は "thinking"。"""
    if not isinstance(c, dict):
        return False
    if c.get("type") == "text":
        return bool(str(c.get("text") or "").strip())
    if c.get("type") == "thinking" and str(c.get("thinking") or "").strip():
        sig = str(c.get("signature") or "")[:120]
        try:
            return b"narration" in base64.b64decode(sig[: len(sig) - len(sig) % 4])
        except Exception:
            return False
    return False


def _queued_text(att: dict) -> str:
    p = att.get("prompt")
    if isinstance(p, str):
        return p
    if isinstance(p, list):
        return " ".join(x.get("text", "") for x in p if isinstance(x, dict))
    return ""


def unanswered_queued(transcript: Path) -> list[str]:
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]
    except Exception:
        return []
    pending: list[str] = []
    for raw in reversed(lines):
        try:
            d = json.loads(raw)
        except Exception:
            continue
        t = d.get("type")
        if t == "assistant":
            content = (d.get("message") or {}).get("content") or []
            # 推論（thinking）は user に表示されないので数えない。narration は表示されるので数える
            if any(_is_visible(c) for c in content):
                break
        elif t == "user":
            content = (d.get("message") or {}).get("content")
            if isinstance(content, str) or (
                isinstance(content, list)
                and any(isinstance(c, dict) and c.get("type") == "text" for c in content)
            ):
                break
        elif t == "attachment":
            att = d.get("attachment") or {}
            if att.get("type") == "queued_command" and (att.get("origin") or {}).get("kind") == "human":
                txt = _queued_text(att).strip()
                if txt and not (len(txt) <= 3 and not any(q in txt for q in "?？")):
                    pending.append(txt)
    return list(reversed(pending))


def _state_path(sid: str) -> Path | None:
    try:
        from _paths import RUNTIME_DIR  # noqa: E402
    except Exception:
        return None
    safe = "".join(c for c in (sid or "x") if c.isalnum() or c in "-_") or "x"
    return RUNTIME_DIR / "queued_answer_state" / f"{safe}.json"


def _load(p: Path | None) -> dict:
    try:
        return json.loads(p.read_text()) if p and p.exists() else {}
    except Exception:
        return {}


def _save(p: Path | None, st: dict) -> None:
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, ensure_ascii=False))
    except Exception:
        pass


def _quote(pending: list[str]) -> str:
    return " / ".join(f"「{p[:80]}」" for p in pending[-4:])


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tp = payload.get("transcript_path")
    if not tp:
        return 0
    event = payload.get("hook_event_name") or "PreToolUse"
    pending = unanswered_queued(Path(tp))
    sp = _state_path(payload.get("session_id", "x"))

    if event == "Stop":
        if not pending or payload.get("stop_hook_active"):
            return 0
        record_fire("feedback_own_artifact_first_suspect_admit_fault", "block",
                    hook_name="block_tool_before_answering_queued_user:stop")
        sys.stderr.write(
            f"作業中に届いた user の発話に、user に見える text で答えないままターンを終えようとしている: {_quote(pending)}\n"
            "その発話に答える文を書いてから終えること。\n")
        return 2

    if not pending:
        if sp and sp.exists():
            try:
                sp.unlink()
            except Exception:
                pass
        return 0

    key = hashlib.sha1(pending[-1].encode()).hexdigest()  # 最後に届いた発話ごとに数える
    st = _load(sp)
    if st.get("key") != key:
        st = {"key": key, "phase": "blocked", "reblock": 0}
        _save(sp, st)
        record_fire("feedback_own_artifact_first_suspect_admit_fault", "block",
                    hook_name="block_tool_before_answering_queued_user")
        sys.stderr.write(
            f"作業中に user の発話が届いていて、まだ user に見える text で答えていません: {_quote(pending)}\n"
            "次のツールを呼ぶ前に、その発話に直接答える文を書くこと。"
            "問いなら結論を 1 行目に。訂正・命令なら受けて何をするかを 1 行。答えたら同じツールを呼び直してよい。\n")
        return 2
    if st.get("phase") == "blocked":
        st["phase"] = "await"
        _save(sp, st)
        return 0
    # phase == await: 呼び直しの応答は記録済みのはずなのに、見える text がまだ無い
    if st.get("reblock", 0) >= MAX_REBLOCK:
        return 0
    st["reblock"] = st.get("reblock", 0) + 1
    st["phase"] = "blocked"
    _save(sp, st)
    record_fire("feedback_own_artifact_first_suspect_admit_fault", "block",
                hook_name="block_tool_before_answering_queued_user:reblock")
    n_resp, n_think = _responses_since_queued(Path(tp))
    sys.stderr.write(
        f"呼び直した応答に、user の画面に出る文（本文、またはツール呼び出し前の状況報告）が無い: {_quote(pending)}\n"
        f"記録の実数: 最後に画面に出る文が記録されてから、あなたの応答の記録 {n_resp} 件（うち推論だけのもの {n_think} 件）。"
        "推論は画面に出ない。その発話に答える文を書いてから、ツールを呼ぶこと。\n")
    return 2


def _responses_since_queued(transcript: Path) -> tuple[int, int]:
    """最初の未回答の queued_command より後の assistant 記録の件数と、そのうち thinking だけのものの件数。
    （2026-10-21 sid 41861b00: 答えを thinking に書いたまま 3 回止められ、記録を見ずに「hook の不具合」と user に報告した。
    止める文に実数を出し、自分の側の欠落だと分かるようにした）"""
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]
    except Exception:
        return 0, 0
    n_resp = n_think = 0
    for raw in reversed(lines):
        try:
            d = json.loads(raw)
        except Exception:
            continue
        t = d.get("type")
        if t == "assistant":
            content = (d.get("message") or {}).get("content") or []
            if any(_is_visible(c) for c in content):
                break
            n_resp += 1
            if any(isinstance(c, dict) and c.get("type") == "thinking" for c in content):
                n_think += 1
        elif t == "user":
            content = (d.get("message") or {}).get("content")
            if isinstance(content, str) or (isinstance(content, list)
                                            and any(isinstance(c, dict) and c.get("type") == "text" for c in content)):
                break
    return n_resp, n_think


if __name__ == "__main__":
    sys.exit(main())
