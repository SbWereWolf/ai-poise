"""Current registry and exact candidate proof are publication prerequisites."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sqlite3
import sys

import pytest

from conftest import git
from poise.common import PoiseError
from poise.infrastructure.result_integration import RuntimeResultIntegration
from .helpers import (
    prepare_completed_task, source_change, integration_input, request,
    advance_ref_with_same_tree,
)


def current_method():
    return {
        "id": "CURRENT_RESULT",
        "argv": [sys.executable, "-c", "print('current-result-verified')"],
        "cwd": ".",
        "environment": {},
        "source_under_test": {
            "kind": "repository", "bindings": [{"kind": "cwd", "path": "."}],
        },
        "verification_plan": {
            "responsibility": "Verify the current produced result.",
            "change_surface": ["src/**"],
            "red_stages": [], "green_stages": ["implementation"],
            "red_failure": None,
        },
        "expected_exit_code": 0,
        "stdout_contains": ["current-result-verified"], "stderr_contains": [],
    }


@pytest.mark.parametrize("terminal_stage", [False, True])
def test_completed_query_projects_late_registered_methods_without_mutating_creation(
    project, terminal_stage,
):
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, registered_methods=[current_method()],
        terminal_stage=terminal_stage,
    )
    database = project["root"] / project["cfg"]["paths"]["state"] / project["cfg"]["paths"]["database"]
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT metadata FROM tasks WHERE id=?", ("T1",),
        ).fetchone()
    creation = json.loads(row[0])
    assert creation["contract"]["methods"] == []
    assert creation["contract"]["checks"]["implementation"] == []
    record = tools.runtime.task_queries.record("T1")
    assert record["status"] == "completed"
    assert [method["id"] for method in record["contract"]["methods"]] == ["CURRENT_RESULT"]
    assert record["contract"]["checks"]["implementation"] == ["CURRENT_RESULT"]
    assert git(worktree, "rev-parse", "HEAD") == accepted


def test_initial_publication_runs_late_registered_current_methods(project):
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, registered_methods=[current_method()],
    )
    packet = request("integrate", integration_input(project, accepted))
    done = tools.invoke(packet)
    assert done["status"] == "integrated"
    assert [item["method"] for item in done["checks"]] == ["CURRENT_RESULT"]
    check = done["checks"][0]
    assert check["passed"] is True
    assert check["integration_head"] == done["target_after"]
    assert check["verified_tree"] == git(project["app"], "rev-parse", "HEAD^{tree}")
    assert Path(check["stdout"]).read_text() == "current-result-verified\n"
    assert tools.invoke(packet)["replayed"] is True


def invalid_receipts(receipts, kind):
    records = deepcopy(list(receipts))
    if kind in ("missing", "interrupted_publication"):
        return ()
    if kind == "partial":
        return tuple(records[:-1])
    if kind == "duplicate":
        return tuple(records + records)
    first = records[0]
    if kind == "wrong_commit":
        first["integration_head"] = "a" * 40
    elif kind == "wrong_tree":
        first["verified_tree"] = "b" * 40
    elif kind == "foreign_method":
        first["method"] = "FOREIGN_RESULT"
    elif kind == "failed":
        first["passed"] = False
    elif kind == "corrupt":
        first.pop("verified_tree")
    elif kind == "wrong_command":
        first["argv"] = [sys.executable, "-c", "print('other-result')"]
    elif kind == "missing_passed":
        first.pop("passed")
    elif kind == "invalid_passed":
        first["passed"] = "true"
    elif kind == "wrong_exit":
        first["actual_exit_code"] = 1
    elif kind == "timed_out":
        first["timed_out"] = True
    elif kind == "cancelled":
        first["cancelled"] = True
    elif kind == "missing_cancelled":
        first.pop("cancelled", None)
    elif kind == "missing_capture":
        first.pop("capture_complete", None)
    elif kind == "incomplete_capture":
        first["capture_complete"] = False
    elif kind == "missing_stdout_path":
        first.pop("stdout")
    elif kind == "missing_stdout_file":
        Path(first["stdout"]).unlink()
    elif kind == "corrupt_stdout":
        Path(first["stdout"]).write_text("tampered stdout\n")
    elif kind == "corrupt_stderr":
        Path(first["stderr"]).write_text("tampered stderr\n")
    elif kind == "missing_definition_digest":
        first.pop("definition_digest", None)
    elif kind == "wrong_definition_digest":
        first["definition_digest"] = "a" * 64
    elif kind == "missing_contract_digest":
        first.pop("contract_digest", None)
    elif kind == "wrong_contract_digest":
        first["contract_digest"] = "b" * 64
    elif kind == "legacy_definition":
        # Preserve the old wire fields, omitting any subsequently added provenance.
        old_fields = {
            "id", "method", "integration_head", "verified_tree", "argv", "cwd",
            "expected_exit_code", "passed", "stdout_digest", "stderr_digest",
            "preview", "stdout", "stderr", "actual_exit_code",
        }
        records[0] = {key: value for key, value in first.items() if key in old_fields}
    else:
        raise AssertionError(kind)
    return tuple(records)


@pytest.mark.parametrize("kind", [
    "missing", "duplicate", "wrong_commit", "wrong_tree", "foreign_method",
    "failed", "corrupt", "wrong_command", "legacy_definition",
    "partial", "interrupted_publication",
    "missing_passed", "invalid_passed", "wrong_exit", "timed_out", "cancelled",
    "missing_cancelled", "missing_capture", "incomplete_capture", "missing_stdout_path",
    "missing_stdout_file", "corrupt_stdout", "corrupt_stderr",
    "missing_definition_digest", "wrong_definition_digest",
    "missing_contract_digest", "wrong_contract_digest",
])
def test_retry_rechecks_invalid_candidate_proof(
    project, monkeypatch, kind,
):
    methods = [current_method()]
    if kind == "partial":
        second = current_method()
        second["id"] = "SECOND_RESULT"
        second["argv"] = [sys.executable, "-c", "print('second-result-verified')"]
        second["stdout_contains"] = ["second-result-verified"]
        methods.append(second)
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=methods, checks=[method["id"] for method in methods],
    )
    packet = request("integrate", integration_input(project, accepted))
    original_checks = RuntimeResultIntegration._run_checks
    original_publish = RuntimeResultIntegration._publish
    executions = []

    def historical_checks(self, record, run):
        executions.append(run.integration_head)
        return original_checks(self, record, run)

    def blocked_publication(self, run, repository):
        blocked = run.publication_blocked("test_publication_blocked", {})
        historical = replace(blocked, checks=invalid_receipts(blocked.checks, kind))
        self._save(run.intent.task_id, historical, run.version)
        return historical

    monkeypatch.setattr(RuntimeResultIntegration, "_run_checks", historical_checks)
    monkeypatch.setattr(RuntimeResultIntegration, "_publish", blocked_publication)
    first = tools.invoke(packet)
    assert first["phase"] == "publication_failed"
    target = git(project["app"], "rev-parse", "HEAD")
    candidate = first["integration_head"]
    if kind == "interrupted_publication":
        # A completed ff-only Git publication with no saved confirmation.
        git(project["app"], "merge", "--ff-only", candidate)

    def real_checks(self, record, run):
        executions.append(run.integration_head)
        return original_checks(self, record, run)

    monkeypatch.setattr(RuntimeResultIntegration, "_run_checks", real_checks)
    monkeypatch.setattr(RuntimeResultIntegration, "_publish", original_publish)
    done = tools.invoke(packet)
    assert executions == [candidate, candidate]
    assert done["status"] == "integrated"
    assert done["accepted_commit"] == accepted
    assert done["target_before"] == target
    assert done["checks"][-1]["method"] == ("SECOND_RESULT" if kind == "partial" else "CURRENT_RESULT")
    assert done["checks"][-1]["passed"] is True
    assert done["checks"][-1]["integration_head"] == done["target_after"]
    assert done["checks"][-1]["verified_tree"] == git(project["app"], "rev-parse", "HEAD^{tree}")
    assert tools.invoke(packet)["replayed"] is True


@pytest.mark.parametrize("damage", ["missing_state", "missing_current_method"])
def test_unavailable_registry_never_becomes_an_authoritative_empty_selection(project, damage):
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, registered_methods=[current_method()],
    )
    database = project["root"] / project["cfg"]["paths"]["state"] / project["cfg"]["paths"]["database"]
    # Deliberately damage only this test-owned disposable DB, never a working DB.
    with sqlite3.connect(database) as connection:
        if damage == "missing_current_method":
            connection.execute("DELETE FROM task_methods WHERE task_id=?", ("T1",))
        else:
            row = connection.execute(
                "SELECT data FROM task_workflows WHERE task_id=?", ("T1",),
            ).fetchone()
            workflow = json.loads(row[0])
            workflow.pop("registry")
            connection.execute(
                "UPDATE task_workflows SET data=? WHERE task_id=?",
                (json.dumps(workflow), "T1"),
            )
    target = git(project["app"], "rev-parse", "HEAD")
    with pytest.raises(PoiseError):
        tools.invoke(request("integrate", integration_input(project, accepted)))
    assert git(project["app"], "rev-parse", "HEAD") == target
    assert worktree.is_dir()


def test_late_registered_methods_follow_actual_target_drift(project, monkeypatch):
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, registered_methods=[current_method()],
    )
    original = RuntimeResultIntegration._run
    advanced = []

    def drift(self, cwd, *args, env=None):
        if args[:2] == ("merge", "--ff-only") and not advanced:
            parent = git(project["app"], "rev-parse", "HEAD")
            advanced.append(advance_ref_with_same_tree(project["app"], parent, "test: target drift"))
        return original(self, cwd, *args, env=env)

    monkeypatch.setattr(RuntimeResultIntegration, "_run", drift)
    done = tools.invoke(request("integrate", integration_input(project, accepted)))
    assert done["status"] == "integrated"
    assert [check["method"] for check in done["checks"]] == ["CURRENT_RESULT", "CURRENT_RESULT"]
    assert done["checks"][0]["integration_head"] != done["checks"][1]["integration_head"]
    assert done["checks"][1]["integration_head"] == done["target_after"]
    assert done["checks"][1]["verified_tree"] == git(project["app"], "rev-parse", "HEAD^{tree}")
    assert done["accepted_commit"] == accepted


def test_authoritative_empty_eligible_registry_is_not_a_failure(project):
    method = current_method()
    method["verification_plan"]["change_surface"] = []
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[method], checks=["CURRENT_RESULT"],
    )
    done = tools.invoke(request("integrate", integration_input(project, accepted)))
    assert done["status"] == "integrated"
    assert done["checks"] == []
    assert done["accepted_commit"] == accepted


def test_late_registered_failing_method_prevents_publication_and_preserves_wip(project):
    method = current_method()
    method["argv"] = [sys.executable, "-c", "import pathlib; print('current-result-verified'); assert not pathlib.Path('target-marker').exists()"]
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, registered_methods=[method],
    )
    marker = project["app"] / "target-marker"
    marker.write_text("target introduces rejected state\n")
    git(project["app"], "add", "target-marker")
    git(project["app"], "commit", "-m", "test: target marker")
    foreign = project["app"] / "foreign-wip.txt"
    foreign.write_text("preserve foreign work\n")
    before = git(project["app"], "rev-parse", "HEAD")
    done = tools.invoke(request("integrate", integration_input(project, accepted)))
    assert done["status"] == "blocked"
    assert done["phase"] == "checks_failed"
    assert done["checks"][-1]["method"] == "CURRENT_RESULT"
    assert done["checks"][-1]["passed"] is False
    assert git(project["app"], "rev-parse", "HEAD") == before
    assert foreign.read_text() == "preserve foreign work\n"
    assert worktree.is_dir()
    assert git(worktree, "merge-base", "--is-ancestor", accepted, "HEAD") == ""


def test_retry_with_missing_proof_stops_when_required_check_fails(project, monkeypatch):
    failure_marker = project["root"] / "verification-failure-marker"
    method = current_method()
    method["argv"] = [
        sys.executable, "-c",
        f"import pathlib; print('current-result-verified'); assert not pathlib.Path({str(failure_marker)!r}).exists()",
    ]
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[method], checks=["CURRENT_RESULT"],
    )
    packet = request("integrate", integration_input(project, accepted))

    def unavailable_publication(self, run, repository):
        blocked = run.publication_blocked("test_publication_blocked", {})
        blocked = replace(blocked, checks=())
        self._save(run.intent.task_id, blocked, run.version)
        return blocked

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeResultIntegration, "_publish", unavailable_publication)
        first = tools.invoke(packet)
    assert first["phase"] == "publication_failed"
    failure_marker.write_text("force actual candidate check failure\n")
    target = git(project["app"], "rev-parse", "HEAD")
    done = tools.invoke(packet)
    assert done["status"] == "blocked"
    assert done["phase"] == "checks_failed"
    assert done["checks"][-1]["passed"] is False
    assert git(project["app"], "rev-parse", "HEAD") == target
    assert worktree.is_dir()


def test_method_replaced_after_creation_uses_current_definition(project):
    original = current_method()
    original["argv"] = [sys.executable, "-c", "print('creation-result')"]
    original["stdout_contains"] = ["creation-result"]
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[original], checks=["CURRENT_RESULT"],
        registered_methods=[current_method()],
    )
    packet = request("integrate", integration_input(project, accepted))
    done = tools.invoke(packet)
    assert done["status"] == "integrated"
    assert len(done["checks"]) == 1
    check = done["checks"][0]
    assert check["argv"] == [sys.executable, "-c", "print('current-result-verified')"]
    assert Path(check["stdout"]).read_text() == "current-result-verified\n"
    assert check["passed"] is True


def test_red_and_baseline_only_methods_are_excluded_from_integration(project):
    baseline = current_method()
    baseline["id"] = "BASELINE_RESULT"
    baseline["verification_plan"]["change_surface"] = []
    red = current_method()
    red["id"] = "EXPECTED_RED"
    red["argv"] = [sys.executable, "-c", "import sys; print('expected-red'); sys.exit(1)"]
    red["expected_exit_code"] = 1
    red["stdout_contains"] = ["expected-red"]
    red["verification_plan"]["green_stages"] = []
    red["verification_plan"]["red_stages"] = ["implementation"]
    red["verification_plan"]["red_failure"] = {
        "exit_code": 1, "stdout_equals": "expected-red\n", "stderr_equals": "",
    }
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[baseline, red],
        checks=["BASELINE_RESULT", "EXPECTED_RED"],
    )
    done = tools.invoke(request("integrate", integration_input(project, accepted)))
    assert done["status"] == "integrated"
    assert done["checks"] == []
    assert done["accepted_commit"] == accepted


def test_valid_publication_retry_preserves_proof_wip_and_idempotency(project, monkeypatch):
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[current_method()], checks=["CURRENT_RESULT"],
    )
    foreign = project["app"] / "foreign-wip.txt"
    foreign.write_text("preserve during publication retry\n")
    packet = request("integrate", integration_input(project, accepted))

    def blocked_publication(self, run, repository):
        blocked = run.publication_blocked("test_publication_blocked", {})
        self._save(run.intent.task_id, blocked, run.version)
        return blocked

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeResultIntegration, "_publish", blocked_publication)
        first = tools.invoke(packet)
    assert first["phase"] == "publication_failed"
    done = tools.invoke(packet)
    assert done["status"] == "integrated"
    assert done["checks"][0] == first["checks"][0]
    assert done["checks"][-1]["method"] == "CURRENT_RESULT"
    assert done["checks"][-1]["passed"] is True
    assert done["checks"][-1]["integration_head"] == done["target_after"]
    assert foreign.read_text() == "preserve during publication retry\n"
    replay = tools.invoke(packet)
    assert replay["replayed"] is True
    assert replay["checks"] == done["checks"]
    assert replay["accepted_commit"] == accepted


def configured_method(value):
    method = current_method()
    method["argv"] = [sys.executable, "-c", "import os; print('configuration='+os.environ['POISE_CHECK_VALUE'])"]
    method["environment"] = {"POISE_CHECK_VALUE": value}
    method["stdout_contains"] = ["configuration="]
    return method


def test_same_command_configuration_replacement_projects_current_definition(project):
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[configured_method("PRIOR_CONFIG")],
        checks=["CURRENT_RESULT"],
        registered_methods=[configured_method("CURRENT_CONFIG")],
    )
    record = tools.runtime.task_queries.record("T1")
    method = record["contract"]["methods"][0]
    assert method["id"] == "CURRENT_RESULT"
    assert method["argv"] == [sys.executable, "-c", "import os; print('configuration='+os.environ['POISE_CHECK_VALUE'])"]
    assert method["environment"] == {"POISE_CHECK_VALUE": "CURRENT_CONFIG"}
    assert method["stdout_contains"] == ["configuration="]
    assert git(worktree, "rev-parse", "HEAD") == accepted


def test_same_candidate_successful_prior_configuration_proof_is_rechecked(
    project, monkeypatch,
):
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[configured_method("CURRENT_CONFIG")],
        checks=["CURRENT_RESULT"],
    )
    foreign = project["app"] / "foreign-wip.txt"
    foreign.write_text("preserve during definition revalidation\n")
    packet = request("integrate", integration_input(project, accepted))
    original_checks = RuntimeResultIntegration._run_checks

    def save_prior_definition_proof(self, run, repository):
        prior_record = deepcopy(self.h.task_queries.record("T1"))
        prior_record["contract"]["methods"] = [configured_method("PRIOR_CONFIG")]
        # Run the independently authored prior configuration on the SAME candidate.
        checked = original_checks(self, prior_record, replace(run, phase="candidate_ready"))
        # A historical persisted run has only its successful prior-definition proof.
        historical = replace(checked, checks=(checked.checks[-1],))
        blocked = historical.publication_blocked("test_publication_blocked", {})
        self._save(run.intent.task_id, blocked, checked.version)
        return blocked

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeResultIntegration, "_publish", save_prior_definition_proof)
        first = tools.invoke(packet)
    assert first["phase"] == "publication_failed"
    assert len(first["checks"]) == 1
    prior = first["checks"][0]
    assert prior["method"] == "CURRENT_RESULT"
    assert prior["passed"] is True
    assert Path(prior["stdout"]).read_text() == "configuration=PRIOR_CONFIG\n"
    assert prior["argv"] == [sys.executable, "-c", "import os; print('configuration='+os.environ['POISE_CHECK_VALUE'])"]
    assert prior["integration_head"] == git(worktree, "rev-parse", "HEAD")
    assert prior["verified_tree"] == git(worktree, "rev-parse", "HEAD^{tree}")

    done = tools.invoke(packet)
    assert done["status"] == "integrated"
    assert len(done["checks"]) == 2
    assert done["checks"][0] == prior
    current = done["checks"][1]
    assert current["method"] == "CURRENT_RESULT"
    assert current["passed"] is True
    assert current["argv"] == [sys.executable, "-c", "import os; print('configuration='+os.environ['POISE_CHECK_VALUE'])"]
    assert Path(current["stdout"]).read_text() == "configuration=CURRENT_CONFIG\n"
    assert current["integration_head"] == prior["integration_head"]
    assert current["verified_tree"] == prior["verified_tree"]
    assert done["accepted_commit"] == accepted
    assert done["history"][:len(first["history"])] == first["history"]
    assert foreign.read_text() == "preserve during definition revalidation\n"
    replay = tools.invoke(packet)
    assert replay["replayed"] is True
    assert replay["checks"] == done["checks"]
