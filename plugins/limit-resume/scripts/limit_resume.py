#!/usr/bin/env python3
"""limit-resume: 利用上限で止まったセッションを、解除後に同じ窓で再開させる。

  hook          StopFailure(rate_limit) から呼ばれる。待ちの仕事を書き、待ち役を切り離して起こす
  wait <sid>    解除時刻まで待ち、まだ止まったままなら、そのセッションの郵便受けへ「続けて」を送る
  status        待ちの仕事と直近の記録を出す
  cancel [sid]  待ちの仕事を消す（sid 省略で全部）

再開するのは「上限に当たったそのセッション自身」だけ。StopFailure はターンが API エラーで終わった
セッションにしか来ないので、開いているだけの別の窓は対象にならない。
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

# フックからも手打ちの status からも同じ場所を見るよう、CLAUDE_PLUGIN_DATA（フック内だけで立つ）は使わない
HOME = Path(os.environ.get("LIMIT_RESUME_HOME") or Path.home() / ".claude" / "limit-resume")
JOBS = HOME / "jobs"
LOG = HOME / "log.jsonl"
SESSIONS = Path(os.environ.get("LIMIT_RESUME_SESSIONS_DIR") or Path.home() / ".claude" / "sessions")

MAX_WAIT_H = float(os.environ.get("LIMIT_RESUME_MAX_WAIT_HOURS", "12"))  # これより先の解除は捨てる
MARGIN_S = float(os.environ.get("LIMIT_RESUME_MARGIN_S", "90"))  # 解除時刻ちょうどは外して少し待つ
SEND_RETRIES = 6         # 送る役が失敗したときの再試行回数（5 分おき）
MAX_RESUMES_6H = 3       # 再開してすぐまた上限、の繰り返しを止める
INTERACTIVE = {"claude-vscode", "cli"}
SENDER_MODEL = os.environ.get("LIMIT_RESUME_SENDER_MODEL", "haiku")
MARKER = "[limit-resume]"

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def log(event, **kw):
    HOME.mkdir(parents=True, exist_ok=True)
    rec = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "event": event, **kw}
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---- 上限の文言を読む -------------------------------------------------------

def parse_limit_message(text, now=None):
    """"You've hit your session limit · resets 4:30pm (Asia/Tokyo)" → (label, 解除 epoch)。

    本体の書式（2.1.281 の Xc）: 24 時間以内は "4:30pm" / "4pm"、それより先は
    "Oct 25, 2:30am" / "Oct 25, 2am"、年が変わるときは "Jan 2, 2027, 2am"。
    """
    if not text:
        return None, None
    m = re.search(r"hit your (.+?)(?: ·|$)", text)
    label = m.group(1).strip() if m else None
    m = re.search(r"resets ([^·(]+?)\s*(?:\(([^)]+)\))?\s*(?:·|$)", text)
    if not m:
        return label, None
    when, tzname = m.group(1).strip(), m.group(2)
    tz = None
    if tzname and ZoneInfo:
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = None
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(tz) if tz else (now or dt.datetime.now().astimezone())
    tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)$", when, re.I)
    if not tm:
        return label, None
    hour = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "pm" else 0)
    minute = int(tm.group(2) or 0)
    dm = re.match(r"([A-Z][a-z]{2}) (\d{1,2})(?:, (\d{4}))?,", when)
    if dm:
        year = int(dm.group(3)) if dm.group(3) else now.year
        target = now.replace(year=year, month=MONTHS[dm.group(1)], day=int(dm.group(2)),
                             hour=hour, minute=minute, second=0, microsecond=0)
        if not dm.group(3) and target < now - dt.timedelta(days=1):
            target = target.replace(year=year + 1)
    else:
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
    return label, target.timestamp()


# ---- セッションの名簿と会話記録 ---------------------------------------------

def find_registry(session_id):
    """~/.claude/sessions/<pid>.json のうち、生きていて sessionId が一致するもの。"""
    for f in SESSIONS.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if d.get("sessionId") != session_id:
            continue
        try:
            os.kill(int(d.get("pid") or f.stem), 0)
        except Exception:
            continue
        return d
    return None


def still_stopped_by_limit(transcript_path):
    """会話記録の最後の user/assistant が、上限のエラーのままか（user が自分で再開していないか）。"""
    try:
        lines = Path(transcript_path).read_text().splitlines()[-400:]
    except Exception:
        return None
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") not in ("user", "assistant") or d.get("isMeta"):
            continue
        return d.get("type") == "assistant" and d.get("error") == "rate_limit"
    return None


def delivered(transcript_path, since):
    try:
        lines = Path(transcript_path).read_text().splitlines()[-200:]
    except Exception:
        return False
    for line in lines:
        if MARKER in line and '"queue-operation"' in line:
            try:
                ts = dt.datetime.fromisoformat(json.loads(line)["timestamp"].replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if ts >= since:
                return True
    return False


# ---- 待ちの仕事 -------------------------------------------------------------

def job_path(sid):
    return JOBS / f"{sid}.json"


def read_job(sid):
    try:
        return json.loads(job_path(sid).read_text())
    except Exception:
        return None


def write_job(job):
    JOBS.mkdir(parents=True, exist_ok=True)
    tmp = job_path(job["session_id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1))
    tmp.replace(job_path(job["session_id"]))


def load_history(sid):
    try:
        return json.loads((JOBS / f"{sid}.history").read_text())
    except Exception:
        return []


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def cmd_hook():
    if os.environ.get("LIMIT_RESUME_SENDER"):
        return  # 送る役の claude -p 自身が上限に当たったときは何もしない
    try:
        p = json.load(sys.stdin)
    except Exception:
        return
    if p.get("error") != "rate_limit" or p.get("agent_id"):
        return  # 下位のエージェントの失敗は、親のターンの終わりで拾う
    sid = p.get("session_id")
    msg = p.get("last_assistant_message") or p.get("error_details") or ""
    label, reset = parse_limit_message(msg)
    reg = find_registry(sid) or {}
    entry = reg.get("entrypoint")
    base = {"session_id": sid, "label": label, "message": msg[:200], "entrypoint": entry}
    if entry not in INTERACTIVE:
        log("skip_noninteractive", **base)
        return
    if reset is None:
        log("skip_unparsed", **base)
        return
    wait_h = (reset - time.time()) / 3600
    if wait_h > MAX_WAIT_H:
        log("drop_far_reset", reset_at=reset, wait_hours=round(wait_h, 2), **base)
        return
    history = [t for t in load_history(sid) if t > time.time() - 6 * 3600]
    if len(history) >= MAX_RESUMES_6H:
        log("drop_loop_guard", resumes_6h=len(history), **base)
        return
    old = read_job(sid) or {}
    job = {**base, "transcript_path": p.get("transcript_path"), "reset_at": reset,
           "created_at": time.time(), "waiter_pid": old.get("waiter_pid")}
    write_job(job)
    log("scheduled", reset_at=reset, wait_hours=round(wait_h, 2), **base)
    if job["waiter_pid"] and alive(job["waiter_pid"]):
        return  # 待ち役はもう居る（新しい解除時刻を読み直す）
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / "waiter.out", "a") as out:
        proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "wait", sid],
                                stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                start_new_session=True, cwd=str(HOME))
    job["waiter_pid"] = proc.pid
    write_job(job)


def send(socket_path, text):
    prompt = (f"Use the SendMessage tool (load it with ToolSearch first if it is deferred) to send exactly the "
              f"following text to the address uds:{socket_path} and nothing else. Do not reply to anything.\n\n{text}")
    env = {**os.environ, "LIMIT_RESUME_SENDER": "1"}
    try:
        r = subprocess.run(["claude", "-p", "--model", SENDER_MODEL, "--no-session-persistence",
                            "--allowedTools", "SendMessage,ToolSearch"],
                           input=prompt, capture_output=True, text=True, timeout=240, env=env, cwd=str(HOME))
    except Exception as e:
        return False, repr(e)
    out = r.stdout or ""
    # 正の判定は相手の会話記録（cmd_wait 側）。ここは送る役が道具を呼んだらしいかの補助だけ
    said_sent = r.returncode == 0 and re.search(r"message id|queued|delivered|sent", out, re.I) is not None
    return said_sent, out[-300:]


def cmd_wait(sid):
    while True:
        job = read_job(sid)
        if not job:
            return
        other = job.get("waiter_pid")
        if other and other != os.getpid() and alive(other):
            return  # 別の待ち役が受け持っている
        if time.time() >= job["reset_at"] + MARGIN_S:
            break
        time.sleep(min(30, max(1, job["reset_at"] + MARGIN_S - time.time())))  # 壁時計で測り直す（スリープ対策）

    for attempt in range(SEND_RETRIES):
        job = read_job(sid)
        if not job:
            return
        reg = find_registry(sid)
        if not reg:
            log("drop_window_closed", session_id=sid)  # 同じ窓でしか再開しない
            break
        stuck = still_stopped_by_limit(job["transcript_path"])
        if stuck is False:
            log("drop_user_resumed", session_id=sid)
            break
        if reg.get("status") != "idle":
            log("wait_busy", session_id=sid, attempt=attempt)
            time.sleep(300)
            continue
        text = (f"{MARKER} 利用上限（{job.get('label') or 'limit'}）が解除されました。"
                "この会話で上限により中断した直前の作業を、中断した地点から続けてください。"
                "（自動送信・この伝言への返信は不要）")
        t0 = time.time()
        said_sent, out = send(reg["messagingSocketPath"], text)
        seen = False
        for _ in range(12):
            if delivered(job["transcript_path"], t0 - 5):
                seen = True
                break
            time.sleep(5)
        # 会話記録に届いた跡がある、または送る役が送ったと言った（記録の書き込みが遅れる場合）なら再送しない。
        # 二重に「続けて」が届くより、1 回も届かない方が害が小さいので、迷ったら送り直さない側に倒す
        if seen or said_sent:
            log("resumed", session_id=sid, delivered_in_transcript=seen, sender_said_sent=said_sent,
                attempt=attempt, out=None if seen else out)
            # 再開がまた上限に当たったときの繰り返し防止の記録だけ残す
            hist = [t for t in load_history(sid) if t > time.time() - 6 * 3600] + [time.time()]
            (JOBS / f"{sid}.history").write_text(json.dumps(hist))
            job_path(sid).unlink(missing_ok=True)
            return
        log("send_failed", session_id=sid, attempt=attempt, out=out)
        time.sleep(300)
    job_path(sid).unlink(missing_ok=True)


def cmd_status():
    jobs = sorted(JOBS.glob("*.json")) if JOBS.exists() else []
    if not jobs:
        print("待ちの仕事: なし")
    for f in jobs:
        j = json.loads(f.read_text())
        at = dt.datetime.fromtimestamp(j["reset_at"]).astimezone().strftime("%m-%d %H:%M")
        print(f"待ち: {j['session_id'][:8]} {j.get('label')} 解除 {at} 待ち役 "
              f"{'生存' if j.get('waiter_pid') and alive(j['waiter_pid']) else '不在'}")
    if LOG.exists():
        print("直近の記録:")
        for line in LOG.read_text().splitlines()[-10:]:
            print("  " + line)


def cmd_cancel(sid=None):
    for f in (JOBS.glob("*.json") if JOBS.exists() else []):
        if sid is None or f.stem.startswith(sid):
            f.unlink()
            print("消した:", f.stem)


if __name__ == "__main__":
    a = sys.argv[1:] or ["status"]
    if a[0] == "hook":
        cmd_hook()
    elif a[0] == "wait":
        cmd_wait(a[1])
    elif a[0] == "cancel":
        cmd_cancel(a[1] if len(a) > 1 else None)
    else:
        cmd_status()
