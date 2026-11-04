#!/usr/bin/env python3
"""セッション記録 (transcript jsonl) を、判断を含まない整理表にして 1 回で出す。

## なぜ存在するか
self-reflection 係は、生の jsonl (数 MB) に Python のワンライナーで行範囲を切り出して読む、を
1 往復ずつ 10 回以上繰り返していた。往復ごとに土台 (係の定義・規則・CLAUDE 系 ≒ 61K) を
送り直すので、費用の 8 割はこの往復回数で決まる (429 回分の実測: 往復の中央値 13・
キャッシュ読み込みの中央値 883K ≒ 1.25 × 往復 × 1 往復目の量)。この道具は切り出しの往復を
1 回にまとめる。

## 要約しない
要点の抽出や言い換えはしない (逐語の範囲・ずれ・語調が落ちるため)。user の発話は全部逐語で、
Claude 側は冒頭だけ、道具の呼び出しは 1 行ずつ出す。深く読む所は --lines で生の行を引く。

## 使い方
    python3 heaven/tools/transcript_digest.py <transcript.jsonl> [--start N]
        → 整理表。--start を省くと、前回の振り返りの起動の直後 (無ければ先頭) から。
    python3 heaven/tools/transcript_digest.py <transcript.jsonl> --lines 120-180
        → その範囲の行を、本文を長めに残して出す。
行番号は 0 始まり (Python の enumerate と同じ)。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_HOOKS = Path(__file__).resolve().parents[2] / ".claude" / "hooks"
sys.path.insert(0, str(_HOOKS))
from _transcript import iter_lines  # type: ignore  # noqa: E402
try:  # 窓の境目と訂正の語彙は hook と同じ判定を使う (写しを持たない)
    from detect_acceptance_signal import _line_is_prior_reflection_fire  # type: ignore
except Exception:  # hook が読めない環境では窓を先頭からにする
    _line_is_prior_reflection_fire = None
try:
    from _signals import correction_pattern_sets  # type: ignore
except Exception:
    correction_pattern_sets = None

USER_CAP = 2000       # user 発話はこれを超えたときだけ切る (貼り付けの長文)
USER_HEAD = 1500
ASST_HEAD = 100
SIG_LEN = 80
_AUTO_PREFIXES = ("<task-notification", "<system-reminder", "<local-command", "<command-")


def _blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _text_of(blocks) -> str:
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _one_line(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n] + "…"


def _signature(name: str, inp) -> str:
    if not isinstance(inp, dict):
        return name
    if inp.get("command") and inp.get("description"):  # Bash はコマンドの冒頭より説明の方が読める
        return f"{name}: {_one_line(inp['description'], 40)} | {_one_line(str(inp['command']), SIG_LEN - 40)}"
    for key in ("command", "file_path", "pattern", "url", "query", "skill", "description", "prompt"):
        if inp.get(key):
            return f"{name}: {_one_line(str(inp[key]), SIG_LEN)}"
    return f"{name}: {_one_line(json.dumps(inp, ensure_ascii=False), SIG_LEN)}"


def _result_len(content) -> int:
    if isinstance(content, str):
        return len(content)
    return len(json.dumps(content, ensure_ascii=False))


def _rows(path: Path, start: int = 0):
    """(行番号, 生の行, dict か None) を 1 行ずつ返す。全行をメモリに持たない（161MB の記録で 1.8GB 使っていた）。"""
    for i, line in enumerate(iter_lines(path)):
        if i < start:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            obj = None
        yield i, line, obj if isinstance(obj, dict) else None


def _correction_marker():
    if correction_pattern_sets is None:
        return lambda s: False
    sets = correction_pattern_sets()
    pats, neg = sets["patterns"], sets["third_party_negation"]

    def hit(s: str) -> bool:
        body = s
        for p in neg:
            body = p.sub("", body)
        return any(p.search(body) for p in pats)
    return hit


def _user_text(obj) -> tuple[str, str] | None:
    """(種別, 本文)。人が打った発話でなければ None。"""
    t = obj.get("type")
    if t == "attachment":
        a = obj.get("attachment") or {}
        if a.get("type") == "queued_command":
            p = a.get("prompt")
            txt = p if isinstance(p, str) else _text_of(_blocks(p))
            # 裏の作業の完了通知も queued_command で届く。人の入力は origin.kind=human
            kind = (a.get("origin") or {}).get("kind")
            if kind not in (None, "human") or txt.lstrip().startswith(_AUTO_PREFIXES):
                return ("自動", txt)
            return ("割り込み", txt)
        return None
    if t != "user" or obj.get("isMeta"):
        return None
    blocks = _blocks((obj.get("message") or {}).get("content"))
    if any(b.get("type") == "tool_result" for b in blocks):
        return None
    txt = _text_of(blocks)
    if not txt.strip():
        return None
    if txt.lstrip().startswith(_AUTO_PREFIXES):
        return ("自動", txt)
    return ("user", txt)


def digest(path: Path, start: int | None, budget: int = 60000) -> str:
    # 1 回目は全体から、行数・窓の境目・先頭の発話・道具の結果の長さだけを取る（行そのものは持たない）
    total, last_fire, first = 0, 0, None
    results = {}  # tool_use_id -> (長さ, is_error)
    for i, line, o in _rows(path):
        total = i + 1
        if _line_is_prior_reflection_fire is not None and _line_is_prior_reflection_fire(line):
            last_fire = i + 1
        if not o:
            continue
        if first is None:
            u = _user_text(o)
            if u and u[0] == "user":
                first = (i, u)
        if o.get("type") == "user":
            for b in _blocks((o.get("message") or {}).get("content")):
                if b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = (_result_len(b.get("content")), bool(b.get("is_error")))
    if start is None:
        start = last_fire
    is_corr = _correction_marker()
    out = [f"# 整理表: {path.name}（全 {total} 行・窓 {start}〜{total - 1} 行・行番号は 0 始まり）"]

    out.append("\n## ① セッション先頭の user 発話")
    if first:
        out.append(f"[{first[0]}] {first[1][1][:USER_HEAD]}")

    tl = []  # (種別, 行, 付帯) — 種別: user / auto / hook / claude / tool / rep
    sigs, errs = Counter(), Counter()
    mems = {}  # memory slug -> 最初に Read した行
    n_user = n_corr = 0
    prev_sig, rep = None, 0

    def flush():
        nonlocal prev_sig, rep
        if prev_sig and rep > 1:
            tl.append(("rep", f"      （同じ呼び出し ×{rep}）", rep - 1))
        prev_sig, rep = None, 0

    for i, _, o in _rows(path, start):
        if not o:
            continue
        ts = (o.get("timestamp") or "")[11:19]
        u = _user_text(o)
        if u:
            flush()
            kind, txt = u
            if kind == "自動":
                tl.append(("auto", f"[{i}] {ts} 自動: {_one_line(txt, 80)}", i))
                continue
            n_user += 1
            mark = ""
            if is_corr(txt):
                n_corr += 1
                mark = " 【訂正候補】"
            body = txt if len(txt) <= USER_CAP else txt[:USER_HEAD] + f"…（+{len(txt) - USER_HEAD}字。全文は --lines {i}-{i}）"
            tl.append(("user", f"[{i}] {ts} {kind}{mark}: {body}", i))
            continue
        t = o.get("type")
        if t == "attachment":
            a = o.get("attachment") or {}
            if a.get("type") == "hook_additional_context":
                flush()
                c = a.get("content")
                c = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                tl.append(("hook", f"[{i}] {ts} hook の注入: {_one_line(c, 120)}", i))
            continue
        if t != "assistant":
            continue
        for b in _blocks((o.get("message") or {}).get("content")):
            if b.get("type") == "text" and b.get("text", "").strip():
                flush()
                txt = b["text"]
                tl.append(("claude", f"[{i}] {ts} Claude（{len(txt)}字）: {_one_line(txt, ASST_HEAD)}", i))
            elif b.get("type") == "tool_use":
                name = b.get("name", "?")
                fp = str((b.get("input") or {}).get("file_path") or "") if isinstance(b.get("input"), dict) else ""
                if name == "Read" and "/memory/" in fp and fp.endswith(".md"):
                    mems.setdefault(Path(fp).stem, i)  # 採用ログの材料: 最初に開いた行
                sig = _signature(name, b.get("input"))
                n, err = results.get(b.get("id"), (0, False))
                sigs[sig] += 1
                if err:
                    errs[sig] += 1
                if sig == prev_sig:
                    rep += 1
                    continue
                flush()
                prev_sig, rep = sig, 1
                tl.append(("tool", f"[{i}] {ts}   → {sig}  ＜結果 {n}字{'・エラー' if err else ''}＞", (i, name, err)))
    flush()

    full = [line for _, line, _ in tl]
    if sum(len(x) + 1 for x in full) <= budget:
        out.append("\n## ② 窓の時系列（user 発話は逐語、Claude は冒頭のみ、道具は 1 行）")
        out += full
    else:
        out.append(f"\n## ② 窓の時系列・畳んだ形（{budget}字を超えたため。user 発話は逐語のまま、"
                   "間の Claude の動きは 1 行に集計。中身は --lines で引く）")
        out += _compact(tl)
    out.append("\n## ③ 集計（窓の中）")
    out.append(f"user 発話 {n_user} 件（訂正候補 {n_corr} 件）・道具の呼び出し {sum(sigs.values())} 回")
    many = [(s, c) for s, c in sigs.most_common() if c >= 3]
    if many:
        out.append("3 回以上の同じ呼び出し:")
        out += [f"  ×{c} {s}" for s, c in many]
    if errs:
        out.append("エラーになった呼び出し:")
        out += [f"  ×{c} {s}" for s, c in errs.most_common()]
    if mems:
        out.append("Read で開いた memory（採用ログの材料・最初に開いた行）:")
        out.append("  " + " / ".join(f"{s} [{i}]" for s, i in mems.items()))
    out.append("\n深く読む所は --lines a-b で生の行を引く。")
    return "\n".join(out)


def _compact(tl) -> list[str]:
    out, grp = [], []

    def close():
        if not grp:
            return
        lines = [x[2] if x[0] != "tool" else x[2][0] for x in grp if x[0] != "rep"]
        claude = [x for x in grp if x[0] == "claude"]
        tools = Counter(x[2][1] for x in grp if x[0] == "tool")
        for x in grp:
            if x[0] == "rep":
                tools["（連続の重複）"] += x[2]
        n_err = sum(1 for x in grp if x[0] == "tool" and x[2][2])
        n_hook = sum(1 for x in grp if x[0] == "hook")
        n_auto = sum(1 for x in grp if x[0] == "auto")
        parts = []
        if claude:
            parts.append(f"Claude 発言 {len(claude)} 回")
        if tools:
            parts.append("道具 " + "・".join(f"{k}{v}" for k, v in tools.most_common(6)))
        if n_err:
            parts.append(f"エラー {n_err}")
        if n_hook:
            parts.append(f"hook の注入 {n_hook}")
        if n_auto:
            parts.append(f"自動通知 {n_auto}")
        rng = f"{min(lines)}-{max(lines)}" if lines else "?"
        out.append(f"   〔{rng} 行: " + "、".join(parts) + "〕")
        if claude:
            out.append("   最後の発言 " + claude[-1][1].strip())
        grp.clear()

    for x in tl:
        if x[0] == "user":
            close()
            out.append(x[1])
        else:
            grp.append(x)
    close()
    return out


def show_lines(path: Path, a: int, b: int, cap: int) -> str:
    out = []
    for i, line, o in _rows(path, a):
        if i > b:
            break
        if not o:
            continue
        t = o.get("type")
        if t == "attachment":
            a_ = o.get("attachment") or {}
            if a_.get("type") in ("queued_command", "hook_additional_context"):
                out.append(f"[{i}] attachment/{a_.get('type')}: {json.dumps(a_.get('prompt') or a_.get('content'), ensure_ascii=False)[:cap]}")
            continue
        if t not in ("user", "assistant"):
            continue
        for blk in _blocks((o.get("message") or {}).get("content")):
            bt = blk.get("type")
            if bt == "text":
                body = blk.get("text", "")
            elif bt == "tool_use":
                body = json.dumps(blk.get("input"), ensure_ascii=False)
            elif bt == "tool_result":
                c = blk.get("content")
                body = c if isinstance(c, str) else _text_of(_blocks(c)) or json.dumps(c, ensure_ascii=False)
            else:
                continue
            more = f"…（+{len(body) - cap}字）" if len(body) > cap else ""
            out.append(f"[{i}] {t}/{bt}: {body[:cap]}{more}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("transcript")
    ap.add_argument("--start", type=int)
    ap.add_argument("--lines", help="a-b[,c-d…]（0 始まり・両端を含む）")
    ap.add_argument("--cap", type=int, default=3000, help="--lines で 1 ブロックに残す字数")
    ap.add_argument("--budget", type=int, default=60000, help="時系列がこの字数を超えたら畳んだ形で出す")
    args = ap.parse_args()
    path = Path(args.transcript)
    if args.lines:  # 複数の範囲は 1 回で引く（範囲ごとに往復しない）: --lines 120-180,400-420
        for rng in args.lines.split(","):
            a, _, b = rng.strip().partition("-")
            print(f"--- {rng.strip()} ---")
            print(show_lines(path, int(a), int(b or a), args.cap))
    else:
        print(digest(path, args.start, args.budget))
    return 0


if __name__ == "__main__":
    sys.exit(main())
