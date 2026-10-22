#!/usr/bin/env python3
"""hook_guard — settings の hook 行を「指すファイルが無ければ何もせず通す」形に書き換える／検査する。

Failure it prevents
-------------------
hook の行が `python3 <path>` のままだと、ファイルを移動・改名した瞬間に python3 が exit 2
（ファイルを開けない）を返し、hook ではそれが「ツールを止める」の意味になる。PreToolUse "*" や
UserPromptSubmit の行なら、同じ設定を読む全セッションの全ツールが止まり、Claude 側から戻せなくなる
（2026-10-22 poe2 の hook 3 本の移動で発生）。

使い方
------
  python3 heaven/tools/hook_guard.py            # 両方の settings を書き換える（冪等）
  python3 heaven/tools/hook_guard.py --check    # 未対応の行・ファイルが無い行を出す（あれば exit 1）

書き換え後の形: f="<path>"; [ -f "$f" ] || exit 0; python3 "$f" <args>
plugin の hooks.json も同じ形で書く（${CLAUDE_PLUGIN_ROOT} を使う）。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = [ROOT / ".local" / "settings.local.json", ROOT / ".claude" / "settings.json"]
GUARDED = re.compile(r'^f="[^"]+"; \[ -f "\$f" \] \|\| exit 0; ')
INTERP = {"python3", "python", "bash", "sh", "node"}


def guard(cmd: str) -> str | None:
    """書き換えた行を返す。対象外（既に対応済み・ファイルを指さない）なら None。"""
    if GUARDED.match(cmd):
        return None
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        return None
    if not parts:
        return None
    if parts[0] in INTERP and len(parts) >= 2:
        interp, path, args = parts[0], parts[1], parts[2:]
        run = f'{interp} "$f"'
    elif "/" in parts[0]:
        interp, path, args = None, parts[0], parts[1:]
        run = '"$f"'
    else:
        return None
    if not re.search(r"\.(py|sh|js|mjs)$", path):
        return None
    tail = "".join(" " + shlex.quote(a) for a in args)
    return f'f="{path}"; [ -f "$f" ] || exit 0; {run}{tail}'


def _path_of(cmd: str) -> str | None:
    m = re.match(r'^f="([^"]+)"', cmd)
    if m:
        return m.group(1)
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    return parts[1] if parts and parts[0] in INTERP and len(parts) > 1 else (parts[0] if parts else None)


def _commands(s: dict):
    for ev, arr in (s.get("hooks") or {}).items():
        for m in arr:
            for h in m.get("hooks", []):
                if h.get("type") == "command" and h.get("command"):
                    yield ev, h


def main() -> int:
    check = "--check" in sys.argv[1:]
    bad = 0
    for p in TARGETS:
        if not p.exists():
            continue
        s = json.loads(p.read_text(encoding="utf-8"))
        changed = 0
        for ev, h in _commands(s):
            new = guard(h["command"])
            path = _path_of(h["command"])
            if path:
                real = os.path.expandvars(path.replace("$CLAUDE_PROJECT_DIR", str(ROOT)))
                if "$" not in real and not Path(real).exists():
                    print(f"MISSING  {p.name} {ev}: {real}")
                    bad += 1
            if new is None:
                continue
            if check:
                print(f"UNGUARDED {p.name} {ev}: {h['command']}")
                bad += 1
            else:
                h["command"] = new
                changed += 1
        if changed:
            p.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"guarded {changed} 行: {p}")
    return 1 if (check and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
