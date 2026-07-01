#!/usr/bin/env python3
"""mem_guard — phys_footprint が閾値超のユーザープロセスを SIGKILL する。

契機: 2026-06-30 Claude Code が 45GB+27GB を抱え 16GB 機を
Jetsam→WindowServer userspace watchdog→強制再起動させた (kernel panic ではない)。

計測は ps rss でなく footprint(phys_footprint) — 16GB 機ではメモリ圧縮で rss が
フットプリントを過小表示し、Jetsam と同じ指標でないと閾値に届かないため。
footprint は 1 プロセス約 0.08 秒と重いので、ps rss で粗くふるった大きい
プロセスにだけ当てる二段構え。

launchd の StartInterval で定期実行する一発もの (常駐ループにしない=自身のリーク回避)。
環境変数:
  MEM_GUARD_THRESHOLD_MB  kill 閾値 (既定 16384 = 16GB)
  MEM_GUARD_PREFILTER_KB  footprint をかける rss 下限 (既定 2GB)
  MEM_GUARD_LOG           kill ログ (既定 ~/Library/Logs/mem-guard.log)
  MEM_GUARD_DRY_RUN=1     kill せず検出だけ記録
"""
import os
import re
import signal
import subprocess
import time

THRESHOLD_MB = int(os.environ.get("MEM_GUARD_THRESHOLD_MB", str(16 * 1024)))
PREFILTER_KB = int(os.environ.get("MEM_GUARD_PREFILTER_KB", str(2 * 1024 * 1024)))
LOG = os.path.expanduser(os.environ.get("MEM_GUARD_LOG", "~/Library/Logs/mem-guard.log"))
DUMP_DIR = os.path.expanduser(os.environ.get("MEM_GUARD_DUMP_DIR", "~/Library/Logs"))
DRY = os.environ.get("MEM_GUARD_DRY_RUN") == "1"
ME = os.getuid()
SELF = {os.getpid(), os.getppid()}

UNIT_MB = {"B": 1 / 1048576, "KB": 1 / 1024, "MB": 1.0, "GB": 1024.0, "TB": 1048576.0}


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line)


def footprint(pid: int):
    """(phys_footprint[MB], 生の footprint 出力) を返す。取得不能なら (None, '')。"""
    try:
        out = subprocess.run(
            ["/usr/bin/footprint", "-p", str(pid)],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None, ""
    m = re.search(r"Footprint:\s+([\d.]+)\s+(B|KB|MB|GB|TB)", out)
    if not m:
        return None, out
    return float(m.group(1)) * UNIT_MB[m.group(2)], out


def dump_detail(pid: int, comm: str, raw: str) -> str:
    """kill 直前に footprint 詳細 (メモリ内訳) を保存し、保存先パスを返す。"""
    path = os.path.join(
        DUMP_DIR, "mem-guard-dump-%d-%s.txt" % (pid, time.strftime("%Y%m%d-%H%M%S"))
    )
    try:
        with open(path, "w") as f:
            f.write("# pid=%d comm=%s captured=%s\n\n" % (pid, comm, time.strftime("%F %T")))
            f.write(raw)
    except OSError as e:
        log("  dump failed pid=%d: %s" % (pid, e))
        return ""
    return path


def main() -> None:
    try:
        ps = subprocess.run(
            ["ps", "-axo", "pid=,uid=,rss=,comm="],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (subprocess.SubprocessError, OSError) as e:
        log(f"ps failed: {e}")
        return

    cands = []
    for ln in ps.splitlines():
        parts = ln.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, uid, rss = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        comm = parts[3]
        if uid != ME:          # 自分の所有プロセスだけ (system 領域は対象外)
            continue
        if pid in SELF:
            continue
        if rss < PREFILTER_KB:  # rss で粗ふるい (footprint は大きいものだけに当てる)
            continue
        cands.append((rss, pid, comm))

    for rss, pid, comm in sorted(cands, reverse=True):
        fp, raw = footprint(pid)
        if fp is None:
            continue
        if fp >= THRESHOLD_MB:
            dump = dump_detail(pid, comm, raw)  # kill 前にメモリ内訳を保存
            log(
                f"KILL pid={pid} comm={comm} "
                f"footprint={fp / 1024:.1f}GB rss={rss / 1048576:.1f}GB "
                f"threshold={THRESHOLD_MB / 1024:.0f}GB dump={dump or '-'}"
                + (" [DRY]" if DRY else "")
            )
            if not DRY:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError as e:
                    log(f"  kill failed pid={pid}: {e}")


if __name__ == "__main__":
    main()
