"""Repository-local workspaces and guarded relocation without Task replacement."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import write_json
from ownership.test_worktree_free_proof import _prepare, _start
from projects.test_update import installed_project, update_tools, work_tools, project_revision
from runtime_services.test_task_restart import restart
from poise.common import load_config
from poise.modules.foundation.errors import PoiseError


def root_request(config_path, revision, root):
    return {
        "schema": "project-worktree-root-1",
        "request_id": "repository-worktrees",
        "config_path": str(config_path),
        "expected_revision": revision,
        "manifest_edits": [{"path": ["paths", "worktrees"], "value": str(root)}],
        "process_updates": [], "state_relocation": None,
        "probe_repository": True,
        "receipt_path": "operations/repository-worktrees.json",
        "reason": "Place Task worktrees inside their repository.",
        "authorization": "The user authorized the exact workspace-root correction.",
    }


def test_explicit_repository_root_preserves_same_task_on_workspace_upgrade(project):
    tools, task = _prepare(project)
    initial = _start(tools, task)
    assert initial["worktree"] is None
    current = tools.runtime.task_queries.record(task["id"])
    born = restart(tools, task["id"], current["version"])
    process = deepcopy(project["process"])
    process["worktree_required"] = True
    edited = tools.invoke(request("task", {
        "action": "edit", "request_id": "require-worktree", "task_id": task["id"],
        "expected_revision": born["revision"], "patch": {"process": process}, "remove": [],
    }))
    tools.invoke(request("task", {
        "action": "ready", "request_id": "ready-worktree", "task_id": task["id"],
        "expected_revision": edited["revision"],
    }))
    resumed = _start(tools, {"id": task["id"]})
    assert resumed["task"] == task["id"]
    assert Path(resumed["worktree"]).is_dir()
    after = tools.runtime.task_queries.record(task["id"])
    assert after["process"]["worktree_required"] is True
    assert len(after["restart_history"]) == 1
    assert after["base"] == current["base"]
    assert after["pending"] is None


def test_restart_cannot_drop_allocated_worktree(project):
    tools, task = _prepare(project, worktree_required=True)
    initial = _start(tools, task)
    current = tools.runtime.task_queries.record(task["id"])
    born = restart(tools, task["id"], current["version"])
    process = deepcopy(project["process"])
    process["worktree_required"] = False
    with pytest.raises(PoiseError, match="allocated workspace"):
        tools.invoke(request("task", {
            "action": "edit", "request_id": "drop-worktree", "task_id": task["id"],
            "expected_revision": born["revision"], "patch": {"process": process}, "remove": [],
        }))
    assert Path(initial["worktree"]).is_dir()


def test_repository_local_root_is_used_by_real_bootstrap(project):
    root = project["app"] / ".worktrees"
    project["cfg"]["paths"]["worktrees"] = str(root)
    tools, task = _prepare(project, worktree_required=True)
    context = _start(tools, task)
    assert context["worktree"] == str(root / task["id"])
    tools.runtime._validate_cancelled_worktree(
        task["id"], tools.runtime.task_queries.record(task["id"]))


@pytest.mark.parametrize("location", ["outside", "repository", "symlink"])
def test_external_root_rejects_escape_and_repository_itself(project, location):
    if location == "outside":
        root = project["app"].parent / "outside"
    elif location == "repository":
        root = project["app"]
    else:
        root = project["app"] / "linked"
        root.symlink_to(project["app"].parent, target_is_directory=True)
    project["cfg"]["paths"]["worktrees"] = str(root)
    write_json(project["config_path"], project["cfg"])
    with pytest.raises(PoiseError):
        load_config(project["config_path"])


def test_root_update_keeps_unstarted_tasks_and_replays(project):
    settings, config, created = installed_project(project)
    client = work_tools(config, "planner")
    client.invoke(request("task", {"action": "create", "task_id": "WAITING",
        "sprint_id": None, "request_id": "waiting"}))
    before = client.runtime.task_queries.record("WAITING")
    packet = root_request(config, created["revision"], project["app"] / ".worktrees")
    changed = update_tools(settings).apply(packet)
    assert changed["status"] == "updated"
    assert load_config(config)[1]["paths"]["worktrees"] == str(project["app"] / ".worktrees")
    assert client.runtime.task_queries.record("WAITING") == before
    assert update_tools(settings).apply(packet)["replayed"] is True


def test_root_update_reconciles_exact_live_recovered_revision(project):
    settings, config, _ = installed_project(project)
    document = load_config(config)[1]
    document["git"]["author_name"] = "Recovered operator"
    write_json(config, document)
    packet = root_request(config, project_revision(config), project["app"] / ".worktrees")
    assert update_tools(settings).apply(packet)["status"] == "updated"
    assert load_config(config)[1]["git"]["author_name"] == "Recovered operator"


def test_root_update_rejects_allocated_worktree(project):
    settings, config, created = installed_project(project)
    client = work_tools(config, "executor")
    context = client.invoke(request("bootstrap", {"task": project["task"],
        "decision": None, "feedback": None, "rework_stage": None}))
    before = config.read_bytes()
    with pytest.raises(PoiseError, match="allocated worktree"):
        update_tools(settings).apply(root_request(config, created["revision"], project["app"] / ".worktrees"))
    assert config.read_bytes() == before
    assert Path(context["worktree"]).is_dir()


@pytest.mark.parametrize('invalid', ['stale', 'other_field', 'missing_authority'])
def test_root_correction_never_becomes_unrestricted_config_editor(project, invalid):
    settings, config, created = installed_project(project)
    packet = root_request(config, created['revision'], project['app'] / '.worktrees')
    if invalid == 'stale':
        packet['expected_revision'] = '0' * 64
    elif invalid == 'other_field':
        packet['manifest_edits'] = [{'path': ['git', 'base_ref'], 'value': 'master'}]
    else:
        packet['authorization'] = ''
    before = config.read_bytes()
    with pytest.raises(PoiseError):
        update_tools(settings).apply(packet)
    assert config.read_bytes() == before


def test_root_update_recovers_interrupted_manifest_publication(project, monkeypatch):
    import poise.infrastructure.project_config as infrastructure
    settings, config, created = installed_project(project)
    packet = root_request(config, created['revision'], project['app'] / '.worktrees')
    original = infrastructure.atomic_write

    def interrupt(path, content, mode):
        original(path, content, mode)
        if path == config:
            raise OSError('interrupted after manifest publication')

    with monkeypatch.context() as patch:
        patch.setattr(infrastructure, 'atomic_write', interrupt)
        with pytest.raises(PoiseError):
            update_tools(settings).apply(packet)
    result = update_tools(settings).apply(packet)
    assert result['replayed'] is True
    assert load_config(config)[1]['paths']['worktrees'] == str(project['app'] / '.worktrees')
