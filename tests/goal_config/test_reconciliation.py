import io
import json
import sqlite3
from pathlib import Path

import pytest

from poise.common import digest
from poise.composition import goal_config_tools
from poise.infrastructure.goal_config import PublicationPending
from poise.interfaces.goal_config import execute
from poise.modules.foundation.errors import PoiseError
from tests.goal_config.helpers import additions, request, settings


def _managed_editor(tmp_path):
    path, selection = settings(tmp_path)
    api = goal_config_tools(path)
    first = api.apply_batch(
        request("create", "writing", None, [], "create-managed", selection)
    )
    return path, api, first


def _external_valid_change(first):
    target = Path(first["config_path"])
    data = json.loads(target.read_text(encoding="utf-8"))
    data["stages"][0]["instruction"] = "Lawful external replacement."
    target.write_text(json.dumps(data), encoding="utf-8")
    return target, data, digest(data)


def _reconcile(first, live_revision, request_id="adopt-live"):
    return {
        "schema": "goal-config-reconcile-1",
        "request_id": request_id,
        "goal_type": "writing",
        "expected_managed_revision": first["revision"],
        "expected_live_revision": live_revision,
        "reason": "A separately authorized migration installed this validated process.",
        "authorization": "The operator authorized adoption of this exact live revision.",
    }


def test_exact_reconcile_adopts_valid_external_revision_and_next_update_succeeds(tmp_path):
    _, api, first = _managed_editor(tmp_path)
    target, _, live_revision = _external_valid_change(first)
    before = target.read_bytes()

    adopted = api.reconcile(_reconcile(first, live_revision))
    updated = api.apply_batch(
        request("update", "writing", live_revision, additions(), "ordinary-next-edit")
    )

    assert adopted["status"] == "reconciled"
    assert adopted["previous_revision"] == first["revision"]
    assert adopted["revision"] == live_revision
    assert adopted["config_unchanged"] is True
    assert before != target.read_bytes()
    assert updated["previous_revision"] == live_revision


def test_status_exposes_managed_and_validated_live_revision_without_rewrite(tmp_path):
    _, api, first = _managed_editor(tmp_path)
    target, _, live_revision = _external_valid_change(first)
    before = target.read_bytes()

    status = api.status({"schema": "goal-config-status-1", "goal_type": "writing"})

    assert status == {
        "status": "goal_config_status",
        "goal_type": "writing",
        "config_path": str(target),
        "managed_revision": first["revision"],
        "live_revision": live_revision,
        "aligned": False,
        "pending_request": None,
    }
    assert target.read_bytes() == before


def test_reconcile_rejects_wrong_managed_or_live_digest_without_mutation(tmp_path):
    _, api, first = _managed_editor(tmp_path)
    target, _, live_revision = _external_valid_change(first)
    before = target.read_bytes()

    for field in ("expected_managed_revision", "expected_live_revision"):
        packet = _reconcile(first, live_revision, f"wrong-{field}")
        packet[field] = "0" * 64
        with pytest.raises(PoiseError, match="revision"):
            api.reconcile(packet)

    assert target.read_bytes() == before
    assert api.status({"schema": "goal-config-status-1", "goal_type": "writing"})[
        "managed_revision"
    ] == first["revision"]


def test_malformed_external_process_cannot_be_statused_or_reconciled(tmp_path):
    _, api, first = _managed_editor(tmp_path)
    target = Path(first["config_path"])
    target.write_text('{"goal_type":"writing"}', encoding="utf-8")
    malformed = digest({"goal_type": "writing"})

    with pytest.raises(PoiseError):
        api.status({"schema": "goal-config-status-1", "goal_type": "writing"})
    with pytest.raises(PoiseError):
        api.reconcile(_reconcile(first, malformed))


def test_pending_publication_blocks_reconcile_and_preserves_recovery(tmp_path, monkeypatch):
    import poise.infrastructure.goal_config as infrastructure

    _, api, first = _managed_editor(tmp_path)
    target = Path(first["config_path"])
    pending = request("update", "writing", first["revision"], additions(), "pending-edit")
    original = infrastructure.atomic_write

    def fail(path, content, mode):
        if path == target:
            raise OSError("injected pending write")
        return original(path, content, mode)

    with monkeypatch.context() as scoped:
        scoped.setattr(infrastructure, "atomic_write", fail)
        with pytest.raises(PublicationPending):
            api.apply_batch(pending)
        with pytest.raises(PublicationPending):
            api.reconcile(_reconcile(first, first["revision"], "adopt-during-pending"))

    assert api.apply_batch(pending)["replayed"] is True


def test_cli_dispatches_reconcile_and_identical_request_replays(tmp_path):
    path, _, first = _managed_editor(tmp_path)
    _, _, live_revision = _external_valid_change(first)
    packet = _reconcile(first, live_revision, "cli-adopt")

    first_output = io.StringIO()
    assert execute(path, io.BytesIO(json.dumps(packet).encode()), first_output) == 0
    first_result = json.loads(first_output.getvalue())

    replay_output = io.StringIO()
    assert execute(path, io.BytesIO(json.dumps(packet).encode()), replay_output) == 0
    replay = json.loads(replay_output.getvalue())

    assert first_result["status"] == "reconciled"
    assert replay["replayed"] is True
    with sqlite3.connect(tmp_path / "state/config-editor.sqlite") as database:
        assert database.execute(
            "SELECT count(*) FROM config_operations WHERE request_id='cli-adopt'"
        ).fetchone()[0] == 1

