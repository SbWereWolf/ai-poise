from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import threading

import pytest

from batch.helpers import request
from conftest import write_json
from harness.application.work import WorkTools
from harness.common import HarnessError
from harness.infrastructure.sqlite.database import SCHEMA_VERSION
from harness.runtime import Harness


def policy(*, minimum=1, maximum=9999, width=4, first=1, step=1):
    return {
        "namespace": {"minimum": minimum, "maximum": maximum},
        "width": width,
        "progression": {"first": first, "step": step},
    }


def configure(project, value=...):
    cfg = deepcopy(project["cfg"])
    if value is not ...:
        cfg["task_ids"] = deepcopy(value)
    write_json(project["config_path"], cfg)
    project["cfg"] = cfg
    return project


def task_contract(project, identifier="EXPLICIT", sprint_id=None):
    task = deepcopy(project["task"])
    task["id"] = identifier
    task["sprint_id"] = sprint_id
    return task


def automatic_intent(project, request_id, *, sprint_id=None, goal=None):
    task = task_contract(project, sprint_id=sprint_id)
    del task["id"]
    if goal is not None:
        task["goal"] = goal
    return {"request_id": request_id, "task": task}


def bootstrap(tools, intent):
    return tools.invoke(
        request(
            "bootstrap",
            {
                "task": deepcopy(intent),
                "decision": None,
                "feedback": None,
                "rework_stage": None,
            },
        )
    )


def allocation_map(result):
    return {
        item["request_id"]: item["task_id"]
        for item in result["allocations"]
    }


def test_public_creation_allocates_task_id(project):
    configure(project, policy())
    tools = WorkTools(Harness(project["config_path"], "creator"))

    result = bootstrap(tools, automatic_intent(project, "create-first"))

    assert result["task"] == "0001"
    assert result["allocation"] == {
        "request_id": "create-first",
        "task_id": "0001",
        "replayed": False,
    }
    assert tools.runtime.task_queries.record("0001")["contract"]["id"] == "0001"


def test_sequential_allocation_uses_configured_width_and_progression(project):
    configure(project, policy(minimum=1, maximum=99, width=4, first=3, step=2))

    allocated = []
    for index in range(3):
        tools = WorkTools(Harness(project["config_path"], f"creator-{index}"))
        allocated.append(
            bootstrap(tools, automatic_intent(project, f"sequence-{index}"))["task"]
        )

    assert allocated == ["0003", "0005", "0007"]


def test_occupied_configured_candidates_are_skipped(project):
    configure(project, policy(minimum=1, maximum=99, width=4, first=3, step=2))
    for index, identifier in enumerate(("0003", "0007")):
        tools = WorkTools(Harness(project["config_path"], f"explicit-{index}"))
        assert bootstrap(tools, task_contract(project, identifier))["task"] == identifier

    first = bootstrap(
        WorkTools(Harness(project["config_path"], "automatic-1")),
        automatic_intent(project, "occupied-1"),
    )
    second = bootstrap(
        WorkTools(Harness(project["config_path"], "automatic-2")),
        automatic_intent(project, "occupied-2"),
    )

    assert [first["task"], second["task"]] == ["0005", "0009"]


def test_concurrent_creation_allocates_unique_atomic_ids(project):
    configure(project, policy(maximum=20))
    ready = threading.Barrier(2)

    def create(index):
        runtime = Harness(project["config_path"], f"parallel-{index}")
        ready.wait(timeout=5)
        return bootstrap(
            WorkTools(runtime), automatic_intent(project, f"parallel-request-{index}")
        )["task"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        allocated = list(pool.map(create, range(2)))

    runtime = Harness(project["config_path"], "reader")
    assert sorted(allocated) == ["0001", "0002"]
    assert {row["id"] for row in runtime.task_queries.summary()} == set(allocated)
    with runtime.store.transaction() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_creation_request_replay_is_stable_and_does_not_consume_id(project):
    configure(project, policy(maximum=20))
    tools = WorkTools(Harness(project["config_path"], "replay-owner"))
    intent = automatic_intent(project, "stable-request")

    first = bootstrap(tools, intent)
    replay = bootstrap(tools, intent)
    next_result = bootstrap(
        WorkTools(Harness(project["config_path"], "next-owner")),
        automatic_intent(project, "next-request"),
    )

    assert first["task"] == replay["task"] == "0001"
    assert replay["allocation"]["replayed"] is True
    assert next_result["task"] == "0002"


def test_creation_request_digest_conflict_is_rejected_without_consumption(project):
    configure(project, policy(maximum=20))
    tools = WorkTools(Harness(project["config_path"], "conflict-owner"))
    bootstrap(tools, automatic_intent(project, "conflicting-request"))

    with pytest.raises(HarnessError, match="(?i)request|digest|conflict"):
        bootstrap(
            tools,
            automatic_intent(
                project,
                "conflicting-request",
                goal="A changed immutable creation contract",
            ),
        )

    next_result = bootstrap(
        WorkTools(Harness(project["config_path"], "after-conflict")),
        automatic_intent(project, "after-conflict"),
    )
    assert next_result["task"] == "0002"
    assert tools.runtime.task_queries.record("0001")["goal"] == project["task"]["goal"]


def test_missing_or_invalid_allocation_policy_fails_explicitly(project):
    configure(project)
    explicit = bootstrap(
        WorkTools(Harness(project["config_path"], "explicit-without-policy")),
        task_contract(project, "LEGACY"),
    )
    assert explicit["task"] == "LEGACY"
    with pytest.raises(HarnessError, match="(?i)task_ids|allocation policy"):
        bootstrap(
            WorkTools(Harness(project["config_path"], "missing-policy")),
            automatic_intent(project, "missing-policy"),
        )

    invalid = [
        None,
        {},
        {"namespace": {"minimum": 1, "maximum": 9}, "width": 4},
        policy(minimum=True),
        policy(width=0),
        policy(step=0),
        policy(minimum=5, maximum=4),
        policy(minimum=1, maximum=5, first=6),
        policy(minimum=1, maximum=100, width=2),
        {**policy(), "unknown": 1},
    ]
    for index, value in enumerate(invalid):
        cfg = deepcopy(project["cfg"])
        cfg["task_ids"] = value
        write_json(project["config_path"], cfg)
        with pytest.raises(HarnessError, match="(?i)task_ids"):
            Harness(project["config_path"], f"invalid-policy-{index}")


def test_exhaustion_leaves_existing_tasks_unchanged(project):
    configure(project, policy(minimum=1, maximum=2, width=4))
    for index in range(2):
        bootstrap(
            WorkTools(Harness(project["config_path"], f"fill-{index}")),
            automatic_intent(project, f"fill-request-{index}"),
        )
    runtime = Harness(project["config_path"], "exhausted")
    before = deepcopy(runtime.task_queries.summary())

    with pytest.raises(HarnessError, match="(?i)exhaust|namespace"):
        bootstrap(
            WorkTools(runtime), automatic_intent(project, "exhausted-request")
        )

    assert runtime.task_queries.summary() == before
    assert {row["id"] for row in before} == {"0001", "0002"}


def test_allocated_identity_is_used_everywhere(project):
    configure(project, policy())
    tools = WorkTools(Harness(project["config_path"], "identity-owner"))
    result = bootstrap(tools, automatic_intent(project, "identity-request"))
    record = tools.runtime.task_queries.record("0001")

    assert result["task"] == record["id"] == record["contract"]["id"] == "0001"
    assert Path(result["worktree"]).name == "0001"
    assert Path(result["task_root"]).name == "0001"
    assert record["branch"] == "tasks/0001"
    assert result["allocation"]["task_id"] == "0001"
    assert "EXPLICIT" not in json.dumps(result, ensure_ascii=False)


def test_worktree_failure_replays_same_reservation_and_recovers(project, monkeypatch):
    configure(project, policy())
    runtime = Harness(project["config_path"], "recovery-owner")
    tools = WorkTools(runtime)
    real_git = runtime._git
    failed = False

    def fail_after_worktree_add(cwd, *args, **kwargs):
        nonlocal failed
        result = real_git(cwd, *args, **kwargs)
        if not failed and args[:2] == ("worktree", "add"):
            failed = True
            raise HarnessError("injected failure after git worktree add")
        return result

    monkeypatch.setattr(runtime, "_git", fail_after_worktree_add)
    intent = automatic_intent(project, "recoverable-request")
    with pytest.raises(HarnessError, match="(?i)0001|recover|repeat"):
        bootstrap(tools, intent)

    record = runtime.task_queries.record("0001")
    assert record is not None
    with runtime.store.unit_of_work() as uow:
        execution, _ = uow.execution.load("0001")
    assert execution["pending"]["kind"] == "worktree_setup"

    monkeypatch.setattr(runtime, "_git", real_git)
    recovered = bootstrap(tools, intent)
    assert recovered["task"] == "0001"
    assert recovered["allocation"]["replayed"] is True
    assert Path(recovered["worktree"]).is_dir()
    assert [row["id"] for row in runtime.task_queries.summary()] == ["0001"]


def _planning_process(project):
    stage = deepcopy(project["process"]["stages"][0])
    stage.update(
        id="draft",
        handler="produce",
        sections={"planned_tasks": "Write the reviewed contracts."},
        required_sections=["planned_tasks"],
        read_only=True,
        allowed_paths=[],
        transitions={"complete": "review"},
        rework_targets=["draft"],
    )
    review = deepcopy(stage)
    review.update(
        id="review",
        handler="inspect",
        sections={"report": "Review."},
        required_sections=["report"],
        transitions={"clear": "publish", "changes_requested": "draft"},
        rework_targets=["draft", "review"],
    )
    publish = deepcopy(stage)
    publish.update(
        id="publish",
        handler="publish",
        sections={},
        required_sections=[],
        transitions={"complete": None},
        rework_targets=["draft"],
    )
    process = deepcopy(project["process"])
    process.update(
        goal_type="planning",
        stages=[stage, review, publish],
        route={"entry": "draft", "max_transitions": 30, "max_stage_visits": 6},
    )
    return process


def _planning_context(project):
    process = _planning_process(project)
    write_json(project["root"] / "config/processes/planning.json", process)
    cfg = deepcopy(project["cfg"])
    cfg["processes"]["planning"] = "config/processes/planning.json"
    cfg["automatic_checks"] = []
    write_json(project["config_path"], cfg)
    parent = task_contract(project, "PLAN")
    parent.update(
        goal_type="planning",
        methods=[],
        checks={stage["id"]: [] for stage in process["stages"]},
        evidence_plan={
            stage["id"]: {
                "subject_methods": {},
                "arguments": [],
                "review_arguments": [],
            }
            for stage in process["stages"]
        },
    )
    runtime = Harness(project["config_path"], "planner")
    tools = WorkTools(runtime)
    return tools, bootstrap(tools, parent)


def _verify(tools, context, *, sections=None, stage_work=None):
    result = deepcopy(context["result_template"])
    result["sections"].update({} if sections is None else sections)
    result["stage_work"] = {} if stage_work is None else stage_work
    return tools.invoke(request("verify", {"result": result, "artifacts": []}))


def _advance_reviewed_plan(tools, context, intents):
    _verify(
        tools,
        context,
        sections={"planned_tasks": json.dumps(intents, ensure_ascii=False)},
    )
    context = tools.invoke(
        request(
            "bootstrap",
            {"task": None, "decision": "continue", "feedback": None, "rework_stage": None},
        )
    )
    _verify(
        tools,
        context,
        sections={"report": "Every automatic creation intent was reviewed."},
        stage_work={
            "coverage": "Reviewed every complete child intent.",
            "findings": [],
            "resolution_decisions": [],
        },
    )
    tools.invoke(request("accept", {}))
    return tools.invoke(
        request(
            "bootstrap",
            {"task": None, "decision": "continue", "feedback": None, "rework_stage": None},
        )
    )


def test_reviewed_task_publication_allocates_atomically_and_replays(project):
    configure(project, policy(maximum=20))
    tools, context = _planning_context(project)
    intents = [
        automatic_intent(project, "reviewed-A"),
        automatic_intent(project, "reviewed-B", goal="Second reviewed child"),
    ]
    context = _advance_reviewed_plan(tools, context, intents)
    result = _verify(
        tools,
        context,
        stage_work={
            "kind": "tasks",
            "section": "planned_tasks",
            "authorization": "User: publish reviewed automatic task intents.",
        },
    )

    assert allocation_map(result) == {"reviewed-A": "0001", "reviewed-B": "0002"}
    assert {row["id"] for row in tools.runtime.task_queries.summary()} == {
        "PLAN",
        "0001",
        "0002",
    }
    replay = tools.invoke(request("verify", {"result": None, "artifacts": []}))
    assert replay["replayed"] is True
    assert allocation_map(replay) == allocation_map(result)


def test_reviewed_publication_failure_rolls_back_allocations(project):
    import sqlite3

    configure(project, policy(maximum=20))
    tools, context = _planning_context(project)
    intents = [
        automatic_intent(project, "rollback-A"),
        automatic_intent(project, "rollback-B", goal="Second rollback child"),
    ]
    context = _advance_reviewed_plan(tools, context, intents)
    with tools.runtime.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER reject_allocated_second BEFORE INSERT ON tasks "
            "WHEN NEW.id='0002' BEGIN SELECT RAISE(ABORT,'injected allocation failure'); END"
        )
    stage_work = {
        "kind": "tasks",
        "section": "planned_tasks",
        "authorization": "User: publish reviewed automatic task intents.",
    }

    with pytest.raises(sqlite3.IntegrityError, match="injected allocation failure"):
        _verify(tools, context, stage_work=stage_work)

    assert tools.runtime.task_queries.record("0001") is None
    assert tools.runtime.task_queries.record("0002") is None
    with tools.runtime.store.unit_of_work() as uow:
        assert uow.actions.load("PLAN", "publish", 1) is None
    with tools.runtime.store.transaction() as db:
        db.execute("DROP TRIGGER reject_allocated_second")
    result = _verify(tools, context, stage_work=stage_work)
    assert allocation_map(result) == {"rollback-A": "0001", "rollback-B": "0002"}


def test_sprint_publication_rewrites_request_aliases_to_allocated_ids(project):
    from sprints.helpers import changes, policy as sprint_policy

    configure(project, policy(maximum=20))
    cfg = deepcopy(project["cfg"])
    cfg["sprint"] = sprint_policy()
    cfg["automatic_checks"] = []
    write_json(project["config_path"], cfg)
    tools = WorkTools(Harness(project["config_path"], "sprint-planner"))
    intents = [
        automatic_intent(project, "member-A", sprint_id="S"),
        automatic_intent(project, "member-B", sprint_id="S", goal="Dependent member"),
    ]
    edges = [{"predecessor": "member-A", "successor": "member-B", "kind": "completion"}]
    drafted = tools.invoke(
        request(
            "sprint",
            {
                "action": "draft",
                "sprint_id": "S",
                "request_id": "draft-auto-members",
                "expected_revision": None,
                "template": {"id": "basic", "version": "1"},
                "changes": changes(intents, edges),
            },
        )
    )
    published = tools.invoke(
        request(
            "sprint",
            {
                "action": "publish",
                "sprint_id": None,
                "request_id": "publish-auto-members",
                "expected_revision": drafted["revision"],
            },
        )
    )

    assert allocation_map(published) == {"member-A": "0001", "member-B": "0002"}
    assert published["eligible"] == ["0001"]
    plan = tools.invoke(
        request(
            "show",
            {"queries": [{"id": "plan", "kind": "sprint", "sprint_id": "S", "view": "plan"}]},
        )
    )["results"][0]["value"]
    assert [task["id"] for task in plan["aggregate"]["plan"]["tasks"]] == ["0001", "0002"]
    assert plan["aggregate"]["plan"]["dependencies"] == [
        {"predecessor": "0001", "successor": "0002", "kind": "completion"}
    ]
    assert not (tools.runtime.state / tools.runtime.paths["worktrees"]).exists()


def test_existing_rows_and_explicit_id_creation_need_no_migration(project):
    configure(project, policy())
    runtime = Harness(project["config_path"], "legacy-owner")
    explicit = bootstrap(WorkTools(runtime), task_contract(project, "LEGACY"))
    with runtime.store.transaction() as db:
        before_version = db.execute("PRAGMA user_version").fetchone()[0]

    reopened = Harness(project["config_path"], "legacy-reader")
    record = reopened.task_queries.record("LEGACY")

    assert explicit["task"] == record["id"] == "LEGACY"
    assert before_version == SCHEMA_VERSION
    with reopened.store.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == before_version
    assert "creation_request" not in record


def test_allocation_is_owned_by_task_port_and_not_callers_or_git():
    root = Path(__file__).resolve().parents[2]
    domain = root / "src/harness/modules/tasks/allocation.py"
    ports = (root / "src/harness/modules/tasks/ports.py").read_text(encoding="utf-8")
    commands = (root / "src/harness/application/tasks.py").read_text(encoding="utf-8")
    adapters = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "src/harness/runtime.py",
            "src/harness/application/catalogue.py",
            "src/harness/application/sprints.py",
            "src/harness/application/planning_publication.py",
        )
    )

    text = domain.read_text(encoding="utf-8")
    assert all(name not in text for name in ("sqlite3", "subprocess", "pathlib", "os."))
    assert "allocate" in ports
    assert "unit_of_work" in commands and "allocation" in commands.lower()
    assert "glob(" not in adapters and "iterdir(" not in adapters
    assert "git branch" not in adapters.lower()
