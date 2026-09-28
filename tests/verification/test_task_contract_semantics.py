"""Task 0114 semantics survive retirement of one-time delivery fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from conftest import WorkPoise, bind_task_requirements, write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import DomainError
from poise.modules.tasks.definition import validate_creation
from tests.verification.test_schema_diagnostics import creation_fixture, decomposition_policy
from tests.verification.test_plan_contract import method

ROOT = Path(__file__).resolve().parents[2]


def direct_task_sql_snapshot(project, *, task_ids, session_ids):
    """Read exact Task state and ownership columns without production projections."""
    database = (
        project["root"]
        / project["cfg"]["paths"]["state"]
        / project["cfg"]["paths"]["database"]
    )
    task_ids = tuple(task_ids)
    session_ids = tuple(session_ids)
    task_marks = ", ".join("?" for _ in task_ids)
    session_marks = ", ".join("?" for _ in session_ids)
    queries = {
        "tasks": (
            f"SELECT id, status, stage_index, iteration, claimed_by, version, "
            f"current_submission_id, metadata FROM tasks WHERE id IN ({task_marks}) ORDER BY id",
            task_ids,
        ),
        "task_workflows": (
            f"SELECT task_id, data FROM task_workflows WHERE task_id IN ({task_marks}) ORDER BY task_id",
            task_ids,
        ),
        "task_execution": (
            f"SELECT task_id, data, version FROM task_execution "
            f"WHERE task_id IN ({task_marks}) ORDER BY task_id",
            task_ids,
        ),
        "sessions": (
            f"SELECT id, task_id FROM sessions WHERE id IN ({session_marks}) "
            f"OR task_id IN ({task_marks}) ORDER BY id",
            session_ids + task_ids,
        ),
        "journal": (
            f"SELECT seq, at, session_id, task_id, event, data FROM journal "
            f"WHERE task_id IN ({task_marks}) OR session_id IN ({session_marks}) ORDER BY seq",
            task_ids + session_ids,
        ),
        "task_events": (
            f"SELECT seq, task_id, version, at, data FROM task_events "
            f"WHERE task_id IN ({task_marks}) ORDER BY seq",
            task_ids,
        ),
        "submissions": (
            f"SELECT seq, task_id, stage, iteration, digest, data FROM submissions "
            f"WHERE task_id IN ({task_marks}) ORDER BY seq",
            task_ids,
        ),
        "task_methods": (
            f"SELECT task_id, method_id, version, data FROM task_methods "
            f"WHERE task_id IN ({task_marks}) ORDER BY task_id, method_id",
            task_ids,
        ),
        "content_contracts": (
            f"SELECT task_id, version, data FROM content_contracts "
            f"WHERE task_id IN ({task_marks}) ORDER BY task_id, version",
            task_ids,
        ),
        "task_artifacts": (
            f"SELECT task_id, artifact_id FROM task_artifacts "
            f"WHERE task_id IN ({task_marks}) ORDER BY task_id, artifact_id",
            task_ids,
        ),
        "artifacts": (
            f"SELECT id, owner, scope, path, digest FROM artifacts "
            f"WHERE owner IN ({task_marks}) ORDER BY id",
            task_ids,
        ),
        "handoffs": (
            f"SELECT seq, actor, request_id, task_id, state, data FROM handoffs "
            f"WHERE task_id IN ({task_marks}) OR actor IN ({session_marks}) ORDER BY seq",
            task_ids + session_ids,
        ),
        "action_runs": (
            f"SELECT task_id, stage, iteration, version, data FROM action_runs "
            f"WHERE task_id IN ({task_marks}) ORDER BY task_id, stage, iteration",
            task_ids,
        ),
        "action_events": (
            f"SELECT seq, task_id, stage, iteration, version, at, data FROM action_events "
            f"WHERE task_id IN ({task_marks}) ORDER BY seq",
            task_ids,
        ),
        "task_proofs": (
            f"SELECT task_id, data FROM task_proofs WHERE task_id IN ({task_marks}) ORDER BY task_id",
            task_ids,
        ),
        "task_proof_layers": (
            f"SELECT task_id, version, data FROM task_proof_layers "
            f"WHERE task_id IN ({task_marks}) ORDER BY task_id, version",
            task_ids,
        ),
        "work_packets": (
            f"SELECT task_id, stage, iteration, digest FROM work_packets "
            f"WHERE task_id IN ({task_marks}) ORDER BY task_id, stage, iteration",
            task_ids,
        ),
    }
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.row_factory = sqlite3.Row
        return {
            name: tuple(dict(row) for row in connection.execute(sql, parameters))
            for name, (sql, parameters) in queries.items()
        }


def direct_git_snapshot(project):
    """Observe refs, registered worktrees, and checkout state through Git itself."""
    repository = project["app"]
    return {
        "refs": subprocess.check_output(
            ["git", "-C", str(repository), "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"],
            text=True,
        ),
        "worktrees": subprocess.check_output(
            ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
            text=True,
        ),
        "status": subprocess.check_output(
            ["git", "-C", str(repository), "status", "--porcelain=v2", "--branch"],
            text=True,
        ),
    }


def definition(goal_type="development"):
    intent, _ = creation_fixture()
    task = intent["task"]
    process = json.loads((ROOT / "config/catalogue/process-templates" / f"{goal_type}.json").read_text())
    task.update(id="CONTRACT", goal_type=goal_type, goal="Verify synthetic Task contract semantics.")
    if goal_type == "documentation":
        task.pop("executable_obligations")
    entry = process["route"]["entry"]
    task["methods"] = [method("BASELINE", plan={
        "responsibility": "Guard the existing repository before produced changes.",
        "change_surface": [], "red_stages": [], "green_stages": [entry], "red_failure": None,
    })]
    task["method_inputs"] = [{
        "method_id": "BASELINE", "repository_inputs": [], "future_outputs": [],
        "reference_profile": {"runner": "python", "parser": "inline-no-path-arguments", "version": 1},
    }]
    stages = [stage["id"] for stage in process["stages"]]
    task["checks"] = {stage: ["BASELINE"] if stage == entry else [] for stage in stages}
    task["evidence_plan"] = {stage: {
        "arguments": [], "review_arguments": [],
        "subject_methods": {"BASELINE": {"exit_codes": [0], "stdout_contains": ["OK"], "stderr_contains": []}} if stage == entry else {},
    } for stage in stages}
    requirements = process["content_contract"]["requirements"]
    task["stage_contracts"] = [{
        "stage_id": stage["id"], "allowed_paths": stage["allowed_paths"],
        "entry_requirements": [r["id"] for r in requirements if r["phase"] == "pre" and stage["id"] in r["stages"]],
        "exit_requirements": [r["id"] for r in requirements if r["phase"] == "post" and stage["id"] in r["stages"]],
    } for stage in process["stages"]]
    task["decomposition"]["phases"] = [{"stage": stage, "skills": ["workflow"], "areas": []} for stage in stages]
    if goal_type == "documentation":
        task["methods"] = []
        task["method_inputs"] = []
        task["checks"] = {stage: [] for stage in stages}
        for stage in process["stages"]:
            plan = task["evidence_plan"][stage["id"]]
            plan["subject_methods"] = {}
            if stage["handler"] == "check":
                plan["arguments"] = [{
                    "id": stage["id"] + "-argument", "kind": "logical",
                    "phase": "prepare", "observation_methods": [],
                }]
    return task, process


def immutable_write_conflict(
    task,
    process,
    *,
    producer,
    consumer,
    writer,
    writer_is_artifact=True,
):
    """Declare one ordinary artifact, then plan a later write to the same path."""
    path = "build/file.md"
    source = {"kind": "stage_output", "producer_stage": producer}
    writer_source = {"kind": "stage_output", "producer_stage": writer}
    task["artifact_requirements"] = [{
        "scope": "task", "pattern": path, "minimum": 1, "maximum": 1,
    }]
    requirements = [
        {
            "id": "immutable-output", "kind": "artifact", "stages": [producer],
            "phase": "post", "scope": "task", "pattern": path,
            "minimum": 1, "maximum": 1, "source": source,
        },
        {
            "id": "immutable-input", "kind": "artifact", "stages": [consumer],
            "phase": "pre", "scope": "task", "pattern": path,
            "minimum": 1, "maximum": 1, "source": source,
        },
    ]
    if writer_is_artifact:
        requirements.append({
            "id": "late-output", "kind": "artifact", "stages": [writer],
            "phase": "post", "scope": "task", "pattern": path,
            "minimum": 1, "maximum": 1, "source": writer_source,
        })
    task["content_contract"]["requirements"].extend(requirements)
    contracts = {item["stage_id"]: item for item in task["stage_contracts"]}
    process_stages = {item["id"]: item for item in process["stages"]}
    for stage in dict.fromkeys((producer, writer)):
        contracts[stage]["allowed_paths"] = list(dict.fromkeys(
            contracts[stage]["allowed_paths"] + ["build/**"]
        ))
        process_stages[stage]["allowed_paths"] = list(contracts[stage]["allowed_paths"])
    contracts[producer]["exit_requirements"].append("immutable-output")
    contracts[consumer]["entry_requirements"].append("immutable-input")
    if writer_is_artifact:
        contracts[writer]["exit_requirements"].append("late-output")
    late_write = method("LATE_WRITE", plan={
        "responsibility": "Verify the planned later write.",
        "change_surface": [path], "red_stages": [], "green_stages": [writer],
        "red_failure": None,
    })
    late_write["argv"] = [sys.executable, "-B", "-m", "pytest", path]
    late_write["stdout_contains"] = []
    task["methods"].append(late_write)
    task["method_inputs"].append({
        "method_id": "LATE_WRITE", "repository_inputs": [],
        "future_outputs": [{"path": path, "producer_stage": writer}],
        "reference_profile": {"runner": "pytest", "parser": "positional-paths", "version": 1},
    })
    task["checks"][writer].append("LATE_WRITE")
    return task, process


def existing_artifact_write_conflict(task, process, *, source, writer):
    """Require an already immutable artifact, then plan a later write to it."""
    path = "build/file.md"
    entry = process["route"]["entry"]
    task["artifact_requirements"] = [{
        "scope": "task", "pattern": path, "minimum": 1, "maximum": 1,
    }]
    task["content_contract"]["requirements"].append({
        "id": "immutable-input", "kind": "artifact", "stages": [entry],
        "phase": "pre", "scope": "task", "pattern": path,
        "minimum": 1, "maximum": 1, "source": source,
    })
    contracts = {item["stage_id"]: item for item in task["stage_contracts"]}
    process_stages = {item["id"]: item for item in process["stages"]}
    contracts[entry]["entry_requirements"].append("immutable-input")
    contracts[writer]["allowed_paths"] = list(dict.fromkeys(
        contracts[writer]["allowed_paths"] + ["build/**"]
    ))
    process_stages[writer]["allowed_paths"] = list(contracts[writer]["allowed_paths"])
    late_write = method("LATE_WRITE", plan={
        "responsibility": "Verify the planned later write.",
        "change_surface": [path], "red_stages": [], "green_stages": [writer],
        "red_failure": None,
    })
    late_write["argv"] = [sys.executable, "-B", "-m", "pytest", path]
    late_write["stdout_contains"] = []
    task["methods"].append(late_write)
    task["method_inputs"].append({
        "method_id": "LATE_WRITE", "repository_inputs": [],
        "future_outputs": [{"path": path, "producer_stage": writer}],
        "reference_profile": {"runner": "pytest", "parser": "positional-paths", "version": 1},
    })
    task["checks"][writer].append("LATE_WRITE")
    return task, process


def remove_late_write_method(task, writer):
    task["methods"] = [item for item in task["methods"] if item["id"] != "LATE_WRITE"]
    task["method_inputs"] = [
        item for item in task["method_inputs"] if item["method_id"] != "LATE_WRITE"
    ]
    task["checks"][writer].remove("LATE_WRITE")


@pytest.mark.parametrize("goal_type", ["development", "documentation"])
def test_baseline_guard_domain_contract(goal_type):
    task, process = definition(goal_type)
    validated = validate_creation(task, process, [], decomposition_policy())
    assert validated["contract"] == task
    if goal_type == "development":
        assert task["methods"][0]["verification_plan"]["change_surface"] == []
        assert task["checks"][process["route"]["entry"]] == ["BASELINE"]
    else:
        assert task["methods"] == []
        assert "executable_obligations" not in task


def test_produced_result_cannot_disguise_itself_as_empty_surface_baseline():
    task, process = definition()
    entry = process["route"]["entry"]
    task["methods"][0]["verification_plan"]["green_stages"] = ["implementation"]
    task["checks"][entry] = []
    task["checks"]["implementation"] = ["BASELINE"]
    with pytest.raises(DomainError, match="route entry"):
        validate_creation(task, process, [], decomposition_policy())


@pytest.mark.parametrize("goal_type", ["development", "documentation"])
def test_obligations_field_follows_exact_process_schema(goal_type):
    task, process = definition(goal_type)
    if goal_type == "development":
        del task["executable_obligations"]
    else:
        task["executable_obligations"] = []
    with pytest.raises(DomainError, match="executable_obligations"):
        validate_creation(task, process, [], decomposition_policy())


@pytest.mark.parametrize("goal_type", ["development", "documentation"])
def test_public_creation_preserves_baseline_and_obligations(project, goal_type):
    task, process = definition(goal_type)
    cfg = project["cfg"]
    cfg["automatic_checks"] = []
    cfg["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 9999}, "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    cfg["task_decomposition"] = decomposition_policy()
    cfg["processes"] = {goal_type: f"config/processes/{goal_type}.json"}
    write_json(project["root"] / cfg["processes"][goal_type], process)
    write_json(project["config_path"], cfg)
    bind_task_requirements(task, project["requirements_registry"])
    task.pop("id")
    runtime = WorkPoise(project["config_path"], "synthetic-contract-owner")
    tools = WorkTools(runtime)
    context = tools.invoke({"operation": "bootstrap", "input": {
        "task": {"request_id": "synthetic-contract-creation", "task": task},
        "decision": None, "feedback": None, "rework_stage": None,
    }, "messages": []})
    assert context["status"] == "active"
    registry = tools.invoke({"operation": "show", "input": {
        "queries": [{"id": "registry", "kind": "verification_registry"}],
    }, "messages": []})["results"][0]["value"]
    assert registry["executable_obligations"] == task.get("executable_obligations", [])
    if goal_type == "documentation":
        assert registry["current"] == []
        return
    baseline = next(item["method"] for item in registry["current"] if item["method"]["id"] == "BASELINE")
    assert baseline["verification_plan"]["change_surface"] == []
    assert baseline["verification_plan"]["green_stages"] == [process["route"]["entry"]]


def test_creation_rejects_earlier_immutable_artifact_rewritten_by_later_stage(project):
    task = deepcopy(project["task"])
    process = deepcopy(project["process"])
    for stage in process["stages"]:
        stage["rework_targets"] = ["tests"]
    immutable_write_conflict(
        task,
        process,
        producer="tests",
        consumer="test_review",
        writer="implementation",
    )
    cfg = project["cfg"]
    cfg["automatic_checks"] = []
    cfg["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 9999}, "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    write_json(project["root"] / cfg["processes"]["development"], process)
    write_json(project["config_path"], cfg)
    task.pop("id")
    tools = WorkTools(WorkPoise(project["config_path"], "immutable-creation-owner"))
    database_before = direct_task_sql_snapshot(
        project,
        task_ids=("0001",),
        session_ids=("immutable-creation-owner",),
    )
    git_before = direct_git_snapshot(project)

    with pytest.raises(DomainError, match="immutable|неизмен"):
        tools.invoke({"operation": "bootstrap", "input": {
            "task": {"request_id": "immutable-write-creation", "task": task},
            "decision": None, "feedback": None, "rework_stage": None,
        }, "messages": []})

    database_after = direct_task_sql_snapshot(
        project,
        task_ids=("0001",),
        session_ids=("immutable-creation-owner",),
    )
    assert database_after == database_before
    assert direct_git_snapshot(project) == git_before
    assert database_after["tasks"] == ()
    assert database_after["task_execution"] == ()
    assert all(row["task_id"] is None for row in database_after["sessions"])
    assert database_after["journal"] == ()


def test_creation_rejects_later_stage_output_without_method_future_output(project):
    task = deepcopy(project["task"])
    process = deepcopy(project["process"])
    for stage in process["stages"]:
        stage["rework_targets"] = ["tests"]
    immutable_write_conflict(
        task,
        process,
        producer="tests",
        consumer="test_review",
        writer="implementation",
    )
    remove_late_write_method(task, "implementation")
    cfg = project["cfg"]
    cfg["automatic_checks"] = []
    cfg["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 9999}, "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    write_json(project["root"] / cfg["processes"]["development"], process)
    write_json(project["config_path"], cfg)
    task.pop("id")
    tools = WorkTools(WorkPoise(project["config_path"], "stage-output-creation-owner"))
    database_before = direct_task_sql_snapshot(
        project,
        task_ids=("0001",),
        session_ids=("stage-output-creation-owner",),
    )
    git_before = direct_git_snapshot(project)

    with pytest.raises(DomainError, match="immutable|неизмен"):
        tools.invoke({"operation": "bootstrap", "input": {
            "task": {"request_id": "stage-output-write-creation", "task": task},
            "decision": None, "feedback": None, "rework_stage": None,
        }, "messages": []})

    database_after = direct_task_sql_snapshot(
        project,
        task_ids=("0001",),
        session_ids=("stage-output-creation-owner",),
    )
    assert database_after == database_before
    assert direct_git_snapshot(project) == git_before
    assert database_after["tasks"] == ()
    assert database_after["task_execution"] == ()


@pytest.mark.parametrize(("first_pattern", "later_pattern"), [
    ("build/*.md", "build/file.*"),
    ("build/[! -~☃].md", "build/?.md"),
])
def test_creation_rejects_overlapping_later_stage_output_patterns(
    project,
    first_pattern,
    later_pattern,
):
    task = deepcopy(project["task"])
    process = deepcopy(project["process"])
    for stage in process["stages"]:
        stage["rework_targets"] = ["tests"]
    immutable_write_conflict(
        task,
        process,
        producer="tests",
        consumer="test_review",
        writer="implementation",
    )
    remove_late_write_method(task, "implementation")
    task["artifact_requirements"][0]["pattern"] = first_pattern
    for requirement in task["content_contract"]["requirements"]:
        if requirement["id"] in {"immutable-output", "immutable-input"}:
            requirement["pattern"] = first_pattern
        elif requirement["id"] == "late-output":
            requirement["pattern"] = later_pattern
    cfg = project["cfg"]
    cfg["automatic_checks"] = []
    cfg["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 9999}, "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    write_json(project["root"] / cfg["processes"]["development"], process)
    write_json(project["config_path"], cfg)
    task.pop("id")
    tools = WorkTools(WorkPoise(project["config_path"], "glob-creation-owner"))
    database_before = direct_task_sql_snapshot(
        project,
        task_ids=("0001",),
        session_ids=("glob-creation-owner",),
    )
    git_before = direct_git_snapshot(project)

    with pytest.raises(DomainError, match="immutable|неизмен"):
        tools.invoke({"operation": "bootstrap", "input": {
            "task": {"request_id": "overlapping-glob-creation", "task": task},
            "decision": None, "feedback": None, "rework_stage": None,
        }, "messages": []})

    database_after = direct_task_sql_snapshot(
        project,
        task_ids=("0001",),
        session_ids=("glob-creation-owner",),
    )
    assert database_after == database_before
    assert direct_git_snapshot(project) == git_before
    assert database_after["tasks"] == ()
    assert database_after["task_execution"] == ()


def test_creation_rejects_preexisting_artifact_rewrite(project):
    task = deepcopy(project["task"])
    process = deepcopy(project["process"])
    for stage in process["stages"]:
        stage["rework_targets"] = ["tests"]
    existing_artifact_write_conflict(
        task,
        process,
        source={"kind": "preexisting"},
        writer="implementation",
    )
    cfg = project["cfg"]
    cfg["automatic_checks"] = []
    cfg["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 9999}, "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    write_json(project["root"] / cfg["processes"]["development"], process)
    write_json(project["config_path"], cfg)
    task.pop("id")
    tools = WorkTools(WorkPoise(project["config_path"], "preexisting-creation-owner"))
    database_before = direct_task_sql_snapshot(
        project,
        task_ids=("0001",),
        session_ids=("preexisting-creation-owner",),
    )
    git_before = direct_git_snapshot(project)

    with pytest.raises(DomainError, match="immutable|неизмен"):
        tools.invoke({"operation": "bootstrap", "input": {
            "task": {"request_id": "preexisting-write-creation", "task": task},
            "decision": None, "feedback": None, "rework_stage": None,
        }, "messages": []})

    database_after = direct_task_sql_snapshot(
        project,
        task_ids=("0001",),
        session_ids=("preexisting-creation-owner",),
    )
    assert database_after == database_before
    assert direct_git_snapshot(project) == git_before
    assert database_after["tasks"] == ()
    assert database_after["task_execution"] == ()


def test_creation_allows_distinct_later_stage_output(project):
    task = deepcopy(project["task"])
    process = deepcopy(project["process"])
    for stage in process["stages"]:
        stage["rework_targets"] = ["tests"]
    immutable_write_conflict(
        task,
        process,
        producer="tests",
        consumer="test_review",
        writer="implementation",
    )
    remove_late_write_method(task, "implementation")
    late_output = next(
        item for item in task["content_contract"]["requirements"]
        if item["id"] == "late-output"
    )
    late_output["pattern"] = "build/other.md"
    task.pop("requirements_snapshot")
    task.pop("requirements_agreement")

    validated = validate_creation(task, process, [], project["cfg"]["task_decomposition"])

    assert next(
        item for item in validated["contract"]["content_contract"]["requirements"]
        if item["id"] == "late-output"
    )["pattern"] == "build/other.md"


def test_creation_allows_nonoverlapping_later_stage_output_patterns(project):
    task = deepcopy(project["task"])
    process = deepcopy(project["process"])
    for stage in process["stages"]:
        stage["rework_targets"] = ["tests"]
    immutable_write_conflict(
        task,
        process,
        producer="tests",
        consumer="test_review",
        writer="implementation",
    )
    remove_late_write_method(task, "implementation")
    task["artifact_requirements"][0]["pattern"] = "build/a*.md"
    for requirement in task["content_contract"]["requirements"]:
        if requirement["id"] in {"immutable-output", "immutable-input"}:
            requirement["pattern"] = "build/a*.md"
        elif requirement["id"] == "late-output":
            requirement["pattern"] = "build/b*.md"
    task.pop("requirements_snapshot")
    task.pop("requirements_agreement")

    validated = validate_creation(task, process, [], project["cfg"]["task_decomposition"])

    patterns = {
        item["pattern"] for item in validated["contract"]["content_contract"]["requirements"]
        if item["id"] in {"immutable-output", "late-output"}
    }
    assert patterns == {"build/a*.md", "build/b*.md"}


def test_creation_allows_zero_count_preexisting_predicate_before_first_output(project):
    task = deepcopy(project["task"])
    process = deepcopy(project["process"])
    for stage in process["stages"]:
        stage["rework_targets"] = ["tests"]
    existing_artifact_write_conflict(
        task,
        process,
        source={"kind": "preexisting"},
        writer="implementation",
    )
    task["artifact_requirements"] = []
    requirement = task["content_contract"]["requirements"][0]
    requirement["minimum"] = 0
    requirement["maximum"] = 0
    cfg = project["cfg"]
    cfg["automatic_checks"] = []
    cfg["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 9999}, "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    write_json(project["root"] / cfg["processes"]["development"], process)
    write_json(project["config_path"], cfg)
    task.pop("id")
    tools = WorkTools(WorkPoise(project["config_path"], "zero-count-creation-owner"))

    context = tools.invoke({"operation": "bootstrap", "input": {
        "task": {"request_id": "zero-count-creation", "task": task},
        "decision": None, "feedback": None, "rework_stage": None,
    }, "messages": []})

    assert context["status"] == "active"
    assert context["task"] == "0001"


def test_creation_allows_future_output_at_its_first_declared_producer(project):
    task = deepcopy(project["task"])
    process = deepcopy(project["process"])
    for stage in process["stages"]:
        stage["rework_targets"] = ["tests"]
    immutable_write_conflict(
        task,
        process,
        producer="tests",
        consumer="test_review",
        writer="tests",
    )
    task.pop("requirements_snapshot")
    task.pop("requirements_agreement")

    validated = validate_creation(task, process, [], project["cfg"]["task_decomposition"])

    assert validated["contract"]["method_inputs"][-1]["future_outputs"] == [{
        "path": "build/file.md", "producer_stage": "tests",
    }]


def test_ready_rejects_earlier_immutable_artifact_rewritten_by_later_stage(project):
    from batch.helpers import configure
    from tasks.test_newborn_lifecycle import create, edit, task_action

    ordinary_task = deepcopy(project["task"])
    ordinary_process = deepcopy(project["process"])
    for stage in ordinary_process["stages"]:
        stage["rework_targets"] = ["implementation"]
    immutable_write_conflict(
        ordinary_task,
        ordinary_process,
        producer="implementation",
        consumer="code_review",
        writer="tests",
        writer_is_artifact=False,
    )
    project["cfg"]["automatic_checks"] = []
    process_path = project["root"] / project["cfg"]["processes"]["development"]
    write_json(process_path, ordinary_process)
    write_json(project["config_path"], project["cfg"])
    configure(project)
    ordinary_tools = WorkTools(WorkPoise(project["config_path"], "ordinary-ready-owner"))
    ordinary_born = create(ordinary_tools, "ORDINARY-EARLY-WRITE")
    ordinary_task["id"] = "ORDINARY-EARLY-WRITE"
    ordinary_task.pop("goal_type")
    ordinary_edited = edit(
        ordinary_tools,
        "ORDINARY-EARLY-WRITE",
        ordinary_born["revision"],
        ordinary_task | {"goal_type": "development"},
        "ordinary-early-write-contract",
    )
    ordinary_ready = task_action(
        ordinary_tools,
        action="ready",
        request_id="ordinary-early-write-ready",
        task_id="ORDINARY-EARLY-WRITE",
        expected_revision=ordinary_edited["revision"],
    )
    assert ordinary_ready["status"] == "available"

    rework_task = deepcopy(project["task"])
    rework_process = deepcopy(ordinary_process)
    rework_stages = {item["id"]: item for item in rework_process["stages"]}
    rework_stages["implementation"]["rework_targets"] = ["tests"]
    immutable_write_conflict(
        rework_task,
        rework_process,
        producer="implementation",
        consumer="code_review",
        writer="tests",
        writer_is_artifact=False,
    )
    write_json(process_path, rework_process)
    configure(project)
    rework_tools = WorkTools(WorkPoise(project["config_path"], "immutable-ready-owner"))
    born = create(rework_tools, "IMMUTABLE-READY")
    rework_task["id"] = "IMMUTABLE-READY"
    rework_task.pop("goal_type")
    edited = edit(
        rework_tools,
        "IMMUTABLE-READY",
        born["revision"],
        rework_task | {"goal_type": "development"},
        "immutable-ready-contract",
    )
    database_before = direct_task_sql_snapshot(
        project,
        task_ids=("IMMUTABLE-READY",),
        session_ids=("immutable-ready-owner",),
    )
    git_before = direct_git_snapshot(project)

    with pytest.raises(DomainError, match="immutable|неизмен"):
        task_action(
            rework_tools,
            action="ready",
            request_id="immutable-write-ready",
            task_id="IMMUTABLE-READY",
            expected_revision=edited["revision"],
        )

    database_after = direct_task_sql_snapshot(
        project,
        task_ids=("IMMUTABLE-READY",),
        session_ids=("immutable-ready-owner",),
    )
    assert database_after == database_before
    assert direct_git_snapshot(project) == git_before
    row = database_after["tasks"][0]
    assert row["id"] == "IMMUTABLE-READY"
    assert row["status"] == "newborn"
    assert row["claimed_by"] == "immutable-ready-owner"
    assert row["version"] == edited["revision"]
    assert database_after["task_execution"] == ()
    assert all(row["task_id"] is None for row in database_after["sessions"])


def test_ready_rejects_declared_arrival_artifact_rewrite(project):
    from batch.helpers import configure
    from tasks.test_newborn_lifecycle import create, edit, task_action

    task = deepcopy(project["task"])
    process = deepcopy(project["process"])
    for stage in process["stages"]:
        stage["rework_targets"] = ["tests"]
    existing_artifact_write_conflict(
        task,
        process,
        source={"kind": "declared_arrival", "arrival_stage": "tests"},
        writer="implementation",
    )
    project["cfg"]["automatic_checks"] = []
    write_json(project["root"] / project["cfg"]["processes"]["development"], process)
    write_json(project["config_path"], project["cfg"])
    configure(project)
    tools = WorkTools(WorkPoise(project["config_path"], "arrival-ready-owner"))
    born = create(tools, "ARRIVAL-READY")
    task["id"] = "ARRIVAL-READY"
    task.pop("goal_type")
    edited = edit(
        tools,
        "ARRIVAL-READY",
        born["revision"],
        task | {"goal_type": "development"},
        "arrival-ready-contract",
    )
    database_before = direct_task_sql_snapshot(
        project,
        task_ids=("ARRIVAL-READY",),
        session_ids=("arrival-ready-owner",),
    )
    git_before = direct_git_snapshot(project)

    with pytest.raises(DomainError, match="immutable|неизмен"):
        task_action(
            tools,
            action="ready",
            request_id="arrival-write-ready",
            task_id="ARRIVAL-READY",
            expected_revision=edited["revision"],
        )

    database_after = direct_task_sql_snapshot(
        project,
        task_ids=("ARRIVAL-READY",),
        session_ids=("arrival-ready-owner",),
    )
    assert database_after == database_before
    assert direct_git_snapshot(project) == git_before
    row = database_after["tasks"][0]
    assert row["id"] == "ARRIVAL-READY"
    assert row["status"] == "newborn"
    assert row["claimed_by"] == "arrival-ready-owner"
    assert row["version"] == edited["revision"]
    assert database_after["task_execution"] == ()


def test_trace_can_be_planned_but_is_required_before_code():
    from poise.modules.content_requirements.domain import ContentPolicy, ContentSnapshot
    stages = ("verification_planning", "test_implementation", "implementation")
    contract = {
        "sections": [],
        "routes": [{"id": "task-verification", "requirements": ["Synthetic requirement."], "points": [
            {"id": "red_method", "kind": "method", "fields": {}, "write_stages": ["verification_planning", "test_implementation"]},
            {"id": "green_method", "kind": "method", "fields": {}, "write_stages": ["verification_planning", "implementation"]},
        ]}],
        "requirements": [
            {"id": "red-before-test-code", "kind": "trace", "route": "task-verification", "point": "red_method", "stages": ["test_implementation"], "phase": "pre", "field_equals": {}},
            {"id": "green-before-production-code", "kind": "trace", "route": "task-verification", "point": "green_method", "stages": ["implementation"], "phase": "pre", "field_equals": {}},
        ],
    }
    policy = ContentPolicy.from_layers({"sections": [], "routes": [], "requirements": []}, contract, stages, ("Synthetic requirement.",), ("PLANNED_RED", "PLANNED_GREEN"), ())
    empty = ContentSnapshot((), ())
    assert policy.evaluate("verification_planning", "pre", empty, ()).passed
    for stage in stages[1:]:
        assert not policy.evaluate(stage, "pre", empty, ()).passed
    planned = policy.apply(empty, "verification_planning", {}, {"task-verification": {"red_method": "PLANNED_RED", "green_method": "PLANNED_GREEN"}})
    for stage in stages[1:]:
        assert policy.evaluate(stage, "pre", planned, ()).passed


@pytest.mark.parametrize('entry_name', ['reproduce', 'initial_observation'])
def test_empty_surface_observation_accepts_planner_named_entry(entry_name):
    task, process = definition()
    previous = process['route']['entry']

    def rename(value):
        if isinstance(value, str):
            return entry_name if value == previous else value
        if isinstance(value, list):
            return [rename(item) for item in value]
        if isinstance(value, dict):
            return {rename(key): rename(item) for key, item in value.items()}
        return value

    task, process = rename(task), rename(process)
    validated = validate_creation(task, process, [], decomposition_policy())
    assert validated['process']['route']['entry'] == entry_name
    assert validated['contract']['methods'][0]['verification_plan']['change_surface'] == []


if __name__ == "__main__":
    if sys.argv[1:] != ["regression"]:
        raise SystemExit("usage: test_task_contract_semantics.py regression")
    raise SystemExit(pytest.main([__file__, "-q"]))
