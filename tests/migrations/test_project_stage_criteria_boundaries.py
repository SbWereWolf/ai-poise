"""Public migration boundary: malformed owner input and source provenance.

Use disposable historical persistence and real CLI/owner writes. Literal type,
status and state expectations do not consume the production validation logic.
"""
import json

import pytest

from tests.migrations.project_criteria_helpers import (
    historical_project_owned, observe, project_receipts, run_migration,
)
from tests.migrations.test_project_stage_criteria_migration import (
    assert_durable_receipt_pending, assert_success, interrupt_first_workflow,
)


def assert_refusal(code, response, stderr, status, context):
    assert code == 2, (response, stderr)
    assert response["status"] == status
    assert isinstance(response["reason"], str) and response["reason"]
    assert any(word in response["reason"].lower() for word in context), response
    assert "Traceback" not in stderr


@pytest.mark.parametrize("invalid", [None, []], ids=["null", "array"])
def test_setup_receipt_nonobject_result_refuses_without_effects(project, invalid):
    case = historical_project_owned(project)
    document = json.loads(case["setup_receipt"].read_text())
    document["result"] = invalid
    case["setup_receipt"].write_text(json.dumps(document))
    before = observe(case)
    code, response, stderr = run_migration(case, fresh=True)
    assert observe(case) == before
    assert project_receipts(case) == []
    assert_refusal(code, response, stderr, "rejected", ["receipt"])


@pytest.mark.parametrize("invalid", [None, []], ids=["null", "array"])
def test_pending_nonobject_before_refuses_without_effects(project, invalid):
    case = historical_project_owned(project)
    # Isolate a nonobject before field in the native pending container.
    pending = {"schema": "project-config-pending-1", "request_id": "foreign",
               "request_digest": "a" * 64, "before": invalid,
               "after": {"config": "b" * 64, "workflows": {}}}
    path = project["root"] / "operations/foreign.json.pending"
    path.write_text(json.dumps(pending))
    before = observe(case)
    code, response, stderr = run_migration(case, fresh=True)
    assert observe(case) == before
    assert project_receipts(case) == []
    assert_refusal(code, response, stderr, "rejected", ["pending"])


def test_selected_settings_missing_root_refuses_without_effects(project):
    case = historical_project_owned(project)
    settings = json.loads(case["settings"].read_text())
    missing = case["settings"].parent / "missing-installation"
    assert not missing.exists()
    settings["root"] = str(missing)
    case["settings"].write_text(json.dumps(settings))
    before = observe(case)
    code, response, stderr = run_migration(case, fresh=True)
    assert observe(case) == before
    assert not missing.exists()
    assert project_receipts(case) == []
    assert_refusal(code, response, stderr, "rejected", ["settings", "root"])


@pytest.mark.parametrize("invalid", [None, []], ids=["null", "array"])
def test_durable_receipt_nonobject_result_preserves_recovery_envelope(project, monkeypatch, invalid):
    case = historical_project_owned(project)
    before = observe(case)
    from poise.infrastructure import project_config
    original = project_config.atomic_write

    def interrupt(path, content, mode):
        original(path, content, mode)
        if json.loads(content).get("schema") == "project-config-receipt-1":
            raise OSError("Fixture interruption after durable receipt.")

    with monkeypatch.context() as scoped:
        scoped.setattr(project_config, "atomic_write", interrupt)
        code, partial, stderr = run_migration(case, patch=scoped)
    assert_refusal(code, partial, stderr, "recovery_required", ["fixture interruption"])
    durable, pending = assert_durable_receipt_pending(case, before)
    receipt_path, _ = durable
    receipt = json.loads(receipt_path.read_text())
    receipt["result"] = invalid
    receipt_path.write_text(json.dumps(receipt))
    altered = observe(case)
    code, response, stderr = run_migration(case, fresh=True)
    assert observe(case) == altered
    assert pending.exists()
    assert_refusal(code, response, stderr, "recovery_required", ["receipt"])
    assert response["request_id"] == "project-owned-stage-criteria"
    assert response["receipt"] == partial["receipt"]
    # Pending retirement and final Task transaction were never verified.
    assert response["resources"] == []
    assert observe(case)["task"]["tasks"] == before["task"]["tasks"]


def test_foreign_history_schema_cannot_authorize_source_revision(project):
    case = historical_project_owned(project)
    receipt = json.loads(case["history_receipt"].read_text())
    assert receipt["result"]["revision"] == case["source_revision"]
    receipt["schema"] = "foreign-document-1"
    case["history_receipt"].write_text(json.dumps(receipt))
    before = observe(case)
    code, response, stderr = run_migration(case, fresh=True)
    assert_refusal(code, response, stderr, "rejected", ["receipt", "revision"])
    assert observe(case) == before
    assert project_receipts(case) == []


def test_selected_source_receipt_drift_before_intent_refuses(project, monkeypatch):
    case = historical_project_owned(project)
    from poise.infrastructure.task_workflow_migration import SqliteTaskWorkflowMigration
    original = SqliteTaskWorkflowMigration.__init__
    at_drift = []

    def change_source_receipt():
        document = json.loads(case["history_receipt"].read_text())
        assert document["result"]["revision"] == case["source_revision"]
        setup = json.loads(case["setup_receipt"].read_text())
        assert setup["result"]["revision"] != case["source_revision"]
        document["result"]["revision"] = "0" * 64
        case["history_receipt"].write_text(json.dumps(document))
        at_drift.append(observe(case))

    def instrument(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.after_preflight = change_source_receipt

    with monkeypatch.context() as scoped:
        scoped.setattr(SqliteTaskWorkflowMigration, "__init__", instrument)
        code, response, stderr = run_migration(case, patch=scoped)
    assert len(at_drift) == 1
    assert_refusal(code, response, stderr, "rejected", ["receipt", "revision", "source", "owner"])
    assert observe(case) == at_drift[0]
    assert project_receipts(case) == []


def test_partial_recovery_rechecks_original_source_receipt(project, monkeypatch):
    case = historical_project_owned(project)
    interrupt_first_workflow(case, monkeypatch)
    document = json.loads(case["history_receipt"].read_text())
    document["result"]["revision"] = "0" * 64
    case["history_receipt"].write_text(json.dumps(document))
    altered = observe(case)
    code, response, stderr = run_migration(case, fresh=True)
    assert_refusal(code, response, stderr, "rejected", ["receipt", "revision", "source", "owner"])
    assert observe(case) == altered
    assert project_receipts(case) == []


def test_unrelated_history_does_not_change_selected_source_or_replay(project):
    case = historical_project_owned(project)
    document = json.loads(case["history_receipt"].read_text())
    document["request_id"] = "unrelated-project-update"
    document["result"]["config_path"] = str(case["bundle"].parent / "other/project.json")
    path = project["root"] / "operations/unrelated-history.json"
    document["result"]["receipt_path"] = str(path)
    path.write_text(json.dumps(document))
    before_bytes = path.read_bytes()
    code, response, stderr = run_migration(case, fresh=True)
    assert_success(case, code, response, stderr)
    assert path.read_bytes() == before_bytes
    # A later unrelated receipt edit must not invalidate the selected provenance.
    document["result"]["revision"] = "0" * 64
    path.write_text(json.dumps(document))
    after = observe(case)
    code, replay, stderr = run_migration(case, fresh=True)
    assert_success(case, code, replay, stderr)
    assert replay == {**response, "replayed": True}
    assert observe(case) == after
