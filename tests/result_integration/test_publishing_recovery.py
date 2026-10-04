"""Persisted publishing requires fresh proof even after unconfirmed Git publication."""
from dataclasses import replace
import sys

import pytest

from conftest import git
from poise.infrastructure.result_integration import RuntimeResultIntegration
from .helpers import prepare_completed_task, source_change, integration_input, request
from .test_current_registry_publication import current_method, invalid_receipts


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("kind", ["missing", "wrong_tree"])
@pytest.mark.parametrize("fresh_failure", [False, True])
def test_persisted_publishing_rechecks_invalid_proof_before_confirmation(
    project, monkeypatch, published, kind, fresh_failure,
):
    failure_marker = project["root"] / "publishing-verification-failure"
    method = current_method()
    method["argv"] = [
        sys.executable, "-c",
        f"import pathlib; print('current-result-verified'); assert not pathlib.Path({str(failure_marker)!r}).exists()",
    ]
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[method], checks=["CURRENT_RESULT"],
    )
    task_branch = tools.runtime.task_queries.record("T1")["branch"]
    target_before = git(project["app"], "rev-parse", "HEAD")
    foreign = project["app"] / "foreign-publishing-work.txt"
    foreign.write_text("preserve foreign publishing work\n")
    packet = request("integrate", integration_input(
        project, accepted, request_id="publishing-recovery-1",
    ))
    original_checks = RuntimeResultIntegration._run_checks
    executions = []

    def observed_checks(self, record, run):
        executions.append(run.integration_head)
        return original_checks(self, record, run)

    def interrupted_publication(self, run, repository):
        assert run.phase == "publishing"
        assert len(run.checks) == 1
        assert run.checks[0]["passed"] is True
        interrupted = replace(
            run, version=run.version + 1,
            checks=invalid_receipts(run.checks, kind),
        )
        self._save(run.intent.task_id, interrupted, run.version)
        if published:
            git(project["app"], "merge", "--ff-only", run.integration_head)
        return interrupted

    monkeypatch.setattr(RuntimeResultIntegration, "_run_checks", observed_checks)
    with monkeypatch.context() as patch:
        patch.setattr(RuntimeResultIntegration, "_publish", interrupted_publication)
        first = tools.invoke(packet)
    assert first["phase"] == "publishing"
    stored = tools.runtime.task_queries.record("T1")["pending"]
    assert stored["phase"] == "publishing"
    assert stored["publication"] is None
    assert stored["cleanup"] == {
        "task_worktree": "pending", "task_branch": "pending",
        "temporary_backups": "pending",
    }
    if kind == "missing":
        assert stored["checks"] == []
    else:
        assert stored["checks"][0]["verified_tree"] == "b" * 40
    candidate = first["integration_head"]
    candidate_tree = git(worktree, "rev-parse", "HEAD^{tree}")
    target_at_recovery = candidate if published else target_before
    assert git(project["app"], "rev-parse", "HEAD") == target_at_recovery
    if fresh_failure:
        failure_marker.write_text("force real candidate verification failure\n")

    done = tools.invoke(packet)
    assert done["accepted_commit"] == accepted
    assert done["source_commit"] == accepted
    assert done["request_id"] == "publishing-recovery-1"
    assert done["history"][:len(first["history"])] == first["history"]
    assert executions == [candidate, candidate]
    check = done["checks"][-1]
    assert check["method"] == "CURRENT_RESULT"
    assert check["integration_head"] == candidate
    assert check["verified_tree"] == candidate_tree
    assert foreign.read_text() == "preserve foreign publishing work\n"
    if fresh_failure:
        assert done["status"] == "blocked"
        assert done["phase"] == "checks_failed"
        assert check["passed"] is False
        assert git(project["app"], "rev-parse", "HEAD") == target_at_recovery
        pending = tools.runtime.task_queries.record("T1")["pending"]
        assert pending["publication"] is None
        assert pending["cleanup"] == {
            "task_worktree": "pending", "task_branch": "pending",
            "temporary_backups": "pending",
        }
        assert worktree.is_dir()
        assert git(project["app"], "show-ref", "--verify", "--hash", f"refs/heads/{task_branch}") == git(worktree, "rev-parse", "HEAD")
    else:
        assert done["status"] == "integrated"
        assert check["passed"] is True
        assert done["target_after"] == candidate
        assert done["target_before"] == target_before
        assert done["cleanup"] == {
            "task_worktree": "removed", "task_branch": "deleted",
            "temporary_backups": "removed",
        }
        assert tools.invoke(packet)["replayed"] is True
