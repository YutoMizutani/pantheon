#!/usr/bin/env python3
"""precommit_privacy の 1 段目（文字の型）が、拾うべき物を拾い、一般的な説明は通すことを確かめる。

見本の値は架空で、このファイル自体が検査に当たらないよう実行時に組み立てる。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import precommit_privacy as pp  # noqa: E402


def make_root() -> Path:
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Taro Yamakawa"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "taro@example.com"], cwd=root, check=True)
    (root / "projects" / "secretproj").mkdir(parents=True)
    (root / "projects" / "example-infra").mkdir(parents=True)
    (root / ".local").mkdir()
    (root / ".local" / "precommit-deny.txt").write_text("# comment\ninternalthing\n")
    return root


def hits(root: Path, text: str, files=("x.py",)) -> list[str]:
    return pp.stage1(root, list(files), [(files[0], text)])


def main() -> int:
    root = make_root()
    home = os.path.expanduser("~")
    must_hit = [
        f"path = '{home}/Developer/repo'",
        "2026-01-01 s" + "id 0dead0ff で読み流した",
        "session " + "-".join(["0" * 8, "0" * 4, "4" + "0" * 3, "8" + "0" * 3, "0" * 12]),
        "connect to " + ".".join(["192", "168", "99", "99"]),
        "thread " + "1" * 19,
        "see projects/secretproj/CLAUDE.md",
        "author: taro@example.com",
        "Yamakawa wrote this",
        "uses internalthing here",
    ]
    must_pass = [
        "projects/<name>/CLAUDE.md を読む",
        "projects/example-infra/ をコピーする",
        "2026-10-25 に退役した hook",
        "version 1.2.3 and port 8080",
        "fingerprint = sha1(source)",
    ]
    bad = [t for t in must_hit if not hits(root, t)] + [t for t in must_pass if hits(root, t)]
    local_file = pp.stage1(root, ["heaven/tools/w/.local/x.sh", ".local/README.md"], [])
    if len(local_file) != 1 or ".local/README.md" in local_file[0]:
        bad.append(f".local の判定: {local_file}")
    for b in bad:
        print("FAIL:", b)
    print("ok" if not bad else f"{len(bad)} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
