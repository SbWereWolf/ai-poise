"""Actual source/commit/fresh-process effects; no manual normal-path ingestion."""
import json
from pathlib import Path
import sqlite3

from tests.accounting.agent_metrics_support import (
    MetricsScene, SESSION, assert_measurements, assert_metadata, committed_observations, literal,
    metadata_values,
)


def open_scene(project, tmp_path, *, source="complete"):
    scene = MetricsScene(project, tmp_path)
    scene.source(source)
    scene.install()
    scene.native("SessionStart")
    return scene


def total_values(reply):
    return sorted(row["value"] for row in reply["metrics"]
                  if row["name"] == "total_tokens" and row["availability"]["state"] == "known")


def test_distinct_intentional_tool_calls_keep_both_effects_and_both_observations(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.install()
    try:
        scene.native("SessionStart")
        first = scene.execute_tool("f16-intended-call-one")
        second = scene.execute_tool("f16-intended-call-two")
        assert first["exit_code"] == second["exit_code"] == 0
        assert first["stdout"] == second["stdout"] == "А\n".encode()
        assert first["stderr"] == second["stderr"] == b""
        assert (tmp_path / "actual-tool-effects.txt").read_text() == "effect\neffect\n"
        reply = scene.wait_query(family="tools", predicate=lambda data: len([
            row for row in data["metrics"] if row["name"] == "output_bytes"]) == 2)
        outputs = [row for row in reply["metrics"] if row["name"] == "output_bytes"]
        assert [row["value"] for row in outputs] == [3, 3]
        assert all(row["unit"] == "bytes" and row["kind"] == "observed" for row in outputs)
        provenance = json.dumps(reply["provenance"])
        assert "f16-intended-call-one" in provenance and "f16-intended-call-two" in provenance
    finally:
        scene.close()


def test_existing_native_fixture_preserves_two_real_intentional_tool_executions(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.install(metrics=False)
    try:
        scene.native("SessionStart")
        first = scene.execute_tool("f16-control-call-one")
        second = scene.execute_tool("f16-control-call-two", exit_code=7)
        assert first["exit_code"] == 0 and second["exit_code"] == 7
        assert first["stdout"] == second["stdout"] == "А\n".encode()
        assert (tmp_path / "actual-tool-effects.txt").read_text() == "effect\neffect\n"
        assert not scene.metrics.exists()
    finally:
        scene.close()


def test_ordinary_stop_processes_all_captured_chunks_without_manual_drain(project, tmp_path):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        # max_records=2; the source is longer. Merely queuing the first chunk fails.
        first = scene.wait_query(family="usage-current")
        assert_measurements(first, literal("expected-usage-complete.json"))
        assert (scene.metrics / "observed.sqlite").is_file()
        with sqlite3.connect(scene.metrics / "observed.sqlite") as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            rows = db.execute("SELECT id,project,session_id,data FROM accounting_cycles").fetchall()
        assert "tasks" not in tables and "task_workflows" not in tables
        assert rows, "F16_METRICS_NEVER_COMMITTED"
        second = scene.query(family="usage-current")
        assert second["metrics"] == first["metrics"]
        clients = [row for row in scene.calls if row["kind"] == "cli"]
        assert clients[-1]["pid"] != clients[-2]["pid"]
        assert not any(row.get("request", {}).get("operation") == "replay" for row in scene.calls)
    finally:
        scene.close()


def test_general_capture_and_fresh_read_require_no_task_configuration(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    source_bytes = source.read_bytes()
    task_path = Path(project["config_path"])
    # The public general publication is complete. Actual operations now lack a Task config.
    task_path.unlink()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        first = scene.wait_query(family="usage-current")
        scene.close()
        second = scene.query(family="usage-current")
        assert_measurements(second, literal("expected-usage-complete.json"))
        assert second["metrics"] == first["metrics"]
        assert all(row["sentinel"] == literal("expected-no-task.json")
                   for row in scene.calls if row["kind"] == "cli")
        assert not task_path.exists()
        task_db = project["root"] / project["cfg"]["paths"]["state"] / project["cfg"]["paths"]["database"]
        assert not task_db.exists()
        assert source.read_bytes() == source_bytes
    finally:
        scene.close()


def test_same_source_observation_is_not_charged_by_each_stop(project, tmp_path):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        first = scene.wait_query(family="usage-current")
        scene.native("Stop")
        second = scene.wait_query(family="usage-current")
        assert total_values(first) == [150]
        assert total_values(second) == [150]
        assert second["metrics"] == first["metrics"]
    finally:
        scene.close()


def test_new_response_appends_usage_without_recharging_prior_response(project, tmp_path):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        assert total_values(scene.wait_query(family="usage-current")) == [150]
        with (scene.sources / "session.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(literal("additional-usage-event.json")) + "\n")
        # Reading alone must not ingest the appended source.
        assert total_values(scene.query(family="usage-current")) == [150]
        scene.native("Stop")
        after = scene.wait_query(family="usage-current", predicate=lambda reply: len(total_values(reply)) == 2)
        assert total_values(after) == literal("expected-additional-totals.json")
    finally:
        scene.close()


def test_partial_last_record_is_coverage_not_fabricated_usage(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    complete_bytes = source.stat().st_size
    tail = b'{"type":"token_usage_record","payload":'
    with source.open("ab") as stream:
        stream.write(tail)
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        reply = scene.wait_query(family="usage-current")
        assert total_values(reply) == [150]
        assert reply["coverage"]["state"] == "partial"
        assert "partial_tail" in json.dumps(reply["coverage"])
        assert not any(row["value"] == 0 and row["name"] == "total_tokens" for row in reply["metrics"])
        provenance = scene.wait_query(family="invocation-provenance", predicate=lambda value:
            any(item is True for item in metadata_values(value, "partial_tail")))
        assert_metadata(provenance, {"partial_tail": True, "boundary": complete_bytes + len(tail),
                                     "complete_offset": complete_bytes})
        assert source.read_bytes()[complete_bytes:] == tail
    finally:
        scene.close()


def test_compatible_fragment_refines_one_fact_without_recharging_it(project, tmp_path):
    scene = open_scene(project, tmp_path, source="missing")
    try:
        scene.native("Stop")
        first = scene.wait_query(family="usage-current")
        assert_measurements(first, literal("expected-usage-missing.json"))
        retained = committed_observations(scene.metrics / "observed.sqlite")
        with (scene.sources / "session.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(literal("compatible-usage-fragment.json")) + "\n")
        scene.native("Stop")
        refined = scene.wait_query(family="usage-current", predicate=lambda value: total_values(value) == [50])
        assert_measurements(refined, literal("expected-compatible-usage.json"))
        after = committed_observations(scene.metrics / "observed.sqlite")
        assert set(retained).issubset(set(after)), "F16_REFINEMENT_REWROTE_IMMUTABLE_OBSERVATIONS"
        assert len(after) > len(retained), "F16_REFINEMENT_HAS_NO_NEW_COMMITTED_OBSERVATION"
        scene.native("Stop")
        repeated = scene.query(family="usage-current")
        assert repeated["metrics"] == refined["metrics"]
    finally:
        scene.close()


def test_malformed_complete_record_keeps_valid_neighbors_and_partial_coverage(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    with source.open("a", encoding="utf-8") as stream:
        stream.write('{"type":"token_usage_record","payload":INVALID}\n')
        stream.write(json.dumps(literal("additional-usage-event.json")) + "\n")
    original = source.read_bytes()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        reply = scene.wait_query(family="usage-current", predicate=lambda value: total_values(value) == [20, 150])
        assert reply["coverage"]["state"] == "partial"
        assert "malformed_record" in json.dumps(reply["coverage"])
        assert source.read_bytes() == original
        assert not any(row["name"] == "total_tokens" and row["value"] == 0 for row in reply["metrics"])
    finally:
        scene.close()


def test_changed_captured_prefix_has_visible_coverage_and_preserved_history(project, tmp_path):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        before = scene.wait_query(family="usage-current")
        source = scene.sources / "session.jsonl"
        source.write_text(source.read_text().replace('"input_tokens": 120', '"input_tokens": 121'))
        scene.native("Stop")
        after = scene.wait_query(family="usage-current", predicate=lambda reply: reply["coverage"]["state"] == "partial")
        assert "source_changed" in json.dumps(after["coverage"])
        assert before["provenance"]
        with sqlite3.connect(scene.metrics / "observed.sqlite") as db:
            data = [row[0] for row in db.execute("SELECT data FROM accounting_cycles")]
        assert data, "F16_CHANGED_SOURCE_ERASED_COMMITTED_HISTORY"
        # The old source hash remains persisted; a changed file does not rewrite it.
        old_hashes = [item["sha256"] for item in before["provenance"] if item.get("sha256")]
        assert old_hashes
        assert any(old in value for old in old_hashes for value in data)
    finally:
        scene.close()


def test_metrics_keep_survive_end_without_archiving_raw_bodies(project, tmp_path):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("UserPromptSubmit", prompt="PRIVATE_F16_PROMPT")
        source_bytes = (scene.sources / "session.jsonl").read_bytes()
        private = literal("private-tool-native.json")
        scene.native("PreToolUse", **{key: value for key, value in private.items() if key != "tool_response"})
        scene.native("PostToolUse", **private)
        scene.native("Stop")
        scene.wait_query(family="usage-current")
        scene.close()
        reply = scene.query(family="usage-current")
        assert total_values(reply) == [150]
        assert (scene.sources / "session.jsonl").read_bytes() == source_bytes
        markers = literal("private-markers.json")
        # Scan only the actual metrics/spool storage, not native owner's temporary inputs.
        for path in scene.metrics.rglob("*"):
            if path.is_file():
                payload = path.read_bytes()
                assert not any(marker.encode() in payload for marker in markers), path
    finally:
        scene.close()


def test_query_scope_does_not_leak_another_session_or_write_observations(project, tmp_path):
    from tests.accounting.test_agent_metrics_queries import query_scene, terminal_capture

    scene = query_scene(project, tmp_path)
    try:
        terminal_capture(scene, "SessionStart")
        terminal_capture(scene, "Stop")
        scene.wait_query(family="usage-current")
        terminal_capture(scene, "SessionEnd")
        before = committed_observations(scene.metrics / "observed.sqlite")
        missing = scene.query(family="usage-current", scope={
            "kind": "session", "id": "00000000-0000-4000-8000-000000000162"})
        assert missing["metrics"] == []
        assert missing["coverage"]["state"] == "unavailable"
        assert committed_observations(scene.metrics / "observed.sqlite") == before
        own = scene.query(family="usage-current", scope={"kind": "session", "id": SESSION})
        assert_measurements(own, literal("expected-usage-complete.json"))
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        if scene.started:
            terminal_capture(scene, "SessionEnd")


