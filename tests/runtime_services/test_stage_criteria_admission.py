"""Public reads/resumption preserve an actually admitted execution revision."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier, Event, Thread, current_thread

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from tests.runtime_services.stage_criteria_helpers import (
    assert_unstarted, boot, criteria, fail_commit, handoff, next_stage, preserved_proof, proposal, rows, show,
    verify_packet, verify_with_proof, version, work,
)


def test_repeated_bootstrap_and_fresh_connection_preserve_bound_revision(project):
    tools, context = boot(project, initial_execution=True)
    admitted = show(tools)
    before = rows(tools)
    refreshed = next_stage(tools)
    assert refreshed["task"] == context["task"]
    assert refreshed["stage"] == "build"
    assert show(tools) == admitted
    assert rows(tools) == before
    reopened = WorkTools(WorkPoise(project["config_path"], "artisan-session"))
    assert show(reopened) == admitted
    assert rows(reopened) == before


def test_design_and_observation_visits_do_not_start_future_execution(project):
    tools, context = boot(project)
    initial = show(tools)
    assert_unstarted(initial["execution"])
    tools.invoke(verify_packet(context))
    assert next_stage(tools)["stage"] == "assess"
    assert show(tools)["execution"] == initial["execution"]


def test_entry_gate_refusal_does_not_bind_or_open_execution(project):
    tools, context = boot(project, input_gate=True)
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    initial = show(tools)
    before = rows(tools)
    blocked = tools.invoke(request("advance", {
        "request_id": "missing-build-input", "task_id": "T1", "target_stage": "build",
    }))
    assert blocked["status"] == "broken"
    assert blocked["failure"]["kind"] == "content_requirements_failed"
    assert blocked["failure"]["phase"] == "pre"
    assert blocked["blocked_transition"] == {"from_stage": "assess", "to_stage": "build"}
    assert show(tools) == initial
    assert_unstarted(show(tools)["execution"])
    assert rows(tools) == before


def test_environment_failure_after_admission_retains_binding_before_any_work_effect(project, monkeypatch):
    tools, context = boot(project)
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    observed = []
    original = tools.runtime._roots

    def failed_environment(data):
        if tools.runtime._stage(data)["id"] == "build":
            observer = WorkTools(WorkPoise(project["config_path"], "artisan-session"))
            observed.append(show(observer))
            raise PoiseError("Fixture execution environment cannot create its artifact root.")
        return original(data)

    monkeypatch.setattr(tools.runtime, "_roots", failed_environment)
    try:
        response = tools.invoke(request("advance", {
            "request_id": "fail-admitted-environment", "task_id": "T1", "target_stage": "build",
        }))
    except PoiseError as error:
        assert "Fixture execution environment" in str(error)
    else:
        assert response["status"] == "broken"
        assert "Fixture execution environment" in json.dumps(response)
    assert len(observed) >= 1
    bound = observed[0]
    assert bound["execution"]["state"] == "started"
    assert bound["execution"]["bound_revision"] == bound["revision"]
    assert show(tools)["execution"] == bound["execution"]
    monkeypatch.setattr(tools.runtime, "_roots", original)
    resumed = tools.invoke(request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert resumed["stage"] == "build"
    assert show(tools)["execution"] == bound["execution"]


def test_other_explicit_grant_does_not_unfreeze_started_execution(project):
    tools, context = boot(project)
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    execution = next_stage(tools)
    assert execution["stage"] == "build"
    bound = show(tools)
    tools.invoke(verify_packet(execution))
    custom = next_stage(tools)
    assert custom["stage"] == "custom"
    assert custom["purpose"] == "other"
    candidate = proposal(tools)
    before = rows(tools)
    with pytest.raises(PoiseError, match="target_started_requires_restart"):
        tools.invoke(verify_packet(custom, work(tools, "change-started", proposals=[candidate])))
    assert rows(tools) == before
    assert show(tools)["criteria"] == bound["criteria"]
    assert show(tools)["execution"] == bound["execution"]


def test_unrelated_future_stage_has_no_start_record_when_first_execution_is_admitted(project):
    tools, context = boot(project, second_execution=True)
    second = show(tools, "build_next")
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    assert next_stage(tools)["stage"] == "build"
    assert show(tools)["execution"]["state"] == "started"
    assert show(tools, "build_next") == second


def test_design_can_refine_future_target_without_rewriting_shared_bound_criteria_or_proof(project):
    tools, context = boot(project, second_execution=True, with_proof=True)
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    execution = next_stage(tools)
    assert execution["stage"] == "build"
    verify_with_proof(tools, execution)
    original_proof = preserved_proof(tools)
    bound = show(tools, "build")
    future = show(tools, "build_next")
    assert bound["criteria"] == future["criteria"]  # The initial text is shared.
    before = rows(tools)
    design = tools.invoke(request("bootstrap", {
        "task": None, "decision": "rework", "rework_stage": "sketch",
        "feedback": "Refine only the not-yet-started second execution target.",
    }))
    assert design["stage"] == "sketch"
    candidate = proposal(tools, "build_next", "Return twice the input, including zero.")
    tools.invoke(verify_packet(design, work(tools, "refine-future-after-build", proposals=[candidate])))
    offered = show(tools, "build_next")["proposals"][0]
    tools.invoke(verify_packet(next_stage(tools), work(tools, "accept-future-after-build", decisions=[{
        "proposal_id": offered["proposal_id"], "proposal_digest": offered["proposal_digest"],
        "decision": "accepted", "reason": "Clarify the future target without changing completed work.",
    }])))
    revised = show(tools, "build_next")
    assert revised["criteria"] == candidate["criteria"]
    assert revised["revision"] != future["revision"]
    assert_unstarted(revised["execution"])
    assert show(tools, "build") == bound
    assert preserved_proof(tools) == original_proof
    after = rows(tools)
    assert after["submissions"][:len(before["submissions"])] == before["submissions"]
    assert after["events"][:len(before["events"])] == before["events"]


def test_malformed_read_is_nonmutating_and_does_not_open_execution(project):
    tools, _ = boot(project)
    before = rows(tools)
    current = show(tools)
    query = {"id": "criteria", "kind": "stage_criteria", "stage_id": "build"}
    malformed = deepcopy(query)
    malformed["start"] = True
    with pytest.raises(PoiseError):
        tools.invoke(request("show", {"queries": [malformed]}))
    assert rows(tools) == before
    assert show(tools) == current


def test_authorized_restart_opens_new_epoch_without_losing_task_wip_or_prior_binding(project):
    tools, context = boot(project, initial_execution=True)
    old = show(tools)
    directory = Path(context["worktree"])
    source = directory / "src/double.py"
    source.write_text("def double(value):\n    return value * 3\n", encoding="utf-8")
    head = git(directory, "rev-parse", "HEAD")
    diff = git(directory, "diff", "--", "src/double.py")
    history = rows(tools)
    newborn = tools.invoke(request("task", {
        "action": "restart", "task_id": "T1", "expected_version": version(tools),
        "request_id": "restart-bound-build",
        "reason": "The accepted criterion needs a reviewed correction.",
        "authorization": "The task owner authorizes revising the bound criterion.",
    }))
    assert newborn["status"] == "newborn"
    assert newborn["task"] == "T1"
    contracts = deepcopy(newborn["draft"]["stage_contracts"])
    revised = json.loads((Path(__file__).resolve().parents[2] /
                          "fixtures/stage-criteria/revised-criteria.json").read_text())["restart"]
    contracts[0]["criteria"] = deepcopy(revised)
    edited = tools.invoke(request("task", {
        "action": "edit", "task_id": "T1", "expected_revision": newborn["revision"],
        "request_id": "revise-restarted-build",
        "patch": {"stage_contracts": contracts}, "remove": [],
    }))
    ready = tools.invoke(request("task", {
        "action": "ready", "task_id": "T1", "expected_revision": edited["revision"],
        "request_id": "ready-restarted-build",
    }))
    assert ready["status"] == "available"
    resumed = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert resumed["worktree"] == str(directory)
    current = show(tools)
    assert current["criteria"] == revised
    assert current["execution"]["state"] == "started"
    assert current["execution"]["epoch"] == old["execution"]["epoch"] + 1
    assert current["execution"]["bound_revision"] != old["execution"]["bound_revision"]
    assert current["history"][:len(old["history"])] == old["history"]
    after = rows(tools)
    assert after["events"][:len(history["events"])] == history["events"]
    assert after["submissions"] == history["submissions"]
    assert source.read_text(encoding="utf-8") == "def double(value):\n    return value * 3\n"
    assert git(directory, "rev-parse", "HEAD") == head
    assert git(directory, "diff", "--", "src/double.py") == diff


def test_stale_restart_cannot_unbind_execution(project):
    tools, _ = boot(project, initial_execution=True)
    old = show(tools)
    before = rows(tools)
    with pytest.raises(PoiseError):
        tools.invoke(request("task", {
            "action": "restart", "task_id": "T1", "expected_version": version(tools) + 1,
            "request_id": "stale-restart-bound-build", "reason": "Reject a stale restart.",
            "authorization": "The task owner authorizes a version-checked restart only.",
        }))
    assert show(tools) == old
    assert rows(tools) == before


def test_restart_revising_one_bound_target_preserves_other_target_binding_and_proof(project):
    tools, context = boot(project, second_execution=True, with_proof=True)
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    first = next_stage(tools)
    assert first["stage"] == "build"
    verify_with_proof(tools, first)
    second = next_stage(tools)
    assert second["stage"] == "build_next"
    verify_with_proof(tools, second)
    original_proof = preserved_proof(tools)
    unaffected, affected = show(tools, "build"), show(tools, "build_next")
    before = rows(tools)
    newborn = tools.invoke(request("task", {
        "action": "restart", "task_id": "T1", "expected_version": version(tools),
        "request_id": "restart-one-of-two-bound-targets",
        "reason": "Correct only the build_next criterion while preserving build's accepted result.",
        "authorization": "The task owner authorizes revising only the build_next criteria.",
    }))
    contracts = deepcopy(newborn["draft"]["stage_contracts"])
    target = next(item for item in contracts if item["stage_id"] == "build_next")
    target["criteria"] = criteria("Return twice the input, including zero.")
    changed = tools.invoke(request("task", {
        "action": "edit", "task_id": "T1", "expected_revision": newborn["revision"],
        "request_id": "edit-only-reopened-target", "patch": {"stage_contracts": contracts}, "remove": [],
    }))
    ready = tools.invoke(request("task", {
        "action": "ready", "task_id": "T1", "expected_revision": changed["revision"],
        "request_id": "ready-only-reopened-target",
    }))
    assert ready["status"] == "available"
    tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert show(tools, "build") == unaffected
    assert preserved_proof(tools) == original_proof
    reopened = show(tools, "build_next")
    assert reopened["criteria"] == target["criteria"]
    assert_unstarted(reopened["execution"])
    assert reopened["execution"]["epoch"] == affected["execution"]["epoch"] + 1
    assert reopened["history"][:len(affected["history"])] == affected["history"]
    after = rows(tools)
    assert after["submissions"][:len(before["submissions"])] == before["submissions"]
    assert after["events"][:len(before["events"])] == before["events"]


def test_public_handoff_and_new_actor_resume_keep_exact_bound_criteria(project):
    tools, context = boot(project, initial_execution=True)
    bound = show(tools)
    released = handoff(tools, "transfer-bound-execution")
    assert released["task"] == "T1"
    successor = WorkTools(WorkPoise(project["config_path"], "successor-artisan"))
    resumed = successor.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert resumed["worktree"] == context["worktree"]
    current = show(successor)
    for field in ("criteria", "revision", "source", "execution"):
        assert current[field] == bound[field]
    assert current["history"][:len(bound["history"])] == bound["history"]


def test_declared_revisit_preserves_binding_instead_of_accepting_a_new_revision(project):
    tools, context = boot(project)
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    execution = next_stage(tools)
    bound = show(tools)
    tools.invoke(verify_packet(execution))
    tools.invoke(verify_packet(next_stage(tools)))
    revisited = tools.invoke(request("bootstrap", {
        "task": None, "decision": "rework",
        "feedback": "Revisit execution under its already bound criteria.", "rework_stage": "build",
    }))
    assert revisited["stage"] == "build"
    current = show(tools)
    assert current["criteria"] == bound["criteria"]
    assert current["revision"] == bound["revision"]
    assert current["execution"] == bound["execution"]


@pytest.mark.parametrize("first", ["decision", "admission"])
def test_two_real_connections_order_decision_and_admission_without_binding_stale_criteria(project, first):
    tools, context = boot(project)
    candidate = proposal(tools)
    tools.invoke(verify_packet(context, work(tools, "offer-racing-criterion", proposals=[candidate])))
    proposed = show(tools)["proposals"][0]
    observation = next_stage(tools)
    initial = show(tools)
    decision_packet = verify_packet(observation, work(tools, "racing-decision", decisions=[{
        "proposal_id": proposed["proposal_id"], "proposal_digest": proposed["proposal_digest"],
        "decision": "accepted", "reason": "Accept the exact revision before actual execution.",
    }]))
    advance_packet = request("advance", {
        "request_id": "racing-admission", "task_id": "T1", "target_stage": "build",
    })
    barrier = Barrier(2)
    first_done = Event()

    def invoke(name, packet):
        # Distinct real runtimes/connections; no mocked SQLite, sleeps or lifecycle edits.
        client = WorkTools(WorkPoise(project["config_path"], "artisan-session"))
        barrier.wait(timeout=15)
        if name != first:
            assert first_done.wait(timeout=15), "The first fixture operation did not finish."
        try:
            return client.invoke(packet)
        finally:
            if name == first:
                first_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        decision = executor.submit(invoke, "decision", decision_packet)
        admission = executor.submit(invoke, "admission", advance_packet)
        decision_result = decision.result(timeout=30)
        admission_result = admission.result(timeout=30)
    assert decision_result["status"] == "verified"
    if first == "admission":
        assert admission_result["status"] == "progression_work_required"
        # This is the still-active same request, not a blind retry of an unknown outcome.
        assert tools.invoke(advance_packet)["status"] == "progression_target_reached"
    else:
        assert admission_result["status"] == "progression_target_reached"
    bound = show(tools)
    assert bound["criteria"] == candidate["criteria"]
    assert bound["revision"] != initial["revision"]
    assert bound["execution"]["state"] == "started"
    assert bound["execution"]["bound_revision"] == bound["revision"]
    assert bound["source"]["proposal_id"] == proposed["proposal_id"]
    assert bound["source"]["proposal_digest"] == proposed["proposal_digest"]


def test_actual_admission_wins_before_competing_future_criteria_edit(project):
    tools, context = boot(project)
    initial = show(tools)
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    admitted = Event()
    observed = {}
    packet = request("advance", {"request_id": "actual-admission-wins", "task_id": "T1", "target_stage": "build"})

    def admit():
        client = WorkTools(WorkPoise(project["config_path"], "artisan-session"))
        response = client.invoke(packet)
        assert response["status"] == "progression_target_reached"
        observed["bound"] = show(client)
        assert observed["bound"]["execution"]["state"] == "started"
        observed["rows"] = rows(client)
        assert client.invoke(packet)["status"] == "progression_target_reached"
        assert rows(client) == observed["rows"]
        admitted.set()

    def losing_edit():
        assert admitted.wait(timeout=15)
        client = WorkTools(WorkPoise(project["config_path"], "artisan-session"))
        # A genuine configured design stage has edit authority, but that authority
        # does not erase a completed admission on another real connection.
        design = client.invoke(request("bootstrap", {
            "task": None, "decision": "rework", "rework_stage": "sketch",
            "feedback": "Attempt to refine criteria after a competing connection admitted execution.",
        }))
        assert design["stage"] == "sketch"
        before = rows(client)
        with pytest.raises(PoiseError, match="target_started_requires_restart"):
            client.invoke(verify_packet(design, work(client, "losing-edit-after-start", proposals=[proposal(client)])))
        assert rows(client) == before
        current = show(client)
        assert {key: value for key, value in current.items() if key != "history"} == {
            key: value for key, value in observed["bound"].items() if key != "history"
        }
        assert current["history"][:len(observed["bound"]["history"])] == observed["bound"]["history"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        admission = executor.submit(admit)
        edit = executor.submit(losing_edit)
        admission.result(timeout=30)
        edit.result(timeout=30)
    assert observed["bound"]["criteria"] == initial["criteria"]
    assert observed["bound"]["revision"] == initial["revision"]
    assert observed["bound"]["execution"]["bound_revision"] == initial["revision"]


def test_admission_transaction_commit_failure_preserves_unstarted_criteria(project, monkeypatch):
    tools, context = boot(project)
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    before, current = rows(tools), show(tools)
    original_version = version(tools)
    tentative = []

    def has_tentative_admission(database):
        row = database.execute("SELECT stage_index,version FROM tasks WHERE id=?", ("T1",)).fetchone()
        if row is not None and row[0] == 2 and row[1] > original_version:
            tentative.append(tuple(row))
            return True
        return False

    observed = fail_commit(monkeypatch, has_tentative_admission)
    effects = []
    original = tools.runtime._roots

    def roots(data):
        if tools.runtime._stage(data)["id"] == "build":
            effects.append("build_environment")
        return original(data)

    monkeypatch.setattr(tools.runtime, "_roots", roots)
    try:
        response = tools.invoke(request("advance", {
            "request_id": "admission-commit-failure", "task_id": "T1", "target_stage": "build",
        }))
    except PoiseError as error:
        assert "Fixture COMMIT failure" in str(error)
    else:
        assert response["status"] == "broken"
        assert "Fixture COMMIT failure" in json.dumps(response)
    assert tentative and tentative[0][0] == 2
    assert observed == ["tentative_owner_writes"]
    assert effects == []
    assert rows(tools) == before
    observer = WorkTools(WorkPoise(project["config_path"], "artisan-session"))
    assert show(observer) == current
    assert_unstarted(show(observer)["execution"])


def test_admission_contending_with_uncommitted_decision_binds_only_committed_agreement(project, monkeypatch):
    from poise.infrastructure import locking
    from poise.infrastructure.sqlite.uow import SqliteUnitOfWork

    tools, context = boot(project)
    candidate = proposal(tools)
    tools.invoke(verify_packet(context, work(tools, "offer-concurrent", proposals=[candidate])))
    proposed = show(tools)["proposals"][0]
    observation = next_stage(tools)
    initial = show(tools)
    clients = {name: WorkTools(WorkPoise(project["config_path"], "artisan-session"))
               for name in ("criteria-decision", "criteria-admission")}
    assert clients["criteria-decision"].runtime.store.database is not clients["criteria-admission"].runtime.store.database
    lock_path = tools.runtime.store.database.lock
    pending, contended, committed = Event(), Event(), Event()
    observed, responses, errors = [], {}, {}
    original_exit, original_flock = SqliteUnitOfWork.__exit__, locking.fcntl.flock

    def commit_boundary(unit, exc_type, exc, traceback):
        if current_thread().name == "criteria-decision" and exc_type is None and not pending.is_set():
            row = unit.tasks.db.execute(
                "SELECT stage FROM submissions WHERE task_id=? ORDER BY seq DESC LIMIT 1", ("T1",),
            ).fetchone()
            if row is not None and row[0] == "assess":
                pending.set()
                if not contended.wait(timeout=15):
                    failure = AssertionError("Admission did not contend on the real Task lock.")
                    errors["boundary"] = failure
                    return original_exit(unit, type(failure), failure, None)
                observed.append("decision_submission_uncommitted")
                result = original_exit(unit, exc_type, exc, traceback)
                committed.set()
                return result
        return original_exit(unit, exc_type, exc, traceback)

    def kernel_lock(handle, operation):
        try:
            return original_flock(handle, operation)
        except BlockingIOError:
            if current_thread().name == "criteria-admission" and Path(handle.name) == lock_path and pending.is_set() and not committed.is_set():
                observed.append("admission_blocked_by_kernel")
                contended.set()
            raise

    monkeypatch.setattr(SqliteUnitOfWork, "__exit__", commit_boundary)
    monkeypatch.setattr(locking.fcntl, "flock", kernel_lock)
    packets = {
        "criteria-decision": verify_packet(observation, work(tools, "concurrent-decision", decisions=[{
            "proposal_id": proposed["proposal_id"], "proposal_digest": proposed["proposal_digest"],
            "decision": "accepted", "reason": "Admit only the exact committed agreement.",
        }])),
        "criteria-admission": request("advance", {
            "request_id": "concurrent-admission", "task_id": "T1", "target_stage": "build",
        }),
    }

    def run(name):
        try:
            if name == "criteria-admission":
                assert pending.wait(timeout=15)
            responses[name] = clients[name].invoke(packets[name])
        except BaseException as error:
            errors[name] = error
        finally:
            if name == "criteria-decision":
                pending.set()
            else:
                contended.set()

    threads = [Thread(target=run, args=(name,), name=name) for name in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert errors == {}
    assert observed.count("admission_blocked_by_kernel") >= 1
    assert observed.count("decision_submission_uncommitted") == 1
    assert committed.is_set()
    assert responses["criteria-decision"]["status"] == "verified"
    assert responses["criteria-admission"]["status"] == "progression_target_reached"
    bound = show(tools)
    assert bound["criteria"] == candidate["criteria"]
    assert bound["revision"] != initial["revision"]
    assert bound["execution"]["bound_revision"] == bound["revision"]
    assert bound["source"]["proposal_id"] == proposed["proposal_id"]
