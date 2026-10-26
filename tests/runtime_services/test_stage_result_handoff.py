"""Integration subject: one public call composes real result, route and claim owners."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.stage_result_handoff_helpers import (
    blocked, business, complete, count, fixture, handoff, receiver, release,
    source_state, start,
)


@pytest.mark.parametrize("worktree", [True, False])
def test_existing_owners_control_route_remains_usable(project, worktree):
    tools, context, packet, counter = start(project, worktree=worktree)
    verified = tools.invoke(request("verify", {
        "result": packet["input"]["result"], "artifacts": [],
    }))
    assert verified["status"] == "verified" and count(counter) == "x"
    advanced = tools.invoke(request("advance", {"task_id": "T1", "request_id": "control-route"}))
    assert advanced["status"] == "role_handoff_required" and advanced["next_stage"] == "review"
    assert release(tools)["status"] == "ownership_released"
    assert source_state(tools)["status"] == "verified"
    assert source_state(tools)["claimed_by"] is None


def test_existing_last_result_control_waits_for_separate_acceptance(project):
    tools, _, packet, counter = start(project, mode="final")
    verified = tools.invoke(request("verify", {"result": packet["input"]["result"], "artifacts": []}))
    assert verified["status"] == "verified" and count(counter) == "x"
    advanced = tools.invoke(request("advance", {"task_id": "T1", "request_id": "control-final"}))
    assert advanced["status"] == "progression_stopped"
    assert advanced["reason"] == "task_acceptance_required"
    assert release(tools)["status"] == "ownership_released"
    assert source_state(tools)["status"] == "verified"


def test_existing_entry_gate_control_observes_a_real_blocked_transition(project):
    tools, _, packet, counter = start(project, mode="same_role", gate=True)
    verified = tools.invoke(request("verify", {"result": packet["input"]["result"], "artifacts": []}))
    assert verified["status"] == "verified" and count(counter) == "x"
    advanced = tools.invoke(request("advance", {"task_id": "T1", "request_id": "control-gate"}))
    assert advanced["status"] == "broken"
    assert source_state(tools)["stage"] == "author"
    assert source_state(tools)["claimed_by"] == "handoff-author"


def test_existing_publication_control_stops_before_separate_user_acceptance(project):
    tools, _, packet, counter = start(project, mode="publish")
    verified = tools.invoke(request("verify", {"result": packet["input"]["result"], "artifacts": []}))
    assert verified["status"] == "verified" and count(counter) == "x"
    advanced = tools.invoke(request("advance", {"task_id": "T1", "request_id": "control-publish"}))
    assert advanced["status"] == "user_acceptance_required"
    assert source_state(tools)["stage"] == "review"


@pytest.mark.parametrize("worktree", [True, False])
def test_one_handoff_verifies_preserves_artifacts_and_releases_both_claims(project, worktree):
    tools, context, packet, counter = start(project, worktree=worktree)
    before = source_state(tools)
    output = complete(handoff(tools, packet))
    assert output["progression"]["status"] == "role_handoff_required"
    assert output["progression"]["next_stage"] == "review"
    assert output["release"]["after"]["worktree_task_id"] is None
    state = source_state(tools)
    assert (state["status"], state["stage"], state["claimed_by"]) == ("verified", "author", None)
    assert state["head"] == before["head"] and state["tree"] == before["tree"]
    assert count(counter) == "x"
    artifact = Path(context["task_root"]) / "artifacts/result.txt"
    assert artifact.read_bytes() == b"Independent stage artifact.\n"
    incoming, resumed = receiver(project)
    assert resumed["stage"] == "author" and resumed["status"] == "verified"
    progressed = incoming.invoke(request("advance", {
        "task_id": "T1", "request_id": output["progression"]["progression"]["request_id"],
    }))
    assert progressed["stage"] == "review"
    assert progressed["status"] == "progression_work_required"


@pytest.mark.parametrize("target", [None, "next_work"])
def test_handoff_stops_at_real_next_work_or_explicit_target_without_executing_it(project, target):
    tools, _, packet, counter = start(project, mode="same_role")
    if target is not None:
        packet["input"]["target_stage"] = target
    output = complete(handoff(tools, packet))
    expected = "progression_work_required" if target is None else "progression_target_reached"
    assert output["progression"]["status"] == expected
    assert source_state(tools)["stage"] == "next_work"
    assert source_state(tools)["status"] == "active"
    assert tools.runtime.show()["status"] == "read_only"
    assert count(counter) == "x"


@pytest.mark.parametrize("mode,expected", [("final", "progression_stopped"),
                                          ("publish", "user_acceptance_required")])
def test_last_result_and_publication_gate_are_transferred_without_autoaccept(project, mode, expected):
    tools, context, packet, counter = start(project, mode=mode)
    output = complete(handoff(tools, packet))
    assert output["progression"]["status"] == expected
    state = source_state(tools)
    assert state["status"] == "verified"
    assert state["stage"] == ("review" if mode == "publish" else "author")
    assert state["claimed_by"] is None and count(counter) == "x"
    assert Path(context["task_root"]).is_dir()
    if mode == "final":
        assert output["progression"]["reason"] == "task_acceptance_required"


def test_completed_exact_replay_preserves_a_distinct_receivers_claim_and_history(project):
    tools, _, packet, counter = start(project)
    first = complete(handoff(tools, packet))
    incoming, _ = receiver(project)
    before = source_state(incoming)
    ownership = incoming.runtime.ownership.snapshot("handoff-reviewer")
    replay = complete(handoff(tools, packet), replayed=True)
    assert {k: v for k, v in replay.items() if k != "replayed"} == {
        k: v for k, v in first.items() if k != "replayed"
    }
    assert source_state(incoming) == before
    assert incoming.runtime.ownership.snapshot("handoff-reviewer") == ownership
    assert ownership.task_id == "T1" and count(counter) == "x"


@pytest.mark.parametrize("field", ["result", "artifacts", "reason", "target_stage", "task_id"])
def test_reused_request_with_changed_intent_refuses_without_new_effects(project, field):
    tools, _, packet, counter = start(project)
    complete(handoff(tools, packet))
    before = source_state(tools)
    changed = deepcopy(packet)
    changes = fixture("changed-intent-values.json")
    if field == "result":
        changed["input"]["result"]["sections"]["report"] = changes["result_report"]
    else:
        changed["input"][field] = changes[field]
    with pytest.raises(PoiseError) as error:
        tools.invoke(changed)
    assert any(word in str(error.value).lower() for word in ("conflict", "конфликт"))
    assert source_state(tools) == before and count(counter) == "x"


def test_foreign_first_call_refuses_without_stealing_the_authors_claim(project):
    tools, _, packet, counter = start(project)
    other = WorkTools(WorkPoise(project["config_path"], "foreign-handoff-caller"))
    before = source_state(tools)
    with pytest.raises(PoiseError) as error:
        other.invoke(packet)
    assert "Unknown work operation" not in str(error.value)
    assert source_state(tools) == before and count(counter) == ""
    assert tools.runtime.ownership.snapshot("handoff-author").task_id == "T1"


def test_receiver_cannot_replay_the_original_callers_completed_request(project):
    tools, _, packet, counter = start(project)
    complete(handoff(tools, packet))
    incoming, _ = receiver(project)
    before = source_state(incoming)
    with pytest.raises(PoiseError):
        incoming.invoke(deepcopy(packet))
    assert source_state(incoming) == before
    assert incoming.runtime.ownership.snapshot("handoff-reviewer").task_id == "T1"
    assert count(counter) == "x"


def test_invalid_input_has_no_verification_artifact_or_claim_effect(project):
    tools, context, packet, counter = start(project)
    before = source_state(tools)
    packet["input"]["result"] = None
    with pytest.raises(PoiseError) as error:
        tools.invoke(packet)
    assert "result" in str(error.value)
    assert source_state(tools) == before and count(counter) == ""
    assert not (Path(context["task_root"]) / "artifacts/result.txt").exists()


def test_failed_verification_is_blocked_without_progression_or_release(project):
    tools, _, packet, counter = start(project, check_exit=7)
    output = blocked(handoff(tools, packet), "verification")
    assert output["verification"]["status"] == "checks_failed"
    assert output["progression"] is None and output["release"] is None
    assert output["verification"]["checks"][0]["actual_exit_code"] == 7
    assert source_state(tools)["claimed_by"] == "handoff-author"
    assert count(counter) == "x"
    repeated = handoff(tools, packet)
    assert business(repeated)["status"] == "handoff_blocked"
    assert count(counter) == "x"


def test_real_entry_gate_failure_retains_the_verified_result_and_current_owner(project):
    tools, _, packet, counter = start(project, mode="same_role", gate=True)
    output = blocked(handoff(tools, packet), "progression")
    assert output["verification"]["status"] == "verified"
    assert output["progression"]["status"] == "broken"
    assert output["release"] is None
    state = source_state(tools)
    assert (state["status"], state["stage"], state["claimed_by"]) == (
        "verified", "author", "handoff-author",
    )
    assert count(counter) == "x"


def test_complete_machine_receipt_retains_full_output_beyond_agent_view_budget(project):
    tools, _, packet, counter = start(project, large=True)
    output = complete(handoff(tools, packet))
    receipt = output["verification"]["checks"][0]
    raw = Path(receipt["stdout"]).read_text(encoding="utf-8")
    assert raw == (fixture("large-output.json"))["stdout"]
    assert len(raw) > 2200 and count(counter) == "x"


def test_handoff_preserves_and_resumes_an_existing_compatible_progression_intent(project):
    tools, _, packet, counter = start(project)
    waiting = tools.invoke(request("advance", {"task_id": "T1", "request_id": "existing-intent"}))
    assert waiting["status"] == "progression_work_required"
    output = complete(handoff(tools, packet))
    assert output["progression"]["progression"]["request_id"] == "existing-intent"
    assert output["progression"]["status"] == "role_handoff_required"
    assert count(counter) == "x"


def test_handoff_without_a_dependent_worktree_preserves_an_independent_worktree(project):
    tools, _, packet, counter = start(project, worktree=False)
    second_workflow = deepcopy(project["workflow"])
    second_workflow["worktree_required"] = True
    write_json(project["root"] / "config/workflows/development.json", second_workflow)
    second_task = deepcopy(project["task"])
    second_task["id"] = "SECOND"
    seed = WorkTools(WorkPoise(project["config_path"], "independent-worktree-seed"))
    seeded = seed.invoke(request("bootstrap", {
        "task": second_task, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert seeded["status"] == "active"
    assert release(seed, "independent-seed-release")["status"] == "ownership_released"
    tools.runtime.ownership.acquire_worktree("SECOND")
    before = tools.runtime.ownership.snapshot("handoff-author")
    assert before.task_id == "T1" and before.worktree_task_id == "SECOND"
    second_state = deepcopy(tools.runtime.task_queries.record("SECOND"))
    output = complete(handoff(tools, packet))
    assert output["release"]["after"]["worktree_task_id"] == "SECOND"
    after = tools.runtime.ownership.snapshot("handoff-author")
    assert after.task_id is None and after.worktree_task_id == "SECOND"
    assert tools.runtime.task_queries.record("SECOND") == second_state
    assert count(counter) == "x"
