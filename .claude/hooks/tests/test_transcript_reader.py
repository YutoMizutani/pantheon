"""会話記録の読み方の試験: 丸ごとメモリに載せないこと、読める内容が変わらないこと。

read_text().splitlines() で丸ごと読むと、記録の約 9 倍のメモリを使い、hook が重なるとマシンが固まる。
"""
import re
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS))

from _transcript import iter_lines, tail_lines  # noqa: E402


def _write(tmp_path, data: bytes) -> Path:
    p = tmp_path / "t.jsonl"
    p.write_bytes(data)
    return p


def test_iter_lines_returns_each_line_without_newline(tmp_path):
    p = _write(tmp_path, b'{"a":1}\n{"b":2}\n')
    assert list(iter_lines(p)) == ['{"a":1}', '{"b":2}']


def test_iter_lines_keeps_unterminated_last_line(tmp_path):
    p = _write(tmp_path, b'{"a":1}\n{"b":')
    assert list(iter_lines(p)) == ['{"a":1}', '{"b":']


def test_iter_lines_accepts_str_path(tmp_path):
    p = _write(tmp_path, b"x\n")
    assert list(iter_lines(str(p))) == ["x"]


def test_iter_lines_does_not_split_on_unicode_line_separators(tmp_path):
    # JSON は U+2028 を生のまま持てる。splitlines() はここで行を割って JSON を壊していた
    p = _write(tmp_path, '{"t":"a b"}\n'.encode("utf-8"))
    assert list(iter_lines(p)) == ['{"t":"a b"}']


def test_iter_lines_survives_invalid_utf8(tmp_path):
    p = _write(tmp_path, b'ok\n\xff\xfe\nend\n')
    lines = list(iter_lines(p))
    assert lines[0] == "ok" and lines[2] == "end" and len(lines) == 3


def test_iter_lines_missing_file_raises_oserror(tmp_path):
    try:
        list(iter_lines(tmp_path / "none.jsonl"))
    except OSError:
        return
    raise AssertionError("OSError expected")


def test_tail_lines_returns_last_n_in_order(tmp_path):
    p = _write(tmp_path, b"1\n2\n3\n4\n")
    assert tail_lines(p, 2) == ["3", "4"]
    assert tail_lines(p, 10) == ["1", "2", "3", "4"]


def test_reading_large_transcript_stays_small(tmp_path):
    """64MB の記録を読んでも、使うメモリは丸ごと読む量より十分小さい。"""
    p = tmp_path / "big.jsonl"
    line = ('{"t":"' + "あ" * 300 + '"}\n').encode("utf-8")
    with open(p, "wb") as f:
        for _ in range(64 * 1024 * 1024 // len(line)):
            f.write(line)
    code = (
        "import json,resource,sys;"
        f"sys.path.insert(0,{str(HOOKS)!r});"
        "from _transcript import iter_lines;"
        "n=sum(1 for l in iter_lines(sys.argv[1]) if json.loads(l));"
        "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"
    )
    out = subprocess.run([sys.executable, "-c", code, str(p)], capture_output=True, text=True, check=True)
    rss = int(out.stdout.strip())
    if sys.platform != "darwin":
        rss *= 1024  # Linux の ru_maxrss は KiB、macOS は byte
    assert rss < 48 * 1024 * 1024, f"peak {rss / 2**20:.0f}MB"


def test_no_hook_reads_whole_file_into_lines():
    """hook が再び丸ごと読みに戻らないこと。"""
    pat = re.compile(r"read_text\([^)]*\)\s*\.splitlines\(\)")
    files = list(HOOKS.glob("*.py")) + list((HOOKS / "local").glob("*.py"))
    bad = [f.name for f in files if pat.search(f.read_text(encoding="utf-8", errors="replace"))]
    assert not bad, bad

