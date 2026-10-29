"""Real public mutations and stored-only reports; current entry RED is explicit."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from tests.accounting.agent_metrics_support import (
    MetricsScene, assert_measurements, committed_observations, literal, replace_tokens,
)
from tests.accounting.test_agent_metrics_updated_contracts import observed_stop
from tests.accounting.test_agent_metrics_storage import arrange_historical_store


def prepared_scene(project, tmp_path, source="complete", *, timezone=None):
    scene = MetricsScene(project, tmp_path)
    if timezone is not None:
        scene.policy["accounting"]["timezone"] = timezone
        scene.policy_path.write_text(json.dumps(scene.policy))
    scene.source(source)
    if source == "complete":
        event = replace_tokens(literal("updated-main-call.json"), scene.tokens)
        with scene.transcript_path.open("a") as stream:
            stream.write(json.dumps(event) + "\n")
    scene.configure_metrics()
    for event in scene.definition["routes"]:
        if event != "Stop":
            scene.definition["routes"][event] = []
    scene.install(metrics=False)
    try:
        scene.native("SessionStart")
        observed_stop(scene, tmp_path / "initial-capture")
    except BaseException:
        scene.close()
        raise
    return scene


def public_packet(scene, operation):
    packet = literal("updated-public-packets.json")[operation]
    return replace_tokens(packet, {"@PROJECT@": scene.project["cfg"]["project"]})


def source_trace(scene, name):
    return replace_tokens(literal("raw-source-observation.json"),
                          {"@SOURCE@": scene.sources, "@LOG@": scene.parent / name})


def admitted(scene, packet, *, trace=None):
    actual = scene.cli(packet, trace_sources=trace)
    assert actual["reached"] == ["real-poise-module-entry"]
    if actual["exit_code"] != 0:
        refusal = json.loads(actual["stdout"])
        raise AssertionError("F16_UPDATED_PUBLIC_OPERATION_NOT_ADMITTED:" + packet["operation"]
                             + ":" + refusal["status"] + ":" + refusal["reason"])
    return json.loads(actual["stdout"])


def test_saved_normalized_targets_reclassify_without_raw_source(project, tmp_path):
    scene = prepared_scene(project, tmp_path)
    try:
        before = scene.query(family="aggregate-readouts", kind="derived")
        expected = literal("expected-single-session-summary.json")
        expected["main_tokens"]["value"] = 150
        expected["unclassified_tokens"]["value"] = 0
        assert_measurements(before, expected)
        base_usage = scene.query(family="usage-current")
        scene.transcript_path.unlink()
        scene.policy["analyses"]["purpose"] = replace_tokens(literal("updated-purpose-policy.json"), scene.tokens)
        scene.policy["analyses"]["purpose"]["exclude"] = ["**"]
        scene.policy_path.write_text(json.dumps(scene.policy))
        trace = source_trace(scene, "reclassification-source-opens.jsonl")
        result = admitted(scene, public_packet(scene, "reclassify"), trace=trace)
        assert result["status"] == "completed" and result["operation"] == "reclassify"
        assert result["classification_version"] == "purpose-14"
        assert result["changed"] == 1
        canonical_policy = json.dumps(scene.policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert result["policy_digest"] == hashlib.sha256(canonical_policy.encode()).hexdigest()
        expected["main_tokens"]["value"] = 0
        expected["auxiliary_tokens"]["value"] = 150
        after = scene.query(family="aggregate-readouts", kind="derived")
        assert_measurements(after, expected)
        assert scene.query(family="usage-current")["metrics"] == base_usage["metrics"]
        stored = committed_observations(scene.metrics / "observed.sqlite")
        again = admitted(scene, public_packet(scene, "reclassify"), trace=trace)
        assert again["changed"] == 0
        assert committed_observations(scene.metrics / "observed.sqlite") == stored
        scene.policy["analyses"]["purpose"]["unused_optional_setting"] = "ignored"
        scene.policy_path.write_text(json.dumps(scene.policy))
        extension = admitted(scene, public_packet(scene, "reclassify"), trace=trace)
        assert extension["changed"] == 0
        assert extension["policy_digest"] == again["policy_digest"]
        assert committed_observations(scene.metrics / "observed.sqlite") == stored
        assert not (scene.parent / "reclassification-source-opens.jsonl").exists()
        assert not scene.transcript_path.exists()
        # No transient raw command or prompt becomes the retained classification source.
        for path in scene.metrics.rglob("*"):
            if path.is_file():
                raw = path.read_bytes()
                assert b'PRIVATE_F16_ASSISTANT' not in raw
                assert b'"arguments"' not in raw
                assert b'"input": "{' not in raw
    finally:
        scene.close()


def test_changed_parser_requires_explicit_source_reprocess(project, tmp_path):
    scene = prepared_scene(project, tmp_path)
    try:
        original = scene.query(family="usage-current")
        sentinel = scene.sources / "adoption.jsonl"
        sentinel.write_text(json.dumps(literal("adoption-event.json")) + "\n")
        sentinel_bytes = sentinel.read_bytes()
        scene.add_source(literal("adoption-source.json"))
        adoption_before = scene.query(family="hook-adoption")
        added = literal("additional-usage-event.json")
        with scene.transcript_path.open("a") as stream:
            stream.write(json.dumps(added) + "\n")
        trace = source_trace(scene, "explicit-reprocess-source-opens.jsonl")
        read_request = literal("query.json")
        read_request["view"]["family"] = "usage-current"
        read = scene.cli(read_request, trace_sources=trace)
        assert read["exit_code"] == 0
        assert json.loads(read["stdout"])["metrics"] == original["metrics"]
        assert not (scene.parent / "explicit-reprocess-source-opens.jsonl").exists()
        scene.policy["collection"]["sources"][1]["parser_version"] = "invocations-2.5"
        scene.policy_path.write_text(json.dumps(scene.policy))
        result = admitted(scene, public_packet(scene, "reprocess"), trace=trace)
        assert result["status"] == "completed" and result["operation"] == "reprocess"
        assert result["selected_sources"] == ["codex-transcript"]
        query = scene.query(family="usage-current")
        totals = sorted(row["value"] for row in query["metrics"]
                        if row["name"] == "total_tokens" and row["availability"]["state"] == "known")
        assert totals == [20, 150]
        accesses = [json.loads(line) for line in (scene.parent / "explicit-reprocess-source-opens.jsonl").read_text().splitlines()]
        assert accesses and {row["path"] for row in accesses} == {str(scene.transcript_path)}
        assert sentinel.read_bytes() == sentinel_bytes
        assert scene.query(family="hook-adoption")["metrics"] == adoption_before["metrics"]
        stored = committed_observations(scene.metrics / "observed.sqlite")
        scene.transcript_path.unlink()
        missing = scene.cli(public_packet(scene, "reprocess"))
        assert missing["exit_code"] == 2
        failure = json.loads(missing["stdout"])
        assert failure["status"] == "unavailable" and failure["reason"] and failure["continuation"]
        assert committed_observations(scene.metrics / "observed.sqlite") == stored
        assert scene.query(family="usage-current")["metrics"] == query["metrics"]
    finally:
        scene.close()


def test_script_targets_survive_reclassification_without_command_body_archive(project, tmp_path):
    scene = prepared_scene(project, tmp_path, "purpose-script")
    try:
        expected = literal("expected-single-session-summary.json")
        expected["main_tokens"]["value"] = 150
        expected["unclassified_tokens"]["value"] = 0
        assert_measurements(scene.query(family="aggregate-readouts", kind="derived"), expected)
        scene.transcript_path.unlink()
        for path in scene.metrics.rglob("*"):
            if path.is_file():
                assert b"PRIVATE_F16_NORMALIZED_SCRIPT" not in path.read_bytes(), "F16_RAW_SCRIPT_ARCHIVE"
        scene.policy["analyses"]["purpose"] = replace_tokens(literal("updated-purpose-policy.json"), scene.tokens)
        scene.policy["analyses"]["purpose"]["exclude"] = ["**"]
        scene.policy_path.write_text(json.dumps(scene.policy))
        trace = source_trace(scene, "script-reclassification-raw-opens.jsonl")
        result = admitted(scene, public_packet(scene, "reclassify"), trace=trace)
        assert result["status"] == "completed" and result["changed"] == 1
        expected["main_tokens"]["value"] = 0
        expected["auxiliary_tokens"]["value"] = 150
        assert_measurements(scene.query(family="aggregate-readouts", kind="derived"), expected)
        assert not (scene.parent / "script-reclassification-raw-opens.jsonl").exists()
    finally:
        scene.close()


def test_purpose_report_preserves_measured_denominator(project, tmp_path):
    scene = prepared_scene(project, tmp_path, "purpose-report")
    try:
        ordinary = scene.query(family="aggregate-readouts", kind="derived")
        assert_measurements(ordinary, literal("expected-purpose-report-metrics.json"))
        before = committed_observations(scene.metrics / "observed.sqlite")
        scene.transcript_path.unlink()
        trace = source_trace(scene, "report-source-opens.jsonl")
        rendered = admitted(scene, public_packet(scene, "markdown"), trace=trace)
        assert rendered["status"] == "read_only"
        assert rendered["metrics"] == ordinary["metrics"]
        assert rendered["provenance"] == ordinary["provenance"]
        assert "rendered_markdown" in rendered, "F16_STORED_PURPOSE_MARKDOWN_MISSING"
        rows = []
        for line in rendered["rendered_markdown"].splitlines():
            if line.startswith("|"):
                cells = [part.strip() for part in line.strip("|").split("|")]
                if not all(set(cell) <= set("-: ") for cell in cells):
                    rows.append(cells)
        assert rows == literal("expected-purpose-markdown-cells.json")
        assert committed_observations(scene.metrics / "observed.sqlite") == before
        assert not (scene.parent / "report-source-opens.jsonl").exists()
    finally:
        scene.close()


def markdown_cells(text):
    result = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [part.strip() for part in line.strip("|").split("|")]
        if not all(set(cell) <= set("-: ") for cell in cells):
            result.append(cells)
    return result


def test_daily_purpose_uses_source_midnight_bounds_and_deduplicated_cost(project, tmp_path):
    scene = prepared_scene(project, tmp_path, "purpose-midnight", timezone="Asia/Yekaterinburg")
    try:
        snapshot = committed_observations(scene.metrics / "observed.sqlite")
        packet = public_packet(scene, "daily")
        packet["view"]["format"] = "json"
        packet["period"].update(**{"from": "2026-10-27T23:59:59+05:00",
                                   "to": "2026-10-28T00:00:01+05:00"})
        scene.transcript_path.unlink()
        trace = source_trace(scene, "daily-report-raw-opens.jsonl")
        result = admitted(scene, packet, trace=trace)
        assert result["status"] == "read_only"
        fields = ("known_tokens", "main_tokens", "auxiliary_tokens", "unclassified_tokens", "unknown_usage")
        days = {}
        for metric in result["metrics"]:
            context = metric["context"]
            if context.get("scope") != "day" or metric["name"] not in fields:
                continue
            assert context["timezone"] == "Asia/Yekaterinburg"
            assert metric["availability"]["state"] == "known"
            assert type(metric["value"]) is int
            day = days.setdefault(context["date"], {"date": context["date"]})
            assert metric["name"] not in day
            day[metric["name"]] = metric["value"]
        assert [days[key] for key in sorted(days)] == literal("expected-purpose-midnight.json")
        assert sum(day["known_tokens"] for day in days.values()) == 360
        assert committed_observations(scene.metrics / "observed.sqlite") == snapshot
        assert not (scene.parent / "daily-report-raw-opens.jsonl").exists()
    finally:
        scene.close()


def test_daily_purpose_formats_millions_without_rounding_stored_cost(project, tmp_path):
    scene = prepared_scene(project, tmp_path, "purpose-large", timezone="Asia/Yekaterinburg")
    try:
        before = committed_observations(scene.metrics / "observed.sqlite")
        scene.transcript_path.unlink()
        result = admitted(scene, public_packet(scene, "daily"),
                          trace=source_trace(scene, "million-report-raw-opens.jsonl"))
        assert result["status"] == "read_only"
        assert markdown_cells(result["rendered_markdown"]) == literal("expected-purpose-million-table.json")
        known = [row["value"] for row in result["metrics"] if row["name"] == "known_tokens"
                 and row["context"].get("scope") == "day"]
        assert known == [2500000000]
        assert all(type(value) is int for value in known)
        assert committed_observations(scene.metrics / "observed.sqlite") == before
        assert not (scene.parent / "million-report-raw-opens.jsonl").exists()
    finally:
        scene.close()


def test_zero_known_spend_has_unavailable_percentage(project, tmp_path):
    scene = prepared_scene(project, tmp_path, "purpose-zero", timezone="Asia/Yekaterinburg")
    try:
        result = admitted(scene, public_packet(scene, "daily"))
        rows = markdown_cells(result["rendered_markdown"])
        assert rows[1][-3:] == ["—", "—", "—"]
        assert rows[-1][-3:] == ["—", "—", "—"]
        assert not any("0,00" in value or "0.00" in value for row in rows[1:] for value in row[-3:])
    finally:
        scene.close()


def test_stored_report_reads_without_source_or_target_analysis_policy(project, tmp_path):
    scene = prepared_scene(project, tmp_path)
    try:
        before = scene.query(family="aggregate-readouts", kind="derived")
        snapshot = committed_observations(scene.metrics / "observed.sqlite")
        scene.transcript_path.unlink()
        scene.policy["analyses"].pop("purpose", None)
        scene.policy_path.write_text(json.dumps(scene.policy))
        request = literal("query.json")
        request["view"].update(kind="derived", family="aggregate-readouts")
        observed = scene.cli(request, trace_sources=source_trace(scene, "stored-report-raw-opens.jsonl"))
        assert observed["exit_code"] == 0
        after = json.loads(observed["stdout"])
        assert after["metrics"] == before["metrics"]
        assert after["provenance"] == before["provenance"]
        assert committed_observations(scene.metrics / "observed.sqlite") == snapshot
        assert not (scene.parent / "stored-report-raw-opens.jsonl").exists()
    finally:
        scene.close()


def test_historical_usage_without_action_evidence_stays_unclassified(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    historical = arrange_historical_store(scene)
    before = committed_observations(scene.metrics / "observed.sqlite")
    scene.policy["analyses"]["purpose"] = replace_tokens(literal("updated-purpose-policy.json"), scene.tokens)
    scene.policy_path.write_text(json.dumps(scene.policy))
    result = admitted(scene, public_packet(scene, "reclassify"),
                      trace=source_trace(scene, "historical-raw-opens.jsonl"))
    assert result["status"] == "partial"
    assert result["changed"] == 0 and result["unavailable_evidence"] == 1
    assert result["continuation"]
    query = scene.query(family="aggregate-readouts", kind="derived",
                        scope={"kind": "project", "id": scene.project["cfg"]["project"]})
    assert_measurements(query, literal("expected-historical-purpose-unavailable.json"))
    with sqlite3.connect(scene.metrics / "observed.sqlite") as db:
        old = db.execute("SELECT id,project,session_id,started_at,ended_at,data "
                         "FROM accounting_cycles WHERE id=?", (historical["cycle_id"],)).fetchall()
        usage = db.execute("SELECT data FROM accounting_usage WHERE id=?", (historical["usage_id"],)).fetchone()
    assert old == before
    assert json.loads(usage[0]) == historical["usage_record"]
    assert not (scene.parent / "historical-raw-opens.jsonl").exists()


@pytest.mark.parametrize("case", literal("updated-invalid-purpose-policies.json"), ids=lambda row: row["id"])
def test_malformed_target_policy_preserves_previous_decisions(project, tmp_path, case):
    scene = prepared_scene(project, tmp_path)
    try:
        before = committed_observations(scene.metrics / "observed.sqlite")
        purpose = replace_tokens(literal("updated-purpose-policy.json"), scene.tokens)
        purpose.update(case.get("patch", {}))
        if "remove" in case:
            purpose.pop(case["remove"])
        scene.policy["analyses"]["purpose"] = purpose
        scene.policy_path.write_text(json.dumps(scene.policy))
        actual = scene.cli(public_packet(scene, "reclassify"))
        assert actual["exit_code"] == 2
        result = json.loads(actual["stdout"])
        assert result["status"] == "rejected" and result["continuation"]
        if "purpose" not in result["reason"]:
            raise AssertionError("F16_TARGET_POLICY_VALIDATION_NOT_REACHED:" + case["id"])
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        scene.close()


@pytest.mark.parametrize("case", literal("updated-invalid-public-cases.json"), ids=lambda row: row["id"])
def test_invalid_analysis_mutation_is_specific_and_preserves_stored_data(project, tmp_path, case):
    scene = prepared_scene(project, tmp_path)
    try:
        before = committed_observations(scene.metrics / "observed.sqlite")
        request = public_packet(scene, case["operation"])
        request.pop("sources", None)
        request.update({key: deepcopy(value) for key, value in case.items()
                        if key not in ("id", "reason_field")})
        actual = scene.cli(request)
        assert actual["exit_code"] == 2
        result = json.loads(actual["stdout"])
        assert result["status"] == "rejected" and result["continuation"]
        if case["reason_field"] not in result["reason"]:
            raise AssertionError("F16_ANALYSIS_DENIAL_DID_NOT_REACH_OWNED_INPUT:"
                                 + case["id"] + ":" + result["reason"])
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        scene.close()


def test_paired_git_result_targets_survive_source_retirement(project, tmp_path):
    scene = prepared_scene(project, tmp_path, "purpose-git-result")
    try:
        expected = literal("expected-purpose-git-result.json")
        assert_measurements(scene.query(family="aggregate-readouts", kind="derived"), expected)
        usage = scene.query(family="usage-current")
        scene.transcript_path.unlink()
        scene.policy["analyses"]["purpose"] = replace_tokens(literal("updated-purpose-policy.json"), scene.tokens)
        scene.policy["analyses"]["purpose"]["exclude"] = ["**"]
        scene.policy_path.write_text(json.dumps(scene.policy))
        trace = source_trace(scene, "git-reclassification-source-opens.jsonl")
        result = admitted(scene, public_packet(scene, "reclassify"), trace=trace)
        assert result["status"] == "completed" and result["changed"] == 1
        expected["main_tokens"]["value"] = 0
        expected["auxiliary_tokens"] = {
            "value": 120, "unit": "tokens", "kind": "derived", "state": "known",
        }
        assert_measurements(scene.query(family="aggregate-readouts", kind="derived"), expected)
        assert scene.query(family="usage-current")["metrics"] == usage["metrics"]
        assert not (scene.parent / "git-reclassification-source-opens.jsonl").exists()
        for path in scene.metrics.rglob("*"):
            if path.is_file():
                assert b"Author: Fixture" not in path.read_bytes(), "F16_RAW_GIT_OUTPUT_ARCHIVE"
    finally:
        scene.close()


def test_reclassification_without_target_policy_preserves_decisions(project, tmp_path):
    scene = prepared_scene(project, tmp_path)
    try:
        before = committed_observations(scene.metrics / "observed.sqlite")
        scene.policy["analyses"].pop("purpose", None)
        scene.policy_path.write_text(json.dumps(scene.policy))
        actual = scene.cli(public_packet(scene, "reclassify"))
        assert actual["exit_code"] == 2
        result = json.loads(actual["stdout"])
        assert result["status"] == "rejected" and result["continuation"]
        if "purpose" not in result["reason"]:
            raise AssertionError("F16_MISSING_TARGET_POLICY_VALIDATION_NOT_REACHED")
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        scene.close()
