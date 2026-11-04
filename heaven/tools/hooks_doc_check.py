#!/usr/bin/env python3
"""hooks_doc_check — 配線した hook の event と、hook が読む・書く payload キーを公式 hooks 文書と照合する。

Failure it prevents
-------------------
hook が payload から読むキー・stdout に書くキーは、どれも transcript の観察か記憶で決めてきた
（公式文書の引用 0）。文書に無い名前を読む hook は、その値が来ない限り何もせず黙って素通りし、
文書に無いキーを書く hook は harness に無視される。どちらも発火ログには出ない。

Observable signal
-----------------
stdout の 1 行目:
  hooks_doc_check: OK events=<配線 event 数> hooks=<配線あり・実体あり> reads=<照合した読み> writes=<照合した書き> skip=<n> warn=<n>
  hooks_doc_check: NG n=<NG 件数> events=… hooks=… skip=… warn=…
  hooks_doc_check: UNAVAILABLE <理由>
続く行は NG / SKIP / WARN / UNWIRED / MISSING のどれかで始まる（grep できる形）。
exit 0 = OK（WARN・SKIP・UNWIRED・MISSING だけを含む）/ 1 = NG あり / 2 = 判定できない。
2 は「一致している」ではない。推測で埋めない。

対象は CLAUDE_PROJECT_DIR（無ければ cwd、`--project-dir` で上書き）の
`.claude/settings.json`・`.local/settings.local.json`・`.claude/hooks/*.py`・`.local/hooks/*.py`
（`_`・`test_` 始まりは除く。`.archive/`・`tests/` は glob が降りない）。

文書は既定で取得し、`~/.claude/runtime/claude_code_docs/hooks.md` と `hooks.md.json`
（url / sha256 / size / fetched_at / changed_at / inputs_mtime）に保存する。サーバは ETag を返さず
条件付き要求も 304 にならないので、変化は本文の sha256 で見る。取得に失敗し保存があれば保存で照合する。

`--quiet-if-unchanged`（日次の点検から呼ぶ用）: 取得した本文の sha256 と、hook・settings の mtime の最大値が
前回の `hooks.md.json` と両方同じなら、照合せず次の 1 行だけ出して exit 0 にする。
  hooks_doc_check: unchanged sha256=<先頭 12 桁> fetched=<前回の取得日時>
どちらかが違う・取得に失敗した・`--offline` のときは通常の出力。

usage:
  python3 hooks_doc_check.py [--offline <hooks.md>] [--url <URL>] [--json] [--project-dir <dir>]
                             [--quiet-if-unchanged]
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://code.claude.com/docs/en/hooks.md"
SETTINGS_FILES = (".claude/settings.json", ".local/settings.local.json")
HOOK_DIRS = (".claude/hooks", ".local/hooks")
_WIRED_RE = re.compile(r"hooks/(?:local/)?([A-Za-z0-9_]+)\.py")
HANDLER_KEYS = {"type", "command", "timeout", "if", "matcher", "args", "async", "asyncRewake",
                "shell", "onFailure", "statusMessage", "once"}
# 文書の出力表に無くても「出力の dict」だと分かる旧名（読み取り側の判定にだけ使う）
LEGACY_OUT = {"updatedPrompt"}
# これを 1 つでも key に持つ dict を hook の出力とみなす
STRONG_OUT = {"hookSpecificOutput", "hookEventName", "permissionDecision", "decision", "continue",
              "stopReason", "suppressOutput", "systemMessage", "additionalContext"}

FIELD_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|")
HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")
IDENT = re.compile(r"`([a-z][a-z0-9_]*)`")


class Unavailable(Exception):
    """判定できない（exit 2）。"""


# ---------------------------------------------------------------- 文書の取得

def _runtime_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "runtime" / "claude_code_docs"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def fetch(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hooks_doc_check)",
                                               "Accept": "text/markdown, text/plain;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def read_meta() -> dict:
    try:
        meta = json.loads((_runtime_dir() / "hooks.md.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def load_doc(url: str, inputs_mtime: float, notes: list[str]) -> str:
    """取得して保存する。失敗したら保存を使う（WARN を notes に足す）。"""
    rt = _runtime_dir()
    body_p, meta_p = rt / "hooks.md", rt / "hooks.md.json"
    meta = read_meta()
    try:
        text = fetch(url)
    except Exception as e:  # noqa: BLE001 — 失敗は理由ごと出す
        if body_p.exists():
            notes.append(f"WARN fetch failed, using cached {meta.get('fetched_at', '?')}: {e}")
            return body_p.read_text(encoding="utf-8")
        raise Unavailable(f"fetch failed: {url}: {e}") from e
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = _now()
    changed = meta.get("changed_at") if meta.get("sha256") == sha else now
    rt.mkdir(parents=True, exist_ok=True)
    body_p.write_text(text, encoding="utf-8")
    meta_p.write_text(json.dumps({"url": url, "sha256": sha, "size": len(text.encode("utf-8")),
                                  "fetched_at": now, "changed_at": changed,
                                  "inputs_mtime": inputs_mtime}, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    return text


# ---------------------------------------------------------------- 文書から取る

class Doc:
    """hooks.md から event・入力・出力のフィールド名を抜く。"""

    def __init__(self, md: str):
        self.lines = md.splitlines()
        self.heads = self._headings()
        self.events = self._events()
        if not self.events:
            raise Unavailable("doc shape changed: ## Hook events")
        common = self._section(r"Common input fields", 3)
        if not common:
            raise Unavailable("doc shape changed: ### Common input fields")
        self.common_in = {m.group(1) for ln in common if (m := FIELD_ROW.match(ln))}
        self.universal_out = {m.group(1) for ln in self._section(r"JSON output", 3)
                              if (m := FIELD_ROW.match(ln))}
        self.input_top: dict[str, set] = {}
        self.input_nested: dict[str, dict[str, set]] = {}
        self.output: dict[str, set] = {}
        self.tool_tables: dict[str, set] = {}
        self.response_tables: dict[str, set] = {}
        for ev in self.events:
            top, nested = self._input_fields(ev)
            self.input_top[ev] = top | self.common_in
            self.input_nested[ev] = nested
            for anchor, keys in nested.items():
                if (m := re.match(r"(tool_input|tool_response)\[(\w+)\]$", anchor)):
                    tables = self.tool_tables if m.group(1) == "tool_input" else self.response_tables
                    tables.setdefault(m.group(2), set()).update(keys)
            self.output[ev] = (self._output_fields(ev) | self.universal_out
                               | {"hookSpecificOutput", "hookEventName"})
        self.all_out = set().union(*self.output.values()) | LEGACY_OUT

    def _headings(self):
        out, fence = [], False
        for n, ln in enumerate(self.lines):
            if ln.startswith("```"):
                fence = not fence
                continue
            if not fence and (m := HEADING.match(ln)):
                out.append((n, len(m.group(1)), m.group(2)))
        return out

    def _section(self, title_pat: str, level: int) -> list[str]:
        start = None
        for n, lv, text in self.heads:
            if start is None and lv == level and re.fullmatch(title_pat, text):
                start = n
                continue
            if start is not None and lv <= level:
                return self.lines[start + 1:n]
        return self.lines[start + 1:] if start is not None else []

    def _events(self) -> list[str]:
        start = end = None
        for n, lv, t in self.heads:
            if lv == 2 and t == "Hook events" and start is None:
                start = n
            elif start is not None and lv == 2 and end is None:
                end = n
        if start is None:
            return []
        end = end or len(self.lines)
        return [t for n, lv, t in self.heads if lv == 3 and start < n < end]

    @staticmethod
    def _json_blocks(sec):
        buf, inside = [], False
        for ln in sec:
            if ln.startswith("```json"):
                inside, buf = True, []
                continue
            if inside and ln.startswith("```"):
                inside = False
                txt = re.sub(r"^\s*\.\.\.\s*$", "", "\n".join(buf), flags=re.M)
                txt = re.sub(r",(\s*[}\]])", r"\1", txt)
                try:
                    obj = json.loads(txt)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
                continue
            if inside:
                buf.append(ln)

    def _input_fields(self, ev: str):
        sec = self._section(rf"{re.escape(ev)} input", 4)
        top, nested = set(), {}
        anchor, fence, prose = "TOP", False, True
        for ln in sec:
            if ln.startswith("```"):
                fence, prose = not fence, False
                continue
            if fence:
                continue
            if (m := re.match(r"^##### (\w+)", ln)):
                anchor, prose = f"tool_input[{m.group(1)}]", False
                continue
            if (m := re.search(r"Each entry in `(\w+)`", ln)):
                anchor = f"{m.group(1)}[]"
            elif re.search(r"in `tool_response`", ln) and anchor.startswith("tool_input["):
                anchor = anchor.replace("tool_input[", "tool_response[")
            if (m := FIELD_ROW.match(ln)):
                prose = False
                if anchor == "TOP":
                    top.add(m.group(1))
                else:
                    nested.setdefault(anchor, set()).add(m.group(1))
                continue
            if prose:  # 節の先頭から最初の表・例・小見出しまでの散文
                top.update(k for k in IDENT.findall(ln) if k not in ("true", "false", "null"))
        for obj in self._json_blocks(sec):
            top.update(obj.keys())
            for k, v in obj.items():
                if isinstance(v, dict):
                    nested.setdefault(k, set()).update(v.keys())
        return top, nested

    def _output_fields(self, ev: str) -> set:
        sec = self._section(rf"{re.escape(ev)} (decision control|output)", 4)
        out = {m.group(1).split(".")[-1] for ln in sec if (m := FIELD_ROW.match(ln))}
        for obj in self._json_blocks(sec):
            out.update(obj.keys())
            if isinstance(obj.get("hookSpecificOutput"), dict):
                out.update(obj["hookSpecificOutput"].keys())
        return out


# ---------------------------------------------------------------- hook のソースから取る

def _is_json_load(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("load", "loads") and getattr(node.func.value, "id", None) == "json")


def _mentions_stdin(node) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == "stdin" and getattr(n.value, "id", None) == "sys"
               for n in ast.walk(node))


def _key_access(node):
    """`x.get("k")` / `x["k"]` なら (k, x の式) を返す。"""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        return node.args[0].value, node.func.value
    if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)):
        return node.slice.value, node.value
    return None, None


class HookSource:
    """payload 変数・入れ子の変数を固定点まで広げ、読みキー・書きキーを抜く。"""

    def __init__(self, src: str, all_out: set):
        self.tree = ast.parse(src)
        self.all_out = all_out
        self.stdin_funcs = {f.name for f in ast.walk(self.tree)
                            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and _mentions_stdin(f)
                            and any(_is_json_load(n) for n in ast.walk(f))}
        self.funcs = {f.name: [a.arg for a in f.args.args] for f in ast.walk(self.tree)
                      if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.payload: set[str] = set()
        self.sub: dict[str, str] = {}  # 変数 → payload の親キー（tool_input など）
        self.line_vars: set[str] = set()
        for _ in range(6):
            before = (len(self.payload), len(self.sub))
            self._propagate()
            if (len(self.payload), len(self.sub)) == before:
                break
        self.top: set[str] = set()
        self.nested: set[tuple[str, str]] = set()
        self.transcript_keys: set[str] = set()
        self._collect_reads()
        self.writes, self.event_names = self._collect_writes()

    def _resolve(self, node):
        """式が payload か、payload の 1 段下（親キー）か。"""
        if isinstance(node, ast.BoolOp):
            node = node.values[0]
        if isinstance(node, ast.Name):
            if node.id in self.payload:
                return "payload"
            if node.id in self.sub:
                return ("sub", self.sub[node.id])
            return None
        key, recv = _key_access(node)
        if key and self._resolve(recv) == "payload":
            return ("sub", key)
        return None

    def _source_kind(self, v):
        if isinstance(v, ast.BoolOp):
            v = v.values[0]
        if _is_json_load(v):
            return "payload" if _mentions_stdin(v) else "line"
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in self.stdin_funcs:
            return "payload"
        return None

    def _bind(self, name: str, value) -> None:
        kind = self._source_kind(value)
        if kind == "payload":
            self.payload.add(name)
        elif kind == "line":
            self.line_vars.add(name)
        else:
            r = self._resolve(value)
            if r == "payload":
                self.payload.add(name)
            elif isinstance(r, tuple):
                self.sub.setdefault(name, r[1])

    def _propagate(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                self._bind(node.targets[0].id, node.value)
            elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                  and node.value is not None):
                self._bind(node.target.id, node.value)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.funcs:
                # 関数の引数で渡る payload を 1 段追う（同位置の仮引数名を足す）
                params = self.funcs[node.func.id]
                for i, arg in enumerate(node.args):
                    if i < len(params):
                        self._bind(params[i], arg)
                for kw in node.keywords:
                    if kw.arg in params:
                        self._bind(kw.arg, kw.value)

    def _collect_reads(self) -> None:
        for node in ast.walk(self.tree):
            key, recv = _key_access(node)
            if not key:
                continue
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                continue
            r = self._resolve(recv)
            if r == "payload":
                self.top.add(key)
            elif isinstance(r, tuple):
                self.nested.add((r[1], key))
            elif isinstance(recv, ast.Name) and recv.id in self.line_vars:
                self.transcript_keys.add(key)

    def _collect_writes(self):
        keys, names = set(), set()

        def dict_keys(d):
            return [k.value for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]

        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Dict):
                continue
            ks = dict_keys(node)
            # 出力の dict = 強い出力キーを含み、キーの半分以上が文書の出力キー
            # （`decision` を含む記録用の dict をログ 1 行として書く hook があるため）
            if not STRONG_OUT & set(ks) or 2 * len(set(ks) & self.all_out) < len(set(ks)):
                continue
            keys.update(ks)
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "hookSpecificOutput" and isinstance(v, ast.Dict):
                    keys.update(dict_keys(v))
                if (isinstance(k, ast.Constant) and k.value == "hookEventName"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    names.add(v.value)
        return keys, names


# ---------------------------------------------------------------- 配線

def load_settings(project: Path):
    """(配線 {hook: [(event, matcher, file)]}, settings の NG 行, 配線 event の集合, 読めた件数)"""
    wiring: dict[str, list] = {}
    events, raw_events, found = set(), [], 0
    for rel in SETTINGS_FILES:
        try:
            data = json.loads((project / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found += 1
        for ev, groups in (data.get("hooks") or {}).items():
            events.add(ev)
            raw_events.append((rel, ev, groups))
            for g in groups or []:
                for h in g.get("hooks") or []:
                    for name in _WIRED_RE.findall(str(h.get("command", ""))):
                        wiring.setdefault(name, []).append((ev, g.get("matcher"), rel))
    return wiring, events, raw_events, found


def hook_files(project: Path) -> dict[str, Path]:
    out = {}
    for d in HOOK_DIRS:
        for p in sorted((project / d).glob("*.py")):
            if p.name.startswith(("_", "test_")):
                continue
            out.setdefault(p.stem, p)
    return out


def inputs_mtime(project: Path) -> float:
    ts = [0.0]
    for d in HOOK_DIRS:
        ts += [p.stat().st_mtime for p in (project / d).glob("*.py") if p.exists()]
    ts += [(project / r).stat().st_mtime for r in SETTINGS_FILES if (project / r).exists()]
    return max(ts)


def _tables_for(parent: str, wires, doc: Doc):
    """(表のある tool → キー集合, 表の無い tool 名)。matcher なし / `*` は表のある全 tool。

    `tool_input` / `tool_response` の中身は tool ごとに違うので、matcher の tool ごとの表で判定する。
    """
    tables = doc.tool_tables if parent == "tool_input" else doc.response_tables
    have, lack = {}, set()
    for _ev, m, _f in wires:
        if m in (None, "", "*"):
            have.update(tables)
            continue
        for t in str(m).split("|"):
            if t in tables:
                have[t] = tables[t]
            else:
                lack.add(t)
    return have, lack


# ---------------------------------------------------------------- 照合

def check(doc: Doc, project: Path):
    wiring, events, raw_events, found = load_settings(project)
    if not found:
        raise Unavailable(f"no settings readable under {project}")
    files = hook_files(project)
    ng, skip, warn, other = [], [], [], []
    detail: dict[str, dict] = {}
    settings_ng = []
    for rel, ev, groups in raw_events:
        if ev not in doc.events:
            settings_ng.append(f"NG settings event {ev} not in doc ({rel})")
        for g in groups or []:
            for h in g.get("hooks") or []:
                for k in h:
                    if k not in HANDLER_KEYS:
                        settings_ng.append(f"NG settings {rel} {ev} handler key {k} not in doc")
    ng += settings_ng

    n_reads = n_writes = n_hooks = 0
    for name in sorted(wiring):
        wires = wiring[name]
        if name not in files:
            other.append(f"MISSING {name} wired in {wires[0][2]} but file not found")
            continue
        n_hooks += 1
        evs = sorted({w[0] for w in wires if w[0] in doc.events})
        try:
            hs = HookSource(files[name].read_text(encoding="utf-8", errors="replace"), doc.all_out)
        except SyntaxError as e:
            other.append(f"SKIP {name} cannot parse: {e}")
            continue
        d = {"events": evs, "reads": sorted(hs.top), "nested": sorted(f"{p}.{k}" for p, k in hs.nested),
             "writes": sorted(hs.writes), "transcript_keys": sorted(hs.transcript_keys),
             "ng": [], "skip": [], "warn": []}
        doc_in = set().union(*(doc.input_top[e] for e in evs)) if evs else set()
        doc_out = set().union(*(doc.output[e] for e in evs)) if evs else set()
        evs_s = ",".join(evs)
        for k in sorted(hs.top):
            n_reads += 1
            if k not in doc_in:
                d["ng"].append(f"NG {name} reads {k} not in doc({evs_s})")
        for parent, k in sorted(hs.nested):
            if parent in ("tool_input", "tool_response"):
                have, lack = _tables_for(parent, wires, doc)
                if any(k in keys for keys in have.values()):
                    n_reads += 1
                    continue
                if lack or not have:
                    # 表の無い tool（MCP・Skill・MultiEdit 等）のキーかもしれない — 照合不能
                    d["skip"].append(f"SKIP {name} reads {parent}.{k} (no table for {'|'.join(sorted(lack)) or '?'})")
                    continue
                n_reads += 1
                where = ",".join(f"PreToolUse/{t}" for t in sorted(have))
                d["ng"].append(f"NG {name} reads {parent}.{k} not in doc({where})")
            else:
                known = set()
                for e in evs:
                    for anchor, keys in doc.input_nested[e].items():
                        if anchor == parent or anchor.startswith(parent + "["):
                            known |= keys
                if not known:
                    d["skip"].append(f"SKIP {name} reads {parent}.{k} (no table for {parent})")
                    continue
                n_reads += 1
                if k not in known:
                    d["ng"].append(f"NG {name} reads {parent}.{k} not in doc({evs_s})")
        for k in sorted(hs.writes):
            n_writes += 1
            if k not in doc_out:
                d["ng"].append(f"NG {name} writes {k} not in doc({evs_s})")
        for v in sorted(hs.event_names):
            if v not in evs:
                d["ng"].append(f"NG {name} writes hookEventName={v} but wired {evs_s}")
        if "Stop" in evs and "transcript_path" in hs.top and "last_assistant_message" not in hs.top:
            d["warn"].append(f"WARN {name} Stop reads transcript_path; doc recommends last_assistant_message")
        ng += d["ng"]
        skip += d["skip"]
        warn += d["warn"]
        detail[name] = d
    for name in sorted(set(files) - set(wiring)):
        if "sys.stdin" in files[name].read_text(encoding="utf-8", errors="replace"):
            other.append(f"UNWIRED {name}")
    counts = {"events": len(events), "hooks": n_hooks, "reads": n_reads, "writes": n_writes,
              "skip": len(skip), "warn": len(warn)}
    return ng, skip, warn, other, counts, detail, settings_ng


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", help="取得せずこの hooks.md で照合する（保存も更新しない）")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--project-dir")
    ap.add_argument("--quiet-if-unchanged", action="store_true",
                    help="文書の sha256 と hook・settings の mtime が前回と同じなら 1 行だけ出す")
    args = ap.parse_args(argv)
    project = Path(args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    notes: list[str] = []
    try:
        if args.offline:
            md = Path(args.offline).read_text(encoding="utf-8")
        else:
            prev = read_meta()
            mtime = inputs_mtime(project)
            md = load_doc(args.url, mtime, notes)
            sha = hashlib.sha256(md.encode("utf-8")).hexdigest()
            if (args.quiet_if_unchanged and not notes and prev.get("sha256") == sha
                    and prev.get("inputs_mtime") == mtime):
                print(f"hooks_doc_check: unchanged sha256={sha[:12]} fetched={prev.get('fetched_at', '?')}")
                return 0
        doc = Doc(md)
        ng, skip, warn, other, c, detail, settings_ng = check(doc, project)
    except (Unavailable, OSError) as e:
        print(f"hooks_doc_check: UNAVAILABLE {e}")
        return 2
    tail = f"events={c['events']} hooks={c['hooks']}"
    if ng:
        print(f"hooks_doc_check: NG n={len(ng)} {tail} skip={c['skip']} warn={c['warn']}")
    else:
        print(f"hooks_doc_check: OK {tail} reads={c['reads']} writes={c['writes']} "
              f"skip={c['skip']} warn={c['warn']}")
    if args.json:
        sha = hashlib.sha256(md.encode("utf-8")).hexdigest()
        print(json.dumps({"events": sorted({e for d in detail.values() for e in d["events"]}),
                          "doc": {"sha256": sha, "fetched_at": None if args.offline else _now(),
                                  "events": len(doc.events)},
                          "hooks": detail, "settings_ng": settings_ng}, ensure_ascii=False, indent=1))
    else:
        for line in notes + ng + skip + warn + other:
            print(line)
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
