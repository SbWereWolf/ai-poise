"""Separate real native sessions retain zero, missing and conflicting usage distinctions."""
import json
import pytest
from tests.accounting.agent_metrics_support import (
    MetricsScene, assert_measurements, assert_metadata, assert_unavailable_metadata,
    committed_observations, literal,
)


@pytest.mark.parametrize("case", literal("purpose-cases.json"), ids=lambda case: case["id"])
def test_actual_response_link_decides_main_auxiliary_or_unclassified_spend(project, tmp_path, case):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    event = literal("purpose-tool-event.json")
    event["payload"].update(name=case["name"], response_id=case["response_id"])
    # The transcript is a source fixture, never a command to execute this tool.
    with source.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\n")
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        expected = literal("expected-single-session-summary.json")
        for name in ("main_tokens", "auxiliary_tokens", "unclassified_tokens"):
            expected[name]["value"] = case[name]
        observed = scene.wait_measurements("aggregate-readouts", expected, kind="derived")
        assert sum(case[name] for name in ("main_tokens", "auxiliary_tokens", "unclassified_tokens")) == 150
        scene.close()
        assert_measurements(scene.query(family="aggregate-readouts", kind="derived"), expected)
        assert observed["metrics"]
    finally:
        scene.close()


def test_single_session_summary_retains_actual_invocation_count_and_known_usage(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.source()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        expected = literal("expected-single-session-summary.json")
        result = scene.wait_measurements("aggregate-readouts", expected, kind="derived")
        assert_metadata(result, {"complete_for_registered_scope": True})
        assert_unavailable_metadata(result, "complete_for_bound_sessions", state="not_applicable", source="task-adapter")
        scene.close()
        assert_measurements(scene.query(family="aggregate-readouts", kind="derived"), expected)
    finally:
        scene.close()


def test_four_sessions_keep_known_subtotal_without_inventing_a_complete_total(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.install()
    try:
        for case in literal("aggregate-session-cases.json"):
            scene.next_session(case["session"], case["filename"])
            scene.source(case["source"])
            scene.native("SessionStart")
            scene.native("Stop")
            expected = literal("expected-usage-" + case["source"] + ".json")
            result = scene.wait_measurements("usage-current", expected)
            assert result["scope"] == {"kind": "session", "id": case["session"]}
            scene.close()
        expected = literal("expected-aggregate-partial.json")
        aggregate = scene.wait_measurements("aggregate-readouts", expected, kind="derived",
                                            scope={"kind": "project", "id": project["cfg"]["project"]})
        assert_measurements(aggregate, expected)
        before = committed_observations(scene.metrics / "observed.sqlite")
        again = scene.query(family="aggregate-readouts", kind="derived",
                            scope={"kind": "project", "id": project["cfg"]["project"]})
        assert again["metrics"] == aggregate["metrics"]
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        scene.close()
