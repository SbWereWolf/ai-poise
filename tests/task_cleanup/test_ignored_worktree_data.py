"""Real-Git cleanup checks for ignored, site-owned worktree data."""

from pathlib import Path
import stat
import subprocess

import pytest

from batch.helpers import request
from conftest import git
from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from result_integration.helpers import prepare_completed_task

from .helpers import branch_exists, cleanup_input


def _classified(commit, *, required=(), disposable=()):
    packet = cleanup_input(commit)
    packet["ignored_data"] = {
        "required_paths": list(required),
        "disposable_paths": list(disposable),
    }
    return packet


def _prepare_renamed_working_config(project):
    tracked = project["app"] / "src" / "site.settings.json"
    tracked.write_bytes(b'{"site":"product-old"}\n')
    git(project["app"], "add", "src/site.settings.json")
    git(project["app"], "commit", "-m", "Track initial working settings")

    def publish_example(root):
        git(root, "mv", "src/site.settings.json", "src/site.settings.example.json")
        (root / "src" / ".gitignore").write_text(
            "site.settings.json\nnew.settings.json\nlocal.cache\n", encoding="utf-8"
        )

    tools, worktree, commit = prepare_completed_task(
        project, publish_example, accept=False
    )
    worktree = Path(worktree)
    local = worktree / "src" / "site.settings.json"
    local.write_bytes(b'{"site":"private-local-value"}\n')
    local.chmod(0o600)
    assert git(worktree, "status", "--porcelain", "--untracked-files=all") == ""
    assert subprocess.check_output(
        ["git", "-C", str(worktree), "ls-files", "--others", "--ignored", "--exclude-standard"],
        text=True,
    ).strip() == "src/site.settings.json"
    return tools, worktree, commit, local


def test_terminal_cleanup_refuses_to_delete_unknown_ignored_site_data(project):
    tools, worktree, commit, local = _prepare_renamed_working_config(project)
    before = local.read_bytes()
    mode = stat.S_IMODE(local.stat().st_mode)
    cancelled = tools.invoke(request("cancel", {"reason": "Cancel fixture task."}))

    blocked = tools.invoke(request("cleanup", cleanup_input(commit)))

    assert cancelled["status"] == "cancelled"
    assert blocked["status"] == "cleanup_blocked"
    assert blocked["blocker"]["reason"] == "ignored_worktree_data_requires_decision"
    assert blocked["blocker"]["resource"]["kind"] == "worktree"
    assert "private-local-value" not in str(blocked)
    assert local.read_bytes() == before
    assert stat.S_IMODE(local.stat().st_mode) == mode == 0o600
    assert worktree.is_dir() and branch_exists(project)
    assert (project["app"] / "src" / "site.settings.json").read_bytes() == b'{"site":"product-old"}\n'


def test_required_ignored_data_stays_in_worktree_until_manual_recovery(project):
    tools, worktree, commit, local = _prepare_renamed_working_config(project)
    tools.invoke(request("cancel", {"reason": "Cancel fixture task."}))
    packet = request("cleanup", _classified(commit, required=["src/site.settings.json"]))

    blocked = tools.invoke(packet)

    assert blocked["status"] == "cleanup_blocked"
    assert blocked["blocker"]["reason"] == "ignored_worktree_data_requires_decision"
    assert local.read_bytes() == b'{"site":"private-local-value"}\n'
    assert worktree.is_dir() and branch_exists(project)

    protected = project["root"] / "operator-protected-site.settings.json"
    local.rename(protected)
    newly_arrived = worktree / "src" / "new.settings.json"
    newly_arrived.write_bytes(b'{"site":"second-private-value"}\n')
    newly_arrived.chmod(0o600)
    assert git(worktree, "status", "--porcelain", "--untracked-files=all") == ""

    cold = WorkTools(Poise(project["config_path"], "cold-ignored-cleanup-retry"))
    blocked_again = cold.invoke(packet)
    assert blocked_again["status"] == "cleanup_blocked"
    assert blocked_again["blocker"]["reason"] == "ignored_worktree_data_requires_decision"
    assert newly_arrived.read_bytes() == b'{"site":"second-private-value"}\n'
    assert stat.S_IMODE(newly_arrived.stat().st_mode) == 0o600
    assert protected.read_bytes() == b'{"site":"private-local-value"}\n'
    assert (project["app"] / "src" / "site.settings.json").read_bytes() == b'{"site":"product-old"}\n'
    assert worktree.is_dir() and branch_exists(project)

    protected_second = project["root"] / "operator-protected-new.settings.json"
    newly_arrived.rename(protected_second)
    completed = WorkTools(Poise(project["config_path"], "safe-ignored-cleanup-retry")).invoke(packet)
    assert completed["status"] == "cleanup_complete"
    assert protected.read_bytes() == b'{"site":"private-local-value"}\n'
    assert stat.S_IMODE(protected.stat().st_mode) == 0o600
    assert protected_second.read_bytes() == b'{"site":"second-private-value"}\n'
    assert stat.S_IMODE(protected_second.stat().st_mode) == 0o600
    assert not worktree.exists() and not branch_exists(project)
    assert any(item.get("event") == "worktree_cleanup_blocked" for item in completed["history"])


def test_disposable_classification_does_not_cover_unknown_ignored_data(project):
    tools, worktree, commit, local = _prepare_renamed_working_config(project)
    cache = worktree / "src" / "local.cache"
    cache.write_bytes(b"disposable cache\n")
    cache.chmod(0o640)
    tools.invoke(request("cancel", {"reason": "Cancel fixture task."}))

    blocked = tools.invoke(request(
        "cleanup", _classified(commit, disposable=["src/local.cache"])
    ))

    assert blocked["status"] == "cleanup_blocked"
    assert blocked["blocker"]["reason"] == "ignored_worktree_data_requires_decision"
    assert local.read_bytes() == b'{"site":"private-local-value"}\n'
    assert stat.S_IMODE(local.stat().st_mode) == 0o600
    assert cache.read_bytes() == b"disposable cache\n"
    assert stat.S_IMODE(cache.stat().st_mode) == 0o640
    assert (project["app"] / "src" / "site.settings.json").read_bytes() == b'{"site":"product-old"}\n'
    assert worktree.is_dir() and branch_exists(project)


def test_exact_disposable_ignored_cache_can_be_removed(project):
    tools, worktree, commit = prepare_completed_task(
        project,
        lambda root: (root / "src" / ".gitignore").write_text(
            "local.cache\n", encoding="utf-8"
        ),
        accept=False,
    )
    worktree = Path(worktree)
    cache = worktree / "src" / "local.cache"
    cache.write_bytes(b"disposable fixture cache\n")
    tools.invoke(request("cancel", {"reason": "Cancel fixture task."}))

    completed = tools.invoke(request(
        "cleanup", _classified(commit, disposable=["src/local.cache"])
    ))

    assert completed["status"] == "cleanup_complete"
    assert not worktree.exists() and not branch_exists(project)


def test_absent_declared_ignored_path_does_not_create_data(project):
    tools, worktree, commit = prepare_completed_task(
        project,
        lambda root: (root / "src" / ".gitignore").write_text(
            "site.settings.json\n", encoding="utf-8"
        ),
        accept=False,
    )
    tools.invoke(request("cancel", {"reason": "Cancel fixture task."}))

    completed = tools.invoke(request(
        "cleanup", _classified(commit, required=["src/site.settings.json"])
    ))

    assert completed["status"] == "cleanup_complete"
    assert not Path(worktree).exists()
    assert not (project["app"] / "src" / "site.settings.json").exists()


@pytest.mark.parametrize("required,disposable,reason", [
    (["src/site.settings.json"], ["src/site.settings.json"], "overlap"),
    (["../site.settings.json"], [], "path"),
    (["src/site.settings.json", "src/site.settings.json"], [], "duplicate"),
])
def test_invalid_classification_fails_before_cleanup_effect(
    project, required, disposable, reason
):
    tools, worktree, commit, local = _prepare_renamed_working_config(project)
    tools.invoke(request("cancel", {"reason": "Cancel fixture task."}))

    with pytest.raises(PoiseError, match=reason):
        tools.invoke(request(
            "cleanup", _classified(commit, required=required, disposable=disposable)
        ))

    assert local.read_bytes() == b'{"site":"private-local-value"}\n'
    assert worktree.is_dir() and branch_exists(project)


@pytest.mark.parametrize("bad_policy", [
    None,
    {"required_paths": "src/site.settings.json", "disposable_paths": []},
    {"required_paths": [], "disposable_paths": [7]},
    {"required_paths": [], "disposable_paths": [], "extra": []},
])
def test_malformed_classification_fails_before_cleanup_effect(project, bad_policy):
    tools, worktree, commit, local = _prepare_renamed_working_config(project)
    tools.invoke(request("cancel", {"reason": "Cancel fixture task."}))
    packet = cleanup_input(commit)
    packet["ignored_data"] = bad_policy

    with pytest.raises(PoiseError, match="ignored_data"):
        tools.invoke(request("cleanup", packet))

    assert local.read_bytes() == b'{"site":"private-local-value"}\n'
    assert worktree.is_dir() and branch_exists(project)


def test_ignored_symlink_cannot_be_declared_disposable(project):
    tools, worktree, commit = prepare_completed_task(
        project,
        lambda root: (root / "src" / ".gitignore").write_text(
            "site.settings.json\n", encoding="utf-8"
        ),
        accept=False,
    )
    worktree = Path(worktree)
    outside = project["root"] / "operator-site.json"
    outside.write_bytes(b'{"site":"external-private"}\n')
    link = worktree / "src" / "site.settings.json"
    link.symlink_to(outside)
    tools.invoke(request("cancel", {"reason": "Cancel fixture task."}))

    blocked = tools.invoke(request(
        "cleanup", _classified(commit, disposable=["src/site.settings.json"])
    ))

    assert blocked["status"] == "cleanup_blocked"
    assert blocked["blocker"]["reason"] == "ignored_worktree_data_unsafe_type"
    assert link.is_symlink()
    assert outside.read_bytes() == b'{"site":"external-private"}\n'
    assert worktree.is_dir() and branch_exists(project)


def test_unreadable_ignored_file_cannot_be_declared_disposable(project):
    tools, worktree, commit = prepare_completed_task(
        project,
        lambda root: (root / "src" / ".gitignore").write_text(
            "local.cache\n", encoding="utf-8"
        ),
        accept=False,
    )
    worktree = Path(worktree)
    unreadable = worktree / "src" / "local.cache"
    unreadable.write_bytes(b"unreadable fixture bytes\n")
    unreadable.chmod(0)
    tools.invoke(request("cancel", {"reason": "Cancel fixture task."}))

    blocked = tools.invoke(request(
        "cleanup", _classified(commit, disposable=["src/local.cache"])
    ))

    assert blocked["status"] == "cleanup_blocked"
    assert blocked["blocker"]["reason"] == "ignored_worktree_data_unreadable"
    assert unreadable.exists()
    assert stat.S_IMODE(unreadable.stat().st_mode) == 0
    assert worktree.is_dir() and branch_exists(project)
