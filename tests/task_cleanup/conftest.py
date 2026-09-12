from __future__ import annotations

import os
from pathlib import Path

import pytest

from task_cleanup.tmpfs import OwnedBasetemp


_TMPFS = Path("/dev/shm")
_OWNED_BASETEMPS: dict[int, OwnedBasetemp] = {}


def pytest_configure(config):
    if config.option.basetemp is not None:
        return
    if not _TMPFS.is_dir() or not os.access(_TMPFS, os.W_OK | os.X_OK):
        raise pytest.UsageError(
            "tests/task_cleanup requires writable /dev/shm for its registered 300-second check"
        )
    owned = OwnedBasetemp.create(_TMPFS)
    config.option.basetemp = owned.path
    _OWNED_BASETEMPS[id(config)] = owned


def pytest_unconfigure(config):
    owned = _OWNED_BASETEMPS.pop(id(config), None)
    if owned is not None:
        owned.remove()
