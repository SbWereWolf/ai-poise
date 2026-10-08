"""Independent journal exception and snapshots for accepted no-op contract."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from runtime_services.restart_auto_support import snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "restart_auto_noop.json"


def expected_noop(commit):
    value = json.loads(FIXTURE.read_text())["result"]
    value["replay"]["start_commit"] = commit
    return value


def expected_digest():
    # Existing explicit-target identity, independently declared input.
    canonical = json.dumps({
        "action": "advance", "task_id": "T1", "target_stage": "tests",
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def assert_noop_response(response, commit):
    assert "replay" in response, "Public replay assessment is missing"
    expected = expected_noop(commit)
    assert response["status"] == expected["status"]
    assert response["replay"] == expected["replay"]


def noop_snapshot(case):
    state = snapshot(case)
    columns = {
        "task_workflows": "task_id,data",
        "task_execution": "task_id,data,version",
        "task_proofs": "task_id,data",
        "task_proof_layers": "task_id,version,data",
        "workflow_layers": "submission_id,task_id,data",
        "section_layers": "submission_id,task_id,section_id,content,content_state",
        "trace_point_layers": "submission_id,task_id,route_id,point_id,data",
        "task_methods": "task_id,method_id,version,data",
        "content_contracts": "task_id,version,data",
        "task_artifacts": "task_id,artifact_id",
        "action_runs": "task_id,stage,iteration,version,data",
        "action_events": "seq,task_id,stage,iteration,version,at,data",
        "sessions": "id,task_id",
    }
    with case["client"].runtime.store.transaction() as db:
        state["other_owned"] = {
            table: [tuple(row) for row in db.execute(
                f"SELECT {fields} FROM {table} WHERE task_id=? ORDER BY rowid", ("T1",),
            )]
            for table, fields in columns.items()
        }
        state["registered_files"] = [tuple(row) for row in db.execute(
            "SELECT a.id,a.owner,a.scope,a.path,a.digest FROM artifacts a "
            "JOIN task_artifacts t ON t.artifact_id=a.id WHERE t.task_id=? ORDER BY a.id",
            ("T1",),
        )]
    return state


def assert_single_noop(before, after, *, commit, request_id="N", actor="replay-owner"):
    old = before["owned"]["journal"]
    rows = after["owned"]["journal"]
    assert len(rows) == len(old) + 1
    assert rows[:-1] == old
    seq, at, session, task, event, raw = rows[-1]
    assert type(seq) is int and seq > max((row[0] for row in old), default=0)
    assert datetime.fromisoformat(at).utcoffset() == timedelta(0)
    assert (session, task, event) == (actor, "T1", "progression.noop")
    assert json.loads(raw) == {
        "request_id": request_id, "digest": expected_digest(),
        "result": expected_noop(commit),
    }
    restored = deepcopy(after)
    restored["owned"]["journal"] = rows[:-1]
    assert restored == before

