from copy import deepcopy
from pathlib import Path
import sys

import pytest

from conftest import write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from conftest import WorkPoise as Poise

from batch.helpers import bootstrap, configure, request, result, verify


def _stage(stage_id, allowed_paths):
    rework_targets = [stage_id]
    if stage_id != "test_remediation":
        rework_targets.append("test_remediation")
    return {
        "id": stage_id,
        "instruction": f"Complete {stage_id}.",
        "read_only": False,
        "allowed_paths": allowed_paths,
        "normalization": "strip",
        "sections": {"report": "Record the result."},
        "required_sections": ["report"],
        "artifact_requirements": [],
        "handler": "produce",
        "transitions": {"complete": None},
        "rework_targets": rework_targets,
    }


def _scenario(project, *, passing_continuation=False, historical_observation=False,
              closed_revise_target=False):
    configure(project)
    project["cfg"]["accounting"]["time_mode"] = "reported"
    stages = [
        _stage("implementation", ["src/**"]),
        _stage("test_remediation", ["tests/**"]),
    ]
    stages[0]["transitions"] = {"complete": "test_remediation"}
    if closed_revise_target:
        closed = _stage("closed_finding_remediation", ["tests/**"])
        closed["handler"] = "revise"
        closed["transitions"] = {"complete": "closed_finding_inspection"}
        inspection = _stage("closed_finding_inspection", [])
        inspection["handler"] = "inspect"
        inspection["read_only"] = True
        inspection["transitions"] = {
            "changes_requested": "closed_finding_remediation",
            "clear": None,
        }
        stages.extend((closed, inspection))
        stages[1]["transitions"] = {"complete": "closed_finding_remediation"}
        stages[0]["rework_targets"].append("closed_finding_remediation")
    if passing_continuation or historical_observation:
        stages[0]["handler"] = "check"
        stages[0]["transitions"] = {
            "satisfied": "test_remediation",
            "not_satisfied": "test_remediation",
            "inconclusive": "test_remediation",
        }
    if historical_observation:
        stages[1]["rework_targets"].append("implementation")
    process = {
        "route": {
            "entry": "implementation",
            "max_transitions": 20,
            "max_stage_visits": 4,
        },
        "goal_type": "development",
        "stages": stages,
        "benefit": {"git_categories": ["code"], "sections": []},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
    }
    project["cfg"]["automatic_checks"] = []
    write_json(project["config_path"], project["cfg"])
    write_json(project["root"] / "config/processes/development.json", process)

    method = {
        "id": "CHECK",
        "argv": [sys.executable, "-c", "raise SystemExit(0)" if passing_continuation else "raise SystemExit(1)"],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 10,
        "expected_exit_code": 0,
        "stdout_contains": [],
        "stderr_contains": [],
    }
    task = deepcopy(project["task"])
    task["methods"] = [method]
    task["checks"] = {
        "implementation": ["CHECK"],
        "test_remediation": [],
        **({
            "closed_finding_remediation": [],
            "closed_finding_inspection": [],
        } if closed_revise_target else {}),
    }
    task["evidence_plan"] = {
        "implementation": {
            "subject_methods": {
                "CHECK": {
                    "exit_codes": [0],
                    "stdout_contains": [],
                    "stderr_contains": [],
                }
            } if passing_continuation else ({
                "CHECK": {
                    "exit_codes": [1],
                    "stdout_contains": [],
                    "stderr_contains": [],
                }
            } if historical_observation else {}),
            "arguments": [{
                "id": "CONTINUE",
                "kind": "logical",
                "phase": "continue",
                "observation_methods": ["CHECK"],
            }] if passing_continuation else [],
            "review_arguments": [],
        },
        "test_remediation": {
            "subject_methods": {},
            "arguments": [],
            "review_arguments": [],
        },
        **({
            "closed_finding_remediation": {
                "subject_methods": {},
                "arguments": [],
                "review_arguments": [],
            },
            "closed_finding_inspection": {
                "subject_methods": {},
                "arguments": [],
                "review_arguments": [],
            },
        } if closed_revise_target else {}),
    }
    project["task"] = task
    session = f"FAILED-CHECK-REWORK-{project['root'].parent.name}"
    tools = WorkTools(Poise(project["config_path"], session))
    return tools, bootstrap(tools, project)


def _show(tools, kind="task"):
    response = tools.invoke(request("show", {"queries": [{"id": kind, "kind": kind}]}))
    return response["results"][0]["value"]


def _fail_current_stage(tools, context, text="failed candidate"):
    failed = verify(tools, result(context, text))
    assert failed["status"] == "checks_failed"
    assert failed["checks"][0]["passed"] is False
    return failed


def _rework(tools, target="test_remediation"):
    return tools.invoke(request("bootstrap", {
        "task": None,
        "decision": "rework",
        "feedback": "Repair the failed verification through the declared test route.",
        "rework_stage": target,
    }))


def test_failed_check_can_rework_to_declared_stage_without_recreating_task(project):
    tools, context = _scenario(project, closed_revise_target=True)
    worktree = context["worktree"]
    worktrees_before = set(Path(worktree).parent.iterdir())
    immutable_before = tools.runtime.current_task()
    contract_before = deepcopy(immutable_before["contract"])
    process_before = deepcopy(immutable_before["process"])
    _fail_current_stage(tools, context)
    before = _show(tools)
    observations = _show(tools, "evidence")["observations"]

    with pytest.raises(PoiseError, match="открыт|finding|исправ"):
        _rework(tools, "closed_finding_remediation")

    assert _show(tools) == before

    recovered = _rework(tools)

    after = _show(tools)
    assert recovered["task"] == context["task"] == "T1"
    assert recovered["worktree"] == worktree
    assert recovered["stage"] == "test_remediation"
    assert recovered["iteration"] == 1
    assert set(Path(worktree).parent.iterdir()) == worktrees_before
    assert after["attempts"] == 0
    assert after["submission_count"] == before["submission_count"] == 1
    assert after["evidence_count"] == before["evidence_count"] == 1
    assert _show(tools, "evidence")["observations"] == observations
    current = tools.runtime.current_task()
    assert current["contract"] == contract_before
    assert current["process"] == process_before
    assert after["workflow"]["transitions"] == before["workflow"]["transitions"] + 1
    assert after["workflow"]["visits"]["implementation"] == before["workflow"]["visits"]["implementation"]
    assert after["workflow"]["visits"]["test_remediation"] == before["workflow"]["visits"]["test_remediation"] + 1
    assert current["pending"] is None
    assert current["publication"] is None
    assert any(
        event.get("reason") == "Repair the failed verification through the declared test route."
        for event in after["history"]
    )

    tests = Path(worktree) / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "recovery.txt").write_text("recovered\n", encoding="utf-8")
    completed = verify(tools, result(recovered, "recovered through the declared route"))
    assert completed["status"] == "verified"
    assert completed["stage"] == "test_remediation"


def test_checks_failed_rework_does_not_bypass_target_allowed_paths(project):
    tools, context = _scenario(project)
    _fail_current_stage(tools, context)
    recovered = _rework(tools)
    Path(recovered["worktree"], "src", "forbidden.py").write_text("forbidden = True\n", encoding="utf-8")

    with pytest.raises(PoiseError, match="вне разрешённой области"):
        verify(tools, result(recovered, "must remain inside the remediation scope"))


def test_active_rework_rejects_without_a_failed_batch(project):
    tools, _ = _scenario(project)
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_a_passed_batch_awaiting_evidence(project):
    tools, context = _scenario(project, passing_continuation=True)
    pending = verify(tools, result(context, "passed observation awaiting evidence"))
    assert pending["status"] == "awaiting_continuation"
    assert pending["checks"][0]["passed"] is True
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_a_failed_batch_for_an_old_tree(project):
    tools, context = _scenario(project)
    _fail_current_stage(tools, context)
    Path(context["worktree"], "src", "double.py").write_text("def double(n):\n    return n + 2\n", encoding="utf-8")
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_a_failed_batch_for_an_old_execution(project, monkeypatch):
    tools, context = _scenario(project)
    _fail_current_stage(tools, context)
    monkeypatch.setenv("LANG", "C")
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_a_failed_batch_for_an_old_submission(project):
    tools, context = _scenario(project)
    _fail_current_stage(tools, context)
    tools.runtime.task_commands.submit(
        "T1",
        tools.runtime.session,
        result(context, "new unverified submission"),
    )
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_a_persisted_batch_from_an_old_stage(project):
    tools, context = _scenario(project, historical_observation=True)
    verified = verify(tools, result(context, "record an implementation observation"))
    assert verified["status"] == "verified"
    assert _show(tools)["workflow"]["outcome"] == "not_satisfied"
    historical = _show(tools, "evidence")["observations"]
    assert historical[0]["stage"] == "implementation"
    assert historical[0]["iteration"] == 1

    current = tools.invoke(request("bootstrap", {
        "task": None,
        "decision": "continue",
        "feedback": None,
        "rework_stage": None,
    }))
    assert current["stage"] == "test_remediation"
    assert current["iteration"] == 1
    assert _show(tools, "evidence")["observations"] == historical
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_a_persisted_batch_from_an_old_iteration(project):
    tools, context = _scenario(project, historical_observation=True)
    verified = verify(tools, result(context, "record an implementation observation"))
    assert verified["status"] == "verified"
    assert _show(tools)["workflow"]["outcome"] == "not_satisfied"
    historical = _show(tools, "evidence")["observations"]
    assert historical[0]["stage"] == "implementation"
    assert historical[0]["iteration"] == 1

    remediation = tools.invoke(request("bootstrap", {
        "task": None,
        "decision": "continue",
        "feedback": None,
        "rework_stage": None,
    }))
    assert verify(tools, result(remediation, "complete the first remediation"))["status"] == "verified"
    current = tools.invoke(request("bootstrap", {
        "task": None,
        "decision": "rework",
        "feedback": "Return to implementation for another iteration.",
        "rework_stage": "implementation",
    }))
    assert current["stage"] == "implementation"
    assert current["iteration"] == 2
    assert _show(tools, "evidence")["observations"] == historical
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_an_unknown_pending_check_outcome(project):
    tools, context = _scenario(project)
    current = tools.runtime.current_task()
    current["pending"] = "checks"
    tools.runtime.store.save(current)
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_a_failed_batch_with_missing_output(project):
    tools, context = _scenario(project)
    failed = _fail_current_stage(tools, context)
    Path(failed["checks"][0]["stdout"]).unlink()
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_an_undeclared_target(project):
    tools, context = _scenario(project)
    _fail_current_stage(tools, context)
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools, "implementation_review")

    assert _show(tools) == before
