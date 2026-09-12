from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

import pytest


_TMPFS = Path("/dev/shm")
_OWNED_BASETEMPS: dict[int, Path] = {}


def pytest_configure(config):
    if config.option.basetemp is not None:
        return
    if not _TMPFS.is_dir() or not os.access(_TMPFS, os.W_OK | os.X_OK):
        raise pytest.UsageError(
            "tests/task_cleanup requires writable /dev/shm for its registered 300-second check"
        )
    base = Path(tempfile.mkdtemp(prefix="ai-poise-task-cleanup-", dir=_TMPFS))
    config.option.basetemp = base
    _OWNED_BASETEMPS[id(config)] = base


def pytest_unconfigure(config):
    base = _OWNED_BASETEMPS.pop(id(config), None)
    if base is not None:
        shutil.rmtree(base)
