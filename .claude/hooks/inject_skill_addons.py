#!/usr/bin/env python3
"""PostToolUse(Skill): 呼ばれた skill に、この環境・このプロジェクト階層だけの追加手順（skill addon）を差し込む。

共有の skill（frame 層・plugin）を書き換えずに、環境やプロジェクトごとの手順を足すための機構。
skill 側に受け口は要らない。追加分のファイルが 1 つも無ければ何もしない（opt-in）。

探す場所（広い方から順に差し込み、食い違うときは後＝深い方を優先）:
  1. repo root の `.local/skill-addons/<名前>.md`（ローカル層）
  2. 対象パスの祖先ディレクトリ（root の直下から深い方へ）ごとの `<dir>/.claude/skill-addons/<名前>.md`
     例: projects/foo/.claude/skill-addons/ → projects/foo/projects/bar/.claude/skill-addons/
対象パス = Skill の args と cwd に現れる、repo 内に実在するパス。
<名前> = skill 名。plugin の skill（`plugin:skill`）は `:` の後ろ。

決して block しない (exit 0)。例外時も黙って通す。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _paths import PROJECT_DIR  # noqa: E402
except Exception:  # pragma: no cover
    sys.exit(0)

try:
    from _fire_counter import record_fire  # noqa: E402
except Exception:  # pragma: no cover
    record_fire = None

MAX_CHARS = 20000
PATH_RE = re.compile(r"[\w.~/-]*/[\w./-]+")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _skill_name(raw: str) -> str | None:
    name = raw.strip().lstrip("/").split(":")[-1]
    return name if NAME_RE.match(name) else None


def _targets(payload: dict, root: Path) -> list[Path]:
    """args と cwd に出てくる、repo 内に実在するパスのうち、最も深いディレクトリ群。"""
    tin = payload.get("tool_input") or {}
    hay = " ".join([str(tin.get("args") or ""), str(payload.get("cwd") or "")])
    out: list[Path] = []
    for tok in PATH_RE.findall(hay):
        p = Path(os.path.expanduser(tok))
        if not p.is_absolute():
            p = root / p
        try:
            p = p.resolve()
            p.relative_to(root)
        except Exception:
            continue
        if not p.exists():
            continue
        d = p if p.is_dir() else p.parent
        if d != root and d not in out:
            out.append(d)
    return out


def _chain(root: Path, targets: list[Path], name: str) -> list[Path]:
    files: list[Path] = []
    base = root / ".local" / "skill-addons" / f"{name}.md"
    if base.is_file():
        files.append(base)
    for t in targets:
        rel = t.relative_to(root).parts
        for i in range(1, len(rel) + 1):
            f = root.joinpath(*rel[:i]) / ".claude" / "skill-addons" / f"{name}.md"
            if f.is_file() and f not in files:
                files.append(f)
    return files


def build_context(payload: dict, root: Path) -> str | None:
    tin = payload.get("tool_input") or {}
    name = _skill_name(str(tin.get("skill") or ""))
    if not name:
        return None
    targets = _targets(payload, root)
    files = _chain(root, targets, name)
    if not files:
        return None
    parts = [
        f"[skill-addons] skill `{name}` に、この環境の追加手順が {len(files)} 件ある。"
        "skill 本体の該当する工程に足して従う。食い違うときは後に並ぶ（深い階層の）方を優先する。"
    ]
    if not targets:
        parts.append(
            "対象パスを判定できなかったので root の分だけを差し込んだ。作業対象が projects/ 配下なら、"
            f"そのディレクトリから root までの各階層の `.claude/skill-addons/{name}.md` も Read して足す。"
        )
    budget = MAX_CHARS
    for f in files:
        body = f.read_text(encoding="utf-8", errors="replace")
        if len(body) > budget:
            body = body[:budget] + f"\n…（切り詰め。全文は {f.relative_to(root)} を Read）"
        budget -= len(body)
        parts.append(f"--- {f.relative_to(root)} ---\n{body}")
        if budget <= 0:
            break
    return "\n\n".join(parts)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        ctx = build_context(payload, PROJECT_DIR.resolve())
    except Exception:
        return 0
    if not ctx:
        return 0
    if record_fire:
        try:
            record_fire("inject_skill_addons", "transform")
        except Exception:
            pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": ctx}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
