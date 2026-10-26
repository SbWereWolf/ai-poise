"""Test-owned handoff arrangements; real WorkTools/Task/Git/SQLite effects.

Integration subject: public stage-result handoff and its existing owner boundaries.
Fixture construction does not derive any expected handoff response from production.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from batch.helpers import request, result
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError


FIXTURES = Path(__file__).parent / "fixtures" / "stage_result_handoff"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def configure(project, *, mode="roles", worktree=True, check_exit=0, large=False, gate=False):
    definitions = fixture("stage-definitions.json")
    names = {
        "roles": ["author", "review"],
        "same_role": ["author", "next_work", "review"],
        "final": ["author"],
        "publish": ["author", "review", "publish"],
    }[mode]
    stages = [deepcopy(definitions[name]) for name in names]
    stages[0]["transitions"] = {"complete": names[1] if len(names) > 1 else None}
    if mode == "publish":
        stages[1]["transitions"]["clear"] = "publish"
        stages[2]["role"] = "reviewer"
    workflow = {
        "goal_type": "development", "worktree_required": worktree,
        "route": {"entry": "author"}, "stages": stages,
        "benefit": {"git_categories": ["code", "documentation"], "sections": []},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
    }
    cfg = deepcopy(project["cfg"])
    cfg["automatic_checks"] = []
    write_json(project["config_path"], cfg)
    write_json(project["root"] / "config/workflows/development.json", workflow)
    counter = project["root"] / "handoff-check-count.txt"
    script = (FIXTURES / "count_check.py").read_text(encoding="utf-8")
    script = script.replace("COUNTER_PATH_LITERAL", repr(str(counter)))
    script = script.replace("EXIT_CODE_LITERAL", repr(check_exit))
    script = script.replace("OUTPUT_SIZE_LITERAL", "3000" if large else "0")
    method = fixture("check-method.json")
    method["argv"] = [sys.executable, "-B", "-c", script]
    task = deepcopy(project["task"])
    task.update(
        methods=[method],
        checks={name: (["CHECK-ONCE"] if name == "author" else []) for name in names},
        method_inputs=[{
            "method_id": "CHECK-ONCE", "repository_inputs": [], "future_outputs": [],
            "reference_profile": {
                "runner": "python", "parser": "inline-no-path-arguments", "version": 1,
            },
        }],
        evidence_plan={name: {"subject_methods": {}, "arguments": [], "review_arguments": []}
                       for name in names},
        decomposition={"kind": "ordinary", "phases": [
            {"stage": name, "skills": ["task-domain"], "areas": []} for name in names
        ], "integration": None},
        stage_contracts=[{
            "stage_id": stage["id"], "allowed_paths": stage["allowed_paths"],
            "entry_requirements": [], "exit_requirements": [],
            "criteria": {"requirements": [], "definition_of_ready": [], "definition_of_done": []},
        } for stage in stages],
        content_contract={"sections": [], "routes": [], "requirements": []},
    )
    if gate:
        assert mode == "same_role"
        task["content_contract"]["requirements"] = [{
            "id": "next-work-entry", "kind": "artifact", "scope": "task",
            "pattern": "gate.txt", "minimum": 1, "maximum": 1,
            "stages": ["next_work"], "phase": "pre", "source": {"kind": "preexisting"},
        }]
        task["stage_contracts"][1]["entry_requirements"] = ["next-work-entry"]
    project["task"] = task
    project["workflow"] = workflow
    project["cfg"] = cfg
    return counter


def start(project, **options):
    counter = configure(project, **options)
    tools = WorkTools(WorkPoise(project["config_path"], "handoff-author"))
    context = tools.invoke(request("bootstrap", {
        "task": deepcopy(project["task"]), "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert context["status"] == "active" and context["stage"] == "author"
    packet = fixture("handoff-packet.json")
    if options.get("mode") == "publish":
        initial = result(context, "Author result before independent fixture inspection.")
        assert tools.invoke(request("verify", {"result": initial, "artifacts": []}))["status"] == "verified"
        intent = {"task_id": "T1", "request_id": "fixture-enter-review", "target_stage": "review"}
        assert tools.invoke(request("advance", intent))["status"] == "role_handoff_required"
        assert release(tools, "fixture-author-release")["status"] == "ownership_released"
        tools, _ = receiver(project)
        context = tools.invoke(request("advance", intent))
        assert context["stage"] == "review" and context["status"] == "progression_target_reached"
    packet["input"]["result"] = result(context, "Stage result supplied in one request.")
    if options.get("mode") == "publish":
        packet["input"]["result"]["stage_work"]["coverage"] = "Independent fixture inspection."
    return tools, context, packet, counter


def handoff(tools, packet):
    try:
        return tools.invoke(deepcopy(packet))
    except PoiseError as error:
        pytest.fail(f"Stage-result handoff did not return its declared outcome: {error}", pytrace=False)


def business(output):
    fields = fixture("business-fields.json")
    assert set(output) - set(fields) <= {"interaction"}
    return {name: output[name] for name in fields}


def complete(output, *, replayed=False):
    value = business(output)
    assert value["status"] == "handoff_completed"
    assert value["task"] == "T1" and value["request_id"] == "handoff-fixture-1"
    assert value["phase"] == "complete" and value["blocked"] is None
    assert type(value["replayed"]) is bool
    if replayed is not None:
        assert value["replayed"] is replayed
    assert value["verification"]["status"] == "verified"
    assert value["release"]["status"] == "ownership_released"
    assert value["release"]["after"]["task_id"] is None
    return value


def blocked(output, phase):
    value = business(output)
    assert value["status"] == "handoff_blocked" and value["phase"] == phase
    assert set(value["blocked"]) == {"reason", "continuation"}
    assert value["blocked"]["reason"].strip()
    assert value["blocked"]["continuation"].strip()
    assert value["replayed"] is False
    return value


def source_state(tools):
    """Observe actual persisted business values, not a predicted runtime result."""
    runtime = tools.runtime
    data = runtime.task_queries.record("T1")
    with runtime.store.transaction() as database:
        events = [tuple(row) for row in database.execute(
            "SELECT event,data FROM journal WHERE task_id=? AND "
            "(event='verify.start' OR event='stage.verified' OR event LIKE 'progression.%') "
            "ORDER BY seq", ("T1",),
        )]
    location = Path(data["worktree"]) if data["worktree"] else Path(runtime.cfg["git"]["repository"])
    return {
        "version": data["version"], "status": data["status"],
        "stage": data["workflow"]["stages"][data["stage_index"]]["id"],
        "claimed_by": data["claimed_by"], "pending": deepcopy(data["pending"]),
        "counts": runtime.store.counts("T1"), "events": events,
        "head": git(location, "rev-parse", "HEAD"),
        "tree": git(location, "rev-parse", "HEAD^{tree}"),
    }


def receiver(project):
    tools = WorkTools(WorkPoise(project["config_path"], "handoff-reviewer"))
    context = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    return tools, context


def count(counter):
    return counter.read_text(encoding="utf-8") if counter.exists() else ""


def release(tools, request_id="control-release"):
    data = tools.runtime.current_task()
    return tools.invoke(request("release", {
        "task_id": data["id"], "request_id": request_id,
        "expected_version": data["version"], "reason": "Hand over the fixture result.",
    }))


def interrupt_after(monkeypatch, owner, name, marker):
    """Call the real owner, then interrupt delivery once; never fake its result."""
    original = getattr(owner, name)
    calls = []

    def wrapped(*args, **kwargs):
        value = original(*args, **kwargs)
        calls.append(deepcopy(value))
        if len(calls) == 1:
            raise PoiseError(marker)
        return value

    monkeypatch.setattr(owner, name, wrapped)
    return calls
