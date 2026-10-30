"""File/diff/check sources keep independent observed byte, line and check facts."""
import json
from datetime import datetime, timezone

from tests.accounting.agent_metrics_support import MetricsScene, assert_measurements, assert_metadata, context_view, literal


def test_recorded_read_diff_and_check_keep_exact_values_and_attribution(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    target = project["app"] / "src/example.py"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"two\nthree\n")
    source = scene.source("files-checks")
    original = source.read_bytes()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        result = scene.wait_query(family="files-skills-checks", predicate=lambda value: any(
            row["name"] == "lines_added" and row["value"] == 2 for row in value["metrics"]))
        assert_measurements(result, literal("expected-files-checks.json"))
        assert_metadata(result, literal("expected-files-checks-metadata.json"))
        # The source records reading four bytes before the edit. Current file size
        # is ten bytes and cannot replace that historical output observation.
        assert target.read_bytes() == b"two\nthree\n"
        assert source.read_bytes() == original
        scene.native("Stop")
        assert scene.query(family="files-skills-checks")["metrics"] == result["metrics"]
    finally:
        scene.close()


def test_turn_counts_aggregate_only_the_recognized_read_write_and_check_events(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    target = project["app"] / "src/example.py"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"two\nthree\n")
    scene.source("files-checks")
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        expected = literal("expected-turn-counts.json")
        scene.wait_measurements("turn-counts", expected)
        scene.native("Stop")
        assert_measurements(scene.query(family="turn-counts"), expected)
    finally:
        scene.close()


def test_individual_tool_fields_stay_linked_to_their_actual_call_and_source_offsets(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    target = project["app"] / "src/example.py"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"two\nthree\n")
    source = scene.source("files-checks")
    raw = source.read_bytes()
    lines = raw.splitlines(keepends=True)
    offset, offsets = 0, {}
    for line in lines:
        event = json.loads(line)
        if event["payload"].get("call_id") == "f16-read-call":
            offsets[event["payload"]["type"]] = offset
        offset += len(line)
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        all_tools = scene.wait_query(family="tools", predicate=lambda value: any(
            row.get("context", {}).get("call_id") == "f16-read-call" and row["name"] == "output_bytes"
            for row in value["metrics"]))
        read = context_view(all_tools, {"call_id": "f16-read-call"})
        assert_measurements(read, literal("expected-read-tool.json"))
        wanted = literal("expected-read-tool-context.json")
        wanted.update(input_offset=offsets["custom_tool_call"], output_offset=offsets["custom_tool_call_output"])
        for key, value in wanted.items():
            assert any(row["context"].get(key) == value for row in read["metrics"]), (key, value)
        assert source.read_bytes() == raw
    finally:
        scene.close()


def test_reported_tool_duration_status_and_times_keep_only_metric_provenance(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source("timed-tool")
    raw = source.read_bytes()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        reply = scene.wait_measurements("tools", literal("expected-timed-tool.json"))
        tool = context_view(reply, {"call_id": "f16-timed-call"})
        for key, expected in literal("expected-timed-tool-context.json").items():
            assert any(row["context"].get(key) == expected for row in tool["metrics"]), (key, expected)
        for key, expected in (("started_at", datetime(2026, 10, 29, 0, 0, 0, 500000, tzinfo=timezone.utc)),
                              ("completed_at", datetime(2026, 10, 29, 0, 0, 0, 750000, tzinfo=timezone.utc))):
            values = [row["context"][key] for row in tool["metrics"] if key in row["context"]]
            assert values and all(datetime.fromisoformat(item.replace("Z", "+00:00")) == expected for item in values)
        scene.close()
        scene.wait_status(lambda code, value: code == 0 and value["delivery"]["pending"] == 0)
        for path in scene.metrics.rglob("*"):
            if path.is_file():
                contents = path.read_bytes()
                assert b"PRIVATE_F16_INPUT" not in contents and b"PRIVATE_F16_OUTPUT" not in contents
        assert source.read_bytes() == raw
    finally:
        scene.close()


def test_recognized_bounded_read_preserves_the_actual_requested_line_interval(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    target = project["app"] / "docs/sample.txt"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"A\nB\nC\n")
    scene.source("bounded-read")
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        reply = scene.wait_query(family="files-skills-checks", predicate=lambda value: any(
            row.get("context", {}).get("call_id") == "f16-range-call" for row in value["metrics"]))
        selected = context_view(reply, {"call_id": "f16-range-call"})
        expected = {"path": "docs/sample.txt", "reader": "sed", "range_start": 1, "range_end": 2}
        for key, value in expected.items():
            assert any(row["context"].get(key) == value for row in selected["metrics"]), (key, value)
        assert_measurements(selected, literal("expected-bounded-read.json"))
        assert target.read_bytes() == b"A\nB\nC\n"
    finally:
        scene.close()
