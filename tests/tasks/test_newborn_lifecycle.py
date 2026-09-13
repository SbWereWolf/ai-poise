"""Public newborn Task lifecycle contract for Task 0077."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from batch.helpers import configure, request
from conftest import WorkPoise as Poise, write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from sprints.helpers import changes as sprint_changes
from sprints.helpers import setup as setup_sprint


def tools(project, session):
    configure(project)
    return WorkTools(Poise(project["config_path"], session))


def task_action(client, **value):
    return client.invoke(request("task", value))


def create(client, task_id, sprint_id=None, request_id=None):
    return task_action(
        client,
        action="create",
        request_id=request_id or f"create-{task_id.lower()}",
        task_id=task_id,
        sprint_id=sprint_id,
    )


def edit(client, task_id, revision, patch, request_id):
    return task_action(
        client,
        action="edit",
        request_id=request_id,
        task_id=task_id,
        expected_revision=revision,
        patch=patch,
    )


def complete_patch(project, task_id, sprint_id=None):
    contract = deepcopy(project["task"])
    contract["id"] = task_id
    contract["sprint_id"] = sprint_id
    contract.pop("goal_type")
    return contract


def test_newborn_standalone_edit_and_ready(project):
    creator = tools(project, "creator")
    born = create(creator, "NEWBORN")
    assert born == {
        "status": "newborn",
        "task": "NEWBORN",
        "revision": 0,
        "sprint": None,
        "claimed_by": "creator",
        "goal_type": None,
        "route_entry": None,
        "ready": False,
    }

    purpose = edit(
        creator,
        "NEWBORN",
        0,
        {"goal": "Editable goal"},
        "edit-newborn-goal",
    )
    assert purpose["revision"] == 1
    assert purpose["draft"] == {"goal": "Editable goal"}

    selected = edit(
        creator,
        "NEWBORN",
        1,
        {"goal_type": "development"},
        "select-newborn-type",
    )
    assert selected["goal_type"] == "development"
    assert selected["route_entry"] == project["process"]["route"]["entry"]
    assert selected["status"] == "newborn"

    assembled = complete_patch(project, "NEWBORN")
    assembled.pop("goal")
    edited = edit(
        creator,
        "NEWBORN",
        2,
        assembled,
        "complete-newborn-contract",
    )
    ready = task_action(
        creator,
        action="ready",
        request_id="ready-newborn",
        task_id="NEWBORN",
        expected_revision=edited["revision"],
    )
    assert ready["status"] == "available"
    assert ready["claimed_by"] is None
    assert ready["route_entry"] == project["process"]["route"]["entry"]

    record = creator.runtime.task_queries.record("NEWBORN")
    assert record["contract"]["goal"] == "Editable goal"
    assert [item["event"] for item in record["history"]][:3] == [
        "created_newborn",
        "newborn_edited",
        "newborn_edited",
    ]
    assert "became_available" in [item["event"] for item in record["history"]]


def test_newborn_ownership_and_switching(project):
    owner = tools(project, "owner")
    create(owner, "FIRST")

    contender = tools(project, "contender")
    with pytest.raises(PoiseError, match="live|uncertain|owner|owned"):
        edit(contender, "FIRST", 0, {"goal": "stolen"}, "steal-first")

    second = create(owner, "SECOND")
    assert second["claimed_by"] == "owner"
    assert owner.runtime.task_queries.record("FIRST")["claimed_by"] is None

    acquired = edit(
        contender,
        "FIRST",
        1,
        {"goal": "legitimate edit"},
        "edit-released-first",
    )
    assert acquired["claimed_by"] == "contender"
    assert contender.runtime.ownership.snapshot("contender").task_id == "FIRST"


def test_newborn_sprint_membership_and_publication(project):
    setup_sprint(project)
    planner = tools(project, "planner")
    drafted = planner.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": "NEWBORN-SPRINT",
        "request_id": "draft-newborn-sprint",
        "expected_revision": None,
        "template": {"id": "basic", "version": "1"},
        "changes": sprint_changes([]),
    }))
    born = create(planner, "SPRINT-TASK", "NEWBORN-SPRINT")
    edited = edit(
        planner,
        "SPRINT-TASK",
        born["revision"],
        complete_patch(project, "SPRINT-TASK", "NEWBORN-SPRINT")
        | {"goal_type": "development"},
        "edit-sprint-newborn",
    )
    member_ready = task_action(
        planner,
        action="ready",
        request_id="ready-sprint-newborn",
        task_id="SPRINT-TASK",
        expected_revision=edited["revision"],
    )
    assert member_ready["status"] == "newborn"
    assert member_ready["ready"] is True

    revised = planner.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": "NEWBORN-SPRINT",
        "request_id": "link-newborn-member",
        "expected_revision": drafted["revision"] + 1,
        "template": None,
        "changes": [
            {"kind": "upsert_tasks", "tasks": ["SPRINT-TASK"]},
            {"kind": "dependencies", "items": []},
        ],
    }))
    assert revised["tasks"][0]["id"] == "SPRINT-TASK"
    assert revised["tasks"][0]["status"] == "newborn"

    published = planner.invoke(request("sprint", {
        "action": "publish",
        "sprint_id": None,
        "request_id": "publish-newborn-members",
        "expected_revision": revised["revision"],
    }))
    assert published["status"] == "planned"
    assert published["tasks"][0]["status"] == "available"
    assert planner.runtime.task_queries.record("SPRINT-TASK")["sprint_id"] == "NEWBORN-SPRINT"


def test_newborn_type_route_and_legacy_materialization(project):
    setup_sprint(project)
    planner = tools(project, "legacy-planner")
    legacy = deepcopy(project["task"])
    legacy["id"] = "LEGACY-TASK"
    legacy["sprint_id"] = "LEGACY-SPRINT"
    drafted = planner.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": "LEGACY-SPRINT",
        "request_id": "legacy-draft",
        "expected_revision": None,
        "template": {"id": "basic", "version": "1"},
        "changes": sprint_changes([]),
    }))
    # Fixture-only representation of a draft persisted by the pre-0077 product.
    # The transition itself remains exclusively public below.
    with planner.runtime.store.transaction() as database:
        row = database.execute(
            "SELECT data FROM sprints WHERE id=?", ("LEGACY-SPRINT",)
        ).fetchone()
        stored = json.loads(row[0])
        stored["aggregate"]["plan"]["tasks"] = [legacy]
        database.execute(
            "UPDATE sprints SET data=? WHERE id=?",
            (json.dumps(stored, ensure_ascii=False, sort_keys=True), "LEGACY-SPRINT"),
        )
    migrated = planner.invoke(request("sprint", {
        "action": "materialize_tasks",
        "sprint_id": "LEGACY-SPRINT",
        "request_id": "materialize-legacy-tasks",
        "expected_revision": drafted["revision"],
    }))
    replay = planner.invoke(request("sprint", {
        "action": "materialize_tasks",
        "sprint_id": "LEGACY-SPRINT",
        "request_id": "materialize-legacy-tasks",
        "expected_revision": drafted["revision"],
    }))
    assert migrated == replay
    assert migrated["materialized"] == ["LEGACY-TASK"]
    record = planner.runtime.task_queries.record("LEGACY-TASK")
    assert record["status"] == "newborn"
    assert record["process"]["route"]["entry"] == project["process"]["route"]["entry"]

    direct = deepcopy(project["task"])
    direct["id"] = "DIRECT-AVAILABLE"
    direct_client = tools(project, "direct")
    context = direct_client.invoke(request("bootstrap", {
        "task": direct,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert context["status"] == "active"
    history = direct_client.runtime.task_queries.record("DIRECT-AVAILABLE")["history"]
    assert [item["event"] for item in history[:3]] == [
        "created_newborn",
        "became_available",
        "started",
    ]
