import pytest


@pytest.mark.xfail(reason='An obligation unexpectedly passes')
def test_probe():
    assert True
