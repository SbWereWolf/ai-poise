from copy import deepcopy

import pytest

from harness.application.work import WorkTools
from harness.common import HarnessError
from harness.runtime import Harness
from sprints.helpers import changes, setup, task

from .helpers import request


def overview(tools, sprint_statuses=None, standalone_task_statuses=None):
    response = tools.invoke(request("show", {"queries": [{
        "id": "overview",
        "kind": "work_overview",
        "sprint_statuses": sprint_statuses,
        "standalone_task_statuses": standalone_task_statuses,
    }]}))
    return response["results"][0]["value"]


def standalone(project, identifier, session):
    contract = task(project, identifier)
    contract["sprint_id"] = None
    tools = WorkTools(Harness(project["config_path"], session))
    context = tools.invoke(request("bootstrap", {
        "task": contract,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    return tools, context


def complete_standalone(project, identifier, session):
    tools, context = standalone(project, identifier, session)
    result = deepcopy(context["result_template"])
    result["sections"]["report"] = "Completed by the test scenario."
    result["commit_message"] = f"test: complete {identifier}"
    assert tools.invoke(request("verify", {"result": result, "artifacts": []}))["status"] == "verified"
    assert tools.invoke(request("accept", {}))["status"] == "completed"
    return tools


def published_sprint(project, identifier, task_ids, edges=()):
    tools = WorkTools(Harness(project["config_path"], f"planner-{identifier}"))
    contracts = []
    for task_id in task_ids:
        contract = task(project, task_id)
        contract["sprint_id"] = identifier
        contracts.append(contract)
    drafted = tools.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": identifier,
        "request_id": f"draft-{identifier}",
        "expected_revision": None,
        "template": {"id": "basic", "version": "1"},
        "changes": changes(contracts, edges),
    }))
    tools.invoke(request("sprint", {
        "action": "publish",
        "sprint_id": None,
        "request_id": f"publish-{identifier}",
        "expected_revision": drafted["revision"],
    }))
    return tools


def test_work_overview_empty_lists_and_exact_query_contract(project):
    setup(project)
    tools = WorkTools(Harness(project["config_path"], "overview-empty"))

    assert overview(tools) == {"sprints": [], "standalone_tasks": []}

    bad_queries = [
        {
            "id": "overview",
            "kind": "work_overview",
            "sprint_statuses": None,
            "standalone_task_statuses": None,
            "extra": True,
        },
        {
            "id": "overview",
            "kind": "work_overview",
            "sprint_statuses": ["planned", "planned"],
            "standalone_task_statuses": None,
        },
        {
            "id": "overview",
            "kind": "work_overview",
            "sprint_statuses": None,
            "standalone_task_statuses": ["blocked"],
        },
    ]
    for query in bad_queries:
        with pytest.raises(HarnessError):
            tools.invoke(request("show", {"queries": [query]}))


def test_work_overview_separates_sprint_members_and_orders_each_list(project):
    setup(project)
    published_sprint(project, "sprint-z", ["member-z"])
    published_sprint(project, "sprint-a", ["member-a"])
    standalone(project, "standalone-z", "standalone-z-session")
    standalone(project, "standalone-a", "standalone-a-session")
    observer = WorkTools(Harness(project["config_path"], "overview-observer"))

    result = overview(observer)

    assert [item["sprint"] for item in result["sprints"]] == ["sprint-a", "sprint-z"]
    assert [item["id"] for item in result["standalone_tasks"]] == ["standalone-a", "standalone-z"]
    assert {item["status"] for item in result["sprints"]} == {"planned"}
    assert {item["status"] for item in result["standalone_tasks"]} == {"active"}
    assert not {"member-a", "member-z"} & {item["id"] for item in result["standalone_tasks"]}


def test_work_overview_filters_lists_independently_with_null_and_empty(project):
    setup(project)
    published_sprint(project, "planned-sprint", ["planned-member"])
    cancelled_sprint = published_sprint(project, "cancelled-sprint", ["cancelled-member"])
    cancelled_sprint.invoke(request("sprint", {
        "action": "force_close",
        "sprint_id": None,
        "request_id": "cancel-cancelled-sprint",
        "reason": "Cancelled by the test scenario",
    }))
    standalone(project, "active-standalone", "active-standalone-session")
    cancelled_task, _ = standalone(project, "cancelled-standalone", "cancelled-standalone-session")
    cancelled_task.invoke(request("cancel", {"reason": "Cancelled by the test scenario"}))
    complete_standalone(project, "completed-standalone", "completed-standalone-session")
    observer = WorkTools(Harness(project["config_path"], "filter-observer"))

    no_sprints = overview(observer, [], None)
    assert no_sprints["sprints"] == []
    assert {item["status"] for item in no_sprints["standalone_tasks"]} == {"active", "cancelled", "completed"}

    no_tasks = overview(observer, None, [])
    assert {item["status"] for item in no_tasks["sprints"]} == {"planned", "cancelled"}
    assert no_tasks["standalone_tasks"] == []

    selected = overview(observer, ["cancelled"], ["active"])
    assert [item["sprint"] for item in selected["sprints"]] == ["cancelled-sprint"]
    assert [item["id"] for item in selected["standalone_tasks"]] == ["active-standalone"]


def test_work_overview_reuses_dependency_aware_blocked_sprint_overview(project):
    setup(project)
    tools = published_sprint(
        project,
        "blocked-sprint",
        ["predecessor", "successor"],
        [{"predecessor": "predecessor", "successor": "successor", "kind": "completion"}],
    )
    tools.invoke(request("sprint", {
        "action": "cancel_tasks",
        "sprint_id": None,
        "request_id": "cancel-predecessor",
        "tasks": ["predecessor"],
        "mode": "single",
        "reason": "Exercise the dependency blocker",
    }))
    expected = deepcopy(tools.runtime.sprint_tools.overview("blocked-sprint"))

    result = overview(tools, ["blocked"], [])

    assert result["sprints"] == [expected]
    assert result["standalone_tasks"] == []
    assert expected["status"] == "blocked"
    assert expected["eligible"] == []
    assert expected["blocked"] == [{
        "task": "successor",
        "reasons": [{"predecessor": "predecessor", "reason": "cancelled_dependency"}],
    }]
