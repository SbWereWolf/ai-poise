from pathlib import Path
from types import SimpleNamespace

from task_cleanup import conftest as fixture_config


class FakeOwnedBasetemp:
    def __init__(self, path: Path):
        self.path = path
        self.removed = False

    def remove(self):
        self.removed = True


def test_pytest_hooks_own_only_the_implicit_basetemp(monkeypatch, tmp_path):
    owned = FakeOwnedBasetemp(tmp_path / "owned" / "pytest")
    observed_roots = []

    def create(root):
        observed_roots.append(root)
        return owned

    monkeypatch.setattr(fixture_config.OwnedBasetemp, "create", create)
    implicit = SimpleNamespace(option=SimpleNamespace(basetemp=None))

    fixture_config.pytest_configure(implicit)

    assert observed_roots == [Path("/dev/shm")]
    assert implicit.option.basetemp == owned.path
    assert fixture_config._OWNED_BASETEMPS[id(implicit)] is owned

    fixture_config.pytest_unconfigure(implicit)

    assert owned.removed is True
    assert id(implicit) not in fixture_config._OWNED_BASETEMPS

    external = tmp_path / "external" / "pytest"
    explicit = SimpleNamespace(option=SimpleNamespace(basetemp=external))

    fixture_config.pytest_configure(explicit)
    fixture_config.pytest_unconfigure(explicit)

    assert explicit.option.basetemp == external
    assert observed_roots == [Path("/dev/shm")]
    assert id(explicit) not in fixture_config._OWNED_BASETEMPS
