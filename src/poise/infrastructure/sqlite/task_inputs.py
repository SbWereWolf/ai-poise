"""Selected Task input materialization and optimistic source comparison."""
from dataclasses import dataclass, replace
import json
from .task_snapshot import capture_task
from ...modules.tasks.newborn import NewbornTask
from ...modules.foundation.errors import PoiseError, VersionConflict


@dataclass(frozen=True)
class ReviewIdentityCapture:
    produced: dict | None
    verified: str | None
    started: str | None
    observed: tuple[str, ...]

    def restore(self):
        if self.produced is None:
            if self.started is None:
                return None
            data = json.loads(self.started)
            return {'executor_actor': data.get('actor'), 'source': 'started_task_event',
                    'stage': data['stage'], 'iteration': data['iteration'], 'submission_id': None}
        actor = None if self.verified is None else json.loads(self.verified).get('actor')
        source = 'verified_task_event' if actor else 'unavailable'
        if actor is None and len(self.observed) == 1:
            actor, source = self.observed[0], 'legacy_verify_start'
        return {'executor_actor': actor, 'source': source, 'stage': self.produced['stage'],
                'iteration': self.produced['iteration'],
                'submission_id': self.produced['submission_id']}


def capture_review_identity(db, task_id, *, include_unsubmitted=False):
    produced = db.execute('SELECT r.submission_id,s.stage,s.iteration,'
        "json_extract(r.data,'$.verified_tree') AS tree FROM task_results r "
        'JOIN submissions s ON s.seq=r.submission_id AND s.task_id=r.task_id '
        "WHERE r.task_id=? AND json_extract(r.data,'$.handler')!='inspect' "
        'ORDER BY s.seq DESC LIMIT 1', (task_id,)).fetchone()
    if produced is None:
        started = (None if not include_unsubmitted else db.execute(
            "SELECT data FROM task_events WHERE task_id=? AND json_extract(data,'$.event')="
            "'started' ORDER BY seq DESC LIMIT 1", (task_id,)).fetchone())
        return ReviewIdentityCapture(None, None, None if started is None else started[0], ())
    verified = db.execute("SELECT data FROM task_events WHERE task_id=? "
        "AND json_extract(data,'$.event')='verified' AND json_extract(data,'$.submission')=? "
        'ORDER BY seq DESC LIMIT 1', (task_id, produced['submission_id'])).fetchone()
    observed = tuple(row[0] for row in db.execute(
        "SELECT DISTINCT session_id FROM journal WHERE task_id=? AND event='verify.start' "
        "AND json_extract(data,'$.stage')=? AND json_extract(data,'$.tree')=? ORDER BY session_id",
        (task_id, produced['stage'], produced['tree'])))
    return ReviewIdentityCapture(dict(produced), None if verified is None else verified[0],
                                 None, observed)


@dataclass(frozen=True)
class SqliteTaskInputs:
    read_set: object
    tasks: dict
    executions: dict
    reviews: dict
    actors: dict
    sprints: dict
    namespace: object | None
    actions: dict
    audits: dict
    families: dict
    receipts: dict
    verification: dict
    histories: dict
    eligibility: dict
    materials: dict
    cycles: dict

    def read_duplicate_family(self, task_id):
        from .duplicate_tasks import restore_duplicate_family
        return restore_duplicate_family(self.families[task_id], task_id)

    def evidence(self, task_id):
        from .evidence import SqliteEvidenceRepository
        return SqliteEvidenceRepository.restore(self.receipts[task_id])

    def criteria_submission_guard(self, task, actor, report):
        from .task_verification import criteria_submission_guard
        task_id = task.state.task_id
        return criteria_submission_guard(self.verification[task_id], self.tasks[task_id].header,
                                          self.execution(task_id)[0], task, actor, report)

    def work_packet(self, task_id, stage, iteration):
        return next((digest for st, it, digest in self.verification[task_id].packets
                     if (st, it) == (stage, iteration)), None)

    def progression_view(self, task_id, request_id=None):
        return self.histories[task_id].progression_view(request_id)

    def replay_progress(self, task_id, request_id):
        return self.histories[task_id].replay_progress(request_id)

    def accepted_replay_sources(self, task_id):
        from .replay import project_accepted_sources
        return project_accepted_sources(self.histories[task_id], self.tasks[task_id])

    def progression_request(self, task_id, request_id, digest):
        return self.histories[task_id].progression_request(self.tasks[task_id].header, request_id, digest)

    def ownership_event_suffix(self, task_id, after_version, through_version):
        return self.histories[task_id].ownership_event_suffix(after_version, through_version)

    def cancellation_recovery_point(self, task_id, version):
        from .task_history import cancellation_recovery_point
        return cancellation_recovery_point(self.histories[task_id], task_id, version)

    def empty_rework_recovery_point(self, task_id, version, report, transition_event):
        from .task_history import empty_rework_recovery_point
        return empty_rework_recovery_point(self.histories[task_id], task_id, version, report, transition_event)

    def begin_progression(self, task_id, actor, request_id, digest, target_stage):
        from .task_history import begin_progression
        return begin_progression(self.histories[task_id], self.tasks[task_id].header,
                                 task_id, actor, request_id, digest, target_stage)

    def verified_ownership_history(self, task_id, stage_id, iteration, digest, through_version):
        from .task_history import verified_ownership_history
        return verified_ownership_history(self.histories[task_id], self.tasks[task_id].header,
                                         task_id, stage_id, iteration, digest, through_version)

    def artifact_records(self, task_id):
        return [dict(row) for row in self.materials[task_id]]

    def sprint_eligibility(self, task_id):
        sid, rows = self.eligibility[task_id]
        return (None if sid is None else self.sprint(sid),
                {row['id']:{key:value for key,value in row.items() if key != 'id'} for row in rows})

    def membership_snapshot(self, task_id):
        raw = self.tasks[task_id]
        if raw is None:
            raise PoiseError(f'Задача не найдена: {task_id}')
        state = self.state(task_id)
        return {'task_id':task_id, 'status':state.status.value, 'claimed_by':state.claimed_by,
            'version':state.version, 'sprint_id':json.loads(raw.header['metadata'])['sprint_id'],
            'execution':None if self.execution(task_id) is None else self.execution(task_id)[0]}

    def exists(self, task_id):
        return self.tasks[task_id] is not None

    def task(self, task_id):
        if self.tasks[task_id] is None:
            raise PoiseError(f'Задача не найдена: {task_id}')
        return self.tasks[task_id].restore()

    def state(self, task_id):
        from .tasks import SqliteTaskRepository
        return SqliteTaskRepository._restore_state(self.tasks[task_id].header)

    def newborn(self, task_id):
        raw = self.tasks[task_id]
        if raw is None:
            raise PoiseError(f'Задача не найдена: {task_id}')
        row = raw.header
        if row['status'] != 'newborn':
            raise PoiseError('Task is not newborn')
        return NewbornTask.restore(task_id, row['claimed_by'], row['version'],
                                   json.loads(row['metadata']))

    def execution(self, task_id):
        row = self.executions[task_id]
        return None if row is None else (json.loads(row[0]), row[1])

    def required_execution(self, task_id):
        value = self.execution(task_id)
        if value is None:
            raise PoiseError('Нет execution state задачи')
        return value

    def review_identity(self, task_id, *, include_unsubmitted=False):
        return self.reviews[(task_id, True) if include_unsubmitted else task_id].restore()

    def ownership(self, actor):
        from ...modules.ownership.domain import OwnershipSnapshot
        return OwnershipSnapshot(actor, *self.actors[actor])

    def sprint(self, sprint_id):
        raw = self.sprints[sprint_id]
        return None if raw is None else json.loads(raw['data'])

    def allocate(self, intent, policy, reserved_ids=()):
        if self.namespace is None:
            raise PoiseError('Task namespace was not declared in the preparation read set')
        return self.namespace.allocate(intent, policy, reserved_ids)

    def creation_receipt(self, request_id):
        if self.namespace is None:
            raise PoiseError('Task namespace was not declared in the preparation read set')
        return next((r for r in self.namespace.receipts if r.request_id == request_id), None)

    def action_binding(self, task_id, stage, iteration):
        from ...modules.actions.domain import ActionBinding
        from .task_lifecycle import restore_lifecycle
        raw = self.tasks[task_id]
        lifecycle = restore_lifecycle(raw.header['version'], raw.header['metadata'],
                                      self.audits[task_id])
        return ActionBinding(task_id, lifecycle, stage, iteration)

    def action_run(self, task_id, stage, iteration):
        from .actions import action_key
        key = action_key(self.action_binding(task_id, stage, iteration))
        row = next((r for r in self.actions[task_id]
                    if (task_id, r['stage'], r['iteration']) == key), None)
        return None if row is None else json.loads(row['data'])

    def restart_context(self, task_id):
        from .task_snapshot import restore_restart_context
        return restore_restart_context(self.tasks[task_id].header['metadata'])

    def restarted_registry(self, task_id):
        from .task_snapshot import restore_restarted_registry
        raw = self.tasks[task_id]
        return restore_restarted_registry(task_id, raw.header['metadata'], raw.workflow, raw.methods)

    def restarted_feedback(self, task_id):
        from ...modules.inspection.domain import FeedbackBook
        raw = self.tasks[task_id].workflow
        if raw is None:
            raise PoiseError('Restarted Task has no preserved feedback state')
        return FeedbackBook.from_dict(json.loads(raw)['feedback'])

    def restarted_evidence(self, task_id):
        from ...modules.evidence.domain import EvidenceBook
        raw = self.tasks[task_id].proof
        if raw is None:
            raise PoiseError('Restarted Task has no preserved evidence state')
        return EvidenceBook.from_dict(json.loads(raw)['book'])

    def worktree_required(self, task_id):
        from .ownership import worktree_required
        row = self.tasks[task_id].header
        return worktree_required(row['status'], json.loads(row['metadata']))


def capture_inputs(db, read_set):
    from .task_allocation import capture_allocations
    from .task_verification import capture_verification
    from .task_history import capture_history
    from .accounting import SqliteAccountingCycles
    from .sprints import SqliteSprintRepository
    from .artifacts import SqliteArtifactRepository
    from .duplicate_tasks import capture_duplicate_family
    from .evidence import SqliteEvidenceRepository
    from .ownership import capture_actor_snapshot
    families = {task_id: capture_duplicate_family(db, task_id)
                for task_id in set(read_set.family_ids) | set(read_set.family_execution_ids) | set(read_set.family_contract_ids)}
    execution_ids = set(read_set.execution_ids)
    for task_id in read_set.family_execution_ids:
        family = families[task_id]
        if family is not None:
            execution_ids.update(row['id'] for row in family[2] if row['status'] == 'completed')
    actors = {}
    ids = set(read_set.task_ids)
    ids.update(task_id if families[task_id] is None else families[task_id][0]
               for task_id in read_set.family_contract_ids)
    for actor in read_set.actors:
        snapshot = capture_actor_snapshot(db, actor)
        actors[actor] = snapshot
        if snapshot[0] is not None:
            ids.add(snapshot[0])
    tasks = {task_id: capture_task(db, task_id) for task_id in sorted(ids)}
    for task_id in read_set.optional_ids:
        if task_id not in tasks:
            exists = db.execute('SELECT 1 FROM tasks WHERE id=?', (task_id,)).fetchone()
            tasks[task_id] = None if exists is None else capture_task(db, task_id)
    sprint_ids = set(read_set.sprint_ids)
    if read_set.related_sprints:
        for task_id in (*read_set.task_ids, *read_set.optional_ids):
            row = db.execute("SELECT json_extract(metadata,'$.sprint_id') FROM tasks WHERE id=?",
                             (task_id,)).fetchone()
            if row is not None and row[0] is not None:
                sprint_ids.add(row[0])
    eligibility = {}
    for task_id in read_set.eligibility_ids:
        row = db.execute("SELECT json_extract(metadata,'$.sprint_id') FROM tasks WHERE id=?", (task_id,)).fetchone()
        sid = None if row is None else row[0]
        if sid is not None:
            sprint_ids.add(sid)
        eligibility[task_id] = sid, (() if sid is None else SqliteSprintRepository.capture_eligibility(db, sid))
    return SqliteTaskInputs(read_set, tasks,
        {task_id: (None if (row := db.execute('SELECT data,version FROM task_execution '
             'WHERE task_id=?', (task_id,)).fetchone()) is None else tuple(row))
         for task_id in sorted(execution_ids)},
        {**{task_id: capture_review_identity(db, task_id) for task_id in read_set.review_ids},
         **{(task_id, True): capture_review_identity(db, task_id, include_unsubmitted=True)
            for task_id in read_set.review_unsubmitted_ids}}, actors,
        {sprint_id: (None if (row := db.execute('SELECT id,project,state,revision,data FROM sprints '
             'WHERE id=?', (sprint_id,)).fetchone()) is None else dict(row))
         for sprint_id in sorted(sprint_ids)},
        capture_allocations(db, read_set.creation_requests) if read_set.allocation else None,
        {task_id: tuple(dict(r) for r in db.execute('SELECT stage,iteration,version,data '
            'FROM action_runs WHERE task_id=? ORDER BY stage,iteration', (task_id,)))
         for task_id in read_set.action_task_ids},
        {task_id: tuple(tuple(r) for r in db.execute('SELECT version,data FROM task_events '
            'WHERE task_id=? ORDER BY seq', (task_id,))) for task_id in read_set.action_task_ids},
        families,
        {task_id: SqliteEvidenceRepository.capture(db, task_id) for task_id in read_set.evidence_ids},
        {task_id: capture_verification(db, task_id) for task_id in read_set.verification_ids},
        {task_id: capture_history(db, task_id) for task_id in read_set.history_ids}, eligibility,
        {task_id: tuple(SqliteArtifactRepository(db).records(task_id)) for task_id in read_set.material_ids},
        {pair: SqliteAccountingCycles.capture_release(db, *pair) for pair in read_set.cycle_releases})


def require_inputs(db, inputs):
    if not isinstance(inputs, SqliteTaskInputs):
        raise PoiseError('Task input snapshot belongs to a different persistence owner')
    # Namespace changes have their own selected-ID/receipt guard, not a global veto.
    ordinary = replace(inputs.read_set, allocation=False)
    expected = replace(inputs, read_set=ordinary, namespace=None)
    current = capture_inputs(db, ordinary)
    if current != expected:
        from ...modules.tasks.ports import TaskSourcesChanged
        raise TaskSourcesChanged(current)
