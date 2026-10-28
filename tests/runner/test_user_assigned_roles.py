"""User-assigned roles do not require different agent/session identities.

Subjects: real Task review admission, verified ownership provenance and public
WorkTools acquisition/progression/verification. Persistence stub is limited to the
unit admission port; public scenarios use the real isolated project fixture.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from poise.application.tasks import require_reviewer_identity_in
from poise.modules.foundation.errors import DomainError
from poise.modules.tasks.domain import TaskStatus
from poise.modules.tasks.progression import crossed_role_boundary
from poise.modules.workflow.domain import RouteDefinition
from poise.application.work import WorkTools
from conftest import WorkPoise
from batch.helpers import result, verify
from tests.runtime_services.test_stage_progression import (
    _released_review_boundary, advance, bootstrap, release_current,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/runner/user-assigned-roles"


def literal(name):
    return json.loads((FIXTURES / name).read_text())


def task():
    return SimpleNamespace(
        route=RouteDefinition.from_workflow(literal("workflow.json")),
        stage=SimpleNamespace(stage_id="build"),
        state=SimpleNamespace(task_id="T-role", status=TaskStatus.VERIFIED,
                              claimed_by="session-A", version=3, iteration=1,
                              submission_digest="submission-role-fixture"),
        progress=SimpleNamespace(outcome="complete"),
    )


@pytest.mark.parametrize("known_author,expected", [
    (True, "same-actor-review.json"), (False, "missing-author-review.json"),
])
def test_user_assigned_reviewer_is_not_rejected_by_actor_identity(known_author, expected):
    identity = literal("identity.json")
    if not known_author:
        identity["executor_actor"] = None
    source = SimpleNamespace(review_identity=lambda _: deepcopy(identity))
    receipt = require_reviewer_identity_in(
        SimpleNamespace(tasks=source), task(), "session-A", target_stage="inspect",
    )
    assert receipt == literal(expected)


def test_one_agent_can_resume_its_other_role_after_public_release():
    assert crossed_role_boundary(task(), "session-A", literal("same-actor-role-history.json")) is True


def test_user_role_assignment_does_not_grant_another_agents_claim():
    with pytest.raises(DomainError, match="actual owner"):
        crossed_role_boundary(task(), "session-B", literal("same-actor-role-history.json"))


@pytest.mark.parametrize("field,value", [("submission_digest", "different"), ("through_version", 4)])
def test_user_role_assignment_preserves_exact_verified_result_provenance(field, value):
    history = literal("same-actor-role-history.json")
    history[field] = value
    with pytest.raises(DomainError, match="unavailable or changed"):
        crossed_role_boundary(task(), "session-A", history)


@pytest.mark.parametrize("entry", ["bootstrap", "acquire"])
def test_one_user_assigned_agent_performs_execution_and_review(project, entry):
    executor = _released_review_boundary(project)
    same_agent = WorkTools(WorkPoise(project["config_path"], "executor"))
    if entry == "bootstrap":
        bootstrap(same_agent, {"id": "T1"})
    else:
        same_agent.runtime.ownership.acquire_task("T1")
    context = advance(same_agent)
    assert context["stage"] == "code_review"
    payload = result(context)
    payload["stage_work"]["coverage"] = "Checked the implementation against the accepted result."
    assert verify(same_agent, payload)["status"] == "verified"
    saved = same_agent.runtime.task_queries.record("T1")
    assert saved["stage_index"] == 2
    assert saved["claimed_by"] == "executor"
    assert release_current(same_agent, "dual-role-complete")["status"] == "ownership_released"
