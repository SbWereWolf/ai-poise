from copy import deepcopy
import importlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

from conftest import WorkPoise as Poise, add_test, write_json
from poise.application.work import WorkTools
from poise.artifacts import inspect_paths
from poise.common import PoiseError, load_config
from poise.interfaces.work import execute
from sprints.helpers import draft, publish, setup as sprint_setup, task as sprint_task
from runtime_services.test_participant_continuity import release_packet
from batch.helpers import request
from result_integration.helpers import (
    integration_guard_method,
    integration_input,
    prepare_completed_task,
    source_change,
)
from task_cleanup.helpers import cleanup_input, prepare_cancelled_task


def configured(project, *, standalone_tasks="standalone"):
    cfg = deepcopy(project["cfg"])
    cfg["paths"]["standalone_tasks"] = standalone_tasks
    return write_json(project["config_path"], cfg)


def completed_sprint_member(project, *, accept):
    configured(project)
    sprint_setup(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    planned = draft(planner, [sprint_task(project, "MEMBER")])
    publish(planner, planned["revision"])

    tools = WorkTools(Poise(project["config_path"], "member"))
    context = tools.invoke(request("bootstrap", {
        "task": {"id": "MEMBER"},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    source = Path(context["worktree"]) / "src" / "sprint_member.py"
    source.write_text("VALUE = 'sprint member'\n", encoding="utf-8")
    payload = deepcopy(context["result_template"])
    payload["sections"]["report"] = "Create a Sprint-member result."
    payload["commit_message"] = "test: create sprint member result"
    verified = tools.invoke(request("verify", {"result": payload, "artifacts": []}))
    assert verified["status"] == "verified"
    if accept:
        completed = tools.invoke(request("accept", {}))
        assert completed["status"] == "completed"
    return tools, verified["commit"]


def test_configuration_requires_the_explicit_standalone_namespace(project):
    old = deepcopy(project["cfg"])
    old["paths"]["tasks"] = old["paths"].pop("standalone_tasks")
    write_json(project["config_path"], old)
    with pytest.raises(PoiseError, match="standalone_tasks"):
        load_config(project["config_path"])

    configured(project)
    _, cfg, _ = load_config(project["config_path"])
    assert cfg["paths"]["standalone_tasks"] == "standalone"
    assert "tasks" not in cfg["paths"]

    both = deepcopy(project["cfg"])
    both["paths"]["tasks"] = "tasks"
    write_json(project["config_path"], both)
    with pytest.raises(PoiseError, match="paths"):
        load_config(project["config_path"])


def test_task_root_selects_standalone_or_sprint_membership(project):
    module_name = "poise.infrastructure.task_paths"
    assert importlib.util.find_spec(module_name) is not None
    task_root = importlib.import_module(module_name).task_root
    configured(project)
    _, cfg, _ = load_config(project["config_path"])
    state = project["root"] / cfg["paths"]["state"]

    assert task_root(state, cfg["paths"], "T1", None) == state / "standalone" / "T1"
    member_root = task_root(state, cfg["paths"], "T1", "S1")
    assert member_root == state / "sprints" / "S1" / "task" / "T1"

    artifact = member_root / "artifacts" / "report.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("result", encoding="utf-8")
    inspected = inspect_paths(
        [str(artifact)],
        {"task": member_root, "sprint": member_root.parents[1]},
        {"task": "T1", "sprint": "S1"},
    )
    assert inspected[0]["scope"] == "task"

    output = io.StringIO()
    execute(
        Poise(project["config_path"], "standalone"),
        io.BytesIO(json.dumps({
            "operation": "bootstrap",
            "input": {
                "task": project["task"],
                "decision": None,
                "feedback": None,
                "rework_stage": None,
            },
            "messages": [],
        }).encode()),
        output,
    )
    receipt = json.loads(output.getvalue())
    context = json.loads(Path(receipt["response_path"]).read_text(encoding="utf-8"))
    assert Path(context["task_root"]) == state / "standalone" / "T1"
    assert Path(receipt["response_path"]).is_relative_to(state / "standalone" / "T1" / "runs")


def test_legacy_task_directory_is_ignored_without_migration_or_fallback(project):
    configured(project)
    state = project["root"] / "state"
    legacy = state / "task" / "T1"
    sentinel = legacy / "legacy-only.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("must remain in the unsupported legacy root\n", encoding="utf-8")

    tools = WorkTools(Poise(project["config_path"], "standalone"))
    context = tools.invoke(request("bootstrap", {
        "task": project["task"],
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))

    current = state / "standalone" / "T1"
    assert Path(context["task_root"]) == current
    assert sentinel.read_text(encoding="utf-8") == "must remain in the unsupported legacy root\n"
    assert not (current / sentinel.name).exists()
    assert not any(path.name == sentinel.name for path in current.rglob("*"))


def test_task_path_consumers_do_not_duplicate_namespace_selection(project):
    repository = Path(__file__).parents[2]
    owner = repository / "src" / "poise" / "infrastructure" / "task_paths.py"
    paths = importlib.import_module("poise.infrastructure.task_paths")
    views = importlib.import_module("poise.infrastructure.result_views")
    consumers = {
        "poise.interfaces.work": {"sprint_root": paths.sprint_root,
                                  "task_response_root": views.task_response_root},
        "poise.runtime": {"sprint_root": paths.sprint_root, "task_root": paths.task_root},
        "poise.infrastructure.result_integration": {"task_root": paths.task_root},
        "poise.infrastructure.result_views": {"task_root": paths.task_root,
                                              "sprint_root": paths.sprint_root},
    }

    assert 'paths["standalone_tasks"]' in owner.read_text(encoding="utf-8")
    for name, bindings in consumers.items():
        consumer = importlib.import_module(name)
        source = Path(consumer.__file__).read_text(encoding="utf-8")
        for binding, canonical in bindings.items():
            assert getattr(consumer, binding) is canonical, name
        assert 'paths["standalone_tasks"]' not in source, consumer
        assert 'paths["sprints"]' not in source, consumer
        assert ' / "task"' not in source, consumer


def test_runtime_evidence_query_artifact_and_handoff_receipts_are_standalone(project):
    configured(project)
    runtime = Poise(project["config_path"], "consumer")
    tools = WorkTools(runtime)
    context = tools.invoke(request("bootstrap", {
        "task": project["task"],
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    expected = project["root"] / "state" / "standalone" / "T1"

    artifacts = tools.invoke(request("artifacts", {"items": [{
        "scope": "task",
        "path": "consumer.txt",
        "source": {"kind": "text", "text": "owned by standalone task"},
    }]}))["artifact_paths"]
    assert Path(artifacts[0]).is_relative_to(expected / "artifacts")

    add_test(context["worktree"])
    payload = deepcopy(context["result_template"])
    payload["sections"]["report"] = "Exercise evidence placement through the public verifier."
    payload["artifact_paths"] = artifacts
    payload["commit_message"] = "test: exercise standalone consumers"
    verified = tools.invoke(request("verify", {"result": payload, "artifacts": []}))
    assert verified["status"] == "verified"
    assert Path(verified["checks"][0]["stdout"]).is_relative_to(expected / "runs")
    assert Path(verified["checks"][0]["stderr"]).is_relative_to(expected / "runs")

    output = io.StringIO()
    execute(
        Poise(project["config_path"], "consumer"),
        io.BytesIO(json.dumps(request("show", {
            "queries": [{"id": "current", "kind": "task"}],
        })).encode()),
        output,
    )
    query_receipt = json.loads(output.getvalue())
    assert Path(query_receipt["response_path"]).is_relative_to(expected / "runs")

    released = tools.invoke(request("release", {
        "request_id": "standalone-consumer-release",
        "reason": "Prove receipt ownership.",
        "task_id": tools.runtime.current_task()['id'],
        "expected_version": tools.runtime.current_task()['_version'],
    }))
    assert released['status'] == 'ownership_released'
    assert not (expected / 'handoffs').exists()


def test_cleanup_preservation_uses_the_standalone_owner_root(project):
    configured(project)
    tools, _, commit, _ = prepare_cancelled_task(project)
    cleaned = tools.invoke(request("cleanup", cleanup_input(commit, kind="preserved")))
    expected = project["root"] / "state" / "standalone" / "T1"

    assert cleaned["status"] == "cleanup_complete"
    assert Path(cleaned["disposition"]["bundle_path"]).is_relative_to(expected)


def test_result_integration_check_receipts_use_the_standalone_owner_root(project):
    configured(project)
    guard = integration_guard_method()
    tools, _, source = prepare_completed_task(
        project,
        source_change,
        methods=[guard],
        checks=[guard["id"]],
    )
    integrated = tools.invoke(request("integrate", integration_input(project, source)))
    expected = project["root"] / "state" / "standalone" / "T1"

    assert integrated["status"] == "integrated"
    assert Path(integrated["checks"][0]["stdout"]).is_relative_to(expected / "runs")
    assert Path(integrated["checks"][0]["stderr"]).is_relative_to(expected / "runs")


def test_cleanup_preservation_uses_the_sprint_member_owner_root(project):
    tools, commit = completed_sprint_member(project, accept=False)
    cancelled = tools.invoke(request("cancel", {"reason": "Exercise Sprint-member cleanup."}))
    assert cancelled["status"] == "cancelled"
    cleaned = tools.invoke(request("cleanup", {
        "request_id": "cleanup-sprint-member",
        "task_id": "MEMBER",
        "commit_disposition": {"kind": "preserved", "expected_commit": commit},
        "authorization": "The test preserves this exact Sprint-member commit.",
    }))
    expected = project["root"] / "state" / "sprints" / "S" / "task" / "MEMBER"

    assert cleaned["status"] == "cleanup_complete"
    assert Path(cleaned["disposition"]["bundle_path"]).is_relative_to(expected)


def test_result_integration_check_receipts_use_the_sprint_member_owner_root(project):
    tools, commit = completed_sprint_member(project, accept=True)
    integrated = tools.invoke(request(
        "integrate",
        integration_input(project, commit, request_id="integrate-sprint-member", task_id="MEMBER"),
    ))
    expected = project["root"] / "state" / "sprints" / "S" / "task" / "MEMBER"

    assert integrated["status"] == "integrated"
    assert Path(integrated["checks"][0]["stdout"]).is_relative_to(expected / "runs")
    assert Path(integrated["checks"][0]["stderr"]).is_relative_to(expected / "runs")


def test_published_sprint_task_bootstrap_and_response_are_nested(project):
    configured(project)
    sprint_setup(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    planned = draft(planner, [sprint_task(project, "MEMBER")])
    publish(planner, planned["revision"])

    output = io.StringIO()
    execute(
        Poise(project["config_path"], "member"),
        io.BytesIO(json.dumps({
            "operation": "bootstrap",
            "input": {
                "task": {"id": "MEMBER"},
                "decision": None,
                "feedback": None,
                "rework_stage": None,
            },
            "messages": [],
        }).encode()),
        output,
    )
    receipt = json.loads(output.getvalue())
    expected = project["root"] / "state" / "sprints" / "S" / "task" / "MEMBER"
    context = json.loads(Path(receipt["response_path"]).read_text(encoding="utf-8"))
    assert Path(context["task_root"]) == expected
    assert Path(receipt["response_path"]).is_relative_to(expected / "runs")


def test_continuation_preserves_distinct_nested_task_and_sprint_artifacts(project, tmp_path):
    sprint_setup(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    planned = draft(planner, [sprint_task(project, "MEMBER")])
    publish(planner, planned["revision"])

    member = WorkTools(Poise(project["config_path"], "member"))
    context = member.invoke(request("bootstrap", {
        "task": {"id": "MEMBER"},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    made = member.invoke(request("artifacts", {"items": [
        {"scope": "task", "path": "task-note.txt", "source": {"kind": "text", "text": "task"}},
        {"scope": "sprint", "path": "sprint-note.txt", "source": {"kind": "text", "text": "sprint"}},
    ]}))
    payload = deepcopy(context["result_template"])
    payload["sections"]["report"] = "Preserve nested owners."
    payload["artifact_paths"] = made["artifact_paths"]
    payload["commit_message"] = "test: preserve nested owners"
    member.runtime.runner.submit(context["task"], member.runtime.session, payload)
    original = member.runtime.store.artifact_records('MEMBER')
    assert member.invoke(release_packet(member))['status'] == 'ownership_released'
    receiver = WorkTools(Poise(project['config_path'], 'next-member-participant'))
    restored = receiver.invoke(request('bootstrap', {
        'task': {'id': 'MEMBER'}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    expected = project['root'] / 'state/sprints/S/task/MEMBER'
    assert Path(restored["task_root"]) == expected
    assert restored['worktree'] == context['worktree']
    assert receiver.runtime.store.artifact_records('MEMBER') == original
    restored_paths = [Path(path) for path in restored["result_template"]["artifact_paths"]]
    assert any(path.is_relative_to(expected) for path in restored_paths)
    assert any(
        path.is_relative_to(expected.parents[1]) and not path.is_relative_to(expected)
        for path in restored_paths
    )
    assert {path.name: path.read_bytes() for path in restored_paths} == {
        'task-note.txt': b'task', 'sprint-note.txt': b'sprint'}
