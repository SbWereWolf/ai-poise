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
        remove=[],
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
        {"id": "history", "kind": "sprint", "sprint_id": sprint_id, "view": "history"},
        {
            "id": "work",
            "kind": "work_overview",
            "sprint_statuses": None,
            "standalone_task_statuses": None,
        },
    ]}))
    return {item["id"]: item["value"] for item in result["results"]}


def add_execution(tools, task_id, **updates):
    snapshot = {
        "worktree": None,
        "branch": None,
        "base": None,
        "attempts": 0,
        "publication": None,
        "pending": None,
        "entry_tree": None,
        "last_report": None,
    }
    snapshot.update(updates)
    with tools.runtime.store.unit_of_work() as uow:
        uow.execution.create(task_id, snapshot)


def start(tools, task_id):
    return tools.invoke(request("bootstrap", {
        "task": {"id": task_id},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))


def release_started(tools, task_id):
    started = start(tools, task_id)
    handed_off = tools.invoke(request("handoff", {
        "request_id": f"release-{task_id.lower()}",
        "reason": "Create a released started Task fixture through the public lifecycle.",
        "result": None,
        "commit_message": None,
        "artifact_paths": [],
    }))
    assert handed_off["status"] == "handed_off"
    return started


def assert_rejected(call, message):
    try:
        call()
    except PoiseError as exc:
        if str(exc) in {"Unknown sprint change", "Unknown sprint action"}:
            pytest.fail(f"EXPECTED_PRODUCT_RED: {exc}", pytrace=False)
        if message not in str(exc):
            pytest.exit(f"UNEXPECTED_PRODUCT_FAILURE: {exc}")
    else:
        pytest.fail(f"Expected rejection containing {message!r}")


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
    create_available(project, planner, "FOREIGN-ACTIVE")
    create_available(project, planner, "STARTED-RELEASED")
    add_execution(planner, "PENDING", pending={"kind": "test-pending"})
    add_execution(planner, "RESULT", last_report={"commit": "a" * 40})

    foreign = client(project, "foreign-owner")
    start(foreign, "FOREIGN-ACTIVE")
    released = client(project, "released-owner")
    release_started(released, "STARTED-RELEASED")
    prepare_published(project, "OTHER", ["OTHER-MEMBER"])

    all_ids = [
        "ELIGIBLE",
        "STANDALONE-ENDPOINT",
        "PENDING",
        "RESULT",
        "FOREIGN-ACTIVE",
        "STARTED-RELEASED",
        "OTHER-MEMBER",
    ]
    cases = [
        (
            ["ELIGIBLE"],
            [{"predecessor": "ELIGIBLE", "successor": "STANDALONE-ENDPOINT", "kind": "result"}],
            "same Sprint",
        ),
        (["FOREIGN-ACTIVE"], [], "unclaimed available"),
        (["STARTED-RELEASED"], [], "never started and has no worktree"),
        (["OTHER-MEMBER"], [], "another Sprint"),
        (["PENDING"], [], "pending external operation"),
        (["RESULT"], [], "existing result"),
        (["ELIGIBLE", "PENDING"], [], "pending external operation"),
    ]
    for index, (task_ids, edges, message) in enumerate(cases):
        before = state_snapshot(planner, "S", all_ids)
        invoke = lambda: adopt(
                planner,
                draft["revision"],
                task_ids,
                edges,
                request_id=f"rejected-adoption-{index}",
            )
        assert_rejected(invoke, message)
        assert state_snapshot(planner, "S", all_ids) == before
        assert_rejected(invoke, message)
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

    try:
        adopted = adopt(
            planner,
            draft["revision"],
            ["0097", "0098"],
            [edge],
            request_id="adopt-migration-tasks",
        )
    except PoiseError as exc:
        if str(exc) == "Unknown sprint change":
            pytest.fail(f"EXPECTED_PRODUCT_RED: {exc}", pytrace=False)
        pytest.exit(f"UNEXPECTED_PRODUCT_FAILURE: {exc}")
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
    invalid_ids = ["FOREIGN", "STARTED", "PENDING", "RESULT"]
    planner, published = prepare_published(
        project,
        "S",
        ["A", "B", "C", *invalid_ids],
        edges,
    )
    foreign = client(project, "extraction-foreign-owner")
    start(foreign, "FOREIGN")
    released = client(project, "extraction-released-owner")
    release_started(released, "STARTED")
    add_execution(planner, "PENDING", pending={"kind": "test-pending"})
    add_execution(planner, "RESULT", last_report={"commit": "b" * 40})
    for task_ids in (["A"], ["B"], ["C"], ["A", "B"]):
        before = state_snapshot(planner, "S", ["A", "B", "C"])
        invoke = lambda: extract(
                planner,
                published["revision"],
                task_ids,
                request_id="reject-extract-" + "-".join(task_ids),
            )
        assert_rejected(invoke, "incoming or outgoing dependenc")
        assert state_snapshot(planner, "S", ["A", "B", "C"]) == before
        assert_rejected(invoke, "incoming or outgoing dependenc")
        assert state_snapshot(planner, "S", ["A", "B", "C"]) == before

    for task_id in invalid_ids:
        before = state_snapshot(planner, "S", ["A", "B", "C", *invalid_ids])
        message = {
            "FOREIGN": "unclaimed available",
            "STARTED": "never started and has no worktree",
            "PENDING": "pending external operation",
            "RESULT": "existing result",
        }[task_id]
        invoke = lambda task_id=task_id: extract(
            planner,
            published["revision"],
            [task_id],
            request_id=f"reject-extract-state-{task_id.lower()}",
        )
        assert_rejected(invoke, message)
        assert state_snapshot(planner, "S", ["A", "B", "C", *invalid_ids]) == before
        assert_rejected(invoke, message)
        assert state_snapshot(planner, "S", ["A", "B", "C", *invalid_ids]) == before


def test_extraction_success_preserves_task_and_overviews(project):
    setup(project)
    planner, published = prepare_published(project, "S", ["A", "B", "ISOLATED"], [
        {"predecessor": "A", "successor": "B", "kind": "completion"},
    ])
    before = deepcopy(planner.runtime.task_queries.record("ISOLATED"))

    try:
        extracted = extract(
            planner,
            published["revision"],
            ["ISOLATED"],
            request_id="extract-isolated",
        )
    except PoiseError as exc:
        if str(exc) == "Unknown sprint action":
            pytest.fail(f"EXPECTED_PRODUCT_RED: {exc}", pytrace=False)
        pytest.exit(f"UNEXPECTED_PRODUCT_FAILURE: {exc}")

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
        text=" ".join(text.split())
        assert "adopt_tasks" in text
        assert "extract_tasks" in text
        assert "входящих и исходящих зависимостей" in text
        assert "атомар" in text.lower()
        assert "status=available, claimed_by=null, worktree=null, pending=null, last_report=null и attempts=0" in text
        assert "identity, immutable history, goal, contract и readiness" in text
        assert "membership, граф, revision и request receipt" in text
        assert "повтор после отказа не считается replay" in text
    for text in english:
        text=" ".join(text.split())
        assert "adopt_tasks" in text
        assert "extract_tasks" in text
        assert "incoming or outgoing dependency" in text
        assert "atomic" in text.lower()
        assert "status=available, claimed_by=null, worktree=null, pending=null, last_report=null, and attempts=0" in text
        assert "Identity, immutable history, goal, contract, and readiness are preserved" in text
        assert "membership, graph, revision, and the request receipt" in text
        assert "retry after rejection is not a replay" in text
    workflow = russian[0]
    assert "0097" in workflow and "0098" in workflow
    assert "0097 -> 0098" in workflow
    assert "result" in workflow
