"""Passing admission guards kept separate from expected replay behavior RED."""

import pytest

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.restart_auto_support import prepared, snapshot


def test_invalid_target_input_preserves_task_git_and_effects(project):
    case = prepared(project)
    before = snapshot(case)
    for target in (None, "", 7, "unknown-stage"):
        with pytest.raises(PoiseError):
            case["client"].invoke(request("advance", {
                "request_id": "invalid", "task_id": "T1", "target_stage": target,
            }))
        assert snapshot(case) == before


def test_foreign_actor_cannot_replay_or_claim_task(project):
    case = prepared(project)
    outsider = WorkTools(WorkPoise(project["config_path"], "foreign-actor"))
    before = snapshot(case)
    with pytest.raises(PoiseError):
        outsider.invoke(request("advance", {
            "request_id": "foreign", "task_id": "T1", "target_stage": "code_review",
        }))
    assert snapshot(case) == before
    assert outsider.runtime.ownership.snapshot("foreign-actor").task_id is None


def test_preexisting_unknown_effect_cannot_trigger_replay(project):
    case = prepared(project)
    runtime = case["client"].runtime
    with runtime.store.unit_of_work() as unit:
        unit.execution.patch("T1", {"pending": "unknown-test-owned-effect"})
    before = snapshot(case)
    with pytest.raises(PoiseError, match="pending|unknown|outcome"):
        case["client"].invoke(request("advance", {
            "request_id": "pending", "task_id": "T1", "target_stage": "code_review",
        }))
    assert snapshot(case) == before
