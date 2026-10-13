import pytest


@pytest.fixture(autouse=True)
def broken_setup():
    raise RuntimeError('Deliberate test infrastructure failure')


def test_probe():
    assert True
