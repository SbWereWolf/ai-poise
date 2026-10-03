from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from conftest import git, verification_plan, write_json
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
        "handler": "produce", "role": "executor",
        "transitions": {"complete": None},
        "rework_targets": rework_targets,
    }


def _scenario(project, *, passing_continuation=False, historical_observation=False,
              closed_revise_target=False, uninterpretable_subject=False, command=None,
              environment=None, extra_command=None):
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
    if passing_continuation or historical_observation or uninterpretable_subject:
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
        },
        "goal_type": "development",
        "worktree_required": True,
        "stages": stages,
        "benefit": {"git_categories": ["code"], "sections": []},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
    }
    project["cfg"]["automatic_checks"] = []
    write_json(project["config_path"], project["cfg"])
    write_json(project["root"] / "config/processes/development.json", process)

    method = {
        "id": "CHECK",
        "argv": command if command is not None else [
            sys.executable,
            "-c",
            "raise SystemExit(0)" if passing_continuation else "raise SystemExit(1)",
        ],
        "cwd": ".",
        "environment": environment if environment is not None else {},
        "source_under_test": {
            "kind": "repository",
            "bindings": [{"kind": "cwd", "path": "."}],
        },
        "verification_plan": verification_plan(
            "Verify the exact failed-check recovery scenario.",
            ["src/**"],
            green_stages=["implementation"],
        ),
        "expected_exit_code": 0,
        "stdout_contains": [],
        "stderr_contains": [],
    }
    task = deepcopy(project["task"])
    # This scenario replaces the route; inherited phase declarations are not valid.
    task["decomposition"]["phases"] = [
        {"stage": stage["id"], "skills": ["task-domain"], "areas": []}
        for stage in stages
    ]
    task["stage_contracts"] = [
        {
            "stage_id": stage["id"],
            "allowed_paths": list(stage["allowed_paths"]),
            "entry_requirements": [],
            "exit_requirements": [],
        }
        for stage in stages
    ]
    task["methods"] = [method]
    task["method_inputs"] = [{
        "method_id": "CHECK",
        "repository_inputs": [],
        "future_outputs": [],
        "reference_profile": {
            "runner": "python",
            "parser": "inline-no-path-arguments",
            "version": 1,
        },
    }]
    task["checks"] = {
        "implementation": ["CHECK"],
        "test_remediation": [],
        **({
            "closed_finding_remediation": [],
            "closed_finding_inspection": [],
        } if closed_revise_target else {}),
    }
    if extra_command is not None:
        second = deepcopy(method)
        second['id'] = 'CHECK2'
        second['argv'] = extra_command
        task['methods'].append(second)
        task['method_inputs'].append({
            **deepcopy(task['method_inputs'][0]), 'method_id': 'CHECK2',
        })
        task['checks']['implementation'].append('CHECK2')
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
            } if historical_observation else ({
                "CHECK": {
                    "exit_codes": [1],
                    "stdout_contains": ["DOC_RULES_OBSERVED"],
                    "stderr_contains": [],
                }
            } if uninterpretable_subject else {})),
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


def test_failed_check_can_rework_to_declared_revise_stage_without_finding(project):
    tools, context = _scenario(project, closed_revise_target=True)
    worktree = context["worktree"]
    worktrees_before = set(Path(worktree).parent.iterdir())
    immutable_before = tools.runtime.current_task()
    contract_before = deepcopy(immutable_before["contract"])
    process_before = deepcopy(immutable_before["process"])
    wip = Path(worktree, "src", "failed-check-wip.txt")
    staged_content = "staged candidate\n"
    wip.write_text(staged_content, encoding="utf-8")
    git(Path(worktree), "add", "src/failed-check-wip.txt")
    wip.write_text(staged_content + "unstaged candidate\n", encoding="utf-8")
    _fail_current_stage(tools, context)
    before = _show(tools)
    observations = _show(tools, "evidence")["observations"]
    git_before = {
        "branch": git(Path(worktree), "symbolic-ref", "--short", "HEAD"),
        "head": git(Path(worktree), "rev-parse", "HEAD"),
        "index": git(Path(worktree), "write-tree"),
        "status": git(Path(worktree), "status", "--porcelain=v1"),
        "content": wip.read_text(encoding="utf-8"),
    }

    recovered = _rework(tools, "closed_finding_remediation")

    after = _show(tools)
    assert recovered["task"] == context["task"] == "T1"
    assert recovered["worktree"] == worktree
    assert recovered["stage"] == "closed_finding_remediation"
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
    assert after["workflow"]["visits"]["closed_finding_remediation"] == (
        before["workflow"]["visits"]["closed_finding_remediation"] + 1
    )
    assert current["pending"] is None
    assert current["publication"] is None
    assert {
        "branch": git(Path(worktree), "symbolic-ref", "--short", "HEAD"),
        "head": git(Path(worktree), "rev-parse", "HEAD"),
        "index": git(Path(worktree), "write-tree"),
        "status": git(Path(worktree), "status", "--porcelain=v1"),
        "content": wip.read_text(encoding="utf-8"),
    } == git_before
    assert after["history"][:-1] == before["history"]
    assert after["history"][-1] == {
        "event": "user_failed_check_rework",
        "stage": "implementation",
        "iteration": 1,
        "reason": "Repair the failed verification through the declared test route.",
        "submission": None,
    }

    tests = Path(worktree) / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "recovery.txt").write_text("recovered\n", encoding="utf-8")
    completed = verify(tools, result(recovered, "recovered through the declared route"))
    assert completed["status"] == "verified"
    assert completed["stage"] == "closed_finding_remediation"


def test_uninterpretable_subject_check_can_rework_to_declared_stage(project):
    tools, context = _scenario(project, uninterpretable_subject=True)
    failed = _fail_current_stage(tools, context, "DOC_RULES-like subject failed without marker")
    receipt = failed["checks"][0]
    before = _show(tools)
    observations = _show(tools, "evidence")["observations"]

    assert receipt["guard"] is False
    assert receipt["interpretable"] is False
    assert receipt["timed_out"] is False
    assert receipt["actual_exit_code"] == 1

    recovered = _rework(tools)

    after = _show(tools)
    current = tools.runtime.current_task()
    assert recovered["stage"] == "test_remediation"
    assert recovered["iteration"] == 1
    assert after["attempts"] == 0
    assert current["publication"] is None
    assert current["pending"] is None
    assert current["entry_tree"] == receipt["tree"]
    assert after["submission_count"] == before["submission_count"] == 1
    assert after["evidence_count"] == before["evidence_count"] == 1
    assert _show(tools, "evidence")["observations"] == observations
    assert any(
        event.get("event") == "user_failed_check_rework"
        and event.get("reason") == "Repair the failed verification through the declared test route."
        for event in after["history"]
    )


def test_checks_failed_rework_does_not_bypass_target_allowed_paths(project):
    tools, context = _scenario(project)
    _fail_current_stage(tools, context)
    recovered = _rework(tools)
    Path(recovered["worktree"], "src", "forbidden.py").write_text("forbidden = True\n", encoding="utf-8")

    with pytest.raises(PoiseError, match="stage contract allowed_paths"):
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


@pytest.mark.parametrize(
    ('stream','mutation'),
    [
        ('stdout','missing'),
        ('stderr','missing'),
        ('stdout','modified'),
        ('stderr','modified'),
    ],
)
def test_active_rework_rejects_a_failed_batch_with_unavailable_output(project, stream, mutation):
    tools, context = _scenario(project)
    failed = _fail_current_stage(tools, context)
    output = Path(failed["checks"][0][stream])
    if mutation == 'missing':
        output.unlink()
    else:
        output.write_text('modified after receipt\n', encoding='utf-8')
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_a_failed_batch_with_malformed_digest_metadata(project):
    tools, context = _scenario(project)
    _fail_current_stage(tools, context)
    with tools.runtime.store.transaction() as db:
        row = db.execute(
            "SELECT data FROM task_proofs WHERE task_id='T1'"
        ).fetchone()
        proof = json.loads(row[0])
        del proof["book"]["batches"][-1]["receipts"][0]["stdout_digest"]
        db.execute(
            "UPDATE task_proofs SET data=? WHERE task_id='T1'",
            (
                json.dumps(
                    proof,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
    before = _show(tools)

    with pytest.raises(PoiseError, match="точного доступного failed check batch"):
        _rework(tools)

    assert _show(tools) == before


def test_active_rework_rejects_an_undeclared_target(project):
    tools, context = _scenario(project)
    _fail_current_stage(tools, context)
    before = _show(tools)

    with pytest.raises(PoiseError):
        _rework(tools, "implementation_review")

    assert _show(tools) == before
