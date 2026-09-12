from copy import deepcopy
from pathlib import Path

import pytest

from conftest import WorkPoise as Poise
from conftest import git, write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError

from .helpers import integration_input, prepare_completed_task, request


def _feature(tree: Path) -> None:
    (tree / "src" / "feature.py").write_text("VALUE = 1\n")


def test_completed_result_integration_uses_live_config_without_digest_gate(project):
    _, source_worktree, source = prepare_completed_task(project, _feature)
    original = Poise(project["config_path"], "worker")
    stored_hash = original.task_queries.record("T1")["config_hash"]
    config = deepcopy(project["cfg"])
    config["automatic_checks"] = []
    config["git"]["push_required"] = False
    config["limits"]["preview_chars"] += 1
    write_json(project["config_path"], config)
    current = WorkTools(Poise(project["config_path"], "worker"))

    assert current.runtime.config_hash != stored_hash
    result = current.invoke(request("integrate", integration_input(project, source)))

    assert result["status"] == "integrated"
    assert not source_worktree.exists()


def test_stale_initial_target_is_rejected_without_touching_source(project):
    tools, source_worktree, source = prepare_completed_task(project, _feature)
    stale = git(project["app"], "rev-parse", "refs/heads/main")
    (project["app"] / "target.txt").write_text("advance\n")
    git(project["app"], "add", "target.txt")
    git(project["app"], "commit", "-m", "chore: advance target")
    target_after = git(project["app"], "rev-parse", "refs/heads/main")
    payload = integration_input(project, source)
    payload["expected_target_commit"] = stale

    with pytest.raises(PoiseError, match="target.*changed|Expected target"):
        tools.invoke(request("integrate", payload))

    assert git(project["app"], "rev-parse", "refs/heads/main") == target_after
    assert source_worktree.exists()


@pytest.mark.parametrize(
    "invalid_state", ["unaccepted", "wrong_source", "dirty_source", "wrong_branch"]
)
def test_accepted_source_identity_and_worktree_are_validated(project, invalid_state):
    tools, source_worktree, source = prepare_completed_task(
        project, _feature, accept=invalid_state != "unaccepted"
    )
    payload = integration_input(project, source)
    if invalid_state == "wrong_source":
        payload["expected_source_commit"] = "f" * 40
    elif invalid_state == "dirty_source":
        (source_worktree / "untracked.txt").write_text("dirty\n")
    elif invalid_state == "wrong_branch":
        git(source_worktree, "branch", "-m", "unexpected-source")
    target_before = git(project["app"], "rev-parse", "refs/heads/main")

    with pytest.raises(PoiseError):
        tools.invoke(request("integrate", payload))

    assert git(project["app"], "rev-parse", "refs/heads/main") == target_before
    assert source_worktree.exists()
