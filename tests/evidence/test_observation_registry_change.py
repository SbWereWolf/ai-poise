from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from poise.common import PoiseError, digest
from tests.evidence.test_paths import arg, input_result, setup


def _replacement(project, marker="y", stages=("measure",)):
    method = deepcopy(project["task"]["methods"][0])
    counter = project["root"] / "calls.txt"
    code = (
        "from pathlib import Path;"
        f"p=Path({str(counter)!r});"
        f"p.write_text(p.read_text()+{marker!r} if p.exists() else {marker!r});"
        "print('observed=3')"
    )
    method["argv"] = [sys.executable, "-B", "-c", code]
    return {"method": method, "stages": list(stages)}


def _change(project, *, request_id="refresh-M", revision=0, operation=None,
            executable_obligations=()):
    selected = operation or {
        "kind": "replace",
        "method_id": "M",
        "registration": _replacement(project),
    }
    return {
        "request_id": request_id,
        "expected_revision": revision,
        "operations": [selected],
        "executable_obligations": list(executable_obligations),
    }


def _payload(context, change, report="Refresh the stale observation contract."):
    input_result(context)
    result = context["result_template"]
    result["sections"]["report"] = report
    result["method_additions"] = change
    return result


def _registry(runtime):
    return runtime.task_queries.verification_registry("T1")


def _state(runtime):
    return {
        "task": runtime.task_queries.record("T1"),
        "registry": _registry(runtime),
        "history": runtime.task_queries.history("T1"),
    }


def test_observe_stage_can_replace_its_current_method(project):
    runtime, _ = setup(project, logical=False)
    context = runtime.bootstrap(project["task_path"])

    receipt = runtime.task_commands.submit(
        "T1", "S1", _payload(context, _change(project))
    )
    registry = _registry(runtime)

    assert receipt.created is True
    assert registry["revision"] == 1
    assert registry["current"][0] == _replacement(project)


def test_stale_revision_and_request_payload_conflict_leave_task_unchanged(project):
    runtime, _ = setup(project, logical=False)
    context = runtime.bootstrap(project["task_path"])
    before = runtime.task_queries.record("T1")

    with pytest.raises(PoiseError, match="revision conflict"):
        runtime.task_commands.submit(
            "T1", "S1", _payload(context, _change(project, revision=1))
        )
    assert runtime.task_queries.record("T1") == before

    runtime.task_commands.submit("T1", "S1", _payload(context, _change(project)))
    after = runtime.task_queries.record("T1")
    conflicting = _change(project)
    conflicting["operations"][0]["registration"] = _replacement(project, "z")
    with pytest.raises(PoiseError, match="request_id"):
        runtime.task_commands.submit(
            "T1", "S1", _payload(context, conflicting, "Conflicting retry.")
        )
    assert runtime.task_queries.record("T1") == after


def test_observe_scope_rejects_structural_and_cross_stage_changes(project):
    runtime, _ = setup(project, logical=False)
    context = runtime.bootstrap(project["task_path"])
    invalid = (
        {"kind": "add", "method_id": "NEW", "registration": _replacement(project)},
        {"kind": "remove", "method_id": "M"},
        {"kind": "reschedule", "method_id": "M", "stages": ["audit"]},
        {
            "kind": "replace",
            "method_id": "M",
            "registration": _replacement(project, stages=("audit",)),
        },
    )

    for index, operation in enumerate(invalid):
        before = _state(runtime)
        with pytest.raises(PoiseError, match="observe"):
            runtime.task_commands.submit(
                "T1",
                "S1",
                _payload(
                    context,
                    _change(project, request_id=f"invalid-{index}", operation=operation),
                ),
            )
        assert _state(runtime) == before
    before = _state(runtime)
    with pytest.raises(PoiseError, match="executable_obligations"):
        runtime.task_commands.submit(
            "T1",
            "S1",
            _payload(
                context,
                _change(project, request_id="invalid-obligations",
                executable_obligations=("requirements[0]",)),
            ),
        )
    assert _state(runtime) == before


def test_non_observe_stage_still_requires_explicit_test_registry_ownership(project):
    from conftest import Poise

    runtime = Poise(project["config_path"], "S1")
    context = runtime.bootstrap(project["task_path"])
    before = _state(runtime)
    with pytest.raises(PoiseError, match="test_registry"):
        runtime.task_commands.submit(
            "T1", "S1", _payload(context, _change(project))
        )
    assert _state(runtime) == before


def test_replacement_preserves_the_previous_registry_snapshot(project):
    runtime, _ = setup(project, logical=False)
    context = runtime.bootstrap(project["task_path"])
    original = deepcopy(_registry(runtime)["current"])

    runtime.task_commands.submit("T1", "S1", _payload(context, _change(project)))
    registry = _registry(runtime)

    assert registry["history"] == [{"revision": 0, "entries": original}]
    assert registry["current"] != original


def test_first_change_records_bound_audit_and_replay_does_not_duplicate_it(project):
    runtime, _ = setup(project, logical=False)
    context = runtime.bootstrap(project["task_path"])
    payload = _payload(context, _change(project))

    first = runtime.task_commands.submit("T1", "S1", payload)
    replay = runtime.task_commands.submit("T1", "S1", payload)
    events = [
        item for item in runtime.task_queries.history("T1")
        if item["event"] == "verification_registry_changed"
    ]

    expected = {
        "actor": "S1",
        "methods": ["M"],
        "new_revision": 1,
        "previous_revision": 0,
        "request_id": "refresh-M",
        "stage": "measure",
        "iteration": 1,
        "task": "T1",
    }
    receipt_id = digest(expected)
    assert first.created is True
    assert first.registry_change == {
        **expected, "receipt_id": receipt_id, "replayed": False,
    }
    assert replay.created is False
    assert replay.registry_change == {
        **expected, "receipt_id": receipt_id, "replayed": True,
    }
    assert len(events) == 1
    assert json.loads(events[0]["reason"]) == {
        **expected, "receipt_id": receipt_id,
    }
    assert events[0]["stage"] == "measure"
    assert events[0]["iteration"] == 1


def test_next_verification_executes_only_the_replacement_definition(project):
    runtime, counter = setup(project, logical=False)
    context = runtime.bootstrap(project["task_path"])
    _payload(context, _change(project))

    report = runtime.verify()

    assert report["status"] == "verified"
    assert counter.read_text(encoding="utf-8") == "y"
    assert report["checks"][0]["argv"] == _replacement(project)["method"]["argv"]


def test_rework_keeps_old_evidence_receipt_and_records_new_execution(project):
    runtime, counter = setup(project, logical=True)
    context = runtime.bootstrap(project["task_path"])
    input_result(context)
    pending = runtime.verify()
    input_result(
        pending["context"],
        [arg([pending["checks"][0]["id"]])],
        phase="continue",
    )
    first = runtime.verify()
    old = deepcopy(first["checks"][0])
    old_stdout = Path(old["stdout"]).read_bytes()
    old_registry = deepcopy(_registry(runtime)["current"][0])
    with runtime.store.unit_of_work() as unit:
        old_proof = unit.tasks.load("T1").evidence_snapshot()
    old_batch = deepcopy(old_proof["book"]["batches"][0])
    old_receipt = old_batch["receipts"][0]

    assert old_receipt["argv"] == old_registry["method"]["argv"]
    assert old_receipt["source_provenance"]["kind"] == "external"
    assert old_receipt["provenance_digest"] == digest(
        old_receipt["source_provenance"]
    )

    context = runtime.bootstrap(
        decision="rework",
        feedback="The observation command is stale.",
        rework_stage="measure",
    )
    runtime.task_commands.submit(
        "T1", "S1", _payload(context, _change(project))
    )
    with runtime.store.unit_of_work() as unit:
        after_replacement = unit.tasks.load("T1").evidence_snapshot()
    with runtime.store.transaction() as database:
        proof_layers = [
            json.loads(row[0])
            for row in database.execute(
                "SELECT data FROM task_proof_layers WHERE task_id='T1' ORDER BY version"
            )
        ]

    assert after_replacement["book"] == old_proof["book"]
    assert old_proof in proof_layers
    assert after_replacement in proof_layers
    assert _registry(runtime)["history"][0]["entries"][0] == old_registry

    input_result(context)
    context["result_template"]["method_additions"] = _change(project)
    pending = runtime.verify()
    input_result(
        pending["context"],
        [arg([pending["checks"][0]["id"]])],
        phase="continue",
    )
    second = runtime.verify()

    assert second["status"] == "verified"
    assert counter.read_text(encoding="utf-8") == "xy"
    assert second["checks"][0]["id"] != old["id"]
    assert Path(old["stdout"]).read_bytes() == old_stdout
    assert second["checks"][0]["stdout_digest"] == old["stdout_digest"]
    with runtime.store.unit_of_work() as unit:
        batches = unit.tasks.load("T1").evidence_snapshot()["book"]["batches"]
    assert batches[0] == old_batch
    assert batches[1]["receipts"][0]["argv"] == _replacement(project)["method"]["argv"]
