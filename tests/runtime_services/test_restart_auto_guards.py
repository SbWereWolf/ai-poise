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


@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
def test_terminal_task_rejects_replay_without_revival(project, terminal):
    case = prepared(project)
    # A terminal aggregate fault fixture, confined to this disposable DB.
    with case["client"].runtime.store.transaction() as database:
        database.execute("UPDATE tasks SET status=?,claimed_by=NULL WHERE id=?", (terminal, "T1"))
        database.execute("UPDATE sessions SET task_id=NULL WHERE id=?", (case["client"].runtime.session,))
    before = snapshot(case)
    with pytest.raises(PoiseError):
        case["client"].invoke(request("advance", {
            "request_id": "terminal", "task_id": "T1", "target_stage": "code_review",
        }))
    assert snapshot(case) == before
    assert case["client"].runtime.task_queries.record("T1")["status"] == terminal


@pytest.mark.parametrize("damage", [None, "stale_index", "dirty_file", "untracked"])
def test_checkout_oracle_detects_wrong_material_independently(project, damage):
    from conftest import git
    from runtime_services.restart_auto_support import assert_checkout, capture_checkout

    root = project["app"]
    commit = git(root, "rev-parse", "HEAD")
    expected = capture_checkout(root, commit)
    case = {"root": root, "checkouts": {commit: expected}}
    path = root / "src/double.py"
    if damage in ("stale_index", "dirty_file"):
        path.write_bytes(b"wrong working bytes\n")
        if damage == "stale_index":
            git(root, "add", "src/double.py")
            path.write_bytes(expected["files"]["src/double.py"])
    elif damage == "untracked":
        (root / "unexpected.txt").write_bytes(b"unexpected replay output\n")
    if damage is None:
        assert_checkout(case, commit)
    else:
        with pytest.raises(AssertionError):
            assert_checkout(case, commit)
    assert git(root, "rev-parse", "HEAD") == commit


@pytest.mark.parametrize("duplicated", ["receipts", "assessments", "transitions"])
def test_effect_oracle_detects_duplicate_confirmed_records(duplicated):
    from runtime_services.restart_auto_support import assert_effect_counts

    effects = {
        "launches": ["C1", "C2", "C3"],
        "receipts": [(1,), (2,), (3,)],
        "assessments": [(4,), (5,), (6,)],
        "transitions": [(7,), (8,), (9,)],
    }
    assert_effect_counts(effects, (3, 3, 3, 3))
    effects[duplicated].append((10,))
    with pytest.raises(AssertionError):
        assert_effect_counts(effects, (3, 3, 3, 3))
    effects[duplicated].pop()
    effects[duplicated][-1] = effects[duplicated][0]
    with pytest.raises(AssertionError):
        assert_effect_counts(effects, (3, 3, 3, 3))
