from copy import deepcopy
import importlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

from conftest import WorkPoise as Poise, write_json
from poise.application.work import WorkTools
from poise.artifacts import inspect_paths
from poise.common import PoiseError, load_config
from poise.interfaces.work import execute
from sprints.helpers import draft, publish, setup as sprint_setup, task as sprint_task
from transfer.helpers import (
    destination,
    enabled as enable_transfer,
    export,
    handoff_args,
    pick,
    restore,
)
from batch.helpers import request


def configured(project, *, standalone_tasks="standalone"):
    cfg = deepcopy(project["cfg"])
    cfg["paths"]["standalone_tasks"] = standalone_tasks
    return write_json(project["config_path"], cfg)


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


def test_all_task_artifact_consumers_use_the_shared_resolver():
    root = Path(__file__).resolve().parents[2]
    consumers = {
        "src/poise/runtime.py",
        "src/poise/interfaces/work.py",
        "src/poise/infrastructure/result_integration.py",
        "src/poise/infrastructure/transfers.py",
    }
    forbidden = ('paths["tasks"]', "paths['tasks']")
    for relative in consumers:
        source = (root / relative).read_text(encoding="utf-8")
        assert "task_root" in source, relative
        assert not any(value in source for value in forbidden), relative


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


def test_transfer_separates_nested_task_and_sprint_artifacts(project, tmp_path):
    sprint_setup(project)
    enable_transfer(project)
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
    saved = export(
        member,
        sprint="S",
        handoff=handoff_args(payload),
    )

    with zipfile.ZipFile(saved["package_path"]) as archive:
        names = archive.namelist()
    assert any(name.startswith("files/task/MEMBER/") and name.endswith("task-note.txt") for name in names)
    assert any(name.startswith("files/sprint/S/") and name.endswith("sprint-note.txt") for name in names)
    assert not any("files/sprint/S/task/MEMBER" in name for name in names)

    target = destination(project, tmp_path / "destination")
    receiver = WorkTools(Poise(target["config_path"], "receiver"))
    restore(receiver, saved["package_path"], saved["package_digest"])
    restored = pick(receiver, "MEMBER")
    expected = target["root"] / "state" / "sprints" / "S" / "task" / "MEMBER"
    assert Path(restored["task_root"]) == expected
    restored_paths = [Path(path) for path in restored["result_template"]["artifact_paths"]]
    assert any(path.is_relative_to(expected) for path in restored_paths)
    assert any(
        path.is_relative_to(expected.parents[1]) and not path.is_relative_to(expected)
        for path in restored_paths
    )
