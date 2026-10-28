"""Known retained input transition and refusal to take over a foreign store."""
import json
import sqlite3
from copy import deepcopy

import pytest

from tests.accounting.agent_metrics_support import FIXTURES, MetricsScene, committed_observations, literal
from tests.accounting.test_agent_metrics_pipeline import total_values


def arrange_historical_store(scene):
    """Literal old SQL/data, no production schema/model/factory executes here."""
    scene.metrics.mkdir()
    data = literal("legacy-store-data.json")
    with sqlite3.connect(scene.metrics / "observed.sqlite") as database:
        database.executescript((FIXTURES / "legacy-optional-v13.sql").read_text())
        envelope = data["envelope"]
        database.execute("INSERT INTO accounting_cycles "
                         "(id,task_id,project,session_id,started_at,ended_at,data) VALUES(?,?,?,?,?,?,?)",
                         (data["cycle_id"], None, scene.project["cfg"]["project"], data["session"],
                          envelope["started"]["audit_utc"], envelope["finished"]["audit_utc"],
                          json.dumps({"kind": "telemetry_envelope", "envelope": envelope})))
        sample = data["usage_record"]["sample"]
        database.execute("INSERT INTO accounting_usage "
                         "(id,task_id,project,source,stream,sequence,occurred_at,data) VALUES(?,?,?,?,?,?,?,?)",
                         (data["usage_id"], None, scene.project["cfg"]["project"], sample["source"],
                          sample["stream"], sample["sequence"], sample["occurred_at"],
                          json.dumps(data["usage_record"])))
    return data


def test_literal_historical_input_is_valid_for_the_existing_optional_owner(project, tmp_path):
    """Positive fixture control on current code, never new-feature acceptance."""
    from poise.application.telemetry import TelemetryEnvelope
    from poise.infrastructure.telemetry import TelemetryDatabase
    from poise.infrastructure.sqlite.accounting import SqliteAccounting
    from poise.modules.accounting.domain import MetricPolicy

    scene = MetricsScene(project, tmp_path)
    data = arrange_historical_store(scene)
    database = TelemetryDatabase(scene.metrics / "observed.sqlite", scene.metrics / "observed.lock", .05, .005)
    owner = SqliteAccounting(database, project["cfg"]["project"], MetricPolicy.parse(scene.policy["accounting"]))
    before = committed_observations(scene.metrics / "observed.sqlite")
    assert owner.accept_telemetry(TelemetryEnvelope(data["cycle_id"], data["envelope"])) is False
    assert committed_observations(scene.metrics / "observed.sqlite") == before
    snapshot = owner.snapshot_nonblocking()
    assert snapshot["tasks"] == {}
    assert len(snapshot["telemetry"]) == 1 and len(snapshot["usage"]) == 1
    assert json.loads(snapshot["usage"][0]["data"]) == data["usage_record"]


def test_real_immutable_receiver_refuses_changed_content_under_the_same_identity(project, tmp_path):
    from poise.application.telemetry import TelemetryEnvelope
    from poise.common import PoiseError
    from poise.infrastructure.telemetry import TelemetryDatabase
    from poise.infrastructure.sqlite.accounting import SqliteAccounting
    from poise.modules.accounting.domain import MetricPolicy

    scene = MetricsScene(project, tmp_path)
    data = arrange_historical_store(scene)
    database = TelemetryDatabase(scene.metrics / "observed.sqlite", scene.metrics / "observed.lock", .05, .005)
    owner = SqliteAccounting(database, project["cfg"]["project"], MetricPolicy.parse(scene.policy["accounting"]))
    before = committed_observations(scene.metrics / "observed.sqlite")
    changed = deepcopy(data["envelope"])
    changed["operation"] = "f16-conflicting-operation"
    with pytest.raises(PoiseError, match="Telemetry identity has conflicting content"):
        owner.accept_telemetry(TelemetryEnvelope(data["cycle_id"], changed))
    assert committed_observations(scene.metrics / "observed.sqlite") == before
    snapshot = owner.snapshot_nonblocking()
    assert len(snapshot["telemetry"]) == 1 and len(snapshot["usage"]) == 1
    assert json.loads(snapshot["usage"][0]["data"]) == data["usage_record"]


def test_known_historical_optional_facts_survive_actual_owner_transition(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    data = arrange_historical_store(scene)
    before = committed_observations(scene.metrics / "observed.sqlite")
    scene.source()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        scene.wait_query(family="usage-current")
        reply = scene.query(family="usage-current", scope={"kind": "project", "id": project["cfg"]["project"]})
        assert total_values(reply) == data["expected_project_totals"]
        with sqlite3.connect(scene.metrics / "observed.sqlite") as database:
            retained = database.execute("SELECT id,project,session_id,started_at,ended_at,data "
                                        "FROM accounting_cycles WHERE id=?", (data["cycle_id"],)).fetchall()
            usage = database.execute("SELECT id,source,stream,sequence,occurred_at,data "
                                     "FROM accounting_usage WHERE id=?", (data["usage_id"],)).fetchall()
        assert retained == before, "F16_TRANSITION_REWROTE_OR_DROPPED_RETAINED_ENVELOPE"
        assert len(usage) == 1
        assert json.loads(usage[0][5]) == data["usage_record"]
        original = reply["metrics"]
        code, recovery = scene.operation("replay")
        assert code == 0 and recovery["failed"] == recovery["corrupt"] == 0
        assert scene.query(family="usage-current", scope={
            "kind": "project", "id": project["cfg"]["project"]})["metrics"] == original
    finally:
        scene.close()


def test_foreign_same_version_sqlite_is_refused_without_taking_ownership(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.metrics.mkdir()
    path = scene.metrics / "observed.sqlite"
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE foreign_owner(id TEXT PRIMARY KEY,value TEXT NOT NULL)")
        database.execute("INSERT INTO foreign_owner(id,value) VALUES(?,?)", ("sentinel", "foreign-f16-value"))
        database.execute("PRAGMA user_version=13")
    before = path.read_bytes()
    observed = scene.cli()
    if observed["exit_code"] == 2 and "invalid choice: 'telemetry'" in observed["stderr"]:
        raise AssertionError("F16_METRICS_CLI_ENTRY_MISSING: actual argparse rejected telemetry")
    assert observed["exit_code"] == 2
    result = json.loads(observed["stdout"])
    assert result["status"] == "rejected"
    assert result["reason"] and result["continuation"]
    assert path.read_bytes() == before
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT id,value FROM foreign_owner ORDER BY id").fetchall() == [
            ("sentinel", "foreign-f16-value")]
        assert database.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == [
            ("foreign_owner",)]
