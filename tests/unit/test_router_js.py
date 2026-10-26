"""0.7.0 RTE cases of the browser router, run under Node (tests/js/*.test.js)."""
import shutil
import subprocess

import pytest

from tests.conftest import ROOT

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("name", sorted(p.name for p in (ROOT / "tests" / "js").glob("*.test.js")))
def test_js(name):
    r = subprocess.run([NODE, "--test", str(ROOT / "tests" / "js" / name)], capture_output=True, text=True,
                       timeout=600)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
