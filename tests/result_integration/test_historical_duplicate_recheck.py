"""A corrupt historical check batch cannot poison one fresh complete batch."""

from dataclasses import replace
import sys

from conftest import git
from poise.infrastructure.result_integration import RuntimeResultIntegration

from .helpers import integration_input, prepare_completed_task, request, source_change


def test_historical_duplicates_require_one_fresh_recheck_before_publication(
    project, monkeypatch,
):
    method = {
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
        "stdout_contains": ["current-result-verified"],
        "stderr_contains": [],
    }
    tools, _, accepted = prepare_completed_task(
        project, source_change, registered_methods=[method],
    )
    packet = request("integrate", integration_input(project, accepted))
    original_checks = RuntimeResultIntegration._run_checks
    original_publish = RuntimeResultIntegration._publish

    def leave_duplicate_history(self, run, repository):
        blocked = run.publication_blocked("test_publication_blocked", {})
        historical = replace(blocked, checks=blocked.checks + blocked.checks)
        self._save(run.intent.task_id, historical, run.version)
        return historical

    monkeypatch.setattr(RuntimeResultIntegration, "_publish", leave_duplicate_history)
    first = tools.invoke(packet)
    assert first["phase"] == "publication_failed"
    assert [item["id"] for item in first["checks"]] == [
        first["checks"][0]["id"], first["checks"][0]["id"],
    ]
    target_before = git(project["app"], "rev-parse", "HEAD")
    assert target_before != first["integration_head"]

    fresh_runs = []

    def one_fresh_recheck(self, record, run):
        fresh_runs.append(run.integration_head)
        assert len(fresh_runs) == 1, "historical duplicates triggered a second recheck"
        return original_checks(self, record, run)

    monkeypatch.setattr(RuntimeResultIntegration, "_run_checks", one_fresh_recheck)
    monkeypatch.setattr(RuntimeResultIntegration, "_publish", original_publish)
    done = tools.invoke(packet)

    assert fresh_runs == [first["integration_head"]]
    assert done["status"] == "integrated"
    assert done["accepted_commit"] == accepted
    assert done["checks"][:2] == first["checks"]
    assert done["checks"][-1]["id"] != first["checks"][0]["id"]
    assert done["checks"][-1]["method"] == "CURRENT_RESULT"
    assert done["checks"][-1]["passed"] is True
    assert done["checks"][-1]["integration_head"] == done["target_after"]
    assert git(project["app"], "rev-parse", "HEAD") == done["target_after"]
    assert tools.invoke(packet)["replayed"] is True
