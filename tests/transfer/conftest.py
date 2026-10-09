"""Explicit external-tool inputs for the compact-recovery integration boundary."""
from pathlib import Path

import pytest


def pytest_addoption(parser):
    group = parser.getgroup('compact-recovery')
    group.addoption('--recovery-node', help='Absolute configured Node executable')
    group.addoption('--recovery-tool', help='Absolute workspace-recover entry point')


@pytest.fixture
def recovery_tool(request):
    values = [request.config.getoption('--recovery-node'),
              request.config.getoption('--recovery-tool')]
    if any(value is None for value in values):
        pytest.fail('Configure --recovery-node and --recovery-tool; not product RED')
    paths = [Path(value) for value in values]
    if any(not path.is_absolute() or not path.is_file() for path in paths):
        pytest.fail('Configured recovery executable is unavailable; not product RED')
    return [str(path) for path in paths]
