"""Integration keeps opaque filesystem names through the shared Git owner."""

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from poise.infrastructure.git_transport import run_git_receipt
from poise.runtime import Poise


@pytest.mark.parametrize("name", [b"valid-\r\n", b"invalid-\xff", b"mixed-\xc3\xa9-\xfe"])
def test_shared_and_native_git_preserve_exact_nul_path_bytes(tmp_path, name):
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    path = b"tests/" + name
    (root / "tests").mkdir()
    (root / os.fsdecode(path)).write_bytes(b"unchanged proof\n")
    arguments = ("ls-files", "--others", "--exclude-standard", "-z")
    expected = path + b"\0"
    oracle = subprocess.check_output(["git", "-C", str(root), *arguments])
    assert oracle == expected
    receipt = run_git_receipt(root, arguments, 10, dict(os.environ))
    assert receipt["actual_exit_code"] == 0
    assert os.fsencode(receipt["stdout"]) == expected
    assert receipt["stderr"] == ""
    context = SimpleNamespace(cfg={"limits": {"git_seconds": 10, "preview_chars": 5}})
    assert os.fsencode(Poise._git(context, root, *arguments)) == expected
