"""Explicitly configured period allocation reads normalized facts, not copied transcripts."""
from tests.accounting.agent_metrics_support import (
    MetricsScene, assert_measurements, assert_metadata, committed_observations, context_view,
    literal, metadata_values, replace_tokens,
)
from tests.accounting.test_agent_metrics_pipeline import total_values
from tests.hook_transport.architecture_support import write


def test_configured_history_keeps_day_week_month_alternatives_without_triple_spend(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.source("files-checks")
    target = project["app"] / "src/example.py"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"two\nthree\n")
    scene.policy["analyses"]["history"] = replace_tokens(literal("history-policy.json"), scene.tokens)
    write(scene.policy_path, scene.policy)
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        expected = literal("expected-history-periods.json")
        history = scene.wait_query(family="history", kind="derived", predicate=lambda value: all(
            period in metadata_values(value, "period") for period in expected["periods"]))
        for period in literal("public-record-context.json")["history_period_contexts"]:
            assert_measurements(context_view(history, period), expected["metrics"])
        assert_metadata(history, literal("expected-history-sources.json"))
        assert total_values(scene.query(family="usage-current")) == [150]
        scene.close()
        before = committed_observations(scene.metrics / "observed.sqlite")
        again = scene.query(family="history", kind="derived")
        assert again["metrics"] == history["metrics"]
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        scene.close()


def test_partial_history_retains_known_amounts_and_refuses_a_complete_efficiency_ratio(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source("files-checks")
    with source.open("a", encoding="utf-8") as stream:
        stream.write('{"payload":INVALID}\n')
    target = project["app"] / "src/example.py"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"two\nthree\n")
    scene.policy["analyses"]["history"] = replace_tokens(literal("history-policy.json"), scene.tokens)
    write(scene.policy_path, scene.policy)
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        history = scene.wait_query(family="history", kind="derived", predicate=lambda value: any(
            row["name"] == "tokens" and row["availability"]["state"] == "partial" for row in value["metrics"]))
        for period in literal("public-record-context.json")["history_period_contexts"]:
            selected = context_view(history, period)
            assert_measurements(selected, literal("expected-partial-history.json"))
            for row in selected["metrics"]:
                if row["name"] in {"tokens", "changed_lines"}:
                    assert row["context"]["complete"] is False
                    assert row["context"]["known"] == row["value"]
        diagnostics = metadata_values(history, "diagnostics")
        assert diagnostics and any(value for value in diagnostics)
    finally:
        scene.close()
