"""Run script-style hook tests under pytest.

Most files here are scripts (`python3 <file>`, no `def test_`). Plain pytest imports
them as modules, finds 0 tests, and reports "no tests ran" (or INTERNALERROR on an
import-time sys.exit) -- a silent false green. This makes each script-style file one
item that passes iff `python3 <file>` exits 0. pytest-style files are left alone."""
import re
import subprocess
import sys

import pytest

_PYTEST_STYLE = re.compile(r"^\s*(async\s+)?def test_|^class Test", re.M)


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makemodule(module_path, parent):
    if _PYTEST_STYLE.search(module_path.read_text(encoding="utf-8", errors="replace")):
        return None  # normal pytest module
    return ScriptFile.from_parent(parent, path=module_path)


class ScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(self, name=self.path.stem)


class ScriptItem(pytest.Item):
    def runtest(self):
        r = subprocess.run([sys.executable, str(self.path)], capture_output=True,
                           text=True, cwd=str(self.path.parent), timeout=300)
        if r.returncode != 0:
            raise AssertionError(f"exit {r.returncode}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")

    def reportinfo(self):
        return self.path, None, f"script: {self.path.name}"
