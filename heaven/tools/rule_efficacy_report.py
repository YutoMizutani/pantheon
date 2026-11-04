#!/usr/bin/env python3
"""規則ごとの「発火 ÷ 呼び出し」を ISO 週で並べ、誕生直後と直近の率を比べる。

rule_adoption_report.py は 30 日の発火を「再発しているか」で読むが、分母が無いので、
発火が増えたのが規則の効かなさか活動量の増加かを分けられない。この道具は
hook_fires.jsonl（全期間）を rule_id × 週で数え、tool_use_weekly.py の週別の tool 呼び出し数で割って
1000 呼び出しあたりの発火率にする。

- 分母: 規則を出す hook の配線（`.claude/settings.json`・`.local/settings.local.json`）を読み、
  PreToolUse / PostToolUse で matcher があれば、その matcher に一致する tool 名の呼び出し数の合計
  （matcher が Bash なら Bash の件数）。Stop・UserPromptSubmit など tool に紐づかない event、
  matcher が無い・`*`、配線が見つからない hook は、その週の tool 呼び出しの総数。
- 分子: hook_fires.jsonl の 1 行を 1 発火として数える（`count` 欄は 1 回の呼び出しの中の違反数なので使わない）。
- 誕生日: `<memory>/<rule_id>.md` の git 初出 → 無ければファイルの作成時刻（無い OS では mtime）、
  と hook の初発火のうち早い方。
- 判定: 誕生週から数えて分母のある最初の 4 週の率（誕生後 4 週）と、分母のある最後の 4 週の率（直近 4 週）を
  比べ、直近 ÷ 誕生後 が 1 − band 未満なら「下がった」、1 + band 超なら「上がった」、間なら「横ばい」。
  両方の窓が週を共有するとき（誕生が新しい）と、どちらかの窓の分子と分母が両方 0 のときは「判定不能」。
  評価の語は書かない（数字と向きだけ）。
- 既定の対象: rule_adoption_report.py の NOT-INTERNALIZED（既定の 30 日の窓）と同じ集合。

注意（既知の偏り）: 分母はトップレベルの transcript だけを数え、subagent の
サブフォルダを含まない。発火は subagent 内の呼び出しでも記録されるので、subagent をよく使う週ほど率は高めに出る。

読むだけ。hook_fires.jsonl・transcript・既存の report は書き換えない（分母のキャッシュだけ
tool_use_weekly.py の置き場に書く）。

Usage:
    python3 heaven/tools/rule_efficacy_report.py              # NOT-INTERNALIZED の表
    python3 heaven/tools/rule_efficacy_report.py --all        # 発火のある全規則
    python3 heaven/tools/rule_efficacy_report.py --rule <rule_id> --series   # 週ごとの率も出す
    python3 heaven/tools/rule_efficacy_report.py --json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tuw = _load("tool_use_weekly")
rar = _load("rule_adoption_report")

REPO = tuw.REPO
STATE_DIR = tuw.JSONL_DIR
TELEM = os.path.join(STATE_DIR, "telemetry", "hook_fires.jsonl")
MEMORY_DIR = os.path.join(STATE_DIR, "memory")
SETTINGS_FILES = (".claude/settings.json", ".local/settings.local.json")
_HOOK_RE = re.compile(r"hooks/(?:local/)?([A-Za-z0-9_]+)\.py")
TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse"})
WINDOW = 4
ALL = "*"


# ---- 入力 ----------------------------------------------------------------

def load_fires(path: str) -> list[dict]:
    out = []
    try:
        f = open(path, errors="replace")
    except OSError:
        return out
    with f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("ts"):
                out.append(rec)
    return out


def load_matchers(repo: str) -> dict[str, set[str]]:
    """hook 名 → matcher の集合。tool に紐づかない配線は ALL を入れる。"""
    res: dict[str, set[str]] = defaultdict(set)
    for rel in SETTINGS_FILES:
        try:
            with open(os.path.join(repo, rel)) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for event, groups in (data.get("hooks") or {}).items():
            for group in groups or []:
                m = group.get("matcher")
                key = m if (event in TOOL_EVENTS and m and m != "*") else ALL
                for h in group.get("hooks") or []:
                    for name in _HOOK_RE.findall(str(h.get("command", ""))):
                        res[name].add(key)
    return dict(res)


def _hook_name(raw: str) -> str:
    # '<hook>:reblock' のような枝の印を外す
    return (raw or "").split(":", 1)[0]


def denominator_key(hooks: set[str], matchers: dict[str, set[str]]) -> frozenset[str]:
    """規則の分母を決める matcher の集合。1 つでも ALL があれば ALL だけ。"""
    keys: set[str] = set()
    for h in hooks:
        ms = matchers.get(_hook_name(h))
        if not ms:
            return frozenset({ALL})
        keys |= ms
    if not keys or ALL in keys:
        return frozenset({ALL})
    return frozenset(keys)


def weekly_denominator(weekly: dict[str, dict[str, int]], key: frozenset[str]) -> dict[str, int]:
    if ALL in key:
        return {w: sum(t.values()) for w, t in weekly.items()}
    pats = [re.compile(m) for m in key]
    out = {}
    for w, t in weekly.items():
        out[w] = sum(n for name, n in t.items() if any(p.fullmatch(name) for p in pats))
    return out


def _git_first_added(path: str) -> datetime | None:
    real = os.path.realpath(path)
    try:
        r = subprocess.run(
            ["git", "-C", os.path.dirname(real), "log", "--diff-filter=A", "--format=%aI", "--", real],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [s for s in r.stdout.splitlines() if s.strip()]
    if r.returncode != 0 or not lines:
        return None
    return datetime.fromisoformat(lines[-1]).astimezone(timezone.utc)


def memory_birth(rule_id: str, memory_dir: str) -> tuple[datetime | None, str]:
    path = os.path.join(memory_dir, rule_id + ".md")
    if not os.path.exists(path):
        return None, ""
    t = _git_first_added(path)
    if t:
        return t, "git"
    st = os.stat(path)
    ts = getattr(st, "st_birthtime", None)
    if ts:
        return datetime.fromtimestamp(ts, timezone.utc), "作成時刻"
    return datetime.fromtimestamp(st.st_mtime, timezone.utc), "mtime"


# ---- 計算（純関数） --------------------------------------------------------

def week_of(t: datetime) -> str:
    y, w, _ = t.astimezone(timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


def rate(fires: int, den: int) -> float | None:
    return None if den <= 0 else 1000.0 * fires / den


def windows(birth_week: str, den: dict[str, int], n: int = WINDOW) -> tuple[list[str], list[str]]:
    weeks = sorted(w for w, d in den.items() if d > 0)
    early = [w for w in weeks if w >= birth_week][:n]
    recent = weeks[-n:]
    return early, recent


def verdict(early_f: int, early_d: int, recent_f: int, recent_d: int, overlap: bool, band: float) -> str:
    if overlap or early_d <= 0 or recent_d <= 0:
        return "判定不能"
    if early_f == 0:
        return "横ばい" if recent_f == 0 else "上がった"
    # 率どうしを割ると 0.075/0.1 = 0.7499… のように境目がずれるので、整数の積で比を作る
    q = (recent_f * early_d) / (early_f * recent_d)
    if q < 1 - band:
        return "下がった"
    if q > 1 + band:
        return "上がった"
    return "横ばい"


def analyze(rule_id: str, fire_weeks: dict[str, int], hooks: set[str], first_fire: datetime,
            mem_birth: datetime | None, weekly: dict[str, dict[str, int]],
            matchers: dict[str, set[str]], band: float) -> dict:
    birth = min(t for t in (first_fire, mem_birth) if t is not None)
    key = denominator_key(hooks, matchers)
    den = weekly_denominator(weekly, key)
    early, recent = windows(week_of(birth), den)
    ef = sum(fire_weeks.get(w, 0) for w in early)
    ed = sum(den[w] for w in early)
    rf = sum(fire_weeks.get(w, 0) for w in recent)
    rd = sum(den[w] for w in recent)
    overlap = bool(set(early) & set(recent))
    v = verdict(ef, ed, rf, rd, overlap, band)
    if v == "判定不能" and not overlap and not early:
        reason = "誕生後に分母のある週が無い"
    elif overlap:
        reason = "誕生後 4 週と直近 4 週が重なる"
    else:
        reason = ""
    series = {w: {"fires": fire_weeks.get(w, 0), "den": den.get(w, 0), "rate": rate(fire_weeks.get(w, 0), den.get(w, 0))}
              for w in sorted(set(den) | set(fire_weeks))}
    return {
        "rule_id": rule_id,
        "hooks": sorted(hooks),
        "denominator": "全 tool" if ALL in key else "|".join(sorted(key)),
        "birth": birth.date().isoformat(),
        "early_weeks": early, "early_fires": ef, "early_den": ed, "early_rate": rate(ef, ed),
        "recent_weeks": recent, "recent_fires": rf, "recent_den": rd, "recent_rate": rate(rf, rd),
        "ratio": (rate(rf, rd) / rate(ef, ed)) if (ed and rd and ef) else None,
        "verdict": v, "reason": reason, "series": series,
    }


def group_fires(fires: list[dict]) -> dict[str, dict]:
    per: dict[str, dict] = {}
    for rec in fires:
        t = rar._parse_ts(rec.get("ts", ""))
        if t is None:
            continue
        rid = rec.get("rule_id") or rec.get("hook") or "?"
        p = per.setdefault(rid, {"weeks": defaultdict(int), "hooks": set(), "first": t, "events": []})
        p["weeks"][week_of(t)] += 1
        if rec.get("hook"):
            p["hooks"].add(rec["hook"])
        p["first"] = min(p["first"], t)
        p["events"].append((t, rec.get("outcome", "?")))
    return per


def not_internalized(per: dict[str, dict], now: datetime, days: int = 30, recent: int = 7,
                     repeat_days: int = 3, quiet: int = 14) -> list[str]:
    """rule_adoption_report.py の既定と同じ判定で NOT-INTERNALIZED の rule_id を返す。"""
    cutoff = now - timedelta(days=days)
    out = []
    for rid, p in per.items():
        inwin = [(t, o) for t, o in p["events"] if t >= cutoff]
        if not inwin:
            continue
        last = max(t for t, _ in inwin)
        stats = {
            "distinct_days": len({t.date() for t, _ in inwin}),
            "days_since_last": (now - last).days,
            "enforcing": bool({o for _, o in inwin} & rar.ENFORCING_OUTCOMES),
        }
        if rar.classify(stats, now, recent, repeat_days, quiet)[0] == "not-internalized":
            out.append(rid)
    return out


# ---- 出力 ----------------------------------------------------------------

def _fmt_rate(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}"


def render(rows: list[dict], series: bool) -> str:
    head = ["規則", "誕生日", "分母", "誕生後4週 発火/分母", "率", "直近4週 発火/分母", "率", "直近÷誕生後", "判定"]
    body = []
    for r in rows:
        body.append([
            r["rule_id"], r["birth"], r["denominator"],
            f"{r['early_fires']}/{r['early_den']}", _fmt_rate(r["early_rate"]),
            f"{r['recent_fires']}/{r['recent_den']}", _fmt_rate(r["recent_rate"]),
            "-" if r["ratio"] is None else f"{r['ratio']:.2f}",
            r["verdict"] + (f"（{r['reason']}）" if r["reason"] else ""),
        ])
    widths = [max(len(x[i]) for x in [head] + body) for i in range(len(head))]
    lines = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(x)) for x in [head] + body]
    if series:
        for r in rows:
            lines.append("")
            lines.append(f"-- {r['rule_id']}（率 = 1000 呼び出しあたり、分母 = {r['denominator']}）")
            for w, s in r["series"].items():
                lines.append(f"   {w}  {s['fires']:>5} / {s['den']:>6}  {_fmt_rate(s['rate']):>8}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="発火のある全規則を対象にする")
    ap.add_argument("--rule", action="append", default=[], help="対象の rule_id（複数可）")
    ap.add_argument("--band", type=float, default=0.25, help="横ばいとみなす直近÷誕生後の幅（既定 ±0.25）")
    ap.add_argument("--series", action="store_true", help="規則ごとの週別の率も出す")
    ap.add_argument("--json", action="store_true", help="JSON で出す")
    ap.add_argument("--fires", default=TELEM, help="hook_fires.jsonl の置き場")
    ap.add_argument("--memory-dir", default=MEMORY_DIR)
    ap.add_argument("--repo", default=REPO, help="settings を読む repo ルート")
    ap.add_argument("--dir", default=tuw.JSONL_DIR, help="分母を数える transcript のフォルダ")
    ap.add_argument("--cache", default=tuw.CACHE)
    args = ap.parse_args(argv)

    if not os.path.isdir(args.dir):
        print(f"transcript のフォルダが無い: {args.dir}", file=sys.stderr)
        return 1
    weekly, _ = tuw.collect(args.dir, args.cache)
    per = group_fires(load_fires(args.fires))
    matchers = load_matchers(args.repo)
    now = datetime.now(timezone.utc)

    if args.rule:
        targets = [r for r in args.rule if r in per]
    elif args.all:
        targets = sorted(per)
    else:
        targets = not_internalized(per, now)

    rows = []
    for rid in targets:
        p = per[rid]
        mb, _ = memory_birth(rid, args.memory_dir)
        rows.append(analyze(rid, p["weeks"], p["hooks"], p["first"], mb, weekly, matchers, args.band))
    order = {"上がった": 0, "横ばい": 1, "下がった": 2, "判定不能": 3}
    rows.sort(key=lambda r: (order[r["verdict"]], -(r["recent_rate"] or 0)))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(render(rows, args.series))
        counts = defaultdict(int)
        for r in rows:
            counts[r["verdict"]] += 1
        print(f"\nRULE_EFFICACY_SUMMARY rules={len(rows)} up={counts['上がった']} flat={counts['横ばい']} "
              f"down={counts['下がった']} undetermined={counts['判定不能']} band={args.band}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
