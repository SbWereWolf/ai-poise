"""Restarted Sprint publication preserves registry, proof and independent claims."""
from copy import deepcopy

import pytest

from batch.helpers import request
from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
from poise.modules.foundation.errors import PoiseError
from runtime_services.test_restart_late_method_inputs import claims
from runtime_services.test_task_restart import restart
from sprints.helpers import publish, task
from sprints.test_unpublish import (
    assert_materials, client, planned, release_member, reopen, unchanged, verified_materials,
)


def test_restarted_sprint_deferred_publication_fault_reload_and_retry(planned, monkeypatch):
    project, planner, published = planned
    foreign = client(project, "independent-owner")
    independent = task(project, "C")
    independent["sprint_id"] = None
    assert foreign.invoke(request("bootstrap", {
        "task": independent, "decision": None, "feedback": None, "rework_stage": None,
    }))["task"] == "C"

    material = verified_materials(project)
    old_registry = deepcopy(planner.runtime.task_queries.verification_registry("A"))
    prior = planner.runtime.task_queries.record("A")
    opened = reopen(planner, published["revision"])

    owner = client(project, "restart-owner")
    born = restart(owner, "A", prior["version"], request_id="restart-deferred-member")
    # Claim through the real ownership owner, without changing persisted fixture rows.
    owner.runtime.ownership.acquire_worktree("A")
    owned = claims(owner)
    assert owned == {
        "tasks": [("A", "restart-owner"), ("C", "independent-owner")],
        "worktrees": [("independent-owner", "C"), ("restart-owner", "A")],
    }
    readied = owner.invoke(request("task", {
        "action": "ready", "task_id": "A", "request_id": "ready-deferred-member",
        "expected_revision": born["revision"],
    }))
    assert readied["status"] == "newborn" and readied["ready"] is True
    assert claims(owner) == owned
    assert_materials(*material)
    release_member(owner, "deferred-member")
    independent_only = {
        "tasks": [("C", "independent-owner")], "worktrees": [("independent-owner", "C")],
    }
    assert claims(planner) == independent_only
    before = unchanged(planner)
    requirements = planner.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}])
    promote = SqliteTaskRepository.promote_prepared

    def interrupt_after_member(repository, *args, **kwargs):
        promote(repository, *args, **kwargs)
        raise PoiseError("Restarted Sprint publication interrupted before commit")

    with monkeypatch.context() as fault:
        fault.setattr(SqliteTaskRepository, "promote_prepared", interrupt_after_member)
        with pytest.raises(PoiseError, match="Restarted Sprint publication interrupted before commit"):
            publish(planner, opened["revision"], "republish-after-restart")
    reloaded = client(project, "planner")
    assert unchanged(reloaded) == before
    assert claims(reloaded) == independent_only
    assert reloaded.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}]) == requirements
    assert_materials(*material)
    republished = publish(reloaded, opened["revision"], "republish-after-restart")
    assert republished["status"] == "planned" and republished["eligible"] == ["A"]
    assert reloaded.runtime.task_queries.record("A")["status"] == "available"
    assert reloaded.runtime.task_queries.verification_registry("A") == old_registry
    assert claims(reloaded) == independent_only
    assert_materials(*material)
