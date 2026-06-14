#!/usr/bin/env python3
"""stall_detector — 「止まっているスレッド」を検知して SessionStart で申し出を促す。

設計 (2026-06-14, user と co-design):
  - 価値: 個人プロジェクトは興味が消えて死ぬのでなく、次の一手が「面倒/怖い/詰まり/
    再入コスト」で止まり、活性化エネルギーを誰も払わず死ぬ。検知器が停止点を surface し、
    親 Claude が摩擦を推定して「申し出」を出す (診断でなく申し出 = 誤検知保険)。
  - 分業: この検知器は decision-free な evidence (どの unit が何日 stalled か) だけを出す。
    摩擦推定と申し出文の生成は親 Claude が in-session でやる (worker は evidence, 結論は親)。
  - 土台: morning-check に相乗りせず独立。SessionStart で発火、ledger を incremental 更新。

検知の穴 (probe で判明) を全部畳んだ v2 信号:
  (a) 主信号 = jsonl の Edit/Write イベント (= 実際に一緒に編集した)。Read だけの監査セッションは
      last_worked を更新しない → 「全部眺めるだけのセッションが全員の時計をリセット」を回避。
  (b) 補助 = filesystem mtime。daemon (logs/runtime/status/*.log/*-wal/*.sqlite) と
      build/dep (target/.venv/node_modules/dist/build...) を除外。fs mtime は罠だらけなので seed 専用。
  (c) サブプロジェクト粒度 = 最近接の CLAUDE.md を持つ祖先 dir を unit にする
      (projects/behavior でなく projects/behavior/operantkit)。
  (d) [撤去済 2026-06-15] 編集 vs 実行の2軸は run 信号(logs/outputs mtime)が不忠実で誤検知したため
      flagging からは外した (編集鮮度だけを信頼)。run は ledger に収集のみ (将来用・現在未使用)。

state: ~/.claude/runtime/stall_ledger.json (runtime, repo 外)。code は heaven/ (agent 領域)。

Opt-in (フレーム層に git で載せる場合): このファイル自体は環境非依存 (JSONL_DIR はリポパスから導出)。
各環境は settings.local.json (ローカル層・gitignore) に下記 SessionStart hook を1行足すまで発火しない:
  "SessionStart":[{"hooks":[{"type":"command",
    "command":"python3 $CLAUDE_PROJECT_DIR/heaven/tools/stall_detector.py --hook","timeout":20}]}]
コードは git・有効化はローカル = signals.json と同じ二層 opt-in。
"""
import os, sys, json, time, glob, re
from datetime import datetime

REPO = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.expanduser("~/Developer/llm")
PROJ = os.path.join(REPO, "projects")
# transcript dir のスラッグ = リポ絶対パスの非英数字を '-' に (Claude Code の命名規則)。環境非依存。
JSONL_DIR = os.path.expanduser("~/.claude/projects/" + re.sub(r"[^a-zA-Z0-9]", "-", REPO))
STATE = os.path.expanduser("~/.claude/runtime/stall_ledger.json")

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# build/dep/vcs noise — pruned everywhere
PRUNE = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
         "target", ".next", ".cache", ".archive", ".pytest_cache", ".ruff_cache", "tmp"}
# daemon/runtime segments — never count as "authoring"
DAEMON_SEG = {"runtime", "status"}
RUN_SEG = {"logs", "outputs"}          # "did we actually run it" signal
MAXDEPTH = 4                            # CLAUDE.md units live near the top

EDIT_THRESHOLD = 14    # days since last edit -> stalled
RUN_THRESHOLD = 21     # edited recently but not run/validated in this many days -> stalled
TOP = 5


JUNK_FILES = {".DS_Store", "Thumbs.db", ".envrc", ".gitignore", ".gitkeep"}
NOTES_SEG = {"notes", "sandbox", "readmelater", "drafts", "chatgpt-import", "reference"}
RUN_MIN_EDIT_AGE = 7   # run-staleness は「編集も N 日以上 cold」のときだけ発火 (昨日触ったものは出さない)


def _noise_file(f):
    return (f in JUNK_FILES
            or f.endswith((".log", ".status", "-wal", "-shm", ".pid", ".lock")) or ".sqlite" in f)


def _stallable(unit_rel):
    """純ドキュメント/notes は寝てて当然。ビルド/実行 marker か TODO を持つ unit だけ stall 対象。"""
    if set(unit_rel.split(os.sep)) & NOTES_SEG:
        return False
    d = os.path.join(REPO, unit_rel)
    for m in ("Taskfile.yml", "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "TODO.md"):
        if os.path.exists(os.path.join(d, m)):
            return True
    for sub in ("tools", "apps", "src"):
        if os.path.isdir(os.path.join(d, sub)):
            return True
    return False


def _to_epoch(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _depth(path):
    return os.path.relpath(path, PROJ).count(os.sep)


def unit_roots():
    """全 unit root (= CLAUDE.md を持つ dir) を長い順に。top-level も fallback として含む。"""
    roots = set()
    if not os.path.isdir(PROJ):
        return []
    for top in os.listdir(PROJ):
        p = os.path.join(PROJ, top)
        if not os.path.isdir(p):
            continue
        roots.add(os.path.realpath(p))
        for dp, dn, fs in os.walk(p):
            if _depth(dp) >= MAXDEPTH:
                dn[:] = []
            dn[:] = [x for x in dn if x not in PRUNE]
            if "CLAUDE.md" in fs:
                roots.add(os.path.realpath(dp))
    return sorted(roots, key=len, reverse=True)


def unit_of(abspath, roots):
    ab = os.path.realpath(abspath)
    for r in roots:
        if ab == r or ab.startswith(r + os.sep):
            return os.path.relpath(r, REPO)
    return None


def _classify(relpath_segments):
    segs = set(relpath_segments)
    if segs & RUN_SEG:
        return "run"
    if segs & DAEMON_SEG:
        return "daemon"
    return "edit"


def seed_from_fs(ledger, roots):
    """初回のみ: fs mtime で edit/run を近似 seed。以後 jsonl edit が上書きしていく。"""
    for top in os.listdir(PROJ):
        p = os.path.join(PROJ, top)
        if not os.path.isdir(p):
            continue
        for dp, dn, fs in os.walk(p):
            dn[:] = [x for x in dn if x not in PRUNE]
            for f in fs:
                fp = os.path.join(dp, f)
                u = unit_of(fp, roots)
                if not u:
                    continue
                try:
                    m = os.lstat(fp).st_mtime
                except OSError:
                    continue
                rel_inside = os.path.relpath(fp, REPO).split(os.sep)
                kind = _classify(rel_inside)
                e = ledger.setdefault(u, {})
                if kind == "run":
                    if m > e.get("run", 0):
                        e["run"] = m
                elif kind == "edit" and not _noise_file(f):
                    if m > e.get("edit", 0):
                        e["edit"] = m
                        e["edit_file"] = os.path.relpath(fp, os.path.join(REPO, u))


def update_from_jsonl(ledger, roots, cursor):
    """cursor 以降に書かれた jsonl だけ読み、Edit/Write イベントで last_edit を更新。"""
    if not os.path.isdir(JSONL_DIR):
        return
    for fp in glob.glob(os.path.join(JSONL_DIR, "*.jsonl")):
        try:
            if os.path.getmtime(fp) <= cursor:
                continue
            with open(fp, "r", errors="ignore") as fh:
                for line in fh:
                    if '"tool_use"' not in line or "projects/" not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    ts = _to_epoch(obj.get("timestamp")) or os.path.getmtime(fp)
                    content = (obj.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for it in content:
                        if not isinstance(it, dict) or it.get("type") != "tool_use":
                            continue
                        if it.get("name") not in EDIT_TOOLS:
                            continue
                        inp = it.get("input") or {}
                        path = inp.get("file_path") or inp.get("notebook_path") or ""
                        if "projects/" not in path:
                            continue
                        u = unit_of(path if os.path.isabs(path) else os.path.join(REPO, path), roots)
                        if not u:
                            continue
                        e = ledger.setdefault(u, {})
                        if ts > e.get("edit", 0):
                            e["edit"] = ts
                            e["edit_file"] = os.path.relpath(path, u) if u in path else os.path.basename(path)
        except OSError:
            continue


def compute_stalls(ledger):
    now = time.time()
    out = []
    for u, d in ledger.items():
        if not _stallable(u):
            continue
        e, r = d.get("edit"), d.get("run")
        ed = (now - e) / 86400 if e else None
        rd = (now - r) / 86400 if r else None
        reason = None
        if ed is not None and ed >= EDIT_THRESHOLD:
            reason = f"{ed:.0f}日 編集なし"
        # run-staleness ブランチは撤去 (user 訂正 2026-06-15): logs/outputs の mtime は「実際に動かしたか」
        # の忠実な信号でなく、編集が最近(operantkit=9d)でも誤検知した。編集鮮度だけを信頼する。
        if reason:
            out.append({"unit": u, "reason": reason, "edit_days": ed, "run_days": rd,
                        "file": d.get("edit_file", ""), "warmth": ed if ed is not None else 999})
    # warmest-first: 「ついさっき冷めた=まだ記憶が温かい」を優先 (古い墓場でなく回収可能な stall)
    out.sort(key=lambda x: x["warmth"])
    # 1つの top-level project の実験墓場で溢れないよう per-project 上限
    picked, per_proj = [], {}
    for s in out:
        key = "/".join(s["unit"].split(os.sep)[:2])   # projects/<top>
        if per_proj.get(key, 0) >= 2:
            continue
        per_proj[key] = per_proj.get(key, 0) + 1
        picked.append(s)
        if len(picked) >= TOP:
            break
    return picked


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(state, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def scan():
    state = load()
    ledger = state.get("ledger", {})
    roots = unit_roots()
    cursor = state.get("last_scan", 0)
    if not ledger:
        seed_from_fs(ledger, roots)          # 初回: fs seed, jsonl 履歴は読まない
    else:
        update_from_jsonl(ledger, roots, cursor)
    state["ledger"] = ledger
    state["last_scan"] = time.time()
    save(state)
    return state, ledger


def surface(mark=True):
    state, ledger = scan()
    today = datetime.now().strftime("%Y-%m-%d")
    surfaced = state.setdefault("surfaced_on", {})
    stalls = [s for s in compute_stalls(ledger) if surfaced.get(s["unit"]) != today]
    if not stalls:
        return ""
    lines = ["🪤 止まっているスレッド (stall detector — 毎日1回/unit)"]
    for s in stalls:
        f = f" — 最後: {s['file']}" if s["file"] else ""
        lines.append(f"- {s['unit']} — {s['reason']}{f}")
    lines.append("→ Claude: 各 unit の TODO/README/最終状態を読み、摩擦タイプを推定して "
                 "「申し出」を1つ出す (診断でなく申し出)。仕様で静止/意図的放棄ならスルー。")
    if mark:
        for s in stalls:
            surfaced[s["unit"]] = today
        save(state)
    return "\n".join(lines)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "--surface"
    try:
        if arg == "--scan":
            _, ledger = scan()
            print(f"scanned: {len(ledger)} units tracked")
        elif arg == "--json":
            scan()
            print(json.dumps(compute_stalls(load().get("ledger", {})), ensure_ascii=False, indent=1))
        elif arg == "--hook":
            text = surface(mark=True)
            if text:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "SessionStart", "additionalContext": text}}, ensure_ascii=False))
        else:  # --surface
            text = surface(mark=False)
            print(text or "(no stalls)")
    except Exception as ex:
        # 自分の停止を silent にしない: hook 出力に loud に出す (dogfood)
        msg = f"🪤 [stall-detector ERROR] {type(ex).__name__}: {ex}"
        if arg == "--hook":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": msg}}, ensure_ascii=False))
        else:
            print(msg)


if __name__ == "__main__":
    main()
