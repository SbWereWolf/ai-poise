"""F16 records supplied observations; external consumers own their analysis."""
from dataclasses import asdict
import hashlib
import json

from tests.accounting.agent_metrics_support import (
    MetricsScene, assert_measurements, committed_observations, literal, replace_tokens,
)


def source_records(scene):
    from poise.infrastructure.agent_metrics.sources import SourceRecord

    raw = scene.transcript_path.read_bytes()
    records, offset = [], 0
    for line in raw.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        records.append(SourceRecord(offset, hashlib.sha256(body).hexdigest(),
                                    json.loads(body), len(body)))
        offset += len(line)
    return records


def assert_supplied_usage(document):
    actual = {
        row["name"]: {"value": row["value"], "unit": row["unit"],
                      "state": row["availability"]["state"], "kind": row["kind"]}
        for row in document["facts"] if row["family"] == "usage-current"
    }
    assert actual == literal("expected-usage-complete.json")


def test_recording_configuration_does_not_require_analytical_options(project, tmp_path):
    from poise.common import PoiseError

    scene = MetricsScene(project, tmp_path)
    scene.source()
    scene.policy.pop("analyses", None)
    scene.policy_path.write_text(json.dumps(scene.policy))
    try:
        try:
            scene.install()
        except PoiseError as error:
            scene.record({"kind": "recording-policy-admission", "error": str(error)})
            assert "analyses" not in str(error), "F16_RECORDING_REQUIRES_ANALYTICAL_CONFIGURATION"
            raise
        scene.native("SessionStart")
        scene.native("Stop")
        assert_measurements(scene.wait_query(family="usage-current"), literal("expected-usage-complete.json"))
    finally:
        scene.close()


def test_recording_normalization_does_not_call_purpose_classifier(project, tmp_path, monkeypatch):
    from poise.modules.accounting.metrics import normalization

    scene = MetricsScene(project, tmp_path)
    scene.source()
    calls = []

    def analytical_call(*args, **kwargs):
        calls.append("purpose")
        raise AssertionError("F16_CLASSIFIER_EXECUTED_DURING_RECORDING")

    # Removing the analytical dependency is valid; the real recording producer
    # must still return the independent supplied values in either case.
    if hasattr(normalization, "classify_normalized"):
        monkeypatch.setattr(normalization, "classify_normalized", analytical_call)
    request = replace_tokens(literal("updated-normalization-request.json"), scene.tokens)
    actual = normalization.response_observations(
        source_records(scene), request["scope"], request["source"],
        request["coverage"], request["processed_at"],
    )
    assert_supplied_usage(actual)
    assert calls == []
    for metadata in actual["metadata"]:
        assert "classification_reason" not in metadata["fields"]
        assert "classification_version" not in metadata["fields"]


def test_recording_capture_processor_does_not_compute_exposure(project, tmp_path, monkeypatch):
    from poise.infrastructure.agent_metrics import runtime
    from poise.infrastructure.agent_metrics.configuration import AgentMetricsPolicy
    from poise.infrastructure.agent_metrics.sources import capture_source
    from poise.infrastructure.clock import SystemClock

    scene = MetricsScene(project, tmp_path)
    scene.source()
    scene.policy["collection"]["max_records"] = 1000
    policy = AgentMetricsPolicy.parse(scene.policy)
    service = runtime.metric_tools(project["cfg"]["project"], policy, project["app"])
    clock = SystemClock()
    processor = runtime.MetricCaptureProcessor(service.repository, clock)
    source = next(row for row in policy.collection["sources"] if row["id"] == "codex-transcript")
    descriptor = capture_source(scene.transcript_path, source)
    document = {
        "kind": "source-capture", "descriptor": descriptor, "policy": scene.policy,
        "policy_digest": policy.digest,
        "scope": {"project": project["cfg"]["project"], "session": scene.session},
        "task_association": {"state": "not_applicable", "reason": "No Task is connected"},
        "turn_id": "turn-f16",
    }
    job = runtime.envelope(document, scene.session, asdict(clock.observe()))
    calls = []

    def analytical_call(*args, **kwargs):
        calls.append("exposure")
        raise AssertionError("F16_EXPOSURE_COMPUTED_DURING_RECORDING")

    if hasattr(runtime, "exposure_observations"):
        monkeypatch.setattr(runtime, "exposure_observations", analytical_call)
    prepared = processor.prepare(job)
    assert len(prepared) == 1
    assert_supplied_usage(prepared[0].data["agent_metrics"])
    assert calls == []


def test_native_recording_persists_observations_without_computed_analytics(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    original = source.read_bytes()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        recorded = scene.wait_query(family="usage-current")
        assert_measurements(recorded, literal("expected-usage-complete.json"))
        rows = committed_observations(scene.metrics / "observed.sqlite")
        documents = [json.loads(row[-1]).get("envelope", {}).get("agent_metrics") for row in rows]
        facts = [document for document in documents if document and document.get("kind") == "facts"]
        assert facts, "F16_RECORDING_NOT_COMMITTED"
        excluded = {"exposure", "history", "skill-selection", "skill-references", "skill-content",
                    "aggregate-readouts", "readiness-documented-only", "legacy-turn-usage"}
        families = {item["family"] for document in facts for item in document["facts"]}
        assert not families.intersection(excluded), "F16_COMPUTED_ANALYTICS_PERSISTED_DURING_RECORDING"
        for document in facts:
            for item in document["metadata"]:
                assert "classification_version" not in item["fields"], "F16_CLASSIFICATION_PERSISTED"
                assert "classification_reason" not in item["fields"], "F16_CLASSIFICATION_PERSISTED"
        assert source.read_bytes() == original
    finally:
        scene.close()
