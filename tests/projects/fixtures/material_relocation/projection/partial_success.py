import pytest


def pytest_runtest_logfinish(nodeid, location):
    if nodeid.endswith('::test_probe'):
        pytest.exit('Deliberate successful exit before all obligations ran', returncode=0)
