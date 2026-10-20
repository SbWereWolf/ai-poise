"""Closed Sprint/member input view and its canonical projections."""
from dataclasses import dataclass
import json
from ...modules.tasks.ports import TaskReadSet
from ...modules.foundation.errors import PoiseError, VersionConflict
from .task_inputs import capture_inputs, require_inputs
from .sprints import SqliteSprintRepository
from .integration_scopes import SqliteIntegrationScopeRepository


@dataclass(frozen=True)
class SprintInputs:
    sprint_id: str | None
    scope_actor: str | None
    scope: str | None
    inputs: object
    rows: tuple
    member_rows: tuple
    requests: tuple
    history: tuple
    placements: tuple

    def record(self):
        return self.inputs.sprint(self.sprint_id)

    def facts(self, record=None, candidates=()):
        rows = {row['id']:row for row in self.member_rows}
        rows.update((row['id'], row) for row in candidates)
        return SqliteSprintRepository.restore_facts(self.record() if record is None else record, tuple(rows.values()))

    def receipt(self, request_id, digest):
        row = next((r for r in self.requests if r['request_id'] == request_id), None)
        if row is None:
            return None
        if row['digest'] != digest:
            raise PoiseError('Request ID already used with another sprint intent')
        return json.loads(row['data'])

    def layers(self):
        return [{'revision':revision, 'data':json.loads(data), 'at':at}
                for revision, data, at in self.history]

    def integration_records(self, task_ids):
        return SqliteIntegrationScopeRepository._edges([r for r in self.placements if r['task_id'] in task_ids])


def capture_sprint(db, sprint_id, actor, task_ids=(), creation_requests=()):
    scope_actor = actor if sprint_id is None else None
    row = db.execute('SELECT sprint_id FROM session_sprints WHERE session_id=?', (actor,)).fetchone()
    scope = None if row is None else row[0]
    sid = scope if sprint_id is None else sprint_id
    rows = SqliteSprintRepository.capture_facts(db, sid)
    optional = set(task_ids) | {r['id'] for r in rows}
    # An immutable creation request selects its already allocated Task input.
    optional.update(r[0] for r in db.execute("SELECT id FROM tasks WHERE "
        "json_extract(metadata,'$.creation_request.request_id') IN (SELECT value FROM json_each(?))",
        (json.dumps(creation_requests),)))
    inputs = capture_inputs(db, TaskReadSet((), optional_ids=tuple(sorted(optional)),
        execution_ids=tuple(sorted(optional)), sprint_ids=(sid,), allocation=True,
        creation_requests=tuple(creation_requests)))
    requests = tuple(dict(r) for r in db.execute('SELECT request_id,digest,data FROM sprint_requests '
        'WHERE sprint_id=? ORDER BY request_id', (sid,)))
    history = tuple(tuple(r) for r in db.execute('SELECT revision,data,at FROM sprint_layers '
        'WHERE sprint_id=? ORDER BY revision', (sid,)))
    placements = tuple(dict(r) for r in db.execute("SELECT seq,task_id,session_id,data FROM journal "
        "WHERE event='integration.backup_scope_relocated' AND task_id IN "
        '(SELECT value FROM json_each(?)) ORDER BY seq', (json.dumps(sorted(optional)),)))
    return SprintInputs(sid, scope_actor, scope if scope_actor is not None else None,
                        inputs, rows, SqliteSprintRepository.capture_task_facts(db, tuple(sorted(optional))),
                        requests, history, placements)


def require_sprint(db, snapshot):
    require_inputs(db, snapshot.inputs)
    if snapshot.scope_actor is not None:
        row = db.execute('SELECT sprint_id FROM session_sprints WHERE session_id=?', (snapshot.scope_actor,)).fetchone()
        if (None if row is None else row[0]) != snapshot.scope:
            raise VersionConflict('Selected Sprint changed; repeat the request')
    current = SqliteSprintRepository.capture_facts(db, snapshot.sprint_id)
    if current != snapshot.rows:
        raise VersionConflict('Sprint members changed; repeat the request')
    members = SqliteSprintRepository.capture_task_facts(db, snapshot.inputs.read_set.optional_ids)
    if members != snapshot.member_rows:
        raise VersionConflict('Selected Sprint Task facts changed; repeat the request')
    placements = tuple(dict(r) for r in db.execute("SELECT seq,task_id,session_id,data FROM journal "
        "WHERE event='integration.backup_scope_relocated' AND task_id IN "
        '(SELECT value FROM json_each(?)) ORDER BY seq',
        (json.dumps(snapshot.inputs.read_set.optional_ids),)))
    if placements != snapshot.placements:
        raise VersionConflict('Sprint integration scope changed; repeat the request')


def capture_sprint_view(db, sprint_id, actor, view):
    row = db.execute('SELECT sprint_id FROM session_sprints WHERE session_id=?', (actor,)).fetchone()
    scope = None if row is None else row[0]
    sid = scope if sprint_id is None else sprint_id
    inputs = capture_inputs(db, TaskReadSet((), sprint_ids=(sid,)))
    rows = SqliteSprintRepository.capture_facts(db, sid) if view == 'current' else ()
    history = (tuple(tuple(r) for r in db.execute('SELECT revision,data,at FROM sprint_layers '
        'WHERE sprint_id=? ORDER BY revision', (sid,))) if view == 'history' else ())
    return SprintInputs(sid, actor if sprint_id is None else None,
        scope if sprint_id is None else None, inputs, rows, rows, (), history, ())
