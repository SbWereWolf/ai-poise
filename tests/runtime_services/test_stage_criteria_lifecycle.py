"""Stage criteria through real WorkTools, SQLite and disposable Git repositories."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from tests.runtime_services.stage_criteria_helpers import (
    assert_unstarted, boot, criteria, handoff, next_stage, proposal, rows, show, verify_packet, work,
)


def test_initial_execution_uses_real_initial_criteria_without_proposal_or_decision(project):
    tools, context = boot(project, initial_execution=True)
    assert context["stage"] == "build"
    assert context["purpose"] == "execution"
    assert context["criteria_authority"] == {"propose": [], "decide": []}
    current = show(tools)
    assert set(current) == {
        "task", "stage_id", "purpose", "revision", "criteria", "entry_requirements",
        "exit_requirements", "source", "execution", "proposals", "history",
    }
    assert current["task"] == "T1"
    assert current["stage_id"] == "build"
    assert current["purpose"] == "execution"
    assert current["criteria"] == criteria()
    assert current["source"]["kind"] == "initial"
    assert set(current["source"]) == {"kind", "contract_digest", "requirements_snapshot_digest"}
    for field in ("contract_digest", "requirements_snapshot_digest"):
        assert isinstance(current["source"][field], str) and len(current["source"][field]) == 64
    assert current["proposals"] == []
    assert current["execution"]["state"] == "started"
    assert current["execution"]["bound_revision"] == current["revision"]
    before = rows(tools)
    assert show(tools) == current
    assert rows(tools) == before


@pytest.mark.parametrize("custom_design,custom_decision", [(False, False), (True, True)])
def test_propose_then_accept_binds_exact_revision_with_declared_custom_authority(
    project, custom_design, custom_decision,
):
    tools, context = boot(project, custom_design=custom_design, custom_decision=custom_decision)
    initial = show(tools)
    candidate = proposal(tools)
    packet = verify_packet(context, work(tools, "propose-build", proposals=[candidate]))
    tools.invoke(packet)
    pending = show(tools)
    assert pending["revision"] == initial["revision"]
    assert pending["criteria"] == initial["criteria"]
    assert_unstarted(pending["execution"])
    assert pending["execution"] == initial["execution"]
    assert len(pending["proposals"]) == 1
    proposed = pending["proposals"][0]
    assert proposed["producer_stage"] == "sketch"
    assert proposed["producer_iteration"] == context["iteration"]
    assert proposed["producer_submission"] > 0
    decision_context = next_stage(tools)
    assert decision_context["stage"] == "assess"
    tools.invoke(verify_packet(decision_context, work(tools, "accept-build", decisions=[{
        "proposal_id": proposed["proposal_id"], "proposal_digest": proposed["proposal_digest"],
        "decision": "accepted", "reason": "The proposed criteria express the intended result.",
    }])))
    accepted = show(tools)
    assert accepted["criteria"] == candidate["criteria"]
    assert accepted["revision"] != initial["revision"]
    assert accepted["source"]["kind"] == "proposal"
    assert accepted["source"]["proposal_id"] == proposed["proposal_id"]
    assert accepted["source"]["proposal_digest"] == proposed["proposal_digest"]
    assert set(accepted["source"]) == {"kind", "proposal_id", "proposal_digest", "decision_id"}
    assert accepted["source"]["decision_id"] is not None
    assert accepted["history"][:len(initial["history"])] == initial["history"]
    agreement = accepted["history"][-1]
    assert {"id", "action", "actor", "stage", "iteration", "old_revision", "new_revision", "reason", "source"} <= set(agreement)
    assert agreement["actor"] == tools.runtime.session
    assert agreement["stage"] == "assess"
    assert agreement["iteration"] == decision_context["iteration"]
    assert agreement["old_revision"] == initial["revision"]
    assert agreement["new_revision"] == accepted["revision"]
    assert agreement["reason"] == "The proposed criteria express the intended result."
    execution_context = next_stage(tools)
    assert execution_context["stage"] == "build"
    started = show(tools)
    assert started["criteria"] == candidate["criteria"]
    assert started["execution"]["state"] == "started"
    assert started["execution"]["bound_revision"] == accepted["revision"]


def test_rejection_keeps_initial_revision_eligible_for_execution(project):
    tools, context = boot(project)
    initial = show(tools)
    tools.invoke(verify_packet(context, work(tools, "propose-rejected", proposals=[proposal(tools)])))
    proposed = show(tools)["proposals"][0]
    tools.invoke(verify_packet(next_stage(tools), work(tools, "reject-build", decisions=[{
        "proposal_id": proposed["proposal_id"], "proposal_digest": proposed["proposal_digest"],
        "decision": "rejected", "reason": "Keep the initial approved criterion.",
    }])))
    current = show(tools)
    assert current["revision"] == initial["revision"]
    assert current["criteria"] == initial["criteria"]
    next_stage(tools)
    assert show(tools)["execution"]["bound_revision"] == initial["revision"]


def test_proposal_replay_does_not_create_another_revision_submission_or_event(project):
    tools, context = boot(project)
    packet = verify_packet(context, work(tools, "stable-proposal", proposals=[proposal(tools)]))
    tools.invoke(packet)
    before = rows(tools)
    before_criteria = show(tools)
    tools.invoke(packet)
    assert rows(tools) == before
    assert show(tools) == before_criteria
    changed = deepcopy(packet)
    changed["input"]["result"]["stage_work"]["criteria_work"]["proposals"][0]["criteria"] = criteria("Return three times the input.")
    with pytest.raises(PoiseError):
        tools.invoke(changed)
    assert rows(tools) == before
    assert show(tools) == before_criteria


@pytest.mark.parametrize("fault", ["stale_task", "stale_criteria", "undeclared_target", "duplicate_target"])
def test_invalid_proposal_batch_has_no_partial_task_or_submission_write(project, fault):
    tools, context = boot(project)
    candidate = proposal(tools)
    change = work(tools, "reject-invalid-batch", proposals=[candidate])
    if fault == "stale_task":
        change["expected_version"] -= 1
    elif fault == "stale_criteria":
        change["proposals"][0]["expected_criteria_revision"] += 1
    elif fault == "undeclared_target":
        change["proposals"][0]["stage_id"] = "custom"
    else:
        change["proposals"].append(deepcopy(candidate))
    before = rows(tools)
    current = show(tools)
    with pytest.raises(PoiseError):
        tools.invoke(verify_packet(context, change))
    assert rows(tools) == before
    assert show(tools) == current


def test_duplicate_decisions_reject_whole_batch_including_accept_then_reject(project):
    tools, context = boot(project)
    tools.invoke(verify_packet(context, work(tools, "offer-once", proposals=[proposal(tools)])))
    proposed = show(tools)["proposals"][0]
    context = next_stage(tools)
    decision = {
        "proposal_id": proposed["proposal_id"], "proposal_digest": proposed["proposal_digest"],
        "decision": "accepted", "reason": "Accept the exact proposal.",
    }
    opposite = {**decision, "decision": "rejected"}
    before = rows(tools)
    current = show(tools)
    with pytest.raises(PoiseError, match="duplicate_proposal_decision"):
        tools.invoke(verify_packet(context, work(tools, "duplicate-decisions", decisions=[decision, opposite])))
    assert rows(tools) == before
    assert show(tools) == current


@pytest.mark.parametrize("second_decision", ["accepted", "rejected"])
def test_different_proposals_for_same_target_cannot_overlap_even_when_one_is_rejected(project, second_decision):
    tools, context = boot(project)
    tools.invoke(verify_packet(context, work(tools, "offer-first-overlap", proposals=[proposal(tools)])))
    again = tools.invoke(request("bootstrap", {
        "task": None, "decision": "rework", "rework_stage": "sketch",
        "feedback": "Create a distinct proposal against the same unchanged target revision.",
    }))
    tools.invoke(verify_packet(again, work(tools, "offer-second-overlap", proposals=[
        proposal(tools, requirement="Return twice the input, including zero."),
    ])))
    offered = show(tools)["proposals"]
    assert len(offered) == 2
    assert offered[0]["proposal_id"] != offered[1]["proposal_id"]
    observation = next_stage(tools)
    decisions = [
        {"proposal_id": item["proposal_id"], "proposal_digest": item["proposal_digest"],
         "decision": disposition, "reason": "Check target overlap independently of decision disposition."}
        for item, disposition in zip(offered, ("accepted", second_decision), strict=True)
    ]
    before, current = rows(tools), show(tools)
    with pytest.raises(PoiseError, match="overlapping_decision_targets"):
        tools.invoke(verify_packet(observation, work(tools, "overlapping-decisions", decisions=decisions)))
    assert rows(tools) == before
    assert show(tools) == current


def test_valid_first_proposal_and_stale_second_target_roll_back_the_complete_packet(project):
    tools, context = boot(project, second_execution=True)
    proposals = [proposal(tools), proposal(tools, "build_next")]
    proposals[1]["expected_criteria_revision"] += 1
    before = rows(tools)
    current = [show(tools, stage) for stage in ("build", "build_next")]
    with pytest.raises(PoiseError):
        tools.invoke(verify_packet(context, work(tools, "valid-then-stale", proposals=proposals)))
    assert rows(tools) == before
    assert [show(tools, stage) for stage in ("build", "build_next")] == current


def test_other_without_explicit_grant_cannot_propose(project):
    tools, context = boot(project, custom_design=True, empty_grants=True)
    candidate = proposal(tools)
    before = rows(tools)
    current = show(tools)
    with pytest.raises(PoiseError, match="criteria_authority_denied"):
        tools.invoke(verify_packet(context, work(tools, "no-other-grant", proposals=[candidate])))
    assert rows(tools) == before
    assert show(tools) == current


def test_disjoint_decision_batch_accepts_both_from_one_entry_version(project):
    tools, context = boot(project, second_execution=True)
    proposals = [proposal(tools, "build"), proposal(tools, "build_next", "Handle zero explicitly.")]
    tools.invoke(verify_packet(context, work(tools, "two-targets", proposals=proposals)))
    offered = [show(tools, target)["proposals"][0] for target in ("build", "build_next")]
    context = next_stage(tools)
    decisions = [
        {
            "proposal_id": item["proposal_id"], "proposal_digest": item["proposal_digest"],
            "decision": "accepted", "reason": "Accept this disjoint target.",
        }
        for item in offered
    ]
    tools.invoke(verify_packet(context, work(tools, "two-decisions", decisions=decisions)))
    for candidate in proposals:
        current = show(tools, candidate["stage_id"])
        assert current["criteria"] == candidate["criteria"]
        assert current["source"]["kind"] == "proposal"


def test_valid_then_wrong_digest_decision_does_not_activate_first_target(project):
    tools, context = boot(project, second_execution=True)
    tools.invoke(verify_packet(context, work(tools, "offer-two", proposals=[
        proposal(tools, "build"), proposal(tools, "build_next"),
    ])))
    offered = [show(tools, target)["proposals"][0] for target in ("build", "build_next")]
    context = next_stage(tools)
    decisions = [
        {
            "proposal_id": item["proposal_id"], "proposal_digest": item["proposal_digest"],
            "decision": "accepted", "reason": "Check atomic decision validation.",
        }
        for item in offered
    ]
    decisions[1]["proposal_digest"] = "0" * 64
    before = rows(tools)
    current = [show(tools, target) for target in ("build", "build_next")]
    with pytest.raises(PoiseError, match="proposal_identity_mismatch"):
        tools.invoke(verify_packet(context, work(tools, "one-invalid-decision", decisions=decisions)))
    assert rows(tools) == before
    assert [show(tools, target) for target in ("build", "build_next")] == current


@pytest.mark.parametrize("malformation", [
    "null", "missing_version", "unknown_field", "bool_version",
    "nonlist_proposals", "both_empty", "both_nonempty", "malformed_proposal",
])
def test_criteria_work_shape_errors_do_not_mutate_task(project, malformation):
    tools, context = boot(project)
    change = work(tools, "bad-shape", proposals=[proposal(tools)])
    packet = verify_packet(context, change)
    payload = packet["input"]["result"]["stage_work"]
    if malformation == "null":
        payload["criteria_work"] = None
    elif malformation == "missing_version":
        del payload["criteria_work"]["expected_version"]
    elif malformation == "unknown_field":
        payload["criteria_work"]["implicit"] = True
    elif malformation == "bool_version":
        payload["criteria_work"]["expected_version"] = True
    elif malformation == "nonlist_proposals":
        payload["criteria_work"]["proposals"] = {}
    elif malformation == "both_empty":
        payload["criteria_work"]["proposals"] = []
    elif malformation == "both_nonempty":
        payload["criteria_work"]["decisions"] = [{}]
    else:
        del payload["criteria_work"]["proposals"][0]["criteria"]
    before = rows(tools)
    with pytest.raises(PoiseError):
        tools.invoke(packet)
    assert rows(tools) == before


def test_accepted_exit_gate_is_used_for_actual_artifact_effect(project):
    tools, context = boot(project, output_gate=True)
    candidate = proposal(tools, requirement="Produce the declared build artifact.")
    candidate["exit_requirements"] = ["build-output"]
    tools.invoke(verify_packet(context, work(tools, "propose-output-gate", proposals=[candidate])))
    proposed = show(tools)["proposals"][0]
    tools.invoke(verify_packet(next_stage(tools), work(tools, "accept-output-gate", decisions=[{
        "proposal_id": proposed["proposal_id"], "proposal_digest": proposed["proposal_digest"],
        "decision": "accepted", "reason": "Require this declared output at execution.",
    }])))
    execution = next_stage(tools)
    bound = show(tools)
    assert bound["exit_requirements"] == ["build-output"]
    packet = verify_packet(execution)
    with pytest.raises(PoiseError):
        tools.invoke(packet)
    assert show(tools)["execution"] == bound["execution"]
    packet["input"]["artifacts"] = [{
        "scope": "task", "path": "outputs/build.txt",
        "source": {"kind": "text", "text": "The build result is available.\n"},
    }]
    assert tools.invoke(packet)["status"] == "verified"
    with tools.runtime.store.transaction() as database:
        artifacts = database.execute(
            "SELECT a.path FROM artifacts a JOIN task_artifacts t ON t.artifact_id=a.id "
            "WHERE t.task_id=? AND a.scope=?", ("T1", "task"),
        ).fetchall()
    paths = [Path(row[0]) for row in artifacts if Path(row[0]).name == "build.txt"]
    assert len(paths) == 1
    assert paths[0].read_text(encoding="utf-8") == "The build result is available.\n"


def test_other_decision_grant_reaches_real_inspect_handler_under_configured_actor(project):
    tools, context = boot(project, custom_design=True, custom_decision=True, inspection_decision=True)
    candidate = proposal(tools)
    tools.invoke(verify_packet(context, work(tools, "other-produce-offer", proposals=[candidate])))
    proposed = show(tools)["proposals"][0]
    advance = request("advance", {
        "request_id": "reach-custom-inspector", "task_id": "T1", "target_stage": "assess",
    })
    assert tools.invoke(advance)["status"] == "role_handoff_required"
    handoff(tools, "handoff-custom-inspector")
    inspector = WorkTools(WorkPoise(project["config_path"], "arbiter-session"))
    inspector.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert inspector.invoke(advance)["status"] == "progression_target_reached"
    context = inspector.invoke(request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert context["stage"] == "assess"
    assert context["purpose"] == "other"
    assert context["handler"] == "inspect"
    tools = inspector
    tools.invoke(verify_packet(context, work(tools, "other-inspect-decision", decisions=[{
        "proposal_id": proposed["proposal_id"], "proposal_digest": proposed["proposal_digest"],
        "decision": "accepted", "reason": "The configured arbiter accepts this exact criterion.",
    }])))
    accepted = show(tools)
    assert accepted["criteria"] == candidate["criteria"]
    assert accepted["source"]["proposal_id"] == proposed["proposal_id"]
