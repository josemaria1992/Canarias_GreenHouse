"""Every packaged browser script parses (a syntax error in one page script silently disables that page)."""
import shutil
import subprocess

import pytest

from tests.conftest import ROOT

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("name", sorted(p.name for p in (ROOT / "src" / "aquacrop" / "web").glob("*.js")))
def test_script_parses(name):
    r = subprocess.run([NODE, "--check", str(ROOT / "src" / "aquacrop" / "web" / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
