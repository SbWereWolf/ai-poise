from pathlib import Path
import subprocess
import sys
import time

import pytest

from task_cleanup.tmpfs import MARKER, PREFIX, OwnedBasetemp


def test_registered_red_uses_an_explicit_owned_linux_tmpfs(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()

    assert base.parent.parent.resolve() == Path("/dev/shm").resolve(strict=True)
    assert base.parent.name.startswith(PREFIX)
    assert (base.parent / MARKER).read_text(encoding="utf-8")


def test_basetemp_cleanup_rejects_wrong_owner_token(tmp_path):
    owned = OwnedBasetemp.create(tmp_path)

    with pytest.raises(RuntimeError, match="unverified"):
        owned.remove("wrong-owner-token")
    assert owned.owner.is_dir()

    owned.remove()
    assert not owned.owner.exists()


def test_basetemp_watchdog_removes_exact_owner_after_parent_is_killed(tmp_path):
    published = tmp_path / "owned-basetemp-path"
    helper = """
from pathlib import Path
import sys
import time
sys.path.insert(0, sys.argv[2])
from task_cleanup.tmpfs import OwnedBasetemp

owned = OwnedBasetemp.create(Path(sys.argv[1]))
Path(sys.argv[3]).write_text(str(owned.owner), encoding="utf-8")
while True:
    time.sleep(60)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            helper,
            str(tmp_path),
            str(Path(__file__).resolve().parents[1]),
            str(published),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    owner = None
    try:
        deadline = time.monotonic() + 5
        while not published.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not published.exists():
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            assert process.stderr is not None
            raise AssertionError(
                f"helper did not publish its exact owned basetemp: {process.stderr.read()}"
            )
        owner = Path(published.read_text(encoding="utf-8"))
        assert owner.is_dir()

        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while owner.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

        assert not owner.exists(), "watchdog left its exact basetemp after parent death"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if owner is not None and owner.exists():
            marker = owner / MARKER
            token = marker.read_text(encoding="utf-8")
            OwnedBasetemp(tmp_path.resolve(), owner, owner / "pytest", token).remove()
