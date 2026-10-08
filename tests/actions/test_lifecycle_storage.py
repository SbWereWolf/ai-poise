"""Independent persisted-input and conservation tests for current action scope."""
from copy import deepcopy
import json

import pytest

from conftest import WorkPoise
from poise.modules.actions.domain import PlanSpec
from poise.modules.foundation.errors import PoiseError
from poise.modules.tasks.newborn import NewbornTask
from .helpers import start, verify
from .lifecycle_helpers import (
    FIXTURES, assert_preserved, command_plan, corrupt_history, metadata,
    prepare, refusal_snapshot, release_current, restart_current, rows, runtime,
)


def saved_run():
    return json.loads((FIXTURES / "lifecycle_saved_run.json").read_text())


def seed_history(client, context):
    value = saved_run()
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with client.store.transaction() as database:
        database.execute("INSERT INTO action_runs(task_id,stage,iteration,version,data) VALUES(?,?,?,?,?)",
                         (context["task"], context["stage"], 1, 2, data))
        database.execute(
            "INSERT INTO action_events(task_id,stage,iteration,version,at,data) VALUES(?,?,?,?,?,?)",
            (context["task"], context["stage"], 1, 2, "2026-10-05T00:00:00+00:00", data),
        )
    return value


def commands(client):
    return client.plan_actions.commands


def audit_refusal(operation):
    try:
        operation()
    except PoiseError as error:
        prefix = "Invalid Task lifecycle audit: "
        if not str(error).startswith(prefix) or not str(error)[len(prefix):].strip():
            # JUnit retains the actual exception context. The literal diagnostic
            # distinguishes a stale-plan rejection from the required audit gate.
            pytest.fail("INVALID_LIFECYCLE_WRONG_REJECTION_REASON")
    else:
        pytest.fail("INVALID_LIFECYCLE_AUDIT_ACCEPTED")


@pytest.mark.parametrize("message", [
    "history service is unavailable", "restart is forbidden for this caller",
    "lifecycle operation unsupported", "Задача связана с другой сессией",
    "Invalid Task lifecycle audit", "Invalid Task lifecycle audit: ",
    "Invalid Task lifecycle audit:  ", "Invalid Task lifecycle audit service: unavailable",
    "invalid Task lifecycle audit: invalid input", "Invalid Task lifecycle audit:invalid input",
    "service says Invalid Task lifecycle audit: invalid input",
], ids=["service", "authority", "operation", "owner", "bare", "empty", "blank", "near-match",
        "case", "spacing", "embedded"])
def test_audit_refusal_oracle_rejects_unrelated_errors(message):
    def invoke():
        raise PoiseError(message)
    with pytest.raises(pytest.fail.Exception, match="^INVALID_LIFECYCLE_WRONG_REJECTION_REASON$"):
        audit_refusal(invoke)


def test_audit_refusal_oracle_accepts_only_precise_invalid_input_category():
    def invoke():
        raise PoiseError("Invalid Task lifecycle audit: from_version must be a nonnegative integer")
    audit_refusal(invoke)


STAGES = [
    "apply", "apply@restart:1", "restart:0:apply", '["apply",1]',
    "этап/λ", "two words", "[0,\"apply\"]", "scope\x00apply",
]


@pytest.mark.parametrize("stage", STAGES, ids=["ordinary", "suffix", "prefix", "json",
                                               "unicode", "spaces", "encoding", "nul"])
def test_initial_and_restarted_stage_storage_are_disjoint_without_rewriting_history(project, stage):
    client, context = runtime(project, stage=stage)
    value = seed_history(client, context)
    assert commands(client).snapshot(context["task"], stage, 1) == value
    before = rows(client, context["task"])
    client, context = restart_current(client, context)
    if commands(client).snapshot(context["task"], stage, 1) is not None:
        pytest.fail("LIFECYCLE_STORAGE_SELECTED_INITIAL_ROW")
    run = commands(client).obtain(context["task"], client.session, stage, 1,
                                  PlanSpec.parse(value["plan"], 1))
    assert run.status == "prepared"
    after = rows(client, context["task"])
    assert_preserved(before, after)
    assert len(after["runs"]) == 2
    assert len({row[:3] for row in after["runs"]}) == 2
    assert context["stage"] == stage  # Logical route names are never encoded or renamed.


@pytest.mark.parametrize("same_plan", [False, True], ids=["different", "identical"])
def test_old_equal_version_run_cannot_overwrite_current_lifecycle(project, tmp_path, same_plan):
    client, context = runtime(project)
    marker = tmp_path / "effect.txt"
    first = PlanSpec.parse(command_plan(marker, "one"), 1)
    old = commands(client).obtain(context["task"], client.session, context["stage"], 1, first)
    assert old.version == 0
    history = rows(client, context["task"])
    client, context = restart_current(client, context)
    second = first if same_plan else PlanSpec.parse(command_plan(marker, "two"), 1)
    try:
        current = commands(client).obtain(context["task"], client.session, context["stage"], 1, second)
    except PoiseError as error:
        if "immutable" in str(error):
            pytest.fail("LIFECYCLE_OBTAIN_SELECTED_HISTORICAL_PLAN")
        raise
    assert current.version == old.version == 0
    before = refusal_snapshot(client, context, marker)
    proposed = old.start(0, {"marker": 0})
    try:
        commands(client).update(context["task"], client.session, context["stage"], 1, old, proposed)
    except PoiseError:
        pass
    else:
        pytest.fail("STALE_EQUAL_VERSION_ACTION_BINDING_ACCEPTED")
    assert refusal_snapshot(client, context, marker) == before
    assert_preserved(history, rows(client, context["task"]))


@pytest.mark.parametrize("missing", [False, True], ids=["empty", "absent"])
def test_legitimate_initial_empty_or_absent_history_preserves_current_action(project, missing):
    client, context = runtime(project)
    value = seed_history(client, context)
    corrupt_history(client, context["task"], [], missing=missing)
    before = rows(client, context["task"])
    assert commands(client).snapshot(context["task"], context["stage"], 1) == value
    assert rows(client, context["task"]) == before


def test_native_zero_boundary_is_real_and_is_not_initial_absence(project):
    # Native newborn domain allows version zero. This is deliberately a model/
    # adapter input check, not a claim of public-create revision zero.
    newborn = NewbornTask.create("ZERO", None, "actor")
    changed = newborn.restart_draft("actor", "Reconsider native draft.", "User authorized restart.")
    assert changed.restart_history[-1]["from_version"] == 0
    client, context = runtime(project)
    seed_history(client, context)
    corrupt_history(client, context["task"], list(changed.restart_history))
    before = rows(client, context["task"])
    if commands(client).snapshot(context["task"], context["stage"], 1) is not None:
        pytest.fail("ZERO_RESTART_BOUNDARY_SELECTED_INITIAL_ACTION")
    assert rows(client, context["task"]) == before


INVALID_VALUES = [None, True, False, "0", "7", "bad", -1, 1.5]


@pytest.mark.parametrize("value", INVALID_VALUES,
                         ids=["null", "true", "false", "zero-string", "integer-string", "text", "negative", "fractional"])
@pytest.mark.parametrize("operation", ["snapshot", "obtain"])
def test_invalid_persisted_boundary_rejects_without_selection_or_writes(project, tmp_path, value, operation):
    client, context = runtime(project)
    historical = seed_history(client, context)
    client, context = restart_current(client, context)
    history = metadata(client, context["task"])["restart_history"]
    history[-1]["from_version"] = value
    corrupt_history(client, context["task"], history)
    marker = tmp_path / "effect.txt"
    before = refusal_snapshot(client, context, marker)
    if operation == "snapshot":
        invoke = lambda: commands(client).snapshot(context["task"], context["stage"], 1)
    else:
        invoke = lambda: commands(client).obtain(
            context["task"], client.session, context["stage"], 1,
            PlanSpec.parse(historical["plan"], 1))
    audit_refusal(invoke)
    assert refusal_snapshot(client, context, marker) == before


@pytest.mark.parametrize("history", [None, "bad", 3, True, {}, [None], ["bad"], [3], [True], [[]], [{}]],
                         ids=["null-list", "text-list", "number-list", "bool-list", "object-list",
                              "null-record", "text-record", "number-record", "bool-record", "nested-list", "missing-boundary"])
def test_malformed_persisted_history_is_rejected_before_action_read(project, tmp_path, history):
    client, context = runtime(project)
    seed_history(client, context)
    client, context = restart_current(client, context)
    corrupt_history(client, context["task"], history)
    marker = tmp_path / "effect.txt"
    before = refusal_snapshot(client, context, marker)
    audit_refusal(lambda: commands(client).snapshot(context["task"], context["stage"], 1))
    assert refusal_snapshot(client, context, marker) == before


@pytest.mark.parametrize("missing", [False, True], ids=["empty", "absent"])
def test_known_restart_cannot_silently_regain_initial_scope_when_history_is_missing(project, tmp_path, missing):
    client, context = runtime(project)
    seed_history(client, context)
    client, context = restart_current(client, context)
    # Genuine restart events remain. Only the declared test-owned field is lost.
    corrupt_history(client, context["task"], [], missing=missing)
    marker = tmp_path / "effect.txt"
    before = refusal_snapshot(client, context, marker)
    assert any(json.loads(row[2])["event"] == "restarted_newborn"
               for row in before["task_events"])
    audit_refusal(lambda: commands(client).snapshot(context["task"], context["stage"], 1))
    assert refusal_snapshot(client, context, marker) == before


@pytest.mark.parametrize("boundaries", [[0, 0], [1, 0], [10**18]],
                         ids=["duplicate", "decreasing", "future-version"])
def test_native_owner_contradictory_boundaries_are_rejected_without_mutation(project, tmp_path, boundaries):
    client, context = runtime(project)
    seed_history(client, context)
    client, context = restart_current(client, context)
    record = metadata(client, context["task"])["restart_history"][-1]
    history = [{**record, "from_version": boundary} for boundary in boundaries]
    corrupt_history(client, context["task"], history)
    marker = tmp_path / "effect.txt"
    before = refusal_snapshot(client, context, marker)
    audit_refusal(lambda: commands(client).snapshot(context["task"], context["stage"], 1))
    assert refusal_snapshot(client, context, marker) == before


def test_invalid_boundary_public_prepare_rejects_before_current_effect(project, tmp_path):
    client, context = runtime(project)
    marker = tmp_path / "effect.txt"
    verify(client, prepare(context, command_plan(marker, "prior")))
    client, context = restart_current(client, context)
    history = metadata(client, context["task"])["restart_history"]
    history[-1]["from_version"] = True
    corrupt_history(client, context["task"], history)
    before = refusal_snapshot(client, context, marker)
    audit_refusal(lambda: verify(client, prepare(context, command_plan(marker, "forbidden"))))
    assert refusal_snapshot(client, context, marker) == before
    assert marker.read_text() == "prior\n"


def test_prepared_binding_survives_release_reacquisition_and_ordinary_versions(project, tmp_path):
    client, context = runtime(project)
    marker = tmp_path / "effect.txt"
    plan = PlanSpec.parse(command_plan(marker, "unchanged"), 1)
    original = commands(client).obtain(context["task"], client.session, context["stage"], 1, plan)
    assert original.version == 0
    before = rows(client, context["task"])
    release_current(client, context, "prepared")
    receiver = WorkPoise(project["config_path"], "PREPARED-RECEIVER")
    resumed = start(receiver, {"id": context["task"]})
    current = commands(receiver).obtain(context["task"], receiver.session, context["stage"], 1, plan)
    assert current.to_dict() == original.to_dict()
    assert resumed["action"]["plan"] == plan.data
    assert rows(receiver, context["task"]) == before
    assert not marker.exists()
    changed = original.start(0, {"marker": "absent"})
    saved = commands(receiver).update(context["task"], receiver.session, context["stage"], 1,
                                      original, changed)
    assert saved.to_dict() == changed.to_dict()
    assert commands(receiver).snapshot(context["task"], context["stage"], 1) == changed.to_dict()
    assert len(rows(receiver, context["task"])["runs"]) == len(before["runs"])


def test_foreign_actor_cannot_mutate_current_binding(project, tmp_path):
    client, context = runtime(project)
    marker = tmp_path / "effect.txt"
    plan = PlanSpec.parse(command_plan(marker, "forbidden"), 1)
    before = refusal_snapshot(client, context, marker)
    with pytest.raises(PoiseError, match="Задача связана с другой сессией"):
        commands(client).obtain(context["task"], "FOREIGN", context["stage"], 1, plan)
    assert refusal_snapshot(client, context, marker) == before


@pytest.mark.parametrize("denial", ["foreign", "stale", "unauthorized"])
def test_restart_and_action_denial_gates_remain_effective_after_restart(project, tmp_path, denial):
    from .helpers import call
    client, context = runtime(project)
    seed_history(client, context)
    client, context = restart_current(client, context)
    marker = tmp_path / "effect.txt"
    before = refusal_snapshot(client, context, marker)
    version = client.task_queries.record(context["task"])["version"]
    if denial == "foreign":
        with pytest.raises(PoiseError, match="^Задача связана с другой сессией$"):
            commands(client).obtain(context["task"], "FOREIGN", context["stage"], 1,
                                    PlanSpec.parse(command_plan(marker, "forbidden"), 1))
        foreign = WorkPoise(project["config_path"], "FOREIGN")
        with pytest.raises(PoiseError, match="owned.*another|handoff"):
            call(foreign, "task", {
                "action": "restart", "request_id": "denied-current-foreign",
                "task_id": context["task"], "expected_version": version,
                "reason": "Foreign actor cannot restart current work.",
                "authorization": "Independent restart grant.",
            })
    else:
        with pytest.raises(PoiseError, match="version" if denial == "stale" else
                           "^Task restart authorization is required$"):
            call(client, "task", {
                "action": "restart", "request_id": "denied-current-" + denial,
                "task_id": context["task"],
                "expected_version": version + 1 if denial == "stale" else version,
                "reason": "Test the current lifecycle denial gate.",
                "authorization": "Independent restart grant." if denial == "stale" else "",
            })
    assert refusal_snapshot(client, context, marker) == before


@pytest.mark.parametrize("restarted", [True, False], ids=["restarted", "initial"])
def test_retained_restarted_run_saves_after_ordinary_version_and_actor_changes(project, tmp_path, restarted):
    client, context = runtime(project)
    if restarted:
        seed_history(client, context)
    historical = rows(client, context["task"])
    if restarted:
        client, context = restart_current(client, context)
    marker = tmp_path / "effect.txt"
    plan = PlanSpec.parse(command_plan(marker, "current"), 1)
    try:
        retained = commands(client).obtain(context["task"], client.session, context["stage"], 1, plan)
    except PoiseError as error:
        if "immutable" in str(error):
            pytest.fail("LIFECYCLE_POSITIVE_SAVE_SELECTED_HISTORY")
        raise
    version = client.task_queries.record(context["task"])["version"]
    release_current(client, context, "retained-restarted")
    receiver = WorkPoise(project["config_path"], "RETAINED-CURRENT-RECEIVER")
    resumed = start(receiver, {"id": context["task"]})
    assert receiver.task_queries.record(context["task"])["version"] > version
    before = rows(receiver, context["task"])
    running = retained.start(0, {"marker": "absent"})
    commands(receiver).update(context["task"], receiver.session, context["stage"], 1, retained, running)
    assert commands(receiver).snapshot(context["task"], context["stage"], 1) == running.to_dict()
    count = 2 if restarted else 1
    assert len(rows(receiver, context["task"])["runs"]) == len(before["runs"]) == count
    # Execute the real declared effect through its runtime owner, then let the
    # normal public verify recover the retained running action by its probe.
    applied = receiver.plan_actions._method(receiver.current_task(), plan.steps[0]["apply"])
    assert applied["passed"] and marker.read_text() == "current\n"
    out = verify(receiver, prepare(resumed, plan.data))
    assert out["status"] == "verified" and out["action"]["status"] == "complete"
    assert out["action"]["steps"][0]["result"]["effect"] == "recovered_by_probe"
    assert marker.read_text() == "current\n"
    assert_preserved(historical, rows(receiver, context["task"]))
    assert len(rows(receiver, context["task"])["runs"]) == count


@pytest.mark.parametrize("contradiction", ["equal-current-version", "retained-earlier-prefix"])
def test_exact_version_and_nonempty_owner_event_contradictions_reject(project, tmp_path, contradiction):
    client, context = runtime(project)
    seed_history(client, context)
    client, context = restart_current(client, context, suffix="first-audit")
    if contradiction == "retained-earlier-prefix":
        client, context = restart_current(client, context, suffix="second-audit", edit=True)
    history = metadata(client, context["task"])["restart_history"]
    if contradiction == "equal-current-version":
        history[-1]["from_version"] = client.task_queries.record(context["task"])["version"]
    else:
        assert len(history) == 2 and history[0]["from_version"] < history[1]["from_version"]
        history = history[:1]
    corrupt_history(client, context["task"], history)
    marker = tmp_path / "effect.txt"
    before = refusal_snapshot(client, context, marker)
    if contradiction == "retained-earlier-prefix":
        assert sum(json.loads(row[2])["event"] == "restarted_newborn"
                   for row in before["task_events"]) == 2
    audit_refusal(lambda: commands(client).snapshot(context["task"], context["stage"], 1))
    assert refusal_snapshot(client, context, marker) == before
