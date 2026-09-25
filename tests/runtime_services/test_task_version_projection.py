from copy import deepcopy
from pathlib import Path

from conftest import WorkPoise as Poise, write_json, bind_task_requirements
from poise.application.work import WorkTools
from batch.helpers import request


def _configure_single_stage(project):
    stage = {
        "id": "write",
        "instruction": "Produce one read-only result.",
        "read_only": True,
        "allowed_paths": [],
        "normalization": "strip",
        "sections": {"report": "Record the result."},
        "required_sections": ["report"],
        "artifact_requirements": [],
        "handler": "produce", "role": "executor",
        "transitions": {"complete": None},
        "rework_targets": ["write"],
    }
    process = {
        "route": {"entry": "write"},
        "goal_type": "development",
        "worktree_required": True,
        "stages": [stage],
        "benefit": {"git_categories": [], "sections": []},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
    }
    project["cfg"]["automatic_checks"] = []
    write_json(project["config_path"], project["cfg"])
    write_json(project["root"] / "config/processes/development.json", process)
    task = {
        "id": "T1",
        "sprint_id": None,
        "goal_type": "development",
        "goal": "Exercise public Task version projections.",
        "requirements": ["Public projections expose the aggregate version."],
        "definition_of_done": ["The public values are mutation-sensitive."],
        "methods": [],
        "method_inputs": [],
        "checks": {"write": []},
        "artifact_requirements": [],
        "content_contract": {"sections": [], "routes": [], "requirements": []},
        "evidence_plan": {
            "write": {"subject_methods": {}, "arguments": [], "review_arguments": []}
        },
        "stage_contracts": [{
            "stage_id": "write",
            "allowed_paths": [],
            "entry_requirements": [],
            "exit_requirements": [],
        }],
    }
    task["decomposition"] = {"kind": "ordinary", "phases": [
        {"stage": "write", "skills": ["task-domain"], "areas": []}], "integration": None}
    bind_task_requirements(task, project["requirements_registry"])
    return task


def _bootstrap(tools, task):
    return tools.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))


def _result(context):
    result = deepcopy(context["result_template"])
    result["sections"]["report"] = "Version projection exercised."
    return result


def _show_task(tools):
    response = tools.invoke(request("show", {
        "queries": [{"id": "task", "kind": "task"}],
    }))
    return response["results"][0]["value"]


def _version(projection, marker):
    assert "version" in projection, f"EXPECTED_MISSING_TASK_VERSION:{marker}"
    return projection["version"]


def test_active_bootstrap_and_show_expose_incrementing_version(project):
    task = _configure_single_stage(project)
    tools = WorkTools(Poise(project["config_path"], "executor"))

    context = _bootstrap(tools, task)
    shown = _show_task(tools)

    authoritative = tools.runtime.task_queries.record(task["id"])["version"]
    assert _version(context, "active") == authoritative
    assert _version(shown, "active-show") == authoritative
    assert "_version" not in context
    assert "_version" not in shown

    verified = tools.invoke(request("verify", {
        "result": _result(context),
        "artifacts": [],
    }))
    shown_after = _show_task(tools)

    assert verified["status"] == "verified"
    authoritative_after = tools.runtime.task_queries.record(task["id"])["version"]
    assert _version(shown_after, "verified-show") == authoritative_after
    assert authoritative_after > authoritative


def test_broken_bootstrap_exposes_restart_version(project):
    task = _configure_single_stage(project)
    requirement = {
        "id": "missing-input",
        "kind": "artifact",
        "stages": ["write"],
        "phase": "pre",
        "scope": "task",
        "pattern": "inputs/missing.txt",
        "minimum": 1,
        "maximum": 1,
        "source": {"kind": "preexisting"},
    }
    task["content_contract"]["requirements"] = [requirement]
    task["stage_contracts"][0]["entry_requirements"] = [requirement["id"]]
    tools = WorkTools(Poise(project["config_path"], "executor"))

    broken = _bootstrap(tools, task)

    assert broken["status"] == "broken"
    assert "_version" not in broken
    authoritative = tools.runtime.task_queries.record(task["id"])["version"]
    restarted = tools.invoke(request("task", {
        "action": "restart",
        "request_id": "restart-from-broken-version",
        "task_id": task["id"],
        "expected_version": _version(broken, "broken"),
        "reason": "The saved entry contract is unattainable.",
        "authorization": "The test authorizes recovery of this unfinished Task.",
    }))
    assert restarted["status"] == "newborn"
    assert authoritative == broken["version"]


def test_terminal_inspection_exposes_version_and_taskless_remains_taskless(project):
    task = _configure_single_stage(project)
    executor = WorkTools(Poise(project["config_path"], "executor"))
    context = _bootstrap(executor, task)
    assert executor.invoke(request("verify", {
        "result": _result(context),
        "artifacts": [],
    }))["status"] == "verified"
    completed = executor.invoke(request("bootstrap", {
        "task": None,
        "decision": "continue",
        "feedback": None,
        "rework_stage": None,
    }))
    assert completed["status"] == "completed"
    completed_version = executor.runtime.task_queries.record(task["id"])["version"]
    assert _version(completed, "completed") == completed_version

    reader = WorkTools(Poise(project["config_path"], "reader"))
    terminal = _bootstrap(reader, {"id": task["id"]})
    taskless = _bootstrap(reader, None)

    assert terminal["status"] == "completed"
    authoritative = reader.runtime.task_queries.record(task["id"])["version"]
    assert _version(terminal, "terminal") == authoritative == completed_version
    assert "_version" not in terminal
    assert taskless["status"] == "read_only"
    assert "version" not in taskless


def test_documentation_and_skills_define_public_version_recovery_contract():
    root = Path(__file__).parents[2]
    batch = (root / "docs/workflows/batch-work.md").read_text(encoding="utf-8")
    workflow = (root / ".agents/skills/poise/SKILL.md").read_text(encoding="utf-8")
    development = (root / ".agents/skills/poise-development/SKILL.md").read_text(
        encoding="utf-8"
    )

    batch = " ".join(batch.split())
    workflow = " ".join(workflow.split())
    development = " ".join(development.split())

    assert (
        "Существующая non-newborn Task возвращает точное текущее поле `version` через "
        "`bootstrap` и текущую проекцию `show`."
    ) in batch
    assert "Приватное имя `_version` не входит в публичный DTO." in batch
    assert (
        "Taskless-ответы не содержат `version`, а newborn Task использует `revision`."
    ) in batch
    assert (
        "Передавайте это значение без изменений как `expected_version` для защищённых "
        "`restart` и исправления stage contract; при конфликте версии заново прочитайте "
        "публичный `bootstrap`/`show`, а не угадывайте значение."
    ) in batch

    for text in (workflow, development):
        assert (
            "Public non-newborn `bootstrap` and current-Task `show` projections expose "
            "the exact current Task version as `version`."
        ) in text
        assert "The private `_version` name is never part of the public DTO." in text
        assert "Taskless responses omit `version`, and newborn Tasks use `revision`." in text
        assert (
            "Pass this value unchanged as `expected_version` for guarded `restart` or "
            "stage-contract repair; on a version conflict, refresh through public "
            "`bootstrap`/`show` instead of guessing."
        ) in text
