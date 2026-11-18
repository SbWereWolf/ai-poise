from copy import deepcopy
from pathlib import Path
import sys

import pytest

from batch.helpers import request, result
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from runner.helpers import decision, finding, inspect, resolution
from runtime_services.stage_result_handoff_helpers import configure, receiver, release


CHECK = """\
from pathlib import Path
import sys

state, counter = Path(STATE_PATH_LITERAL), Path(COUNTER_PATH_LITERAL)
mode = state.read_text()
with counter.open("a") as calls:
    calls.write(mode)
if mode == "blocked\\n":
    print("fixture: external data unavailable", file=sys.stderr)
    sys.exit(23)
assert mode == "ready\\n"
print("fixture: external data ready")
"""


def verify(tools, payload, retry=None):
    args = {"result": deepcopy(payload), "artifacts": []}
    if retry is not None:
        args["retry"] = retry
    return tools.invoke(request("verify", args))


def setup_failed_candidate(project, limit=3):
    counter = configure(project, mode="same_role")
    state = project["root"] / "external-verification-state.txt"
    state.write_text("ready\n", encoding="utf-8")
    project["cfg"]["limits"]["verify_attempts"] = limit
    write_json(project["config_path"], project["cfg"])
    method = project["task"]["methods"][0]
    script = CHECK.replace("STATE_PATH_LITERAL", repr(str(state)))
    script = script.replace("COUNTER_PATH_LITERAL", repr(str(counter)))
    method["argv"] = [sys.executable, "-B", "-c", script]
    method["stdout_contains"] = ["fixture: external data ready"]
    workflow = project["workflow"]
    initial, correction, review = workflow["stages"]
    audit = deepcopy(review)
    audit.update(id="audit", transitions={"clear": None, "changes_requested": "next_work"})
    verification = deepcopy(correction)
    verification.update(id="verification", rework_targets=["verification"])
    initial["transitions"] = {"complete": "audit"}
    correction["transitions"] = {"complete": "verification"}
    workflow["stages"] = [initial, audit, correction, verification, review]
    write_json(project["root"] / "config/workflows/development.json", workflow)
    task = project["task"]
    phases = {phase["stage"]: phase for phase in task["decomposition"]["phases"]}
    contracts = {contract["stage_id"]: contract for contract in task["stage_contracts"]}
    for name, template in [("audit", "review"), ("verification", "next_work")]:
        phases[name] = {**phases[template], "stage": name}
        contracts[name] = {**contracts[template], "stage_id": name}
        task["evidence_plan"][name] = deepcopy(task["evidence_plan"][template])
    names = [stage["id"] for stage in workflow["stages"]]
    task["decomposition"]["phases"] = [phases[name] for name in names]
    task["stage_contracts"] = [contracts[name] for name in names]
    task["checks"] = {name: ["CHECK-ONCE"] if name == "verification" else [] for name in names}
    method["verification_plan"].update(change_surface=["src/**"], green_stages=["verification"])
    author = WorkTools(WorkPoise(project["config_path"], "handoff-author"))
    context = author.invoke(request("bootstrap", {
        "task": deepcopy(project["task"]), "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert verify(author, result(context, "Initial candidate."))["status"] == "verified"
    enter_review = {"task_id": "T1", "request_id": "fixture-initial-review", "target_stage": "audit"}
    assert author.invoke(request("advance", enter_review))["status"] == "role_handoff_required"
    assert release(author, "fixture-initial-release")["status"] == "ownership_released"
    reviewer, _ = receiver(project)
    context = reviewer.invoke(request("advance", enter_review))
    review = result(context, "Independent fixture finding.")
    review["stage_work"] = inspect([finding()])
    assert verify(reviewer, review)["stage_outcome"] == "changes_requested"
    enter_author = {"task_id": "T1", "request_id": "fixture-correction", "target_stage": "next_work"}
    assert reviewer.invoke(request("advance", enter_author))["status"] == "role_handoff_required"
    assert release(reviewer, "fixture-review-release")["status"] == "ownership_released"
    author.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    context = author.invoke(request("advance", enter_author))
    correction_result = result(context, "Candidate correction awaiting independent inspection.")
    correction_result["stage_work"] = {"resolutions": [resolution()]}
    assert verify(author, correction_result)["status"] == "verified"
    context = author.invoke(request("advance", {
        "task_id": "T1", "request_id": "fixture-verify-correction", "target_stage": "verification",
    }))
    workspace = Path(context["worktree"])
    source = (git(workspace, "rev-parse", "HEAD"), git(workspace, "rev-parse", "HEAD^{tree}"))
    payload = result(context, "Verify the proposed correction before independent inspection.")
    state.write_text("blocked\n", encoding="utf-8")
    failed = verify(author, payload)
    assert failed["status"] == "checks_failed"
    [check] = failed["checks"]
    assert check["actual_exit_code"] == 23
    assert Path(check["stderr"]).read_text() == "fixture: external data unavailable\n"
    assert counter.read_text() == "blocked\n"
    assert verify(author, payload)["checks"] == failed["checks"]
    assert counter.read_text() == "blocked\n"
    retry = {
        "request_id": "fixture-recovered-verification",
        "failed_check_id": check["id"],
        "reason": "External fixture was recovered without changing the submitted source.",
    }
    return author, reviewer, payload, retry, state, counter, workspace, source, failed


def repeat(author, payload, retry):
    try:
        return verify(author, payload, retry)
    except PoiseError as error:
        pytest.fail(f"Explicit verification retry did not admit the recovered fixture: {error}", pytrace=False)


def test_recovered_external_data_is_checked_again_before_independent_handoff(project):
    author, reviewer, payload, retry, state, counter, workspace, source, failed = setup_failed_candidate(project)
    state.write_text("ready\n", encoding="utf-8")
    assert verify(author, payload)["checks"] == failed["checks"]
    assert counter.read_text() == "blocked\n"

    fresh = repeat(author, payload, retry)
    assert fresh["status"] == "verified"
    [previous] = failed["checks"]
    [current] = fresh["checks"]
    assert (current["commit"], current["tree"]) == source
    assert current["id"] != previous["id"]
    assert current["argv"] == previous["argv"]
    assert current["actual_exit_code"] == 0
    assert Path(current["stdout"]).read_text() == "fixture: external data ready\n"
    assert Path(previous["stderr"]).read_text() == "fixture: external data unavailable\n"
    assert previous["actual_exit_code"] == 23
    prior_output = author.invoke(request("show", {"queries": [{
        "id": "previous-failure", "kind": "tool_result", "receipt_id": previous["id"],
        "representation": "full", "range": None,
    }]}))
    assert "fixture: external data unavailable" in prior_output["results"][0]["value"]["text"]
    assert counter.read_text() == "blocked\nready\n"
    assert verify(author, payload, retry)["checks"] == fresh["checks"]
    assert counter.read_text() == "blocked\nready\n"
    assert (git(workspace, "rev-parse", "HEAD"), git(workspace, "rev-parse", "HEAD^{tree}")) == source
    assert [item["id"] for item in fresh["feedback"]["pending_resolutions"]] == ["R1"]
    with pytest.raises(PoiseError, match="verified|failed|провер"):
        verify(author, payload, {**retry, "request_id": "fixture-already-verified"})
    assert counter.read_text() == "blocked\nready\n"

    intent = {"task_id": "T1", "request_id": "fixture-retry-review", "target_stage": "review"}
    assert author.invoke(request("advance", intent))["status"] == "role_handoff_required"
    assert release(author, "fixture-retry-release")["status"] == "ownership_released"
    reviewer, _ = receiver(project)
    context = reviewer.invoke(request("advance", intent))
    review = result(context, "Independent disposition after fresh verification.")
    review["stage_work"] = inspect(decisions=[decision()])
    disposition = verify(reviewer, review)
    assert disposition["stage_outcome"] == "clear"
    assert disposition["feedback"]["pending_resolutions"] == []


def test_fresh_terminal_failure_has_its_own_receipt_and_replays_without_spawning(project):
    author, _, payload, retry, _, counter, _, _, failed = setup_failed_candidate(project)
    fresh = repeat(author, payload, retry)
    assert fresh["status"] == "checks_failed"
    [old] = failed["checks"]
    [new] = fresh["checks"]
    assert new["id"] != old["id"]
    assert new["actual_exit_code"] == 23
    assert Path(new["stderr"]).read_text() == "fixture: external data unavailable\n"
    assert verify(author, payload, retry)["checks"] == fresh["checks"]
    with pytest.raises(PoiseError):
        verify(author, payload, {**retry, "reason": "Changed request intent."})
    assert counter.read_text() == "blocked\nblocked\n"


@pytest.mark.parametrize("condition", ["foreign", "stale", "budget"])
def test_retry_admission_preserves_terminal_failure_on_refusal(project, condition):
    author, reviewer, payload, retry, state, counter, _, _, failed = setup_failed_candidate(
        project, limit=1 if condition == "budget" else 3,
    )
    state.write_text("ready\n", encoding="utf-8")
    if condition == "stale":
        retry["failed_check_id"] = "missing-fixture-observation"
    retry["request_id"] = f"fixture-refusal-{condition}"
    caller = reviewer if condition == "foreign" else author
    expected_reason = {
        "foreign": "own|owner|claim|влад",
        "stale": "failed|receipt|наблюд",
        "budget": "verify_attempts",
    }[condition]
    with pytest.raises(PoiseError, match=expected_reason):
        verify(caller, payload, retry)
    assert counter.read_text() == "blocked\n"
    assert verify(author, payload)["checks"] == failed["checks"]


def test_ordinary_rework_cannot_bypass_pending_independent_disposition(project):
    author, _, payload, _, _, counter, _, _, failed = setup_failed_candidate(project)
    with pytest.raises(PoiseError, match="R1"):
        author.invoke(request("bootstrap", {
            "task": None, "decision": "rework",
            "feedback": "Do not bypass pending inspection.", "rework_stage": None,
        }))
    assert verify(author, payload)["checks"] == failed["checks"]
    assert counter.read_text() == "blocked\n"
