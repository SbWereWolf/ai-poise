import pytest

from .support import Arrangement


@pytest.fixture
def case(project, tmp_path, monkeypatch):
    value = Arrangement(project, tmp_path, monkeypatch)
    try:
        yield value
    finally:
        value.close()
