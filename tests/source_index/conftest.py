"""Reap only test-owned process groups; a leaked producer is an actual error."""
import os
import signal
import subprocess

import pytest

from .helpers import ACTIVE_SCENES


@pytest.fixture(autouse=True)
def reap_index_clients():
    ACTIVE_SCENES.clear()
    yield
    leaked = []
    for scene in ACTIVE_SCENES:
        for child in scene.peers:
            if child.poll() is None:
                leaked.append(child.pid)
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=3)
    ACTIVE_SCENES.clear()
    assert leaked == [], f'Test-owned processes required teardown termination: {leaked}'
