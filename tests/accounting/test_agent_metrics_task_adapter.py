"""A real optional Task owner enriches observations without being a collection prerequisite."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.accounting.agent_metrics_support import (
    MetricsScene, SESSION, assert_measurements, assert_metadata, assert_unavailable_metadata,
    literal, metadata_values,
)
from tests.accounting.agent_metrics_task_support import quality_source, sprint_source


def test_optional_sprint_source_control_uses_real_publication_and_native_task_acquisition(project, tmp_path):
    scene, active, _ = sprint_source(project, tmp_path, metrics=False)
    try:
        assert active["task"] == "T1" and active["stage"] == "work"
        assert Path(active["sprint_root"]).is_dir()
        assert Path(active["task_root"]).is_dir()
        assert not scene.metrics.exists()
    finally:
        scene.close()


def test_actual_published_sprint_enriches_task_facts_and_survives_session_end(project, tmp_path):
    scene, _, _ = sprint_source(project, tmp_path, metrics=True)
    try:
        scene.native("UserPromptSubmit")
        scene.native("Stop")
        linked = scene.wait_query(family="optional-task-policy",
                                  predicate=lambda value: "S" in metadata_values(value, "sprint_id"))
        assert_metadata(linked, {"task_id": "T1", "sprint_id": "S", "session_id": SESSION})
        expected = literal("expected-usage-complete.json")
        scene.wait_measurements("usage-current", expected, scope={"kind": "sprint", "id": "S"})
        scene.close()
        for scope in ({"kind": "sprint", "id": "S"}, {"kind": "task", "id": "T1"}):
            assert_measurements(scene.query(family="usage-current", scope=scope), expected)
    finally:
        scene.close()


def test_optional_task_source_control_uses_real_feedback_release_and_native_reacquisition(project, tmp_path):
    scene, active, observed = quality_source(project, tmp_path, metrics=False)
    try:
        assert active["stage"] == "amend" and active["task"] == "T1"
        assert {item["id"] for item in active["workflow"]["feedback"]["open_findings"]} == {"F1", "F2"}
        assert Path(active["task_root"]).is_dir() and Path(active["task_root"]).name == "T1"
        assert observed["status"] == "read_only"
        assert not scene.metrics.exists()
    finally:
        scene.close()


def test_available_task_feedback_and_stage_metadata_are_bound_to_the_real_native_owner(project, tmp_path):
    before = datetime.now(timezone.utc)
    scene, active, _ = quality_source(project, tmp_path, metrics=True)
    try:
        scene.native("UserPromptSubmit")
        scene.native("Stop")
        reply = scene.wait_query(family="optional-task-policy", scope={"kind": "task", "id": "T1"},
                                 predicate=lambda value: {"F1", "F2"}.issubset(
                                     {item for item in metadata_values(value, "finding_id") if isinstance(item, str)}))
        assert_metadata(reply, literal("expected-quality-task-context.json"))
        findings = {item for item in metadata_values(reply, "finding_id") if isinstance(item, str)}
        assert findings == {"F1", "F2"}
        assert_metadata(reply, {"task_path": active["task_root"]})
        skills = metadata_values(reply, "required_skill")
        for value in metadata_values(reply, "required_skills"):
            if isinstance(value, list):
                skills.extend(value)
        assert {item for item in skills if isinstance(item, str)} == {"task-domain"}
        reasons = metadata_values(reply, "binding_reason")
        assert any(isinstance(item, str) and item.strip() for item in reasons)
        after = datetime.now(timezone.utc)
        captured = metadata_values(reply, "captured_at")
        assert captured and all(before <= datetime.fromisoformat(item.replace("Z", "+00:00")) <= after
                                for item in captured)
        for field in literal("unavailable-quality-task-fields.json"):
            assert_unavailable_metadata(reply, field, state="unknown", source="task-adapter")
        assert_unavailable_metadata(reply, "policy_hash", state="unknown", source="skill-policy")
        scene.close()
        retained = scene.query(family="optional-task-policy", scope={"kind": "task", "id": "T1"})
        assert_metadata(retained, literal("expected-quality-task-context.json"))
    finally:
        scene.close()


@pytest.mark.parametrize("assigned", [False, True], ids=["no-current-task", "actual-current-task"])
def test_connected_task_owner_records_actual_association_or_explicit_unavailability(project, tmp_path, assigned):
    scene = MetricsScene(project, tmp_path, connected=True)
    if not assigned:
        scene.source()
    scene.definition, scene.events = scene.general.native_definition(all_events=True, task=True)
    scene.install()
    try:
        scene.native("SessionStart")
        if assigned:
            binding = scene.service.latest_binding(SESSION, "primary")
            assert binding["external_session"] == SESSION
            request = literal("task-bootstrap.json")
            request["input"]["task"] = project["task"]
            actual = scene.work(request)
            scene.record({"kind": "actual-task-owner-bootstrap", "result": actual})
            assert actual["task"] == "T1"
            # These facts become available only after the real Task assignment.
            # The test does not demand rebinding earlier taskless observations.
            scene.source()
        scene.native("UserPromptSubmit")
        scene.native("Stop")
        reply = scene.wait_query(family="usage-current")
        assert_measurements(reply, literal("expected-usage-complete.json"))
        def association_arrived(value):
            if assigned:
                return "T1" in metadata_values(value, "task_id")
            rows = [row for row in value["metrics"] if row["name"] == "task_id"]
            return bool(rows) or value["coverage"]["state"] in {"unavailable", "not_applicable"}

        association = scene.wait_query(family="optional-task-policy", predicate=association_arrived)
        if assigned:
            assert_metadata(association, {"task_id": "T1", "session_id": SESSION})
            scoped = scene.query(family="usage-current", scope={"kind": "task", "id": "T1"})
            assert_measurements(scoped, literal("expected-usage-complete.json"))
            # A fresh read client has not acquired the observed Task.
            from poise.runtime import Poise
            from conftest import DeterministicClock
            observer = Poise(project["config_path"], "f16-independent-task-readback", DeterministicClock())
            try:
                assert observer.current_task() is None
            finally:
                observer.telemetry.dispatcher.close()
        else:
            rows = [row for row in association["metrics"] if row["name"] == "task_id"]
            if rows:
                assert len(rows) == 1 and rows[0]["value"] is None
                availability = rows[0]["availability"]
            else:
                availability = association["coverage"]
            assert availability["state"] in {"unavailable", "not_applicable"}
            assert availability["reason"]
            assert "T1" not in json.dumps(association["provenance"])
        scene.close()
        assert_measurements(scene.query(family="usage-current"), literal("expected-usage-complete.json"))
    finally:
        scene.close()
