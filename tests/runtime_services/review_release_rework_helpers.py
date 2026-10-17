"""Test-owned arrangements; public commands create every current lifecycle event."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from batch.helpers import request, result, verify
from conftest import WorkPoise, bind_task_requirements, git, write_json
from poise.application.work import WorkTools


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/review-release-rework"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def call(client, operation, **arguments):
    return client.invoke(request(operation, arguments))


def acquire(client, task=None):
    return call(client, "bootstrap", task={"id": "T1"} if task is None else task,
                decision=None, feedback=None, rework_stage=None)


def advance(client):
    return call(client, "advance", task_id="T1", request_id="rr-progress")


def preserve(client, request_id):
    return call(client, "handoff", request_id=request_id,
                reason="Transfer the actual current result to the other test participant.",
                result=None, commit_message=None, artifact_paths=[])


def release(client, request_id):
    version = client.runtime.task_queries.record("T1")["version"]
    packet = request("release", {"task_id": "T1", "request_id": request_id,
                                "expected_version": version,
                                "reason": "The named executor continues this reviewed result."})
    receipt = client.invoke(packet)
    assert receipt["status"] == "ownership_released"
    assert receipt["after"]["task_id"] is None
    assert receipt["after"]["worktree_task_id"] is None
    return packet, receipt


def prepare(project):
    data = fixture("scenarios.json")
    workflow = fixture("workflow.json")
    cfg = deepcopy(project["cfg"])
    cfg["automatic_checks"] = []
    cfg["runtime_services"]["handoff"] = data["handoff_settings"]
    path = Path(cfg["workflows"]["development"])
    if not path.is_absolute():
        path = project["config_path"].parent / path
    write_json(path, workflow)
    write_json(project["config_path"], cfg)
    task = deepcopy(project["task"])
    task.update(goal=data["goal"], requirements=data["requirements"],
                definition_of_done=data["definition_of_done"],
                methods=[], method_inputs=[], artifact_requirements=[],
                content_contract={"sections": [], "routes": [], "requirements": []})
    task.pop("executable_obligations", None)
    task["checks"] = {stage["id"]: [] for stage in workflow["stages"]}
    task["evidence_plan"] = {
        stage["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
        for stage in workflow["stages"]
    }
    task["stage_contracts"] = [
        {"stage_id": stage["id"], "allowed_paths": stage["allowed_paths"],
         "entry_requirements": [], "exit_requirements": [],
         "criteria": deepcopy(data["criteria"])}
        for stage in workflow["stages"]
    ]
    task["decomposition"] = {
        "kind": "ordinary", "integration": None,
        "phases": [{"stage": stage["id"], "skills": ["task-domain"], "areas": []}
                   for stage in workflow["stages"]],
    }
    bind_task_requirements(task, project["requirements_registry"])
    executor = WorkTools(WorkPoise(project["config_path"], "executor"))
    reviewer = WorkTools(WorkPoise(project["config_path"], "reviewer"))
    context = acquire(executor, task)
    return {"executor": executor, "reviewer": reviewer, "initial": context,
            "data": data, "project": project, "worktree": Path(context["worktree"])}


def seed_older_handoff(case):
    """Initial synthetic legacy input only; never edit current Task/history rows.

    The obsolete released row is inserted before any genuine current handoff.
    Its test bundle contains the actual disposable initial source. It is not an
    authenticated native receipt or reconstruction of missing ERP material.
    """
    client = case["executor"]
    worktree = case["worktree"]
    directory = case["project"]["root"] / "synthetic-historical-input"
    directory.mkdir()
    bundle = directory / "fixture-source.bundle"
    git(worktree, "bundle", "create", str(bundle), "HEAD")
    git(worktree, "bundle", "verify", str(bundle))
    specification = deepcopy(case["data"]["synthetic_handoff"])
    receipt = {
        "worktree": str(worktree), "tree": git(worktree, "rev-parse", "HEAD^{tree}"),
        "commit": git(worktree, "rev-parse", "HEAD"), "bundle_path": str(bundle),
        "bundle_digest": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "preserved_artifacts": [], "artifact_mapping": {},
    }
    specification.update(plan={"reason": "Synthetic legacy test input, not native evidence."},
                         receipt=receipt)
    with client.runtime.store.unit_of_work() as unit:
        unit.handoffs.insert(specification)
    case["synthetic_handoff"] = deepcopy(specification)


def saved_results(client):
    with client.runtime.store.transaction() as database:
        return [tuple(row) for row in database.execute(
            "SELECT submission_id,data FROM task_results WHERE task_id=? ORDER BY submission_id",
            ("T1",),
        )]


def review(case):
    executor, reviewer = case["executor"], case["reviewer"]
    completed = verify(executor, result(case["initial"], case["data"]["first_report"]))
    assert completed["status"] == "verified"
    case["first_results"] = saved_results(executor)
    case["source"] = (git(case["worktree"], "rev-parse", "HEAD"),
                      git(case["worktree"], "rev-parse", "HEAD^{tree}"))
    assert advance(executor)["status"] == "role_handoff_required"
    assert preserve(executor, "executor-first-review")["status"] == "handed_off"
    assert acquire(reviewer)["status"] == "verified"
    context = advance(reviewer)
    assert context["stage"] == "audit"
    payload = result(context, case["data"]["review_report"])
    payload["stage_work"] = {"coverage": case["data"]["review_coverage"],
                             "findings": [deepcopy(case["data"]["finding"])],
                             "resolution_decisions": []}
    outcome = verify(reviewer, payload)
    assert outcome["stage_outcome"] == "changes_requested"
    assert outcome["next_stage"] == "amend"
    case["reviewed_results"] = saved_results(reviewer)
    case["review_outcome"] = outcome
    return outcome


def assert_current_correction(case, context, iteration):
    assert context["task"] == "T1"
    assert context["stage"] == "amend", (
        context["status"], context["stage"], context["iteration"],
    )
    assert context["iteration"] == iteration
    assert context["status"] == "progression_work_required"
    assert case["executor"].runtime.task_queries.record("T1")["status"] == "active"
    actual = context["workflow"]["feedback"]["open_findings"]
    expected = {**case["data"]["finding"], "stage": "audit", "iteration": 1}
    assert actual == [expected]
    assert saved_results(case["executor"])[:len(case["first_results"])] == case["first_results"]
    assert (git(case["worktree"], "rev-parse", "HEAD"),
            git(case["worktree"], "rev-parse", "HEAD^{tree}")) == case["source"]
    ownership = case["executor"].runtime.ownership.snapshot("executor")
    assert ownership.task_id == "T1" and ownership.worktree_task_id == "T1"
    foreign = case["reviewer"].runtime.ownership.snapshot("reviewer")
    assert foreign.task_id is None and foreign.worktree_task_id is None


def current_correction(case):
    assert acquire(case["executor"])["status"] == "verified"
    context = advance(case["executor"])
    assert_current_correction(case, context, 1)
    return context


def add_held_wip(case):
    for relative, text in case["data"]["held_wip"].items():
        (case["worktree"] / relative).write_text(text, encoding="utf-8")


def snapshot(client, worktree, actors):
    runtime = client.runtime
    with runtime.store.unit_of_work() as unit:
        execution = deepcopy(unit.execution.load("T1"))
        evidence = deepcopy(unit.evidence.list_for("T1"))
    return {
        "task": deepcopy(runtime.task_queries.record("T1")),
        "execution": execution, "evidence": evidence, "results": saved_results(client),
        "ownership": {actor: runtime.ownership.snapshot(actor) for actor in actors},
        "head": git(worktree, "rev-parse", "HEAD"),
        "tree": git(worktree, "rev-parse", "HEAD^{tree}"),
        "index": git(worktree, "ls-files", "--stage"),
        "status": git(worktree, "status", "--porcelain=v1"),
        "source_bytes": {str(path.relative_to(worktree)): path.read_bytes()
                         for path in sorted((worktree / "src").rglob("*")) if path.is_file()},
    }
