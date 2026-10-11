"""Test infrastructure subject: lock-holder deadlines collect actual children."""
import fcntl
import os
import subprocess

import pytest

from requirements_registry.crud_support import FIXTURES
from requirements_registry.test_crud_failures import held_outer_lock


def recorded_children(monkeypatch, *, stuck=False):
    processes = []
    original = subprocess.Popen

    def spawn(arguments, **kwargs):
        arguments = list(arguments)
        if stuck:
            arguments[2] = str(FIXTURES / "crud_stuck_lock_holder.py")
        child = original(arguments, **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", spawn)
    return processes


def assert_collected(child):
    assert child.poll() is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)


def test_holder_without_readiness_is_stopped_and_collected(tmp_path, monkeypatch):
    lock = tmp_path / "bounded.lock"
    children = recorded_children(monkeypatch)
    with lock.open("a+b") as blocker:
        fcntl.flock(blocker, fcntl.LOCK_EX)
        with pytest.raises(TimeoutError, match="did not announce readiness"):
            with held_outer_lock(lock, readiness_seconds=0.05, cleanup_seconds=0.1):
                pytest.fail("a blocked holder cannot announce lock acquisition")
        assert len(children) == 1
        assert_collected(children[0])
    with lock.open("a+b") as released:
        fcntl.flock(released, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_holder_without_exit_is_killed_collected_and_unlocks(tmp_path, monkeypatch):
    lock = tmp_path / "bounded.lock"
    children = recorded_children(monkeypatch, stuck=True)
    with pytest.raises(TimeoutError, match="did not exit; child collected"):
        with held_outer_lock(lock, readiness_seconds=2, cleanup_seconds=0.05):
            assert children[0].poll() is None
    assert len(children) == 1
    assert_collected(children[0])
    assert children[0].returncode == -9
    with lock.open("a+b") as released:
        fcntl.flock(released, fcntl.LOCK_EX | fcntl.LOCK_NB)
