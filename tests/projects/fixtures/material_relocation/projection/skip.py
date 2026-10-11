import pytest


@pytest.mark.skip(reason='An obligation is skipped')
def test_probe():
    assert False
