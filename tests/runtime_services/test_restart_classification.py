"""Public restarted readiness must preserve work while reclassifying checks."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import bootstrap, request, result, verify
from conftest import add_test
from poise.modules.foundation.errors import DomainError, PoiseError
from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
from runtime_services.test_restart_registry_preservation import (
    persisted, projection, ready_task, restart_task, setup_current,
    verification_calls, changes_fixture, wire_hash,
)
from runtime_services.test_task_restart import git, process_contract
from verification.test_current_registry_mutation import (
    configure_public_registry_case, public_tools, change, operation,
)


def prepare_documentation_restart(project):
    configure_public_registry_case(project)
    project["task"].update(
        methods=[], method_inputs=[],
        checks={stage["id"]: [] for stage in project["process"]["stages"]},
        planning={
            "schema": "task-planning-1", "template": None,
            "restart_revision_policy": {
                "reviewer": ["process"],
                "user": ["process", "methods", "method_inputs", "checks",
                         "decomposition", "evidence_plan", "stage_contracts",
                         "executable_obligations"],
            },
        },
    )
    project["cfg"]["automatic_checks"][0]["by_stage"] = deepcopy(project["task"]["checks"])
    tools = public_tools(project, "classification-executor")
    boot = bootstrap(tools, project)
    root = Path(boot["worktree"])
    wip = root / "classification-wip.txt"
    wip.write_bytes(b"Unfinished work must survive readiness.\n")
    before = tools.runtime.task_queries.record("T1")
    registry_before = projection(tools)
    assert registry_before["executable_obligations"] == ["requirements[0]", "definition_of_done[0]"]
    assert registry_before["current"] == []
    newborn = restart_task(tools, "T1")
    process = deepcopy(newborn["process"])
    # Two real documentation stages; no verification-registry schema owner.
    process["stages"] = process["stages"][:2]
    first, second = process["stages"]
    first["sections"].pop("test_registry")
    first["allowed_paths"] = ["docs/**"]
    second["transitions"] = {"clear": None, "changes_requested": first["id"]}
    for stage in process["stages"]:
        stage["rework_targets"] = [first["id"], second["id"]]
    patch = process_contract(newborn["draft"]["goal_type"], process)
    patch["process"] = process
    edited = tools.invoke(request("task", {
        "action": "edit", "task_id": "T1", "request_id": "documentation-contract",
        "expected_revision": newborn["revision"], "patch": patch,
        "remove": ["executable_obligations"],
    }))
    return tools, before, registry_before, edited, root, wip


def work_state(root, wip):
    return (git(root, "rev-parse", "HEAD"), git(root, "symbolic-ref", "HEAD"),
            git(root, "write-tree"),
            git(root, "status", "--porcelain=v1", "--untracked-files=all"),
            wip.read_bytes())


def test_public_ready_reclassifies_empty_registry_and_replays_without_work(project, verification_calls):
    tools, before, registry_before, edited, root, wip = prepare_documentation_restart(project)
    rows = persisted(tools, "T1")
    work = work_state(root, wip)
    assert verification_calls == []

    response = ready_task(tools, "T1", edited["revision"])

    assert response["status"] == "available"
    assert response["task"] == "T1"
    after = tools.runtime.task_queries.record("T1")
    assert after["contract"]["goal"] == before["contract"]["goal"]
    assert after["worktree"] == str(root)
    assert after["requirements_snapshot"] == before["requirements_snapshot"]
    assert after["requirements_agreement"] == before["requirements_agreement"]
    current = projection(tools)
    assert current["current"] == []
    assert current["executable_obligations"] == []
    assert current["revision"] == registry_before["revision"] + 1
    assert current["history"] == [{"revision": 0, "entries": []}]
    assert len(current["requests"]) == 1
    audit = current["requests"][0]["audit"]
    expected_audit = json.loads((Path(__file__).parent / "fixtures/classification_audit.json").read_text())
    assert audit == {**expected_audit, "receipt_id": wire_hash(expected_audit)}
    saved = persisted(tools, "T1")
    assert json.loads(saved["execution"][0][0])["base"] == json.loads(rows["execution"][0][0])["base"]
    for key in ("methods", "results", "submissions", "evidence", "layers"):
        assert saved[key] == rows[key]
    assert saved["events"][:len(rows["events"])] == rows["events"]
    assert work_state(root, wip) == work
    assert ready_task(tools, "T1", edited["revision"]) == response
    assert persisted(tools, "T1") == saved
    assert projection(tools) == current
    assert work_state(root, wip) == work
    assert verification_calls == []


def test_requirements_publication_failure_rolls_back_classification(project, monkeypatch):
    tools, _, _, edited, root, wip = prepare_documentation_restart(project)
    before = persisted(tools, "T1")
    registry_before = projection(tools)
    ownership = tools.runtime.ownership.snapshot("classification-executor")
    query = [{"id": "requirements", "kind": "registry"}]
    requirements = deepcopy(tools.runtime.requirements_commands.query(query))
    work = work_state(root, wip)
    gate = tools.runtime.task_commands.requirements_gate
    original = gate.publish_created
    entered = []

    def fail_publication(*args, **kwargs):
        entered.append(True)
        original(*args, **kwargs)
        raise PoiseError("Injected Requirements publication failure")

    monkeypatch.setattr(gate, "publish_created", fail_publication)
    with pytest.raises(PoiseError, match="Injected Requirements publication failure"):
        ready_task(tools, "T1", edited["revision"])
    assert entered == [True]
    assert persisted(tools, "T1") == before
    assert projection(tools) == registry_before
    assert tools.runtime.ownership.snapshot("classification-executor") == ownership
    assert tools.runtime.requirements_commands.query(query) == requirements
    assert work_state(root, wip) == work
    monkeypatch.setattr(gate, "publish_created", original)
    assert ready_task(tools, "T1", edited["revision"])["status"] == "available"
    assert projection(tools)["revision"] == registry_before["revision"] + 1


def authority_restart(tools, role="reviewer", fields=None):
    authorization = {"role": role, "decision": "Authorize this isolated classification fixture."}
    if fields is not None:
        authorization["revision_fields"] = fields
    return tools.invoke(request("task", {
        "action": "restart", "task_id": "T1", "request_id": "classification-restart",
        "expected_version": tools.runtime.task_queries.record("T1")["version"],
        "reason": "Change only the agreed executable classification.",
        "authorization": authorization,
    }))


def edit_classification(tools, newborn, obligations, request_id="classification-edit"):
    return tools.invoke(request("task", {
        "action": "edit", "task_id": "T1", "request_id": request_id,
        "expected_revision": newborn["revision"],
        "patch": {"executable_obligations": obligations}, "remove": [],
    }))


def conservation(tools, root, wip):
    return {
        "rows": persisted(tools, "T1"), "registry": projection(tools),
        "requirements": deepcopy(tools.runtime.requirements_commands.query([
            {"id": "requirements", "kind": "registry"},
        ])),
        "owner": tools.runtime.ownership.snapshot("classification-executor"),
        "work": work_state(root, wip),
    }


@pytest.mark.parametrize("action", ["edit", "ready"])
def test_foreign_newborn_actor_cannot_edit_or_ready_classification(project, action):
    tools, _, _, edited, root, wip = prepare_documentation_restart(project)
    foreign = public_tools(project, "foreign-classification-actor")
    before = conservation(tools, root, wip)
    with pytest.raises(DomainError, match="ownership|owned|влад"):
        if action == "edit":
            edit_classification(foreign, edited, [])
        else:
            ready_task(foreign, "T1", edited["revision"])
    assert conservation(tools, root, wip) == before


def test_restart_without_classification_grant_rejects_edit_atomically(project):
    tools, _ = setup_current(project)
    newborn = authority_restart(tools, role="user")
    before = persisted(tools, "T1")
    registry_before = projection(tools)
    with pytest.raises(DomainError, match="forbids changes"):
        edit_classification(tools, newborn, [])
    assert persisted(tools, "T1") == before
    assert projection(tools) == registry_before


def test_reviewer_grant_reclassifies_history_without_resurrecting_methods(project):
    tools, registry_before = setup_current(project)
    release = tools.invoke(request("handoff", {
        "request_id": "release-classification-review", "result": None,
        "reason": "Let the distinct fixture reviewer grant the exact classification change.",
        "commit_message": "Preserve verified fixture before reviewer grant", "artifact_paths": [],
    }))
    assert release["status"] == "handed_off"
    tools = public_tools(project, "classification-reviewer")
    tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    newborn = authority_restart(tools, fields=["executable_obligations"])
    edited = edit_classification(tools, newborn, ["requirements[0]"])
    before = persisted(tools, "T1")
    assert ready_task(tools, "T1", edited["revision"])["status"] == "available"
    current = projection(tools)
    assert current["revision"] == 2
    assert current["current"] == registry_before["current"]
    assert current["executable_obligations"] == ["requirements[0]"]
    assert current["history"][:-1] == registry_before["history"]
    assert current["requests"][:-1] == registry_before["requests"]
    assert current["requests"][-1]["audit"]["actor"] == "classification-reviewer"
    assert current["requests"][-1]["audit"]["methods"] == []
    assert current["requests"][-1]["audit"]["previous_executable_obligations"] == [
        "requirements[0]", "definition_of_done[0]",
    ]
    assert current["requests"][-1]["audit"]["new_executable_obligations"] == ["requirements[0]"]
    saved = persisted(tools, "T1")
    for key in ("methods", "results", "submissions", "evidence", "layers"):
        assert saved[key] == before[key]
    assert saved["events"][:len(before["events"])] == before["events"]


@pytest.mark.parametrize("case", ["stale", "conflicting-replay", "schema-field"])
def test_ready_classification_guards_are_atomic(project, case):
    tools, _, _, edited, root, wip = prepare_documentation_restart(project)
    revision = edited["revision"]
    if case == "conflicting-replay":
        ready_task(tools, "T1", revision)
    elif case == "schema-field":
        edited = edit_classification(tools, edited, [])
        revision = edited["revision"]
    before = conservation(tools, root, wip)
    with pytest.raises(PoiseError):
        ready_task(tools, "T1", revision - 1 if case != "schema-field" else revision)
    assert conservation(tools, root, wip) == before


def test_executor_cannot_grant_own_reviewer_classification_revision(project):
    tools, _ = setup_current(project)
    before = persisted(tools, "T1")
    registry_before = projection(tools)
    with pytest.raises(DomainError, match="distinct reviewer"):
        authority_restart(tools, fields=["executable_obligations"])
    assert persisted(tools, "T1") == before
    assert projection(tools) == registry_before


def test_reclassified_task_preserves_old_green_but_executes_fresh_candidate(project):
    configure_public_registry_case(project)
    project["task"]["planning"] = {
        "schema": "task-planning-1", "template": None,
        "restart_revision_policy": {"reviewer": ["process"], "user": ["executable_obligations"]},
    }
    counter = project["root"] / "classification-proof-calls.txt"
    probe = changes_fixture()["probe"]
    probe["environment"]["COUNTER"] = str(counter)
    project["task"]["methods"] = [probe]
    project["task"]["method_inputs"] = [{
        "method_id": "PROBE", "repository_inputs": [], "future_outputs": [],
        "reference_profile": {"runner": "python", "parser": "inline-no-path-arguments", "version": 1},
    }]
    checks = {stage["id"]: [] for stage in project["process"]["stages"]}
    checks["test_implementation"] = ["PROBE"]
    project["task"]["checks"] = checks
    project["cfg"]["automatic_checks"][0]["by_stage"] = deepcopy(checks)
    tools = public_tools(project, "classification-executor")
    boot = bootstrap(tools, project)
    add_test(boot["worktree"])
    payload = result(boot)
    payload["method_additions"] = change(operation("replace", "PROBE", registration={
        "method": probe, "stages": ["test_implementation"],
        "evidence_kind": "executable_test", "covers": ["requirements[0]", "definition_of_done[0]"],
    }), request_id="prior-classification-proof")
    old = verify(tools, payload)
    old_receipt = old["checks"][0]
    assert old["status"] == "verified" and old_receipt["passed"] is True
    assert counter.read_text() == "old\n"
    rows = persisted(tools, "T1")
    registry_before = projection(tools)
    newborn = authority_restart(tools, role="user")
    edited = edit_classification(tools, newborn, ["requirements[0]"])
    ready_task(tools, "T1", edited["revision"])
    assert projection(tools)["current"] == registry_before["current"]
    assert projection(tools)["requests"][:-1] == registry_before["requests"]
    assert counter.read_text() == "old\n"
    for key in ("results", "evidence", "submissions", "layers"):
        assert persisted(tools, "T1")[key] == rows[key]
    resumed = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    (Path(resumed["worktree"]) / "tests/classification-candidate.txt").write_text("A new candidate.\n")
    fresh = verify(tools, result(resumed))
    receipt = fresh["checks"][0]
    assert fresh["status"] == "verified" and receipt["passed"] is True
    assert receipt["actual_exit_code"] == 0
    assert receipt["id"] != old_receipt["id"]
    assert receipt["execution_key"] != old_receipt["execution_key"]
    assert receipt["commit"] != old_receipt["commit"]
    assert receipt["commit"] == fresh["verification_commit"]
    assert receipt["tree"] == fresh["verified_tree"]
    assert counter.read_text() == "old\nold\n"
    assert [row for row in persisted(tools, "T1")["evidence"] if row[0] == old_receipt["id"]] == [
        row for row in rows["evidence"] if row[0] == old_receipt["id"]]


def test_validation_after_classification_preparation_is_atomic(project, monkeypatch):
    tools, _, _, edited, root, wip = prepare_documentation_restart(project)
    commands = tools.runtime.task_commands
    prepare = commands._prepare_restarted_creation
    entered = []

    def incompatible_contract(contract, *args, **kwargs):
        candidate = kwargs["current_registry"]
        assert candidate.executable_obligations == ()
        assert candidate.revision == 1
        entered.append(True)
        invalid = deepcopy(contract)
        invalid["stage_contracts"][0]["allowed_paths"] = ["src/**"]
        return prepare(invalid, *args, **kwargs)

    monkeypatch.setattr(commands, "_prepare_restarted_creation", incompatible_contract)
    before = conservation(tools, root, wip)
    with pytest.raises(PoiseError, match="allowed_paths|scope|пут|contract"):
        ready_task(tools, "T1", edited["revision"])
    assert entered == [True]
    assert conservation(tools, root, wip) == before


def test_promotion_failure_rolls_back_reclassified_candidate(project, monkeypatch):
    tools, _, _, edited, root, wip = prepare_documentation_restart(project)
    promote = SqliteTaskRepository.promote_newborn
    entered = []

    def fail_after_promotion(repository, *args, **kwargs):
        promote(repository, *args, **kwargs)
        entered.append(True)
        raise PoiseError("Injected reclassified promotion failure")

    monkeypatch.setattr(SqliteTaskRepository, "promote_newborn", fail_after_promotion)
    before = conservation(tools, root, wip)
    with pytest.raises(PoiseError, match="Injected reclassified promotion failure"):
        ready_task(tools, "T1", edited["revision"])
    assert entered == [True]
    assert conservation(tools, root, wip) == before
