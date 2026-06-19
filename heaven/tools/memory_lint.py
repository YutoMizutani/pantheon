#!/usr/bin/env python3
"""memory_lint — heaven/memory/ の健全性を 1 コマンドで監査する。

防ぐ失敗 (failure-it-prevents):
  - orphan: index (MEMORY*.md) から参照されない memory は auto-load 経路で
    recall 不能 = 書いた学習が silent に死蔵される。
    実例 (原環境): ある判断記録が index 漏れし、却下済み案を再提案しかけた。
  - broken: index が実在しないファイルを指す = index が嘘をつく。
  - frontmatter 不備: name と filename の不一致は [[wikilink]] 解決を壊す。
  - MEMORY.md 肥大: auto-load 上限 (公式: 先頭 200 行 or 25KB の先に達した方) を
    超えると超過分が silent truncate され recall を失う。byte/行 両方の閾値接近を
    早期警告する。

observable-signal: 本スクリプトの exit code (0=clean / 1=issues / 2=crash)
  と件数つきレポート。定期チェック (cron 等) から composable に呼べる。

契約: read-only。修正は提示のみで auto-fix しない。

重大度は「recall を壊すか」で 3 段階 (仕様準拠の押し付けはしない):
  ERROR = recall 破壊 (orphan / broken index ref)
  WARN  = 将来の解決破綻リスク (name が filename と完全別系統 = 二重名,
          auto-load 上限接近)
  INFO  = 実害なし (name 欠落 — stem で [[link]] 解決可 / type が仕様 4 種外 /
          dangling [[wikilink]] — 「後で書くマーカー」仕様)
filename の type プレフィックス慣習 (feedback_X.md の name: X) は不一致と
みなさない。

usage:
  python3 memory_lint.py            # human-readable レポート (INFO 含む)
  python3 memory_lint.py --quiet    # 定期実行用: clean なら 1 行、
                                    # 問題時は 🧠 [memory-lint] マーカー + ERROR/WARN のみ
  python3 memory_lint.py --json     # 機械可読
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
# MEMORY.md は毎セッション auto-load される。公式仕様
# (https://code.claude.com/docs/en/memory): 「先頭 200 行、または先頭 25KB、
# いずれか先に達した方」がロードされ、超過分は silent truncate される。
# byte 上限は公式 25KB に ~600B のヘッドルームを取った保守値を warn 閾値に使う
# (旧コメントの「実測 24.4KB」は出所不明だったため公式 citation に差し替え)。
# 行上限は公式どおり 200 (旧実装は byte しか見ておらず行制約を取りこぼしていた)。
AUTOLOAD_LIMIT_BYTES = 24_400
AUTOLOAD_LIMIT_LINES = 200
# early-warn は「真に限界へ接近」時だけ鳴らす。0.9 だと 195 枚規模の自然な定常域
# (~22.5KB) で毎朝 morning-check が発火し loud×frequent な noise になる
# ([[feedback_observability_only_for_silent_failures]])。0.95 = 23,180B で発火 →
# 公式 25KB の手前 ~1.8KB という実用的 consolidation window を残しつつ定常域は [ok]。
AUTOLOAD_WARN_RATIO = 0.95

VALID_TYPES = {"user", "feedback", "project", "reference"}

_MD_LINK_RE = re.compile(r"\]\(([A-Za-z0-9._\-]+\.md)\)")
_WIKILINK_RE = re.compile(r"\[\[([A-Za-z0-9_\-]+)\]\]")
_NAME_RE = re.compile(r"^name:\s*[\"']?([A-Za-z0-9_\-]+)[\"']?\s*$", re.M)
# タイトル文 name (スペース/日本語入り) を「欠落」と誤報しないための raw 捕捉
_NAME_RAW_RE = re.compile(r"^name:\s*(.+?)\s*$", re.M)
_DESC_RE = re.compile(r"^description:\s*(\S.*)$", re.M)
_TYPE_RE = re.compile(r"^\s*type:\s*[\"']?([A-Za-z\-]+)[\"']?\s*$", re.M)


def normalize_slug(s: str) -> str:
    """[[link]] と filename は - / _ が混在して使われるので同一視する。"""
    return s.strip().lower().replace("-", "_")


def split_frontmatter(text: str) -> str:
    """先頭の --- ... --- ブロックを返す。無ければ空文字。"""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[: end + 4] if end != -1 else ""


def parse_frontmatter(text: str) -> dict:
    fm = split_frontmatter(text)
    name = _NAME_RE.search(fm)
    name_raw = _NAME_RAW_RE.search(fm)
    desc = _DESC_RE.search(fm)
    typ = _TYPE_RE.search(fm)
    return {
        "name": name.group(1) if name else None,
        "name_raw": name_raw.group(1) if name_raw else None,
        "description": desc.group(1).strip() if desc else None,
        "type": typ.group(1) if typ else None,
    }


def name_matches_filename(name: str, stem: str) -> bool:
    """type プレフィックス慣習を許容した name/filename 一致判定。

    feedback_verify_before_negating.md の name: verify-before-negating は一致。
    name: verify-before-claiming (完全別系統) のみ二重名として不一致。
    """
    n, s = normalize_slug(name), normalize_slug(stem)
    return s == n or s.endswith("_" + n)


def extract_index_refs(text: str) -> set[str]:
    return set(_MD_LINK_RE.findall(text))


def extract_wikilinks(text: str) -> set[str]:
    return set(_WIKILINK_RE.findall(text))


def lint(memory_dir: Path = MEMORY_DIR) -> dict:
    index_files = sorted(memory_dir.glob("MEMORY*.md"))
    memory_files = sorted(
        p for p in memory_dir.glob("*.md") if not p.name.startswith("MEMORY")
    )
    index_names = {p.name for p in index_files}

    referenced: set[str] = set()
    for p in index_files:
        referenced |= extract_index_refs(p.read_text(encoding="utf-8"))
    referenced -= index_names  # 分冊同士の相互参照は memory 参照ではない

    existing = {p.name for p in memory_files}
    orphans = sorted(existing - referenced)
    broken = sorted(referenced - existing)

    dual_names: list[dict] = []  # WARN: name が filename と完全別系統
    fm_info: list[dict] = []  # INFO: 実害のない仕様逸脱
    name_slugs: set[str] = set()
    all_wikilinks: set[str] = set()
    for p in memory_files:
        text = p.read_text(encoding="utf-8")
        all_wikilinks |= extract_wikilinks(text)
        fm = parse_frontmatter(text)
        infos = []
        if not fm["name"]:
            if fm["name_raw"]:
                infos.append(
                    f"name がタイトル文で slug でない ([[link]] 参照不能): {fm['name_raw'][:40]!r}"
                )
            else:
                infos.append("name 欠落 (stem で [[link]] 解決可)")
        elif not name_matches_filename(fm["name"], p.stem):
            dual_names.append({"file": p.name, "name": fm["name"]})
        if not fm["description"]:
            infos.append("description 欠落 (recall キーが無い)")
        if fm["type"] and fm["type"] not in VALID_TYPES:
            infos.append(f"type '{fm['type']}' は仕様 4 種外")
        if infos:
            fm_info.append({"file": p.name, "problems": infos})
        if fm["name"]:
            name_slugs.add(normalize_slug(fm["name"]))

    resolvable = name_slugs | {normalize_slug(p.stem) for p in memory_files}
    dangling = sorted(
        w for w in all_wikilinks if normalize_slug(w) not in resolvable
    )

    autoload = memory_dir / "MEMORY.md"
    autoload_bytes = autoload.stat().st_size if autoload.exists() else 0
    autoload_lines = (
        autoload.read_text(encoding="utf-8").count("\n") + 1 if autoload.exists() else 0
    )

    return {
        "memory_files": len(memory_files),
        "index_files": [p.name for p in index_files],
        "orphans": orphans,
        "broken_index_refs": broken,
        "dual_names": dual_names,
        "frontmatter_info": fm_info,
        "dangling_wikilinks_info": dangling,
        "autoload_bytes": autoload_bytes,
        "autoload_limit_bytes": AUTOLOAD_LIMIT_BYTES,
        "autoload_lines": autoload_lines,
        "autoload_limit_lines": AUTOLOAD_LIMIT_LINES,
        # 公式は byte/行 いずれか先に達した方で truncate するので、どちらかが警告域なら warn。
        "autoload_warn": (
            autoload_bytes > AUTOLOAD_LIMIT_BYTES * AUTOLOAD_WARN_RATIO
            or autoload_lines > AUTOLOAD_LIMIT_LINES * AUTOLOAD_WARN_RATIO
        ),
    }


def render(report: dict) -> tuple[str, int]:
    lines: list[str] = []
    issues = 0

    lines.append(
        f"memory_lint: {report['memory_files']} memories, "
        f"index = {', '.join(report['index_files'])}"
    )

    if report["orphans"]:
        issues += len(report["orphans"])
        lines.append(f"\n[ERROR] orphan (index 未参照 = recall 不能) {len(report['orphans'])} 件:")
        lines += [f"  - {f}" for f in report["orphans"]]
    if report["broken_index_refs"]:
        issues += len(report["broken_index_refs"])
        lines.append(f"\n[ERROR] broken index ref (実体なし) {len(report['broken_index_refs'])} 件:")
        lines += [f"  - {f}" for f in report["broken_index_refs"]]
    if report["dual_names"]:
        issues += len(report["dual_names"])
        lines.append(f"\n[WARN] 二重名 (name が filename と完全別系統) {len(report['dual_names'])} 件:")
        lines += [f"  - {i['file']}: name '{i['name']}'" for i in report["dual_names"]]

    pct_b = report["autoload_bytes"] / report["autoload_limit_bytes"] * 100
    pct_l = report["autoload_lines"] / report["autoload_limit_lines"] * 100
    marker = "[WARN]" if report["autoload_warn"] else "[ok]"
    if report["autoload_warn"]:
        issues += 1
    lines.append(
        f"\n{marker} MEMORY.md auto-load: {report['autoload_bytes']:,}B "
        f"/ {report['autoload_limit_bytes']:,}B ({pct_b:.0f}%), "
        f"{report['autoload_lines']} 行 / {report['autoload_limit_lines']} 行 ({pct_l:.0f}%) "
        f"— 公式は byte/行 いずれか先に達した方で truncate"
    )

    if report["frontmatter_info"]:
        lines.append(f"\n[info] frontmatter 仕様逸脱 (実害なし) {len(report['frontmatter_info'])} 件:")
        lines += [
            f"  - {i['file']}: {'; '.join(i['problems'])}"
            for i in report["frontmatter_info"]
        ]
    if report["dangling_wikilinks_info"]:
        lines.append(
            f"\n[info] 未解決 [[wikilink]] {len(report['dangling_wikilinks_info'])} 件 "
            "(仕様上 fine — 将来書くマーカー):"
        )
        lines += [f"  - [[{w}]]" for w in report["dangling_wikilinks_info"]]

    lines.append("\nclean ✓" if issues == 0 else f"\n{issues} issue(s)")
    return "\n".join(lines), (0 if issues == 0 else 1)


def render_quiet(report: dict) -> tuple[str, int]:
    """定期実行用: clean なら 1 行、問題時はマーカー + ERROR/WARN のみ。

    🧠 [memory-lint] を観測したセッションが修復する閉ループの起点。
    """
    problems: list[str] = []
    for f in report["orphans"]:
        problems.append(f"  ERROR orphan (recall 不能): {f}")
    for f in report["broken_index_refs"]:
        problems.append(f"  ERROR broken index ref: {f}")
    for i in report["dual_names"]:
        problems.append(f"  WARN 二重名: {i['file']} (name '{i['name']}')")
    if report["autoload_warn"]:
        problems.append(
            f"  WARN MEMORY.md auto-load {report['autoload_bytes']:,}B "
            f"/ {report['autoload_limit_bytes']:,}B ({report['autoload_lines']}行) — "
            "観測セッションが畳む owner。protocol: auto-load section を 1 つ on-demand 分冊 "
            "(MEMORY-*.md) へ verbatim 退避し、本体にトリガ pointer を残す "
            "(既存例: section 5/6.5/7-10/web)。recall ゼロ損・移送後に再 lint で orphan 0 を確認"
        )
    if not problems:
        return (
            f"ok: {report['memory_files']} memories / index 健全 "
            f"(auto-load {report['autoload_bytes'] / report['autoload_limit_bytes'] * 100:.0f}%)",
            0,
        )
    header = (
        f"🧠 [memory-lint] {len(problems)} 件 — index/recall の健全性が壊れています。"
        "観測したセッションは heaven/tools/memory_lint.py (full 出力) を見て修復:"
    )
    return "\n".join([header, *problems]), 1


def main(argv: list[str]) -> int:
    report = lint()
    if "--json" in argv:
        text, code = render(report)  # exit code は同一基準で算出
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return code
    if "--quiet" in argv:
        text, code = render_quiet(report)
        print(text)
        return code
    text, code = render(report)
    print(text)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        print(f"memory_lint crashed: {exc}", file=sys.stderr)
        sys.exit(2)
