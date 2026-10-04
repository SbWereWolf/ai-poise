"""Authorized legacy restart must distinguish historical and revised Requirements context."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
import os
import sys

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from batch.helpers import request, result, verify
from conftest import add_test, bind_task_requirements, write_json
from poise.modules.foundation.errors import PoiseError
from runtime_services.test_task_restart import git, restart
from sprints.helpers import bootstrap as sprint_bootstrap
from sprints.helpers import draft as sprint_draft
from sprints.helpers import publish as sprint_publish
from sprints.helpers import setup as setup_sprint
from sprints.helpers import task as sprint_task
from tasks.test_newborn_lifecycle import complete_patch, create, edit, task_action, tools
from tasks.test_planning_flexibility import full_draft


INCIDENTS = (
    ("ERP-EXCEL-IN04", "Excel export obeys its revised Requirements context."),
    ("ERP-HARNESS-P001", "ERP harness obeys its revised Requirements context."),
    ("AIP-MCP-CLEANUP-GUARD-001", "MCP cleanup guard obeys its revised Requirements context."),
)
HISTORICAL_REFUSAL = "Restarted Task Requirements context differs from persisted state"


def _assert_source_identity() -> None:
    import poise

    root = Path(__file__).resolve().parents[2]
    assert Path(poise.__file__).resolve().is_relative_to(root / "src"), (
        poise.__file__, root
    )
    assert Path(os.environ["PYTHONPATH"]).resolve() == root / "src"


def _ready(client, task_id: str, revision: int, request_id: str):
    return task_action(
        client,
        action="ready",
        task_id=task_id,
        expected_revision=revision,
        request_id=request_id,
    )


def _new_revised_context(client, project, requirement: str) -> dict:
    contract = deepcopy(project["task"])
    contract["requirements"] = [requirement]
    bind_task_requirements(contract, client.runtime.requirements_commands.store.registry())
    return {
        key: deepcopy(contract[key])
        for key in ("requirements", "requirements_snapshot", "requirements_agreement")
    }


def _create_restarted(project, task_id: str, requirement: str):
    _assert_source_identity()
    client = tools(project, f"owner-{task_id.lower()}")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], {**complete_patch(project, task_id), "goal_type": "development"}, f"fill-{task_id}")
    first = _ready(client, task_id, filled["revision"], f"first-ready-{task_id}")
    original = deepcopy(client.runtime.task_queries.record(task_id))
    restarted = restart(
        client,
        task_id,
        first["revision"],
        request_id=f"restart-{task_id}",
        authorization="User explicitly authorized this unfinished legacy Task restart.",
    )
    revised = _new_revised_context(client, project, requirement)
    revised["definition_of_done"] = [f"{task_id}: revised behavior is independently verified."]
    changed = edit(client, task_id, restarted["revision"], revised, f"reagree-{task_id}")
    return client, original, changed, revised


def _observable_state(client, task_id: str) -> dict:
    runtime = client.runtime
    record = deepcopy(runtime.task_queries.record(task_id))
    with runtime.store.unit_of_work() as uow:
        context = deepcopy(uow.tasks.restart_context(task_id))
    with runtime.store.transaction() as database:
        receipts = tuple(
            (row["seq"], row["data"])
            for row in database.execute(
                "SELECT seq,data FROM journal WHERE task_id=? AND event='newborn.action' ORDER BY seq",
                (task_id,),
            )
        )
        events = tuple(
            (row["seq"], row["data"])
            for row in database.execute(
                "SELECT seq,data FROM task_events WHERE task_id=? ORDER BY seq", (task_id,)
            )
        )
        proof = tuple(
            row["data"] for row in database.execute(
                "SELECT data FROM task_proofs WHERE task_id=?", (task_id,)
            )
        )
    return {
        "task": record,
        "context": context,
        "proof_and_registry": _historical_proof_state(client, task_id),
        "receipts": receipts,
        "events": events,
        "proof": proof,
        "requirements": deepcopy(runtime.requirements_commands.store.registry().to_dict()),
        "ownership": deepcopy(runtime.ownership.snapshot(client.runtime.session)),
        "head": git(runtime.cfg["git"]["repository"], "rev-parse", "HEAD"),
        "worktree_status": git(runtime.cfg["git"]["repository"], "status", "--short"),
    }


def _exercise_changed_context(project, task_id: str, requirement: str) -> str:
    client, original, changed, revised = _create_restarted(project, task_id, requirement)
    before = _observable_state(client, task_id)
    try:
        result = _ready(client, task_id, changed["revision"], f"revised-ready-{task_id}")
    except PoiseError as error:
        assert str(error) == HISTORICAL_REFUSAL
        assert _observable_state(client, task_id) == before
        return "historical-refusal"
    assert result["status"] == "available"
    current = client.runtime.task_queries.record(task_id)
    assert current["contract"]["requirements"] == revised["requirements"]
    assert current["contract"]["definition_of_done"] == revised["definition_of_done"]
    assert client.runtime.task_commands.requirements_snapshot(task_id) == revised["requirements_snapshot"]
    with client.runtime.store.unit_of_work() as uow:
        context = uow.tasks.restart_context(task_id)
    assert context["requirements_snapshot"] == revised["requirements_snapshot"]
    assert context["requirements_agreement"] == revised["requirements_agreement"]
    assert current["id"] == original["id"]
    assert current["sprint_id"] == original["sprint_id"]
    assert current["branch"] == original["branch"]
    assert current["worktree"] == original["worktree"]
    assert current["history"][:len(original["history"])] == original["history"]
    history = context["restart_history"]
    assert history[-1]["from_status"] == "available"
    assert history[-1]["before_requirements_snapshot"] == original["requirements_snapshot"]
    assert history[-1]["before_requirements_agreement"] == original["requirements_agreement"]
    return "accepted"


def test_probe_all_three_incident_classes(project):
    outcomes = [_exercise_changed_context(project, task_id, requirement) for task_id, requirement in INCIDENTS]
    assert len(set(outcomes)) == 1, outcomes
    if outcomes[0] == "historical-refusal":
        print("LEGACY_READY_CONTEXT_REJECTED")
    else:
        assert outcomes == ["accepted", "accepted", "accepted"]
        print("LEGACY_READY_CONTEXT_ACCEPTED")


def test_unchanged_context_uses_persisted_history(project):
    _assert_source_identity()
    task_id = "UNCHANGED-CONTEXT"
    client = tools(project, "owner-unchanged")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], {**complete_patch(project, task_id), "goal_type": "development"}, "fill-unchanged")
    first = _ready(client, task_id, filled["revision"], "first-ready-unchanged")
    stored = client.runtime.task_commands.requirements_snapshot(task_id)
    restarted = restart(client, task_id, first["revision"], request_id="restart-unchanged")
    assert restarted["draft"]["requirements_snapshot"] == stored
    client.runtime.requirements_commands.apply({
        "request_id": "drift-live-after-publication",
        "expected_revision": 1,
        "operations": [{"kind": "put_requirement", "requirement": {
            "id": "FIXTURE-SYSTEM",
            "level": "system",
            "status": "obsolete",
            "text": "This live requirement changed after the Task was first published.",
        }}],
    })
    assert client.runtime.requirements_commands.store.registry().to_dict() != project["requirements_registry"].to_dict()
    assert _ready(client, task_id, restarted["revision"], "again-ready-unchanged")["status"] == "available"
    assert client.runtime.task_commands.requirements_snapshot(task_id) == stored


def test_revised_sprint_member_preserves_nonempty_worktree_and_wip(project):
    _assert_source_identity()
    task_id = "SPRINT-REVISED"
    setup_sprint(project)
    client = tools(project, "sprint-owner")
    proposed = sprint_draft(client, [sprint_task(project, task_id)])
    sprint_publish(client, proposed["revision"])
    active = sprint_bootstrap(client, task_id)
    worktree = Path(active["worktree"])
    assert active["task"] == task_id
    tracked = worktree / "src" / "double.py"
    tracked.write_text("def double(value):\n    return value * 7\n", encoding="utf-8")
    staged = worktree / "tests" / "sprint-staged.txt"
    staged.parent.mkdir(exist_ok=True)
    staged.write_bytes(b"staged WIP\n")
    git(worktree, "add", "tests/sprint-staged.txt")
    untracked = worktree / "local-note.txt"
    untracked.write_bytes(b"untracked WIP\n")
    before_git = {
        "head": git(worktree, "rev-parse", "HEAD"),
        "index": git(worktree, "write-tree"),
        "status": git(worktree, "status", "--porcelain=v1", "--untracked-files=all"),
        "tracked": tracked.read_bytes(),
        "staged": staged.read_bytes(),
        "untracked": untracked.read_bytes(),
    }
    assert before_git["status"]
    before_task = deepcopy(client.runtime.task_queries.record(task_id))
    assert before_task["sprint_id"] == "S"
    assert before_task["branch"] and before_task["worktree"] == str(worktree)
    restarted = restart(client, task_id, before_task["version"], request_id="restart-sprint-revised")
    revised = _new_revised_context(client, project, "The sprint member's revised contract is agreed.")
    revised["definition_of_done"] = ["Revised sprint member is validated."]
    changed = edit(client, task_id, restarted["revision"], revised, "reagree-sprint")
    assert _ready(client, task_id, changed["revision"], "ready-sprint-revised")["status"] == "available"
    current = client.runtime.task_queries.record(task_id)
    assert current["id"] == task_id
    assert current["sprint_id"] == before_task["sprint_id"] == "S"
    assert current["branch"] == before_task["branch"]
    assert current["worktree"] == before_task["worktree"] == str(worktree)
    assert current["contract"]["requirements"] == revised["requirements"]
    assert {
        "head": git(worktree, "rev-parse", "HEAD"),
        "index": git(worktree, "write-tree"),
        "status": git(worktree, "status", "--porcelain=v1", "--untracked-files=all"),
        "tracked": tracked.read_bytes(),
        "staged": staged.read_bytes(),
        "untracked": untracked.read_bytes(),
    } == before_git
    assert client.runtime.sprint_tools.overview("S")["tasks"][0]["id"] == task_id


def _historical_proof_state(client, task_id: str) -> dict:
    with client.runtime.store.transaction() as database:
        row = database.execute(
            "SELECT data FROM task_proofs WHERE task_id=?", (task_id,)
        ).fetchone()
        workflow = database.execute(
            "SELECT data FROM task_workflows WHERE task_id=?", (task_id,)
        ).fetchone()
        return {
            "current_proof": None if row is None else row["data"],
            "layers": tuple(
                (item["version"], item["data"])
                for item in database.execute(
                    "SELECT version,data FROM task_proof_layers WHERE task_id=? ORDER BY version",
                    (task_id,),
                )
            ),
            "evidence": tuple(
                (item["id"], item["data"])
                for item in database.execute(
                    "SELECT id,data FROM evidence WHERE task_id=? ORDER BY id", (task_id,)
                )
            ),
            "registry": None if workflow is None else workflow["data"],
            "methods": tuple(
                (item["method_id"], item["data"])
                for item in database.execute(
                    "SELECT method_id,data FROM task_methods WHERE task_id=? ORDER BY method_id",
                    (task_id,),
                )
            ),
            "work_packets": tuple(
                (item["stage"], item["iteration"], item["digest"])
                for item in database.execute(
                    "SELECT stage,iteration,digest FROM work_packets WHERE task_id=? ORDER BY stage,iteration",
                    (task_id,),
                )
            ),
        }


def test_revised_ready_retains_real_prior_proof_without_reusing_it(project):
    _assert_source_identity()
    import json

    task_id = "PROOF-REVISED"
    project["process"]["stages"][0]["sections"]["test_registry"] = "Register the audited method."
    write_json(project["root"] / "config/processes/development.json", project["process"])
    client = tools(project, "proof-owner")
    initial = deepcopy(project["task"])
    initial["id"] = task_id
    context = client.invoke(request("bootstrap", {
        "task": initial, "decision": None, "feedback": None, "rework_stage": None,
    }))
    add_test(context["worktree"])
    payload = result(context)
    payload["sections"]["test_registry"] = "Register AUDITED-GREEN for the implementation stage."
    registered = deepcopy(project["task"]["methods"][1])
    registered["id"] = "AUDITED-GREEN"
    registered["argv"] = [
        sys.executable, "-B", "-m", "unittest", "discover",
        "-s", "tests", "-p", "test_double.py", "-v",
    ]
    registered["verification_plan"]["green_stages"] = ["implementation"]
    payload["method_additions"] = {
        "request_id": "register-audited-green",
        "expected_revision": 0,
        "operations": [{
            "kind": "add", "method_id": "AUDITED-GREEN",
            "registration": {"method": registered, "stages": ["implementation"]},
        }],
        "executable_obligations": [],
    }
    first = verify(client, payload)
    assert first["status"] == "verified"
    assert first["checks"] and first["checks"][0]["passed"] is True
    previous = client.runtime.task_queries.record(task_id)
    old_snapshot = deepcopy(previous["requirements_snapshot"])
    old_agreement = deepcopy(previous["requirements_agreement"])
    old = _historical_proof_state(client, task_id)
    assert old["layers"] and old["evidence"]
    assert json.loads(old["current_proof"])["book"]["batches"]
    old_registry = json.loads(old["registry"])["registry"]
    old_registry_view = deepcopy(client.runtime.task_queries.verification_registry(task_id))
    assert old["methods"]
    assert old_registry["revision"] == 1
    assert old_registry["history"]
    assert old_registry["requests"]
    assert any(method_id == "AUDITED-GREEN" for method_id, _ in old["methods"])
    assert any(item["method"]["id"] == "AUDITED-GREEN" for item in old_registry_view["current"])
    restarted = restart(client, task_id, previous["version"], request_id="restart-proof")
    revised = _new_revised_context(client, project, "A verified Task accepts a newly agreed requirement.")
    revised["definition_of_done"] = ["Independent verification of revised Task."]
    changed = edit(client, task_id, restarted["revision"], revised, "reagree-proof")
    assert _ready(client, task_id, changed["revision"], "ready-proof-revised")["status"] == "available"
    after = _historical_proof_state(client, task_id)
    assert after["layers"][:len(old["layers"])] == old["layers"]
    assert after["evidence"][:len(old["evidence"])] == old["evidence"]
    assert json.loads(after["current_proof"])["book"]["batches"] == []
    assert after["methods"] == old["methods"]
    assert json.loads(after["registry"])["registry"] == old_registry
    assert client.runtime.task_queries.verification_registry(task_id) == old_registry_view
    assert client.runtime.task_commands.requirements_snapshot(task_id) == revised["requirements_snapshot"]
    with client.runtime.store.unit_of_work() as uow:
        new_context = uow.tasks.restart_context(task_id)
    assert old_agreement != revised["requirements_agreement"]
    assert new_context["requirements_snapshot"] == revised["requirements_snapshot"]
    assert new_context["requirements_agreement"] == revised["requirements_agreement"]
    assert new_context["restart_history"][-1]["before_requirements_snapshot"] == old_snapshot
    assert new_context["restart_history"][-1]["before_requirements_agreement"] == old_agreement


@pytest.mark.parametrize("invalid, expected_cause", (
    ("text-only", "Task requirements snapshot does not match Task requirements"),
    ("snapshot-only", "Task requirements snapshot does not match Task requirements"),
    ("agreement-only", "Explicit full-text requirements agreement is required"),
    ("missing-snapshot", "Task requirements snapshot has an invalid shape"),
    ("missing-agreement", "Explicit full-text requirements agreement is required"),
))
def test_rejects_invalid_changed_context(project, invalid, expected_cause):
    _assert_source_identity()
    task_id = f"INVALID-{invalid.upper()}"
    client, _, changed, revised = _create_restarted(project, task_id, f"{invalid} revised requirement.")
    original = deepcopy(project["task"])
    patch = {}
    if invalid == "text-only":
        patch = {
            "requirements_snapshot": original["requirements_snapshot"],
            "requirements_agreement": original["requirements_agreement"],
        }
    elif invalid == "snapshot-only":
        patch = {
            "requirements": original["requirements"],
            "requirements_agreement": original["requirements_agreement"],
        }
    elif invalid == "agreement-only":
        patch = {
            "requirements": original["requirements"],
            "requirements_snapshot": original["requirements_snapshot"],
        }
    elif invalid == "missing-snapshot":
        patch = {"requirements_snapshot": None}
    elif invalid == "missing-agreement":
        patch = {"requirements_agreement": None}
    broken = edit(client, task_id, changed["revision"], patch, f"break-{invalid}")
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError) as failure:
        _ready(client, task_id, broken["revision"], f"invalid-ready-{invalid}")
    assert str(failure.value) == expected_cause
    assert _observable_state(client, task_id) == before


@pytest.mark.parametrize("authorization", (None, "", 17))
def test_restart_rejects_missing_or_invalid_legacy_authority(project, authorization):
    _assert_source_identity()
    task_id = "INVALID-AUTHORITY"
    client = tools(project, "owner-authority")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], {
        **complete_patch(project, task_id), "goal_type": "development",
    }, "fill-authority")
    first = _ready(client, task_id, filled["revision"], "ready-authority")
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError) as failure:
        restart(
            client, task_id, first["revision"],
            request_id=f"restart-invalid-authority-{authorization}",
            authorization=authorization,
        )
    assert str(failure.value) == "Task restart authorization is required"
    assert _observable_state(client, task_id) == before


def test_stale_revision_rejects_before_changed_context_preflight(project):
    _assert_source_identity()
    task_id = "STALE-REVISION"
    client, _, changed, _ = _create_restarted(project, task_id, "Revised exact contract.")
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError) as failure:
        _ready(client, task_id, changed["revision"] - 1, "stale-revision-ready")
    assert str(failure.value) == "Newborn Task revision changed"
    assert _observable_state(client, task_id) == before


def test_changed_context_rejects_stale_live_registry(project):
    _assert_source_identity()
    task_id = "STALE-LIVE-REGISTRY"
    client, _, changed, _ = _create_restarted(project, task_id, "Revised exact contract.")
    client.runtime.requirements_commands.apply({
        "request_id": "stale-live-registry-update",
        "expected_revision": 1,
        "operations": [{"kind": "put_requirement", "requirement": {
            "id": "FIXTURE-SYSTEM", "level": "system", "status": "obsolete",
            "text": "Invalidates the new task snapshot before ready.",
        }}],
    })
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError) as failure:
        _ready(client, task_id, changed["revision"], "stale-live-ready")
    assert str(failure.value) == "Task requirements snapshot contains unresolved gaps"
    assert _observable_state(client, task_id) == before


def test_publication_failure_after_promotion_rolls_back_task_state(project, monkeypatch):
    _assert_source_identity()
    task_id = "ROLLBACK-PUBLICATION"
    client, _, changed, _ = _create_restarted(project, task_id, "Publication must be atomic with promotion.")
    gate_type = type(client.runtime.task_commands.requirements_gate)
    real_publish = gate_type.publish_created
    invoked = []

    def fail_after_publication(gate, uow, subject, context):
        real_publish(gate, uow, subject, context)
        invoked.append(subject)
        raise RuntimeError("injected publication boundary failure")

    monkeypatch.setattr(gate_type, "publish_created", fail_after_publication)
    before = _observable_state(client, task_id)
    with pytest.raises(RuntimeError, match="^injected publication boundary failure$"):
        _ready(client, task_id, changed["revision"], "ready-publication-rollback")
    assert invoked == [task_id]
    assert _observable_state(client, task_id) == before


def test_concurrent_task_edit_between_preflight_and_promotion_is_detected(project, monkeypatch):
    _assert_source_identity()
    task_id = "CONCURRENT-TASK-EDIT"
    client, _, changed, _ = _create_restarted(project, task_id, "A concurrent edit must win cleanly.")
    commands = client.runtime.task_commands
    original_prepare = commands.prepare_creation
    after_concurrent_edit = []

    def interleave_edit(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        assert edit(client, task_id, changed["revision"], {
            "goal": "Concurrent edit remains authoritative.",
        }, "concurrent-goal-edit")["revision"] == changed["revision"] + 1
        after_concurrent_edit.append(_observable_state(client, task_id))
        return prepared

    monkeypatch.setattr(commands, "prepare_creation", interleave_edit)
    with pytest.raises(PoiseError, match="^Newborn Task changed after creation preflight$"):
        _ready(client, task_id, changed["revision"], "ready-concurrent-task")
    assert len(after_concurrent_edit) == 1
    assert _observable_state(client, task_id) == after_concurrent_edit[0]


def test_registry_change_during_preflight_rejects_without_task_mutation(project, monkeypatch):
    _assert_source_identity()
    task_id = "CONCURRENT-REGISTRY"
    client, _, changed, _ = _create_restarted(project, task_id, "A concurrent registry edit invalidates the new context.")
    commands = client.runtime.task_commands
    original_prepare = commands.prepare_creation
    after_registry_change = []

    def interleave_registry_change(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        client.runtime.requirements_commands.apply({
            "request_id": "concurrent-registry-change",
            "expected_revision": 1,
            "operations": [{"kind": "put_requirement", "requirement": {
                "id": "FIXTURE-SYSTEM", "level": "system", "status": "obsolete",
                "text": "Concurrent live change invalidates the new snapshot.",
            }}],
        })
        after_registry_change.append(_observable_state(client, task_id))
        return prepared

    monkeypatch.setattr(commands, "prepare_creation", interleave_registry_change)
    with pytest.raises(PoiseError) as failure:
        _ready(client, task_id, changed["revision"], "ready-concurrent-registry")
    assert str(failure.value) == "Task requirements registry changed after creation preflight"
    assert len(after_registry_change) == 1
    assert _observable_state(client, task_id) == after_registry_change[0]


def test_planned_restart_keeps_revision_authority(project):
    _assert_source_identity()
    task_id = "PLANNED-AUTHORITY"
    client = tools(project, "owner-planned")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], full_draft(project, task_id), "fill-planned")
    first = _ready(client, task_id, filled["revision"], "first-ready-planned")
    restarted = restart(
        client, task_id, first["revision"], request_id="restart-planned",
        authorization={"role": "reviewer", "decision": "Reviewer authorized only goal correction."},
    )
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError) as failure:
        edit(client, task_id, restarted["revision"], {
            "definition_of_done": ["Unapproved acceptance revision."],
        }, "unapproved-planned-dod")
    assert str(failure.value) == "Task restart revision policy forbids changes: ['definition_of_done']"
    assert _observable_state(client, task_id) == before


def test_planned_user_authority_accepts_reagreed_context(project):
    _assert_source_identity()
    task_id = "PLANNED-REAGREED"
    client = tools(project, "owner-planned-positive")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], full_draft(project, task_id), "fill-planned-positive")
    first = _ready(client, task_id, filled["revision"], "first-ready-planned-positive")
    historical = client.runtime.task_commands.requirements_snapshot(task_id)
    restarted = restart(
        client, task_id, first["revision"], request_id="restart-planned-positive",
        authorization={"role": "user", "decision": "User authorizes the revised Requirements context."},
    )
    revised = _new_revised_context(client, project, "The planning-enabled contract is revised by its user.")
    revised["definition_of_done"] = ["The planning-enabled revision is independently checked."]
    changed = edit(client, task_id, restarted["revision"], revised, "reagree-planned-positive")
    assert _ready(client, task_id, changed["revision"], "ready-planned-positive")["status"] == "available"
    current = client.runtime.task_queries.record(task_id)
    assert current["contract"]["requirements"] == revised["requirements"]
    assert client.runtime.task_commands.requirements_snapshot(task_id) == revised["requirements_snapshot"]
    with client.runtime.store.unit_of_work() as uow:
        context = uow.tasks.restart_context(task_id)
    assert context["requirements_snapshot"] == revised["requirements_snapshot"]
    assert context["requirements_agreement"] == revised["requirements_agreement"]
    audit = context["restart_history"][-1]
    assert audit["planning_revision"]["authorization"]["role"] == "user"
    assert audit["planning_revision"]["before_contract"]["requirements_snapshot"] == historical
    assert "requirements" in audit["resolved_revision"]["changed_fields"]


def test_revised_legacy_ready_replay_conflict_and_stale_revision(project):
    _assert_source_identity()
    task_id = "REVISED-REPLAY"
    client, _, changed, revised = _create_restarted(project, task_id, "A revised ready must replay exactly.")
    request_id = "ready-revised-replay"
    unborn = _observable_state(client, task_id)
    with pytest.raises(PoiseError) as stale:
        _ready(client, task_id, changed["revision"] - 1, "stale-revised-newborn")
    assert str(stale.value) == "Newborn Task revision changed"
    assert _observable_state(client, task_id) == unborn
    first = _ready(client, task_id, changed["revision"], request_id)
    assert first["status"] == "available"
    before = _observable_state(client, task_id)
    assert _ready(client, task_id, changed["revision"], request_id) == first
    assert _observable_state(client, task_id) == before
    with pytest.raises(PoiseError) as conflict:
        _ready(client, task_id, changed["revision"] + 1, request_id)
    assert str(conflict.value) == "Request ID already used with another Task action intent"
    assert _observable_state(client, task_id) == before
    with pytest.raises(PoiseError) as unavailable:
        _ready(client, task_id, changed["revision"], "fresh-but-stale-revised-ready")
    assert str(unavailable.value) == "Task is not newborn"
    assert _observable_state(client, task_id) == before
    assert client.runtime.task_queries.record(task_id)["contract"]["requirements"] == revised["requirements"]


def test_exact_replay_and_conflicting_request_id(project):
    _assert_source_identity()
    task_id = "REPLAY-CONTEXT"
    client = tools(project, "owner-replay")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], {**complete_patch(project, task_id), "goal_type": "development"}, "fill-replay")
    first = _ready(client, task_id, filled["revision"], "ready-replay")
    before = _observable_state(client, task_id)
    replayed = _ready(client, task_id, filled["revision"], "ready-replay")
    assert replayed == first
    assert _observable_state(client, task_id) == before
    with pytest.raises(PoiseError) as conflict:
        _ready(client, task_id, filled["revision"] + 1, "ready-replay")
    assert str(conflict.value) == "Request ID already used with another Task action intent"
    assert _observable_state(client, task_id) == before


def _run_probe() -> int:
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        status = pytest.main(["-q", "-s", f"{__file__}::test_probe_all_three_incident_classes"])
    if status != 0:
        sys.stderr.write(output.getvalue() + errors.getvalue())
        return 1
    red = "LEGACY_READY_CONTEXT_REJECTED" in output.getvalue()
    green = "LEGACY_READY_CONTEXT_ACCEPTED" in output.getvalue()
    if red == green:
        sys.stderr.write("Probe outcome is not uniquely RED or GREEN.\n")
        return 1
    if red:
        sys.stdout.write("LEGACY_READY_CONTEXT_REJECTED\n")
        return 17
    sys.stdout.write("LEGACY_READY_CONTEXT_ACCEPTED\n")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--poise-red-green-probe"]:
        raise SystemExit(2)
    raise SystemExit(_run_probe())
