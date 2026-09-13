"""Sprint ownership and reversible membership conversion contract (Task 0100)."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import configure, request
from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from sprints.helpers import changes, publish, setup, task


def client(project, session):
    configure(project)
    return WorkTools(Poise(project["config_path"], session))


def task_action(tools, **payload):
    return tools.invoke(request("task", payload))


def create_available(project, tools, task_id):
    born = task_action(
        tools,
        action="create",
        request_id=f"create-{task_id.lower()}",
        task_id=task_id,
        sprint_id=None,
    )
    contract = task(project, task_id)
    contract["sprint_id"] = None
    patch = deepcopy(contract)
    patch.pop("id")
    patch.pop("sprint_id")
    edited = task_action(
        tools,
        action="edit",
        request_id=f"edit-{task_id.lower()}",
        task_id=task_id,
        expected_revision=born["revision"],
        patch=patch,
    )
    ready = task_action(
        tools,
        action="ready",
        request_id=f"ready-{task_id.lower()}",
        task_id=task_id,
        expected_revision=edited["revision"],
    )
    assert ready["status"] == "available"
    return ready


def draft_empty(tools, sprint_id="S", request_id="draft-empty"):
    return tools.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": sprint_id,
        "request_id": request_id,
        "expected_revision": None,
        "template": {"id": "basic", "version": "1"},
        "changes": changes([]),
    }))


def adopt(tools, revision, task_ids, edges=(), sprint_id="S", request_id="adopt"):
    return tools.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": sprint_id,
        "request_id": request_id,
        "expected_revision": revision,
        "template": None,
        "changes": [
            {"kind": "adopt_tasks", "ids": list(task_ids)},
            {"kind": "dependencies", "items": list(edges)},
        ],
    }))


def extract(tools, revision, task_ids, sprint_id="S", request_id="extract"):
    return tools.invoke(request("sprint", {
        "action": "extract_tasks",
        "sprint_id": sprint_id,
        "request_id": request_id,
        "expected_revision": revision,
        "task_ids": list(task_ids),
    }))


def public_view(tools, sprint_id="S"):
    result = tools.invoke(request("show", {"queries": [
        {"id": "sprint", "kind": "sprint", "sprint_id": sprint_id, "view": "current"},
        {
            "id": "work",
            "kind": "work_overview",
            "sprint_statuses": None,
            "standalone_task_statuses": None,
        },
    ]}))
    return {item["id"]: item["value"] for item in result["results"]}


def state_snapshot(tools, sprint_id, task_ids):
    return {
        "sprint": deepcopy(public_view(tools, sprint_id)),
        "tasks": {
            task_id: deepcopy(tools.runtime.task_queries.record(task_id))
            for task_id in task_ids
        },
    }


def prepare_published(project, sprint_id, task_ids, edges=()):
    planner = client(project, f"planner-{sprint_id}")
    contracts = []
    for task_id in task_ids:
        contract = task(project, task_id)
        contract["sprint_id"] = sprint_id
        contracts.append(contract)
    drafted = planner.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": sprint_id,
        "request_id": f"draft-{sprint_id}",
        "expected_revision": None,
        "template": {"id": "basic", "version": "1"},
        "changes": changes(contracts, edges),
    }))
    published = planner.invoke(request("sprint", {
        "action": "publish",
        "sprint_id": sprint_id,
        "request_id": f"publish-{sprint_id}",
        "expected_revision": drafted["revision"],
    }))
    return planner, published


def test_adoption_rejections_are_atomic(project):
    setup(project)
    planner = client(project, "planner")
    draft = draft_empty(planner)
    create_available(project, planner, "ELIGIBLE")
    create_available(project, planner, "STANDALONE-ENDPOINT")
    create_available(project, planner, "PENDING")
    create_available(project, planner, "RESULT")

    with planner.runtime.store.unit_of_work() as uow:
        uow.execution.patch("PENDING", {"pending": {"kind": "test-pending"}})
        uow.execution.patch("RESULT", {"last_report": {"commit": "a" * 40}})

    foreign = client(project, "foreign-owner")
    foreign.invoke(request("bootstrap", {
        "task": {"id": "STANDALONE-ENDPOINT"},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    prepare_published(project, "OTHER", ["OTHER-MEMBER"])

    all_ids = [
        "ELIGIBLE",
        "STANDALONE-ENDPOINT",
        "PENDING",
        "RESULT",
        "OTHER-MEMBER",
    ]
    cases = [
        (
            ["ELIGIBLE"],
            [{"predecessor": "ELIGIBLE", "successor": "STANDALONE-ENDPOINT", "kind": "result"}],
            "same Sprint",
        ),
        (["STANDALONE-ENDPOINT"], [], "unclaimed available"),
        (["OTHER-MEMBER"], [], "another Sprint"),
        (["PENDING"], [], "pending external operation"),
        (["RESULT"], [], "existing result"),
        (["ELIGIBLE", "PENDING"], [], "pending external operation"),
    ]
    for index, (task_ids, edges, message) in enumerate(cases):
        before = state_snapshot(planner, "S", all_ids)
        with pytest.raises(PoiseError, match=message):
            adopt(
                planner,
                draft["revision"],
                task_ids,
                edges,
                request_id=f"rejected-adoption-{index}",
            )
        assert state_snapshot(planner, "S", all_ids) == before


def test_adopt_publish_dependency_and_preserve_tasks(project):
    setup(project)
    planner = client(project, "migration-planner")
    before = {}
    for task_id in ("0097", "0098"):
        create_available(project, planner, task_id)
        before[task_id] = deepcopy(planner.runtime.task_queries.record(task_id))
    draft = draft_empty(planner, request_id="draft-migration")
    edge = {"predecessor": "0097", "successor": "0098", "kind": "result"}

    adopted = adopt(
        planner,
        draft["revision"],
        ["0097", "0098"],
        [edge],
        request_id="adopt-migration-tasks",
    )
    assert adopted["dependencies"] == [edge]
    assert {item["id"] for item in adopted["tasks"]} == {"0097", "0098"}
    for task_id in before:
        after = planner.runtime.task_queries.record(task_id)
        assert after["id"] == before[task_id]["id"]
        assert after["contract"] == before[task_id]["contract"]
        assert after["status"] == before[task_id]["status"] == "available"
        assert after["history"][:-1] == before[task_id]["history"]
        assert after["sprint_id"] == "S"

    published = publish(planner, adopted["revision"], "publish-migration")
    assert published["dependencies"] == [edge]
    assert published["eligible"] == ["0097"]
    sprint_view = public_view(planner)["sprint"]
    assert sprint_view["dependencies"] == [edge]
    assert sprint_view["eligible"] == ["0097"]
    assert public_view(planner)["work"]["standalone_tasks"] == []
    assert planner.invoke(request("bootstrap", {
        "task": {"id": "S"},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))["eligible"] == ["0097"]


def test_extraction_rejects_every_edge_direction_atomically(project):
    setup(project)
    edges = [
        {"predecessor": "A", "successor": "B", "kind": "completion"},
        {"predecessor": "C", "successor": "A", "kind": "result"},
    ]
    planner, published = prepare_published(project, "S", ["A", "B", "C"], edges)
    for task_ids in (["A"], ["B"], ["C"], ["A", "B"]):
        before = state_snapshot(planner, "S", ["A", "B", "C"])
        with pytest.raises(PoiseError, match="incoming or outgoing dependenc"):
            extract(
                planner,
                published["revision"],
                task_ids,
                request_id="reject-extract-" + "-".join(task_ids),
            )
        assert state_snapshot(planner, "S", ["A", "B", "C"]) == before


def test_extraction_success_preserves_task_and_overviews(project):
    setup(project)
    planner, published = prepare_published(project, "S", ["A", "B", "ISOLATED"], [
        {"predecessor": "A", "successor": "B", "kind": "completion"},
    ])
    before = deepcopy(planner.runtime.task_queries.record("ISOLATED"))

    extracted = extract(
        planner,
        published["revision"],
        ["ISOLATED"],
        request_id="extract-isolated",
    )

    assert {item["id"] for item in extracted["tasks"]} == {"A", "B"}
    assert extracted["dependencies"] == [
        {"predecessor": "A", "successor": "B", "kind": "completion"},
    ]
    after = planner.runtime.task_queries.record("ISOLATED")
    assert after["id"] == before["id"]
    assert after["contract"] == before["contract"]
    assert after["status"] == before["status"] == "available"
    assert after["history"][:-1] == before["history"]
    assert after["sprint_id"] is None
    views = public_view(planner)
    assert {item["id"] for item in views["sprint"]["tasks"]} == {"A", "B"}
    assert [item["id"] for item in views["work"]["standalone_tasks"]] == ["ISOLATED"]


def test_existing_creation_and_draft_newborn_removal_regression(project):
    setup(project)
    planner = client(project, "regression-planner")
    create_available(project, planner, "STANDALONE")
    standalone = planner.runtime.task_queries.record("STANDALONE")
    assert standalone["status"] == "available"
    assert standalone["sprint_id"] is None

    contract = task(project, "NATIVE")
    drafted = planner.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": "NATIVE-SPRINT",
        "request_id": "draft-native",
        "expected_revision": None,
        "template": {"id": "basic", "version": "1"},
        "changes": changes([contract]),
    }))
    native = planner.runtime.task_queries.record("NATIVE")
    assert native["status"] == "newborn"
    assert native["sprint_id"] == "NATIVE-SPRINT"

    removed = planner.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": "NATIVE-SPRINT",
        "request_id": "remove-native-newborn",
        "expected_revision": drafted["revision"],
        "template": None,
        "changes": [{"kind": "remove_tasks", "ids": ["NATIVE"]}],
    }))
    assert removed["tasks"] == []
    detached = planner.runtime.task_queries.record("NATIVE")
    assert detached["status"] == "newborn"
    assert detached["sprint_id"] is None


def test_membership_conversion_documentation_contract():
    root = Path(__file__).resolve().parents[2]
    russian = [
        (root / "docs/workflows/sprints.md").read_text(),
        (root / "docs/governance/development-rules.md").read_text(),
    ]
    english = [
        (root / "AGENTS.md").read_text(),
        (root / ".agents/skills/poise/SKILL.md").read_text(),
    ]
    for text in russian:
        assert "adopt_tasks" in text
        assert "extract_tasks" in text
        assert "входящих и исходящих зависимостей" in text
        assert "атомар" in text.lower()
    for text in english:
        assert "adopt_tasks" in text
        assert "extract_tasks" in text
        assert "incoming or outgoing dependency" in text
        assert "atomic" in text.lower()
    workflow = russian[0]
    assert "0097" in workflow and "0098" in workflow
    assert "0097 -> 0098" in workflow
    assert "result" in workflow
