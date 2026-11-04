"""渡す前の成果物（HTML・md）から、出典・確度の札が無い言い切りの文を列挙する。

二次資料（記事・まとめ・検索の要約）の記述を、一次ソースで確かめないまま事実として書くのを止める検査。
文（HTML は p・li・表の行、md は段落・箇条書き）ごとに、仕組み・数値・確定を言い切る語があるのに、
同じ文に出典・確度の札が無いものを NG にする。表の行は、表の見出しか直後の注に札があれば受け継ぐ。

NG の直し方は 2 つ: 一次ソースを取って札（出典名）を付ける / 「記事」「推測」「未確認」と書く。
札が付いていても、要約が出典の範囲を越えていないか（一部 → 全部）はこの検査では分からない。
判断の根拠になる文は、要約と並べて出典の原文を引く（.claude/rules/common/user-statement-scope.md）。

札の語は既定の汎用語に、プロジェクトの語を足せる: --tokens <ファイル>（1 行 1 語。正規表現可）。

使い方: python3 heaven/tools/source_audit.py <成果物> [...] [--tokens <ファイル>]
終了コード: 0 = NG なし / 1 = NG あり
"""
import argparse, html, re, sys

BASE_SRC = [r"出典", r"一次ソース", r"公式", r"仕様書", r"ドキュメント", r"https?://", r"引用", r"実測", r"実機",
            r"推測", r"記事", r"未確認", r"確かめていない", r"見つかっていない", r"非公開", r"記録"]
CLAIM = re.compile(r"確定|必ず|常に|出ます|出る|落と|上が|上げ|下が|増え|増加|減少|できる|できない|使え|挿せ|"
                   r"効果|倍|%|価値|義務|禁止|必要|不要|違反|適用され|対象外")
HTML_BLOCK = re.compile(r"(?s)<(p|li|tr|h[1-6])\b[^>]*>(.*?)</\1>")


def load_src(tokens_file):
    pats = list(BASE_SRC)
    if tokens_file:
        pats += [ln.strip() for ln in open(tokens_file, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    return re.compile("|".join(pats))


def text(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def audit_html(s, src):
    s = re.sub(r"(?s)<(script|style|svg)\b.*?</\1>", " ", s)
    tables = []
    for tm in re.finditer(r"(?s)<table\b.*?</table>", s):
        # 横に流す包み（<div class="scroll"> 等）を閉じてから注記を置く形も、表の注記として読む
        after = re.match(r'(?s)\s*(?:</div>\s*)*<p class="note">(.*?)</p>', s[tm.end():])
        head = re.search(r"(?s)<tr\b[^>]*>(.*?)</tr>", tm.group(0))
        ok = bool(src.search(text((head.group(1) if head else "") + " " + (after.group(1) if after else ""))))
        tables.append((tm.start(), tm.end(), ok))
    out = []
    for m in HTML_BLOCK.finditer(s):
        if m.group(1).startswith("h"):
            continue
        if m.group(1) == "tr" and any(a <= m.start() < b and ok for a, b, ok in tables):
            continue
        out.append(text(m.group(2)))
    return out


def audit_md(s):
    s = re.sub(r"(?s)```.*?```", " ", s)
    out = []
    for blk in re.split(r"\n\s*\n", s):
        for ln in blk.splitlines():
            ln = ln.strip()
            if re.match(r"^([-*+]|\d+\.)\s", ln):
                out.append(ln)
        para = " ".join(l.strip() for l in blk.splitlines() if l.strip() and not re.match(r"^([-*+]|\d+\.|#|\|)", l.strip()))
        if para:
            out.append(para)
    return out


def audit(path, src):
    s = open(path, encoding="utf-8").read()
    blocks = audit_html(s, src) if path.endswith((".html", ".htm")) else audit_md(s)
    return [t[:110] for t in blocks if len(t) >= 12 and CLAIM.search(t) and not src.search(t)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--tokens")
    a = ap.parse_args()
    src = load_src(a.tokens)
    n = 0
    for p in a.paths:
        for b in audit(p, src):
            print(f"札なし {p}: {b}"); n += 1
    print("OK" if n == 0 else f"{n} 件。一次ソースを取って札を付けるか、記事・推測・未確認と書く")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
