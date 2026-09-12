from pathlib import Path

import pytest

from task_cleanup.tmpfs import MARKER, PREFIX, OwnedBasetemp


def test_registered_red_uses_an_explicit_owned_linux_tmpfs(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()

    assert base.parent.parent.resolve() == Path("/dev/shm").resolve(strict=True)
    assert base.parent.name.startswith(PREFIX)
    assert (base.parent / MARKER).read_text(encoding="utf-8")


def test_basetemp_cleanup_rejects_wrong_owner_token(tmp_path):
    owned = OwnedBasetemp.create(tmp_path)

    with pytest.raises(RuntimeError, match="unverified"):
        owned.remove("wrong-owner-token")
    assert owned.owner.is_dir()

    owned.remove()
    assert not owned.owner.exists()
