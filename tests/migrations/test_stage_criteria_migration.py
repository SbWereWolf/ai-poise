"""Independent new-wire/CLI contracts, not a replacement migration engine."""

from copy import deepcopy
import io
import json
from pathlib import Path

import pytest

from poise.application.task_process_migration import TaskProcessMigrationCommands
from poise.interfaces import task_process_migration
from poise.modules.foundation.errors import PoiseError


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/stage-criteria"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordingPort:
    """Observe the real application parser's dispatch without filesystem effects."""

    def __init__(self, response):
        self.requests = []
        self.response = response

    def migrate(self, request):
        self.requests.append(request)
        return deepcopy(self.response)


def test_new_single_apply_request_reaches_existing_migration_port():
    packet = fixture("migration-request.json")
    expected = {"status": "migrated", "request_id": "migrate-fixture-criteria"}
    port = RecordingPort(expected)
    observed = TaskProcessMigrationCommands(port).migrate(packet)
    assert observed == expected
    assert len(port.requests) == 1
    assert port.requests[0].schema == "task-stage-criteria-migration-1"
    assert port.requests[0].request_id == "migrate-fixture-criteria"
    assert packet == fixture("migration-request.json")


@pytest.mark.parametrize("fault", [
    "missing_inventory", "unknown_field", "empty_mapping", "duplicate_resource", "empty_authorization",
])
def test_invalid_mapping_is_rejected_before_port_dispatch(fault):
    packet = fixture("migration-request.json")
    if fault == "missing_inventory":
        del packet["expected_inventory_digest"]
    elif fault == "unknown_field":
        packet["phase"] = "prepare"
    elif fault == "empty_mapping":
        packet["mapping"] = []
    elif fault == "duplicate_resource":
        packet["mapping"].append(deepcopy(packet["mapping"][0]))
    else:
        packet["authorization"] = ""
    port = RecordingPort({})
    with pytest.raises(PoiseError):
        TaskProcessMigrationCommands(port).migrate(packet)
    assert port.requests == []


def test_cli_partial_publication_returns_exact_recovery_envelope_and_exit_two(monkeypatch, tmp_path):
    expected = fixture("migration-recovery-response.json")
    port = RecordingPort(expected)
    monkeypatch.setattr(task_process_migration, "task_process_migration_tools", lambda path: port)
    output = io.StringIO()
    code = task_process_migration.execute(
        tmp_path / "project.json",
        io.BytesIO(json.dumps(fixture("migration-request.json")).encode("utf-8")),
        output,
    )
    assert json.loads(output.getvalue()) == expected
    assert code == 2
    assert len(port.requests) == 1
    assert "phase" not in json.loads(output.getvalue())
    assert "prepared" not in output.getvalue()
