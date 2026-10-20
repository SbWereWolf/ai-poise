"""Task namespace input, exact receipts and selected-ID commit guards."""
from dataclasses import dataclass
import json
from ...modules.tasks.allocation import Allocation, allocate_identity
from ...modules.foundation.errors import PoiseError, VersionConflict


@dataclass(frozen=True)
class CreationReceipt:
    task_id: str
    request_id: str
    digest: str
    snapshot: str | None
    agreement: str | None
    metadata: str
    task_fact: dict

    def requirements_context(self):
        return {'requirements_snapshot': None if self.snapshot is None else json.loads(self.snapshot),
                'requirements_agreement': None if self.agreement is None else json.loads(self.agreement)}


@dataclass(frozen=True)
class TaskAllocationCapture:
    occupied: frozenset[str]
    receipts: tuple[CreationReceipt, ...]

    def allocate(self, intent, policy, reserved_ids=()):
        return allocate_identity(intent, policy, self.occupied,
            ((r.task_id, r.request_id, r.digest) for r in self.receipts), reserved_ids)


def capture_allocations(db, request_ids):
    occupied = frozenset(row[0] for row in db.execute('SELECT id FROM tasks UNION SELECT id FROM sprints'))
    rows = db.execute(
        "SELECT id,json_extract(metadata,'$.creation_request.request_id'),"
        "json_extract(metadata,'$.creation_request.digest'),"
        "json_extract(metadata,'$.requirements_snapshot'),"
        "json_extract(metadata,'$.requirements_agreement'),metadata FROM tasks "
        "WHERE json_extract(metadata,'$.creation_request.request_id') IN "
        '(SELECT value FROM json_each(?)) ORDER BY id', (json.dumps(request_ids),)).fetchall()
    from .task_facts import capture_task_facts
    facts = {row['id']:row for row in capture_task_facts(db, tuple(row[0] for row in rows))}
    receipts = tuple(CreationReceipt(*row, facts[row[0]]) for row in rows)
    return TaskAllocationCapture(occupied, receipts)


def creation_replay(db, allocation):
    if allocation.request_id is None:
        return None
    row = db.execute("SELECT id,json_extract(metadata,'$.creation_request.digest') AS digest "
        "FROM tasks WHERE json_extract(metadata,'$.creation_request.request_id')=? ORDER BY id LIMIT 1",
        (allocation.request_id,)).fetchone()
    if row is None:
        return None
    if row['digest'] != allocation.digest:
        raise PoiseError('Creation request digest conflict; immutable intent changed')
    return Allocation(allocation.request_id, row['id'], allocation.digest, True)


def require_allocation(db, allocation, receipt):
    replay = creation_replay(db, allocation)
    if replay is not None:
        if not allocation.replayed or replay.task_id != allocation.task_id:
            return False
        row = db.execute("SELECT json_extract(metadata,'$.requirements_snapshot'),"
            "json_extract(metadata,'$.requirements_agreement') FROM tasks WHERE id=?",
            (allocation.task_id,)).fetchone()
        if receipt is None or tuple(row) != (receipt.snapshot, receipt.agreement):
            raise VersionConflict('Creation Requirements context changed; repeat the request')
        from .task_facts import capture_task_facts
        current = capture_task_facts(db, (allocation.task_id,))
        if not current or current[0] != receipt.task_fact:
            raise VersionConflict('Creation Task changed during preparation; repeat the request')
        return True
    if allocation.replayed:
        raise VersionConflict('Creation receipt changed; repeat the request')
    occupied = db.execute('SELECT id FROM tasks WHERE id=? UNION SELECT id FROM sprints WHERE id=?',
                          (allocation.task_id, allocation.task_id)).fetchone()
    if occupied is not None and allocation.request_id is None:
        raise PoiseError('Task ID already exists; publication cannot replace it')
    return occupied is None
