"""受諾検知（bare の ok 等）の注入文は、その合図が承認ではないことを明示する（承認を頼んだ直後の bare の「ok」を
承認と読み、hook と settings を書き換えた例があった。注入文は「User acceptance signal detected」とだけ書いていた）。
ok 以外の「y」「承認」は承認として受ける — なので y・承認 を「承認ではない」側に巻き込まない"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import detect_acceptance_signal as d


def test_reminder_and_skip_notice_say_ok_is_not_approval():
    for s in (d._REMINDER, d._SKIP_NOTICE):
        assert "承認ではない" in s and "ok" in s


def test_not_approval_line_does_not_cover_y_or_shounin():
    line = next(l for l in d._REMINDER.splitlines() if "承認ではない" in l)
    assert "「y」" not in line.split("承認ではない")[0]
    assert "「y」「承認」" in line.split("承認ではない")[1]   # y・承認 は承認として受ける、と同じ行の後半で言う
