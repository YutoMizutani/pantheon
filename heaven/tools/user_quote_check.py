"""渡す前の成果物（HTML・md・txt）に、user の発言の引用・user を出典にした書き方が入っていないかを検査する。

成果物の根拠は一次ソースで書く。user の発言を根拠や訂正の経緯として載せると、言っていないこと
（範囲を広げた言い換え）まで user が言ったように見える。

止めるもの:
  1. user を主語・出典にした書き方（「user「」「user の実機観測」「ユーザーの申告」「あなたの観測」など）
  2. このセッションの user の発言と 10 文字以上一致する部分（逐語の引用）

使い方: python3 heaven/tools/user_quote_check.py <ページ> [<ページ> ...] [--transcript <jsonl>]
  --transcript を省くと、このリポジトリのセッション記録のうち更新が最も新しいものを読む。
終了コード: 問題なし 0 / 見つかった 1。
"""
import argparse, glob, html, json, os, re, sys

ATTR = re.compile(r"user\s*[「『の]|user\s*申告|ユーザー\s*[「『の]|本人の申告|あなたの(?:観測|申告|発言)|実機観測")
MIN = 14  # 10 では自分の文と user の文の偶然の重なりを拾った


def page_text(path):
    s = open(path, encoding="utf-8").read()
    if path.endswith((".html", ".htm")):
        s = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", s)
        s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return s


def norm(s):
    return re.sub(r"\s+", "", s)


def user_texts(jsonl):
    out = []
    for line in open(jsonl, encoding="utf-8", errors="ignore"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        # isMeta = Skill の本文などの差し込み、isCompactSummary = 要約で再開したときの私の要約（どちらも user の発言ではない）
        if d.get("type") != "user" or d.get("isMeta") or d.get("isCompactSummary"):
            continue
        c = (d.get("message") or {}).get("content")
        body = c if isinstance(c, str) else "".join(
            b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")
        body = re.sub(r"(?s)<[a-z_-]+>.*?</[a-z_-]+>", " ", body)  # system-reminder などの差し込みを除く
        if not body.strip() or body.lstrip().startswith(("Stop hook feedback:", "[SYSTEM", "Tool loaded")):
            continue
        for ln in body.splitlines():
            ln = ln.strip()
            if ln.startswith(">"):  # user が私の文を貼った行は user の発言ではない
                continue
            ln = re.sub(r"\S+://\S+", " ", ln).strip()  # URL の部分は発言でない（中のキャラ名などの識別子が成果物の見出しと一致する）
            if len(norm(ln)) >= MIN:
                out.append(ln)
    return out


def latest_transcript():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    slug = re.sub(r"[^A-Za-z0-9]", "-", root)
    fs = glob.glob(os.path.expanduser(f"~/.claude/projects/{slug}/*.jsonl"))
    return max(fs, key=os.path.getmtime) if fs else None


def check(path, utter):
    bad = []
    t = page_text(path)
    for m in ATTR.finditer(t):
        bad.append(f"user を主語・出典にした書き方: …{t[max(0, m.start() - 20):m.end() + 30].strip()}…")
    nt = norm(t)
    for u in utter:
        nu = norm(u)
        for i in range(0, len(nu) - MIN + 1):
            w = nu[i:i + MIN]
            if len(re.findall(r"[ァ-ヴー・]", w)) >= MIN * 0.6:  # 用語（カタカナ）だけの一致は引用と数えない
                continue
            if w in nt:
                bad.append(f"user の発言と一致: 「{u[:60]}」")
                break
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="+")
    ap.add_argument("--transcript")
    a = ap.parse_args()
    tr = a.transcript or latest_transcript()
    utter = user_texts(tr) if tr else []
    if not tr:
        print("[user_quote_check] セッション記録が見つからない。逐語一致の検査は省いた", file=sys.stderr)
    n = 0
    for p in a.pages:
        for b in check(p, utter):
            print(f"NG {p}: {b}"); n += 1
    print("OK" if n == 0 else f"{n} 件。user の発言は成果物に載せず、一次ソースを根拠に書き直す")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
