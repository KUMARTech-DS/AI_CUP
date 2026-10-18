"""Runs the web/ frontend suite (tests/frontend, node:test) so a plain `pytest` covers it too."""
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).parent / "frontend"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js 20+ not installed")
def test_frontend_suite():
    r = subprocess.run(["node", "--test", str(FRONTEND)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
