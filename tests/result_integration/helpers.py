from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from poise.application.work import WorkTools
from conftest import WorkPoise as Poise

from conftest import git, write_json


def request(operation, inputs):
    return {"operation": operation, "input": inputs, "messages": []}


def prepare_completed_task(
    project,
    change,
    *,
    task_id="T1",
    accept=True,
    methods=(),
    checks=(),
):
    cfg = deepcopy(project["cfg"])
    cfg["automatic_checks"] = []
    cfg["git"]["push_required"] = False
    write_json(project["config_path"], cfg)
    process = {
        "route": {"entry": "implementation", "max_transitions": 10, "max_stage_visits": 3},
        "goal_type": "development",
        "stages": [{
            "id": "implementation",
            "instruction": "Create the accepted result.",
            "read_only": False,
            "allowed_paths": ["src/**"],
            "normalization": "strip",
            "sections": {"report": "Record the result."},
            "required_sections": ["report"],
            "artifact_requirements": [],
            "handler": "produce",
            "transitions": {"complete": None},
            "rework_targets": ["implementation"],
        }],
        "benefit": {"git_categories": ["code"], "sections": []},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
    }
    write_json(project["root"] / "config/processes/development.json", process)
    task = {
        "id": task_id,
        "sprint_id": None,
        "goal_type": "development",
        "goal": "Create an accepted source commit.",
        "requirements": ["The source commit is preserved until integration."],
        "definition_of_done": ["The completed result can be integrated."],
        "methods": list(methods),
        "artifact_requirements": [],
        "checks": {"implementation": list(checks)},
        "evidence_plan": {
            "implementation": {"subject_methods": {}, "arguments": [], "review_arguments": []}
        },
        "content_contract": {"sections": [], "routes": [], "requirements": []},
    }
    runtime = Poise(project["config_path"], "worker")
    tools = WorkTools(runtime)
    context = tools.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    source_worktree = Path(context["worktree"])
    change(source_worktree)
    result = deepcopy(context["result_template"])
    result["sections"]["report"] = "Accepted source result."
    result["commit_message"] = "feat: accepted source result"
    verified = tools.invoke(request("verify", {"result": result, "artifacts": []}))
    assert verified["status"] == "verified"
    if accept:
        completed = tools.invoke(request("accept", {}))
        assert completed["status"] == "completed"
    return tools, source_worktree, verified["commit"]


def integration_input(project, source_commit, request_id="integrate-1", resolutions=()):
    return {
        "request_id": request_id,
        "task_id": "T1",
        "expected_source_commit": source_commit,
        "expected_target_commit": git(project["app"], "rev-parse", "refs/heads/main"),
        "authorization": "The user accepted the completed task result.",
        "resolutions": list(resolutions),
    }
