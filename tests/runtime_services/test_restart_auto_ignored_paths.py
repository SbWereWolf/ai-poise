"""Lossless Git path handling at the existing replay workspace owner."""

from pathlib import Path
import subprocess

import pytest

from poise.common import PoiseError
from poise.infrastructure.replay_workspace import GitReplayWorkspace


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args],
                            capture_output=True, text=True, check=True)
    return result.stdout if "-z" in args else result.stdout.strip()


def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-q", "-b", "task")
    git(root, "config", "user.name", "Independent test fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "core.quotePath", "true")
    return root


@pytest.mark.parametrize("name", ["ordinary", "odd\tname", "odd\nname", "é-owner", "владелец"])
@pytest.mark.parametrize("accepted_kind", ["file", "directory"])
def test_ignored_file_directory_collision_preserves_exact_work(tmp_path, name, accepted_kind):
    root = repository(tmp_path)
    path = root / "tests" / name
    path.parent.mkdir()
    if accepted_kind == "file":
        path.write_bytes(b"accepted historical file\n")
    else:
        path.mkdir()
        (path / "accepted.txt").write_bytes(b"accepted historical child\n")
    git(root, "add", "--", "tests")
    git(root, "commit", "-qm", "Accept historical source")
    accepted = git(root, "rev-parse", "HEAD")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    git(root, "rm", "-qr", "--", "tests")
    git(root, "commit", "-qm", "Preserve work before historical replay")
    start = git(root, "rev-parse", "HEAD")
    (root / ".git" / "info" / "exclude").write_text("tests/*\n")
    path.parent.mkdir(exist_ok=True)
    if accepted_kind == "file":
        path.mkdir()
        foreign = path / "foreign.txt"
    else:
        foreign = path
    foreign.write_bytes(b"independent foreign bytes must survive\n")
    git(root, "update-ref", "refs/poise/replay/test", start)
    assert git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""
    index = (root / ".git" / "index").read_bytes()
    owner = GitReplayWorkspace(git, {"worktree": str(root), "branch": "task"}, root)
    with pytest.raises(PoiseError, match="Ignored files collide"):
        owner.checkout_accepted(accepted, tree, start)
    assert foreign.read_bytes() == b"independent foreign bytes must survive\n"
    assert (root / ".git" / "index").read_bytes() == index
    assert git(root, "rev-parse", "HEAD") == start
    assert git(root, "symbolic-ref", "--short", "HEAD") == "task"
    assert git(root, "rev-parse", "refs/poise/replay/test") == start
    assert git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""


@pytest.mark.parametrize("name", ["ordinary", "odd\tname", "odd\nname", "é-owner", "владелец"])
def test_preservation_scope_reads_actual_git_path(tmp_path, name):
    root = repository(tmp_path)
    path = root / "tests" / name
    path.parent.mkdir()
    path.write_bytes(b"accepted source\n")
    git(root, "add", "--", "tests")
    git(root, "commit", "-qm", "Accept historical source")
    base = git(root, "rev-parse", "HEAD")
    path.write_bytes(b"owned committed task work\n")
    git(root, "add", "--", "tests")
    git(root, "commit", "-qm", "Save owned task work")
    start = git(root, "rev-parse", "HEAD")
    owner = GitReplayWorkspace(git, {
        "worktree": str(root), "branch": "task", "base": base,
        "contract": {"stage_contracts": [{"allowed_paths": ["tests/**"]}]},
    }, root)
    assert owner.inspect_preservation({}) == start
    assert path.read_bytes() == b"owned committed task work\n"
    assert git(root, "rev-parse", "HEAD") == start
