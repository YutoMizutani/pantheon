#!/usr/bin/env python3
"""precommit_privacy — commit の前に、stage した変更へ個人情報・個人の作業履歴・ローカル層の中身が
入っていないかを調べ、見つかれば commit を止める（git の pre-commit から呼ぶ）。

フレーム層は公開されるので、ローカル層で育った改善を昇格させるときに、作業の経緯（セッション ID・
その日の出来事・user の発言）やローカルのプロジェクトの名前が一緒に入り込みやすい。

段は 2 つ:
  1. 文字の型で決まるもの（必ず走る）: ホームのパス・ログイン名・git の名前とメール・ローカル IP・
     17〜20 桁の ID・セッション ID・`projects/<実在するプロジェクト名>`・ignore されるはずの `.local/` の
     ファイル・`.local/precommit-deny.txt` の語（1 行 1 つの正規表現。この環境だけの語を足す置き場）。
     語彙は環境から求める — このファイルに特定の人の名前は書かない。
  2. 意味を読まないと決まらないもの（`claude` があれば走る）: stage した差分を `claude -p` に渡し、
     個人の作業の経緯・ローカル層のプロジェクトの中身・個人情報を判定させる。

    python3 heaven/tools/precommit_privacy.py            # stage した変更を調べる（pre-commit から）
    PANTHEON_PRIVACY_CLAUDE=0 git commit ...             # 2 段目を飛ばす（claude が使えないとき）
    git commit --no-verify ...                           # 両方飛ばす（中身を自分で確かめたときだけ）

外から基準を足す（別のリポジトリの hook から呼ぶとき）:
    --policy <ファイル>        2 段目の判定に足す基準の文（繰り返し可）
    --deny <ファイル>          1 段目に足す禁止語（1 行 1 つの正規表現・繰り返し可）
    --projects-root <dir>     `projects/<名前>` を内部プロジェクトとみなす repo（既定は commit する repo）
    --allow-author-name       git の user.name（作者本人）を著者表記として書くのを許す
"""
import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MAX_DIFF_CHARS = 60000
CLAUDE_TIMEOUT = 240


def git(*args: str, root: Path) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True).stdout


def added_lines(diff: str) -> list[tuple[str, str]]:
    """unified diff から (ファイル, 足した行) を返す。"""
    out, f = [], ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            f = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((f, line[1:]))
    return out


def local_projects(root: Path) -> list[str]:
    pdir = root / "projects"
    if not pdir.is_dir():
        return []
    return sorted(p.name for p in pdir.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and p.name != "example-infra")


def patterns(root: Path, opts=None) -> list[tuple[str, re.Pattern]]:
    pats: list[tuple[str, str]] = []
    home = os.path.expanduser("~")
    if home not in ("", "/"):
        pats.append(("ホームのパス", re.escape(home)))
    login = getpass.getuser()
    if login:
        pats.append(("ログイン名", rf"(?<![A-Za-z0-9_]){re.escape(login)}(?![A-Za-z0-9_])"))
    keys = ("user.email",) if opts and opts.allow_author_name else ("user.name", "user.email")
    for key in keys:
        v = git("config", key, root=root).strip()
        if v:
            pats.append((f"git の {key}", re.escape(v)))
            if key == "user.name":
                pats += [(f"git の {key} の一部", rf"(?i)\b{re.escape(w)}\b") for w in v.split() if len(w) >= 3]
    pats += [
        ("ローカル IP", r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
        ("17〜20 桁の ID", r"(?<!\d)\d{17,20}(?!\d)"),
        ("セッション ID", r"\bsid[ =:]+[0-9a-f]{6,}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    ]
    names = local_projects(projects_root(root, opts))
    if names:
        pats.append(("ローカルのプロジェクト", r"\bprojects/(?:" + "|".join(map(re.escape, names)) + r")\b"))
    for deny in [root / ".local" / "precommit-deny.txt"] + [Path(d) for d in (opts.deny if opts else [])]:
        if not deny.is_file():
            continue
        for line in deny.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                pats.append(("この環境の禁止語", line.strip()))
    return [(label, re.compile(p)) for label, p in pats]


def projects_root(root: Path, opts=None) -> Path:
    return Path(opts.projects_root) if opts and opts.projects_root else root


def stage1(root: Path, files: list[str], lines: list[tuple[str, str]], opts=None) -> list[str]:
    hits = []
    for f in files:
        parts = f.split("/")
        if ".local" in parts[:-1] and f != ".local/README.md":
            hits.append(f"{f}: ignore されるはずのローカル層（.local/）のファイル")
    pats = patterns(root, opts)
    for f, text in lines:
        for label, rx in pats:
            m = rx.search(text)
            if m:
                hits.append(f"{f}: {label}「{m.group(0)}」 — {text.strip()[:120]}")
    return hits


PROMPT = """あなたは公開リポジトリへの commit を止める検査役です。標準入力は、これから commit する差分のうち足した行だけです（「### ファイル名」の下に行）。
このリポジトリは、誰でも clone して使う汎用の仕組み（フレーム層）です。作者個人の環境の情報は、別のローカル層に置く決まりです。
次に当たる行を全部挙げてください:
1. 個人情報: 人の名前・アカウント名・メールアドレス・住所・電話番号・端末名・認証情報。
2. 個人の作業の経緯: 特定のセッションや日に起きた出来事の記録（「◯月◯日のセッションで〜を誤った」）、user の発言の引用、特定の人の運用の決まり。
3. ローカル層のプロジェクトの中身: 次の名前のプロジェクトや、その中の機能・ファイル・決まりへの言及: {projects}
当たらないもの: 仕組みの一般的な説明、`<name>` のような置き換え前の例、仕組み自体がいつ変わったかの記録（「2026-10-25 に退役」）、汎用の道具やサービスの名前、仕組みが防ぐ失敗の型を一般化して書いたもの（「知らせを読み流して書き込み禁止の場所に書いた例があった」のように、いつ・誰の・どのプロジェクトかを特定できない動機の説明）。
2 に当たるのは、日付・セッション ID・ストーリー ID・発言の引用・プロジェクト名など、特定の人の特定の出来事に結び付く手がかりを含む行だけ。
{extra}出力は JSON だけ: {{"findings": [{{"file": "...", "text": "当たった行の抜粋", "kind": "1|2|3", "why": "理由を短く"}}]}}。無ければ {{"findings": []}}。"""


def chunks(lines: list[tuple[str, str]]) -> list[str]:
    """差分を MAX_DIFF_CHARS ごとの塊にする（切り捨てない — 後ろのファイルを見落とさないため）。"""
    out, body, size, cur = [], [], 0, None
    for f, text in lines:
        add = ([f"### {f}"] if f != cur else []) + [text]
        n = sum(len(x) + 1 for x in add)
        if body and size + n > MAX_DIFF_CHARS:
            out.append("\n".join(body))
            body, size = [f"### {f}"], len(f) + 5
            add = [text]
            n = len(text) + 1
        body += add
        size += n
        cur = f
    if body:
        out.append("\n".join(body))
    return out


def judge(prompt: str, diff: str) -> list[str]:
    try:
        r = subprocess.run(
            ["claude", "-p", "--setting-sources", "", "--tools", "", "--model", "sonnet",
             "--no-session-persistence", "--output-format", "text", prompt],
            input=diff, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
            cwd=os.path.expanduser("~"))
    except subprocess.TimeoutExpired:
        return ["Claude の判定が時間切れ（PANTHEON_PRIVACY_CLAUDE=0 で 2 段目だけ飛ばせる）"]
    m = re.search(r"\{.*\}", r.stdout, re.S)
    try:
        found = json.loads(m.group(0))["findings"] if m else None
    except (ValueError, KeyError):
        found = None
    if found is None:
        return [f"Claude の判定を読めない（終了コード {r.returncode}）: {(r.stdout or r.stderr).strip()[:200]}"]
    kinds = {"1": "個人情報", "2": "個人の作業の経緯", "3": "ローカル層のプロジェクト", "4": "追加の基準"}
    return [f"{x.get('file')}: {kinds.get(str(x.get('kind')), x.get('kind'))} — {x.get('why')} — {str(x.get('text'))[:120]}"
            for x in found]


def extra_criteria(opts) -> str:
    out = []
    if opts and opts.allow_author_name:
        out.append("このリポジトリの作者本人の名前が、著者表記・LICENSE・パッケージの authors・リポジトリの URL に出るのは 1 に当たらない。")
    for p in (opts.policy if opts else []):
        f = Path(p)
        if f.is_file():
            out.append(f.read_text(encoding="utf-8").strip())
    return ("追加の基準（上の 1〜3 に加えて、これに当たる行も挙げる。kind は当てはまる番号か \"4\"）:\n" + "\n\n".join(out) + "\n") if out else ""


def stage2(root: Path, lines: list[tuple[str, str]], opts=None) -> tuple[list[str], str]:
    if os.environ.get("PANTHEON_PRIVACY_CLAUDE") == "0":
        return [], "2 段目（Claude の判定）は PANTHEON_PRIVACY_CLAUDE=0 で飛ばした"
    if not lines:
        return [], ""
    if not shutil.which("claude"):
        return [], "claude が無いので 2 段目は飛ばした"
    prompt = PROMPT.format(projects=", ".join(local_projects(projects_root(root, opts))) or "（なし）",
                           extra=extra_criteria(opts))
    parts = chunks(lines)
    with ThreadPoolExecutor(max_workers=min(4, len(parts))) as ex:
        results = list(ex.map(lambda d: judge(prompt, d), parts))
    note = f"差分を {len(parts)} つに分けて判定した" if len(parts) > 1 else ""
    return [h for r in results for h in r], note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", action="append", default=[])
    ap.add_argument("--deny", action="append", default=[])
    ap.add_argument("--projects-root")
    ap.add_argument("--allow-author-name", action="store_true")
    opts = ap.parse_args()
    root = Path(git("rev-parse", "--show-toplevel", root=Path.cwd()).strip() or ".")
    # 差分を取れなかったのに空の差分として通すと、検査をすり抜ける。git が失敗したら止める
    diffs = []
    for args in (("--name-only",), ("-U0", "--no-color")):
        r = subprocess.run(["git", "diff", "--cached", "--diff-filter=ACMR", *args], cwd=root,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[privacy] stage した差分を読めない（git diff の終了コード {r.returncode}）: {r.stderr.strip()[:200]}",
                  file=sys.stderr)
            return 1
        diffs.append(r.stdout)
    files, lines = diffs[0].split(), added_lines(diffs[1])
    hits = stage1(root, files, lines, opts)
    ai, note = stage2(root, lines, opts)
    if note:
        print(f"[privacy] {note}", file=sys.stderr)
    if not hits and not ai:
        return 0
    print("[privacy] 公開リポジトリに入れない情報が stage されています。直してから commit し直してください。", file=sys.stderr)
    for h in hits:
        print(f"  [型] {h}", file=sys.stderr)
    for h in ai:
        print(f"  [意味] {h}", file=sys.stderr)
    print("  （誤検出なら .local/precommit-deny.txt を見直す／中身を確かめた上で git commit --no-verify）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
