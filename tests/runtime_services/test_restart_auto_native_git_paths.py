"""Actual Poise Git transport must preserve exact native path boundaries."""

import subprocess
from types import SimpleNamespace

import pytest

from poise.common import PoiseError
from poise.infrastructure.replay_workspace import GitReplayWorkspace
from poise.runtime import Poise


def raw_git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def native_git(root, *args):
    context = SimpleNamespace(cfg={"limits": {"git_seconds": 30, "preview_chars": 2000}})
    return Poise._git(context, root, *args)


def changed_repository(tmp_path, name):
    root = tmp_path / "repository"
    root.mkdir()
    raw_git(root, "init", "-q", "-b", "task")
    raw_git(root, "config", "user.name", "Independent native transport fixture")
    raw_git(root, "config", "user.email", "fixture@example.invalid")
    path = root / "tests" / name
    path.parent.mkdir()
    path.write_bytes(b"accepted source\n")
    raw_git(root, "add", "--", "tests")
    raw_git(root, "commit", "-qm", "Accept source")
    base = raw_git(root, "rev-parse", "HEAD").decode().strip()
    path.write_bytes(b"owned committed work\n")
    raw_git(root, "add", "--", "tests")
    raw_git(root, "commit", "-qm", "Save owned task work")
    return root, path, base


@pytest.mark.parametrize("name,expected", [
    ("ordinary", b"tests/ordinary\0"),
    ("odd\nname", b"tests/odd\nname\0"),
    ("odd\rname", b"tests/odd\rname\0"),
    ("odd\r\nname", b"tests/odd\r\nname\0"),
])
def test_native_nul_path_stream_preserves_literal_bytes(tmp_path, name, expected):
    root, path, base = changed_repository(tmp_path, name)
    arguments = ("diff", "--name-only", "-z", base, "HEAD")
    assert raw_git(root, *arguments) == expected
    assert native_git(root, *arguments).encode("utf-8") == expected
    assert path.read_bytes() == b"owned committed work\n"


@pytest.mark.parametrize("name,allowed,permitted", [
    ("ordinary", "tests/ordinary", True),
    ("odd\nname", "tests/odd\nname", True),
    ("odd\rname", "tests/odd\rname", True),
    ("odd\r\nname", "tests/odd\r\nname", True),
    ("odd\rname", "tests/odd\nname", False),
    ("odd\r\nname", "tests/odd\nname", False),
])
def test_native_preservation_scope_distinguishes_cr_and_lf(tmp_path, name, allowed, permitted):
    root, path, base = changed_repository(tmp_path, name)
    head = raw_git(root, "rev-parse", "HEAD").decode().strip()
    index = (root / ".git" / "index").read_bytes()
    owner = GitReplayWorkspace(native_git, {
        "worktree": str(root), "branch": "task", "base": base,
        "contract": {"stage_contracts": [{"allowed_paths": [allowed]}]},
    }, root)
    if permitted:
        assert owner.inspect_preservation({}) == head
    else:
        with pytest.raises(PoiseError, match="out-of-scope"):
            owner.inspect_preservation({})
    assert raw_git(root, "rev-parse", "HEAD").decode().strip() == head
    assert (root / ".git" / "index").read_bytes() == index
    assert path.read_bytes() == b"owned committed work\n"
