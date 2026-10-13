import pytest


@pytest.mark.xfail(reason='An obligation is expected to fail')
def test_probe():
    assert False
