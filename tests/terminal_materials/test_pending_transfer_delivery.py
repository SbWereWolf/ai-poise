"""Real prepared-transfer obligation and installed-tool continuation."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from transfer.helpers import enabled, export
from batch.helpers import request
from result_integration.helpers import integration_input
from .helpers import (absent, agree, arrange, declaration, delivery_status,
                      file_output, fresh, settle)
from .test_handoff_settlement import handoff_rows


def test_prepared_transfer_blocks_delivery_until_real_export_finishes(project, recovery_tool):
    enabled(project, recovery_tool)
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "customer-result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    assert tools.invoke(request("accept", {}))["status"] == "completed"
    assert root.is_dir()
    assert handoff_rows(tools) == []
    foreign, foreign_root, foreign_source, _ = arrange(
        project, identifier="FOREIGN-CATALOG-EXPORT", session="catalog-export-owner")
    foreign_record = deepcopy(foreign.runtime.task_queries.record("FOREIGN-CATALOG-EXPORT"))
    foreign_history = foreign.runtime.task_queries.history("FOREIGN-CATALOG-EXPORT")
    record = deepcopy(tools.runtime.task_queries.record("T1"))
    history = tools.runtime.task_queries.history("T1")
    request_id = "pending-transfer-guard"
    # Locate an input fault through the existing owner, never an expected result.
    directory = tools.runtime.transfer_tools.port._request_dir({
        "action": "export", "request_id": request_id,
    })
    obstruction = directory / "create-flow.json"
    obstruction.mkdir(parents=True)

    with pytest.raises(IsADirectoryError):
        export(tools, ids=["T1"], request_id=request_id, handoff=None)
    with tools.runtime.store.database.read_transaction() as database:
        saved = json.loads(database.execute(
            "SELECT data FROM transfer_requests WHERE actor = ? AND request_id = ?",
            (tools.runtime.session, request_id),
        ).fetchone()[0])
    assert saved["phase"] == "prepared"
    assert saved["task_ids"] == ["T1"]
    assert tools.runtime.transfer_repository.pending_consumers("T1") is True

    blocked = settle(fresh(project))

    assert blocked["status"] == "delivery_blocked"
    assert blocked["reason"] == "transfer_pending"
    assert root.is_dir() and source.read_bytes() == b"customer result\n"
    assert not destination.exists()
    assert tools.runtime.task_queries.record("T1") == record
    assert tools.runtime.task_queries.history("T1")[:len(history)] == history
    assert handoff_rows(tools) == []
    assert foreign.runtime.task_queries.record("FOREIGN-CATALOG-EXPORT") == foreign_record
    assert foreign_source.read_bytes() == b"customer result\n"

    obstruction.rmdir()
    exported = export(tools, ids=["T1"], request_id=request_id, handoff=None)
    assert exported["status"] == "exported"
    package = Path(exported["package_path"])
    assert package.is_file()
    assert tools.runtime.transfer_repository.pending_consumers("T1") is False
    assert tools.invoke(request("integrate", integration_input(project, commit)))["status"] == "integrated"
    assert delivery_status(fresh(project))["status"] == "delivery_complete"
    absent(root)
    assert destination.read_bytes() == b"customer result\n"
    assert package.is_file()
    assert handoff_rows(tools) == []
    assert foreign.runtime.task_queries.record("FOREIGN-CATALOG-EXPORT") == foreign_record
    assert foreign.runtime.task_queries.history("FOREIGN-CATALOG-EXPORT") == foreign_history
    assert foreign_root.is_dir() and foreign_source.read_bytes() == b"customer result\n"
