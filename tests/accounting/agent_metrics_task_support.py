"""Real optional Task source arrangement; no telemetry adapter or native receipts are fabricated."""
from tests.accounting.agent_metrics_support import MetricsScene, SESSION, literal
from tests.accounting.test_quality import quality_session
from tests.batch.helpers import request


def publish_sprint_source(project):
    """Arrange a published Task through its existing public Sprint owner."""
    from poise.runtime import Poise
    from conftest import DeterministicClock
    from tests.sprints.helpers import setup, task, publish_existing_contract

    setup(project)
    planner = Poise(project["config_path"], "f16-fixture-sprint-planner", DeterministicClock())
    try:
        project["task"] = publish_existing_contract(planner, task(project, "T1"), "S")
        assert planner.current_task() is None
    finally:
        planner.telemetry.dispatcher.close()


def sprint_source(project, tmp_path, *, metrics):
    """Publish a real Sprint before the external native session acquires its Task."""
    publish_sprint_source(project)
    scene = MetricsScene(project, tmp_path, connected=True)
    scene.definition, scene.events = scene.general.native_definition(all_events=True, task=True)
    scene.install(metrics=metrics)
    scene.native("SessionStart")
    try:
        body = literal("task-bootstrap.json")
        body["input"]["task"] = {"id": "T1"}
        active = scene.work(body)
        assert active["task"] == "T1" and active["stage"] == "work"
        assert active["sprint_root"] and active["task_root"]
        observed = scene.work(request("show", {"queries": [{"id": "task", "kind": "task"}]}))
        scene.record({"kind": "actual-sprint-task-source", "bootstrap": active, "show": observed})
        scene.source()
        return scene, active, observed
    except BaseException:
        scene.close()
        raise


def quality_source(project, tmp_path, *, metrics):
    source_runtime, source_work, context = quality_session(project, "tool_cycle")
    release = literal("source-quality-release.json")
    release["expected_version"] = source_runtime.task_queries.record("T1")["version"]
    try:
        outcome = source_work.invoke(request("release", release))
        assert outcome["after"]["task_id"] is None and outcome["after"]["worktree_task_id"] is None
    finally:
        source_runtime.telemetry.dispatcher.close()
    scene = MetricsScene(project, tmp_path, connected=True)
    scene.definition, scene.events = scene.general.native_definition(all_events=True, task=True)
    scene.install(metrics=metrics)
    scene.native("SessionStart")
    try:
        binding = scene.service.latest_binding(SESSION, "primary")
        body = literal("task-bootstrap.json")
        body["input"]["task"] = {"id": "T1"}
        active = scene.work(body)
        assert active["task"] == "T1" and active["stage"] == "amend"
        observed = scene.work(request("show", {
            "queries": [{"id": "task", "kind": "task"}]}))
        scene.record({"kind": "actual-optional-task-source", "bootstrap": active, "show": observed})
        scene.source()
        return scene, active, observed
    except BaseException:
        scene.close()
        raise
