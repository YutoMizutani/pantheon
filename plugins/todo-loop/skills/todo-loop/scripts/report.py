#!/usr/bin/env python3
"""todo-loop の報告 PDF — 無人セッションが書いた報告（JSON）を 1 起動分の PDF にまとめる。送信はしない（runner が send.sh に渡す）。

無人セッションは、終わり際に「報告の置き場」へ JSON を 1 つ書く。この道具はその置き場の JSON を集め、施策ごとに 8 項目
（課題・やったこと・まとめ・つまづいたこと・工夫したところ・何が嬉しいのか・発展可能性・完了したかどうか）を並べた HTML を作り、
手元の Chrome（Chromium）で PDF に印刷する。PDF の末尾には毎回、TODO.md の承認待ち（awaiting-approval）の一覧と各ストーリーの
外部作用 欄を載せる。

  python3 report.py build --kind run --dir <報告の置き場> --title "..." --out x.pdf --todo <TODO.md> [--fallback S-12 S-13]
  python3 report.py check <報告の json>     # 無人セッションが書いた JSON を検査する（OK なら終了コード 0）

JSON の形（配列。1 要素 = 1 施策。値は文字列か文字列の配列）:
  [{"施策": "一言の題", "ストーリー": ["S-12"], "課題": "...", "やったこと": "...", "まとめ": "...",
    "つまづいたこと": "...（無ければ「無し」）", "工夫したところ": "...", "何が嬉しいのか": "...", "発展可能性": "...",
    "完了したかどうか": "完了 / 一部（残り）/ 未完了（理由）"}]

--fallback に挙げた ID のうち JSON が無いものは、TODO.md の見出し・範囲・証跡・引き継ぎから「報告なし」と明記して埋める。
HTML は PDF と同じ名前（拡張子 .html）で残す。Chrome が見つからなければ HTML だけ作って終了コード 3 を返す。
Chrome の場所: 環境変数 TODO_LOOP_CHROME → macOS の標準の位置 → PATH 上の chromium / google-chrome。
"""
import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys

FIELDS = ["課題", "やったこと", "まとめ", "つまづいたこと", "工夫したところ", "何が嬉しいのか", "発展可能性", "完了したかどうか"]
KIND_LABEL = {"create": "TODO 出し", "run": "TODO ラン"}
HEAD_RE = r"^## (S-\d+) — (.+?) \| (調査|実装) \| (\w+) \| ([\w-]+)\s*$"
MAC_CHROMES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
PATH_CHROMES = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"]


def find_chrome():
    env = os.environ.get("TODO_LOOP_CHROME", "")
    if env:
        return env if os.path.exists(env) or shutil.which(env) else None
    for p in MAC_CHROMES + [os.path.expanduser("~" + p) for p in MAC_CHROMES]:
        if os.path.exists(p):
            return p
    for n in PATH_CHROMES:
        p = shutil.which(n)
        if p:
            return p
    return None


def load_entries(path):
    d = json.load(open(path, encoding="utf-8"))
    return d if isinstance(d, list) else d.get("entries", [d])


def check(path):
    try:
        entries = load_entries(path)
    except Exception as e:
        return [f"JSON として読めない: {e}"]
    errs = [] if entries else ["項目が 0 件"]
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errs.append(f"{i}: 要素が object でない"); continue
        if not str(e.get("施策", "")).strip():
            errs.append(f"{i}: 施策 が無い")
        if not e.get("ストーリー") or not isinstance(e.get("ストーリー"), list):
            errs.append(f"{i}: ストーリー（ID の配列）が無い")
        for f in FIELDS:
            v = e.get(f, "")
            if not (" ".join(map(str, v)) if isinstance(v, list) else str(v)).strip():
                errs.append(f"{i}: {f} が空")
    return errs


def stories(todo):
    """TODO.md の全ストーリー: [{id, 題, 状態, body}]"""
    if not todo or not os.path.exists(todo):
        return []
    text = open(todo, encoding="utf-8").read()
    head = re.compile(HEAD_RE, re.M)
    out = []
    for m in head.finditer(text):
        nxt = re.compile(r"^## ", re.M).search(text, m.end())
        out.append(dict(id=m.group(1), 題=m.group(2), 状態=m.group(5), body=text[m.end(): nxt.start() if nxt else len(text)]))
    return out


def field(body, name, multiline=False):
    """`- name:` 欄の値。multiline なら次の `- 欄:` か見出しまでの続きの行も含める"""
    lines = body.splitlines()
    for i, l in enumerate(lines):
        m = re.match(rf"^- {re.escape(name)}:\s*(.*)$", l)
        if not m:
            continue
        val = [m.group(1)]
        if multiline:
            for nl in lines[i + 1:]:
                if re.match(r"^- [^\s:]+:", nl) or nl.startswith("## ") or nl.strip() == "---":
                    break
                val.append(nl)
        return "\n".join(val).strip()
    return ""


def fallback_entry(sid, by_id):
    s = by_id.get(sid)
    note = "（このストーリーのセッションは報告を書かなかった。TODO.md の記録から機械的に埋めた）"
    if not s:
        s = dict(id=sid, 題="（TODO.md に見出しが無い）", 状態="?", body="")
    return {
        "施策": s["題"], "ストーリー": [sid], "報告なし": True,
        "課題": field(s["body"], "範囲") or note, "やったこと": field(s["body"], "証跡") or "記録なし", "まとめ": note,
        "つまづいたこと": field(s["body"], "引き継ぎ") or "記録なし", "工夫したところ": "記録なし", "何が嬉しいのか": "記録なし",
        "発展可能性": "記録なし", "完了したかどうか": f"TODO.md の状態: {s['状態']}",
    }


def para(t):
    t = "\n".join(f"・{x}" for x in t) if isinstance(t, list) else str(t)
    return "".join(f"<p>{html.escape(line)}</p>" for line in t.splitlines() if line.strip())


CSS = """
@page { size: A4; margin: 14mm 13mm; }
body { font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP", sans-serif; font-size: 10.5pt; color: #1d1d1f; line-height: 1.6; }
h1 { font-size: 16pt; margin: 0 0 4px; } .sub { color: #666; font-size: 9pt; margin-bottom: 14px; }
.toc { border: 1px solid #ddd; border-radius: 6px; padding: 8px 12px; margin-bottom: 16px; font-size: 9.5pt; }
.card { border: 1px solid #d0d0d0; border-radius: 8px; padding: 10px 14px; margin: 0 0 14px; }
.card h2 { font-size: 13pt; margin: 0 0 2px; } .ids { color: #555; font-size: 9pt; margin-bottom: 6px; }
.nr { background: #fff3cd; border: 1px solid #e0c060; padding: 2px 6px; border-radius: 4px; font-size: 9pt; }
dt { font-weight: 700; color: #0b5cad; margin-top: 7px; } dd { margin: 2px 0 0 0; } dd p { margin: 0 0 3px; }
.done { font-weight: 700; }
.approval { border-top: 2px solid #999; margin-top: 20px; padding-top: 8px; }
.approval h2 { font-size: 13pt; } .approval h3 { font-size: 11pt; margin: 10px 0 2px; }
pre { white-space: pre-wrap; word-break: break-all; background: #f6f6f6; border: 1px solid #e0e0e0; border-radius: 4px; padding: 6px 8px; font-size: 8.5pt; }
"""


def approvals_html(all_stories):
    waits = [s for s in all_stories if s["状態"] == "awaiting-approval"]
    out = ["<div class='approval' id='awaiting-approval'><h2>承認待ち（awaiting-approval）の一覧</h2>"]
    if not waits:
        out.append("<p>承認待ちのストーリーは無い。</p>")
    else:
        out.append(f"<p>{len(waits)} 件。外部作用 欄の内容でよければ、TODO.md の見出しの状態を <code>approved</code> に書き換える。</p>")
        for s in waits:
            ext = field(s["body"], "外部作用", multiline=True) or "（外部作用 欄が無い）"
            out.append(f"<h3>{html.escape(s['id'])} — {html.escape(s['題'])}</h3><div>外部作用:</div><pre>{html.escape(ext)}</pre>")
    out.append("</div>")
    return "".join(out)


def build(kind, rdir, title, out, todo, fallback):
    all_stories = stories(todo)
    by_id = {s["id"]: s for s in all_stories}
    entries, have = [], set()
    for p in sorted(glob.glob(os.path.join(rdir, "*.json")), key=os.path.getmtime):  # 書いた順
        try:
            es = load_entries(p)
        except Exception as e:
            es = [{"施策": os.path.basename(p), "ストーリー": [], "報告なし": True, **{f: f"報告の JSON が読めない: {e}" for f in FIELDS}}]
        for e in es:
            if not isinstance(e, dict):
                continue
            have.update(e.get("ストーリー") or [])
            entries.append(e)
    for sid in fallback or []:
        if sid not in have:
            entries.append(fallback_entry(sid, by_id))
            have.add(sid)
    toc = "".join(f"<li>{html.escape(str(e.get('施策', '')))}（{html.escape(', '.join(e.get('ストーリー') or []))}）— "
                  f"{html.escape(str(e.get('完了したかどうか', ''))[:60])}{'【報告なし】' if e.get('報告なし') else ''}</li>" for e in entries)
    cards = []
    for e in entries:
        dl = "".join(f"<dt>{f}</dt><dd{' class=done' if f == '完了したかどうか' else ''}>{para(e.get(f, ''))}</dd>" for f in FIELDS)
        nr = '<span class="nr">報告なし・TODO.md から補完</span>' if e.get("報告なし") else ""
        cards.append(f"<div class='card'><h2>{html.escape(str(e.get('施策', '')))}</h2>"
                     f"<div class='ids'>{html.escape(', '.join(e.get('ストーリー') or []))} {nr}</div><dl>{dl}</dl></div>")
    body = "".join(cards) if entries else "<p>この回の報告は 0 件。</p>"
    doc = (f"<!doctype html><html lang='ja'><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{CSS}</style></head>"
           f"<body><h1>{html.escape(title)}</h1><div class='sub'>{KIND_LABEL[kind]}の報告・{len(entries)} 件</div>"
           f"<div class='toc'><ol>{toc}</ol></div>{body}{approvals_html(all_stories)}</body></html>")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    src = os.path.splitext(os.path.abspath(out))[0] + ".html"
    open(src, "w", encoding="utf-8").write(doc)
    chrome = find_chrome()
    if not chrome:
        print(f"[report] Chrome が見つからない（TODO_LOOP_CHROME で指定できる）。HTML だけ作った: {src}", file=sys.stderr)
        return None, src, len(entries)
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={os.path.abspath(out)}", f"file://{src}"], check=True, capture_output=True, timeout=120)
    if not os.path.exists(out) or not os.path.getsize(out):
        raise SystemExit("[report] PDF が空")
    return out, src, len(entries)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--kind", choices=KIND_LABEL, required=True)
    b.add_argument("--dir", required=True)
    b.add_argument("--title", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--todo", default="", help="補完と承認待ちの一覧に読む TODO.md")
    b.add_argument("--fallback", nargs="*")
    c = sub.add_parser("check")
    c.add_argument("path")
    a = ap.parse_args()
    if a.cmd == "build":
        pdf, src, n = build(a.kind, a.dir, a.title, a.out, a.todo, a.fallback)
        print(f"[report] {pdf or src}（{n} 件）")
        sys.exit(0 if pdf else 3)
    errs = check(a.path)
    print("\n".join(errs) if errs else "OK")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
