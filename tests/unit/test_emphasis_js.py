"""Runs the JavaScript tests for the highlight splitter, when Node is installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).resolve().parents[1] / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_emphasis_js_tests_pass() -> None:
    result = subprocess.run(
        ["node", "--test", *map(str, sorted(JS_TESTS.glob("*.test.mjs")))],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
