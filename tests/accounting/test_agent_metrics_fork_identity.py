"""Actual thread/root/direct-parent identity, independent of copied metadata order."""
import json

from tests.accounting.agent_metrics_support import MetricsScene, assert_measurements, literal
from tests.accounting.test_agent_metrics_updated_contracts import observed_stop


def test_source_owner_retains_child_thread_and_distinct_root_session(tmp_path):
    from poise.infrastructure.agent_metrics.sources import capture_source, scan_jsonl, owned_records
    from tests.accounting.agent_metrics_support import FIXTURES, replace_tokens

    source = tmp_path / "unrelated-filename.jsonl"
    source.write_text(replace_tokens((FIXTURES / "fork-inherited.jsonl").read_text(),
                                    {"@CHECKOUT@": tmp_path}))
    specification = literal("updated-normalization-request.json")["source"]
    batch = scan_jsonl(capture_source(source, specification), max_source_bytes=1048576, max_records=2)
    expected = literal("expected-fork-thread-identities.json")
    owner, metadata, selected, excluded = owned_records(batch)
    if owner != expected["thread_id"]:
        raise AssertionError("F16_FORK_SOURCE_OWNER_IS_ROOT_OR_INHERITED_PARENT")
    assert metadata["id"] == expected["thread_id"]
    assert metadata["session_id"] == expected["root_session_id"]
    assert metadata["forked_from_id"] == expected["direct_parent_thread_id"]
    usage = [row for row in selected if row.event["type"] == "token_usage_record"]
    assert len(usage) == 1
    assert usage[0].event["payload"]["thread_id"] == expected["thread_id"]
    assert usage[0].event["payload"]["session_id"] == expected["root_session_id"]
    assert usage[0].event["payload"]["response_id"] == expected["owned_response_id"]
    assert usage[0].event["payload"]["usage"]["total_tokens"] == 20
    assert excluded


def test_actual_stop_keeps_child_spend_out_of_inherited_parent_scope(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.source("fork-inherited")
    scene.configure_metrics()
    for event in scene.definition["routes"]:
        if event != "Stop":
            scene.definition["routes"][event] = []
    scene.install(metrics=False)
    try:
        scene.native("SessionStart")
        observed_stop(scene, tmp_path / "fork-native-trace")
        child = scene.query(family="usage-current")
        assert_measurements(child, literal("expected-usage-fork-inherited.json"))
        project_view = scene.query(family="usage-current",
                                   scope={"kind": "project", "id": project["cfg"]["project"]})
        totals = [row["value"] for row in project_view["metrics"]
                  if row["name"] == "total_tokens" and row["availability"]["state"] == "known"]
        assert totals == [20], "F16_INHERITED_PARENT_USAGE_CHARGED_TO_CHILD_SOURCE"
        expected = literal("expected-fork-thread-identities.json")
        provenance = scene.query(family="invocation-provenance")
        owned = [row for row in provenance["context"] if row.get("response_id") == expected["owned_response_id"]]
        assert owned
        assert all(row["thread_id"] == expected["thread_id"] for row in owned)
        assert all(row.get("root_session_id") == expected["root_session_id"] for row in owned)
    finally:
        scene.close()
