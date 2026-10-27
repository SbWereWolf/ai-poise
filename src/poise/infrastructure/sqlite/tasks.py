from __future__ import annotations
import json
from dataclasses import replace
from copy import deepcopy
from datetime import datetime, timezone
from ...modules.tasks.domain import (
    Change,
    EmptyReworkRecoveryPoint,
    StageSpec,
    Task,
    TaskStageContracts,
    TaskState,
    TaskStatus,
)
from ...modules.tasks.contracts import stages_from_workflow, stored_content_policy_from_metadata, evidence_plan_from_metadata
from ...modules.content.domain import ContentState, SectionValue
from ...modules.verification.domain import CheckRegistry
from ...modules.content_requirements.domain import ContentSnapshot, TraceValue
from ...modules.workflow.domain import RouteDefinition, RouteProgress
from ...modules.inspection.domain import FeedbackBook
from ...modules.evidence.domain import EvidenceBook
from ...modules.foundation.errors import PoiseError, VersionConflict
from ...modules.tasks.newborn import NewbornTask
from ...modules.tasks.stage_criteria import StageCriteriaBook, split_work
from ...modules.tasks.lifecycle import TaskLifecycle, invalid_audit


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def progression_view(db, task_id: str, request_id: str | None = None) -> dict | None:
    actions = [json.loads(row[0]) for row in db.execute(
        "SELECT data FROM journal WHERE task_id=? AND event='progression.started' "
        "ORDER BY seq",
        (task_id,),
    )]
    if request_id is not None:
        actions = [item for item in actions if item["request_id"] == request_id]
    if not actions:
        return None
    action = actions[-1]
    reached = db.execute(
        "SELECT 1 FROM journal WHERE task_id=? AND event IN ('progression.reached','progression.result') "
        "AND json_extract(data,'$.request_id')=? LIMIT 1",
        (task_id, action["request_id"]),
    ).fetchone()
    return {
        "request_id": action["request_id"],
        "target_stage": action["target_stage"],
        "status": "reached" if reached is not None else "active",
    }


def without_retired_method_timeout(value: dict) -> dict:
    normalized = {**value, "method": dict(value["method"])}
    normalized["method"].pop("timeout_seconds", None)
    return normalized


class SqliteTaskRepository:
    """The only writer of Task aggregate, submitted content and domain events."""
    def __init__(self, connection):
        self.db = connection

    def pending_artifact_consumers(self, source_id):
        rows = self.db.execute("SELECT t.id,w.data FROM tasks t JOIN task_workflows w ON w.task_id=t.id WHERE t.id!=? AND t.status NOT IN ('completed','cancelled') AND json_extract(w.data,'$.duplicate_reuse') IS NOT NULL", (source_id,))
        for row in rows:
            proof = json.loads(row['data'])['duplicate_reuse']
            candidate = proof['candidate']
            if candidate['source_task_id'] == source_id and candidate['artifact_source']['records'] and not proof['verified']:
                return True
        return False

    def read_duplicate_family(self, task_id):
        from .duplicate_tasks import read_duplicate_family
        # Library use outside an existing UoW still gets one read snapshot.
        own_view = not self.db.in_transaction
        if own_view:
            self.db.execute('BEGIN')
        try:
            return read_duplicate_family(self.db, task_id)
        finally:
            if own_view:
                self.db.execute('ROLLBACK')

    def link_duplicate(self, task_id, parent_id, actor):
        if task_id == parent_id or not self.exists(parent_id):
            raise PoiseError('Duplicate requires a different existing parent')
        row = self.db.execute('SELECT status,version,metadata FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None or row['status'] != 'newborn':
            raise PoiseError('Duplicate lineage is assigned only on newborn creation')
        metadata, event = self.prepare_duplicate_link(row['metadata'], parent_id)
        self.db.execute('UPDATE tasks SET metadata=? WHERE id=?', (metadata,task_id))
        self.record_duplicate_link(task_id, actor, event)

    @staticmethod
    def prepare_duplicate_link(raw, parent_id):
        metadata = json.loads(raw)
        if 'duplicate_parent_id' in metadata:
            raise PoiseError('Duplicate parent is immutable')
        metadata['duplicate_parent_id'] = parent_id
        return encode(metadata), encode({'parent_id':parent_id})

    def record_duplicate_link(self, task_id, actor, event):
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),actor,task_id,'duplicate.created',
             event))

    def release_recovery_claim(self, task_id, version, owner, actor, request_id, reason, recovery):
        """Exact operator-authorized ownership release; no workflow/content rewrite."""
        changed = self.db.execute(
            'UPDATE tasks SET claimed_by=NULL,version=version+1 '
            'WHERE id=? AND version=? AND claimed_by=?', (task_id, version, owner))
        if changed.rowcount != 1:
            raise VersionConflict('Ownership snapshot changed')
        self._event(task_id, version + 1, {
            'event': 'ownership_released', 'actor': actor, 'recovery': recovery,
            'previous_owner': owner, 'request_id': request_id, 'reason': reason})


    def exists(self, task_id):
        return self.db.execute("SELECT 1 FROM tasks WHERE id=?",(task_id,)).fetchone() is not None

    def cancellation_recovery_point(self, task_id: str, version: int) -> TaskState:
        from .task_history import capture_history, cancellation_recovery_point
        return cancellation_recovery_point(capture_history(self.db, task_id), task_id, version)

    def review_identity(self, task_id: str, *, include_unsubmitted=False) -> dict | None:
        from .task_inputs import capture_review_identity
        return capture_review_identity(self.db, task_id,
                                       include_unsubmitted=include_unsubmitted).restore()

    def selected_result_author(self, task_id: str) -> str | None:
        from .task_inputs import capture_review_identity
        return capture_review_identity(self.db, task_id).selected_actor

    def _event_range(self, task_id, after_version, through_version):
        from .task_history import capture_history
        return capture_history(self.db, task_id).event_range(after_version, through_version)

    def versioned_event_suffix(self, task_id, after_version, through_version):
        return self._event_range(task_id, after_version, through_version)

    def ownership_event_suffix(self, task_id, after_version, through_version):
        from .task_history import capture_history
        return capture_history(self.db, task_id).ownership_event_suffix(after_version, through_version)

    def verified_ownership_history(self, task_id, stage_id, iteration, submission_digest, through_version):
        from .task_history import capture_history, verified_ownership_history
        return verified_ownership_history(capture_history(self.db, task_id), self._load_state_row(task_id),
            task_id, stage_id, iteration, submission_digest, through_version)

    def _newborn_metadata(self, newborn, config_hash):
        row = self.db.execute('SELECT metadata FROM tasks WHERE id=?',
                              (newborn.task_id,)).fetchone()
        if row is None:
            raise PoiseError(f'Задача не найдена: {newborn.task_id}')
        return self.newborn_metadata(newborn, config_hash, json.loads(row[0]))

    @staticmethod
    def newborn_metadata(newborn, config_hash, previous):
        metadata = newborn.metadata(config_hash)
        if 'duplicate_parent_id' in previous:
            metadata['duplicate_parent_id'] = previous['duplicate_parent_id']
        if newborn.restart_history:
            for key in ("requirements_snapshot", "requirements_agreement"):
                if key in previous:
                    metadata[key] = deepcopy(previous[key])
        if any(item.get('from_status') != 'newborn' for item in newborn.restart_history):
            if not isinstance(previous.get('contract'), dict):
                raise PoiseError('Restarted Task has no frozen registry contract context')
            metadata['contract'] = deepcopy(previous['contract'])
            context_key = 'registry_workflow' if 'newborn' in previous else 'workflow'
            if not isinstance(previous.get(context_key), dict):
                raise PoiseError('Restarted Task has no frozen registry workflow context')
            metadata['registry_workflow'] = deepcopy(previous[context_key])
        return metadata

    def is_newborn(self, task_id):
        row = self.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {task_id}")
        return row[0] == TaskStatus.NEWBORN.value

    def create_newborn(self, newborn: NewbornTask, config_hash: str) -> None:
        self.create_newborn_prepared(newborn, encode(newborn.metadata(config_hash)))

    def create_newborn_prepared(self, newborn, metadata):
        if self.exists(newborn.task_id):
            raise PoiseError("Task ID already exists; newborn creation cannot replace it")
        if self.db.execute("SELECT 1 FROM sprints WHERE id=?", (newborn.task_id,)).fetchone():
            raise PoiseError("Task/sprint ID collision")
        self.db.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,NULL,?)",
            (
                newborn.task_id,
                TaskStatus.NEWBORN.value,
                0,
                1,
                newborn.claimed_by,
                newborn.version,
                metadata,
            ),
        )
        self._event(newborn.task_id, newborn.version, {
            "event": "created_newborn",
            "stage": "newborn",
            "iteration": 1,
        })

    def action_receipt_raw(self, task_id, request_id, digest):
        from .task_actions import capture_action_receipt
        return capture_action_receipt(self.db, task_id, request_id, digest)

    def action_receipt(self, task_id: str, request_id: str, digest: str):
        raw = self.action_receipt_raw(task_id, request_id, digest)
        return None if raw is None else raw.result()

    def stage_contract_receipt_raw(self, task_id, request_id):
        row = self.db.execute("SELECT data FROM journal WHERE task_id=? "
            "AND event='stage_contract.action' AND json_extract(data,'$.request_id')=? "
            'ORDER BY seq LIMIT 1', (task_id, request_id)).fetchone()
        if row is None:
            return None
        entry = self.db.execute("SELECT json_extract(h.value,'$.version') FROM tasks t, "
            "json_each(t.metadata,'$.stage_contract_history') h WHERE t.id=? "
            "AND json_extract(h.value,'$.request_id')=? LIMIT 1", (task_id, request_id)).fetchone()
        return row[0], None if entry is None else entry[0]

    @staticmethod
    def restore_stage_contract_receipt(raw, task_id, request_id, intent):
        if raw is None:
            return None
        saved = json.loads(raw[0])
        if ({k:v for k,v in saved.items() if k != 'old'} !=
                {k:v for k,v in intent.items() if k != 'old'}):
            raise PoiseError('stage contract request conflict: request_id has another intent')
        if raw[1] is None:
            raise PoiseError('stage contract receipt has no immutable history entry')
        return {'status':('stage_contracts_initialized' if saved['action'] == 'initialize_stage_contracts'
                          else 'stage_contract_revised'), 'task':task_id, 'request_id':request_id,
                'version':raw[1], 'old':deepcopy(saved['old']), 'new':deepcopy(saved['new']), 'replayed':True}

    def stage_contract_receipt(self, task_id, request_id, intent):
        return self.restore_stage_contract_receipt(self.stage_contract_receipt_raw(task_id, request_id),
                                                   task_id, request_id, intent)

    def save_stage_contract_change(self, change, expected_version, audit):
        from .task_snapshot import capture_task
        from .task_writes import prepare_stage_contract_change
        prepared = prepare_stage_contract_change(capture_task(self.db, change.task.state.task_id),
                                                 change, expected_version, audit)
        self.save_prepared_stage_contract_change(prepared)
        return prepared.result

    def save_prepared_stage_contract_change(self, prepared):
        self.save_prepared(prepared.write)
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(), prepared.actor, prepared.write.state.task_id,
             'stage_contract.action', prepared.audit))

    def stage_contract_context(self, task_id: str) -> dict:
        row = self.db.execute("SELECT metadata FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {task_id}")
        metadata = json.loads(row[0])
        return {
            "current": deepcopy(metadata["contract"].get("stage_contracts")),
            "history": deepcopy(metadata.get("stage_contract_history", [])),
        }

    def remember_action(self, task_id: str, actor: str, request_id: str,
                        digest: str, result: dict) -> None:
        self.remember_prepared_action(task_id, actor, encode({
            'request_id': request_id, 'digest': digest, 'result': result}))

    def remember_prepared_action(self, task_id, actor, data):
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(), actor, task_id, 'newborn.action', data))

    def accepted_replay_sources(self, task_id):
        from .replay import accepted_sources
        return accepted_sources(self.db, task_id)

    def replay_progress(self, task_id, request_id):
        row = self.db.execute(
            "SELECT data FROM journal WHERE task_id=? AND event='progression.phase' "
            "AND json_extract(data,'$.request_id')=? ORDER BY seq DESC LIMIT 1",
            (task_id, request_id),
        ).fetchone()
        return None if row is None else json.loads(row[0])['cursor']

    def remember_replay_progress(self, task_id, actor, request_id, cursor):
        self.db.execute(
            "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), actor, task_id, 'progression.phase',
             encode({'request_id': request_id, 'cursor': deepcopy(cursor)})),
        )

    def remember_replay_result(self, task_id, actor, request_id, digest, result):
        saved = self.progression_request(task_id, request_id, digest)
        if saved is not None and saved['result'] is not None:
            if saved['result'] != result:
                raise PoiseError('Terminal progression result is immutable')
            return saved['result']
        self.db.execute(
            "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), actor, task_id, 'progression.result',
             encode({'request_id': request_id, 'digest': digest, 'result': deepcopy(result)})),
        )
        return deepcopy(result)

    def progression_request_raw(self, task_id, request_id):
        from .task_history import TaskHistoryCapture, ProgressionRequestCapture
        header = self.db.execute('SELECT status,version FROM tasks WHERE id=?', (task_id,)).fetchone()
        terminal = () if header is None else tuple(dict(r) for r in self.db.execute(
            'SELECT version,data FROM task_events WHERE task_id=? AND version=?',
            (task_id, header['version'])))
        journal = tuple(dict(r) for r in self.db.execute("SELECT event,data FROM journal WHERE task_id=? "
            "AND event IN ('progression.started','progression.noop','progression.result') "
            "AND json_extract(data,'$.request_id')=? ORDER BY seq", (task_id, request_id)))
        return ProgressionRequestCapture(None if header is None else dict(header),
                                         TaskHistoryCapture(terminal, journal, (), ()))

    @staticmethod
    def restore_progression_request(raw, request_id, digest):
        return raw.restore(request_id, digest)

    def progression_request(self, task_id, request_id, digest):
        return self.restore_progression_request(self.progression_request_raw(task_id, request_id), request_id, digest)

    def remember_progression_noop(self, task_id, actor, request_id, digest, result):
        saved = self.progression_request(task_id, request_id, digest)
        if saved is not None:
            if saved['result'] is None:
                raise PoiseError('Progression identity already belongs to an active request')
            return saved['result']
        self.db.execute(
            "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), actor, task_id, 'progression.noop',
             encode({'request_id': request_id, 'digest': digest, 'result': deepcopy(result)})),
        )
        return deepcopy(result)

    def begin_progression(self, task_id, actor, request_id, digest, target_stage):
        from .task_history import capture_history, begin_progression
        view, data = begin_progression(capture_history(self.db, task_id), self._load_state_row(task_id),
                                      task_id, actor, request_id, digest, target_stage)
        if data is not None:
            self.append_prepared_progression(task_id, actor, self.prepare_progression_entry('started', data))
        return view

    @staticmethod
    def prepare_progression_entry(kind, data):
        if kind not in ('started','reached','phase','result','noop'):
            raise PoiseError('Unknown progression journal entry')
        return 'progression.' + kind, encode(data)

    def append_prepared_progression(self, task_id, actor, prepared):
        event, data = prepared
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                        (datetime.now(timezone.utc).isoformat(), actor, task_id, event, data))

    def finish_progression(
        self, task_id: str, actor: str, request_id: str, stage_id: str
    ) -> None:
        current = progression_view(self.db, task_id, request_id)
        if current is None:
            raise PoiseError("Unknown progression request")
        if current["status"] == "reached":
            return
        self.db.execute(
            "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                actor,
                task_id,
                "progression.reached",
                encode({"request_id": request_id, "stage": stage_id}),
            ),
        )

    def load_newborn(self, task_id: str) -> NewbornTask:
        from .ownership import require_unambiguous
        require_unambiguous(self.db, task_id)
        row = self.db.execute(
            "SELECT status,claimed_by,version,metadata FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {task_id}")
        if row["status"] != TaskStatus.NEWBORN.value:
            raise PoiseError("Task is not newborn")
        return NewbornTask.restore(
            task_id, row["claimed_by"], row["version"], json.loads(row["metadata"])
        )

    def newborn_creation(self, task_id: str):
        newborn = self.load_newborn(task_id)
        if newborn.workflow is None:
            raise PoiseError('Newborn Task has no selected goal_type')
        return newborn, {
            'id':task_id,
            'sprint_id':newborn.sprint_id,
            **deepcopy(newborn.draft),
        }

    def action_lifecycle(self, task_id: str) -> TaskLifecycle:
        from .task_lifecycle import restore_lifecycle
        row = self.db.execute('SELECT version,metadata FROM tasks WHERE id=?',
                              (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f'Задача не найдена: {task_id}')
        events = tuple(tuple(r) for r in self.db.execute(
            'SELECT version,data FROM task_events WHERE task_id=? ORDER BY seq', (task_id,)))
        return restore_lifecycle(row['version'], row['metadata'], events)

    def restart_context(self, task_id: str) -> dict:
        from .task_snapshot import restore_restart_context
        row = self.db.execute('SELECT metadata FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f'Задача не найдена: {task_id}')
        return restore_restart_context(row[0])

    def publish_requirements_context(self, task_id, snapshot, agreement) -> None:
        row = self.db.execute('SELECT metadata FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f'Задача не найдена: {task_id}')
        self.patch_prepared_requirements(self.prepare_requirements_context(task_id, row[0], snapshot, agreement))

    @staticmethod
    def prepare_requirements_context(task_id, raw, snapshot, agreement):
        from .task_writes import PreparedRequirementsPatch
        metadata = json.loads(raw)
        if metadata.get('requirements_snapshot') is not None or metadata.get('requirements_agreement') is not None:
            raise PoiseError('Task requirements snapshot is immutable and already exists')
        metadata['requirements_snapshot'] = deepcopy(snapshot)
        metadata['requirements_agreement'] = deepcopy(agreement)
        return PreparedRequirementsPatch(task_id, raw, encode(metadata))

    def restart_newborn(
        self,
        newborn: NewbornTask,
        expected_version: int,
        config_hash: str,
        reason: str,
        authorization: str,
    ) -> None:
        self.restart_newborn_prepared(newborn, expected_version,
            encode(self._newborn_metadata(newborn, config_hash)), self.prepare_restart_event(reason, authorization))

    @staticmethod
    def prepare_restart_event(reason, authorization):
        return encode({'event':'restarted_newborn', 'stage':'newborn', 'iteration':1,
                       'reason':reason, 'authorization':authorization})

    def restart_newborn_prepared(self, newborn, expected_version, metadata, event):
        if newborn.version != expected_version + 1:
            raise VersionConflict("Invalid Task restart version step")
        updated = self.db.execute(
            "UPDATE tasks SET status=?,stage_index=0,iteration=1,claimed_by=?,version=?,"
            "current_submission_id=NULL,metadata=? WHERE id=? AND status IN "
            "('available','active','verified','accepted') AND version=?",
            (
                TaskStatus.NEWBORN.value,
                newborn.claimed_by,
                newborn.version,
                metadata,
                newborn.task_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise VersionConflict("Task changed before restart")
        self.db.execute('INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)',
            (newborn.task_id, newborn.version, datetime.now(timezone.utc).isoformat(), event))

    def save_newborn(self, newborn: NewbornTask, expected_version: int,
                     config_hash: str, event: str) -> None:
        if newborn.version == expected_version:
            return
        metadata = encode(self._newborn_metadata(newborn, config_hash))
        self.save_newborn_prepared(newborn, expected_version, metadata, event)

    def save_newborn_prepared(self, newborn, expected_version, metadata, event):
        if newborn.version == expected_version:
            return
        if newborn.version != expected_version + 1:
            raise VersionConflict("Недопустимый шаг версии newborn Task")
        updated = self.db.execute(
            "UPDATE tasks SET claimed_by=?,version=?,metadata=? "
            "WHERE id=? AND status=? AND version=?",
            (
                newborn.claimed_by,
                newborn.version,
                metadata,
                newborn.task_id,
                TaskStatus.NEWBORN.value,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise VersionConflict("Конфликт версии newborn Task")
        self._event(newborn.task_id, newborn.version, {
            "event": event,
            "stage": "newborn",
            "iteration": 1,
        })

    def detach_newborn(self, task_id: str, expected_sprint: str) -> None:
        newborn = self.load_newborn(task_id)
        if newborn.sprint_id != expected_sprint:
            raise VersionConflict("Newborn Task Sprint membership changed")
        changed = newborn.detach_from_sprint()
        metadata = json.loads(self.db.execute(
            "SELECT metadata FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0])
        self.save_newborn(changed, newborn.version, metadata["config_hash"], "sprint_detached")

    def save_newborn_ownership(self, newborn: NewbornTask, expected_version: int, event: str) -> None:
        if newborn.version == expected_version:
            return
        if newborn.version != expected_version + 1:
            raise VersionConflict('Invalid newborn ownership version step')
        updated = self.db.execute(
            'UPDATE tasks SET claimed_by=?,version=? WHERE id=? AND status=? AND version=?',
            (newborn.claimed_by, newborn.version, newborn.task_id, 'newborn', expected_version),
        )
        if updated.rowcount != 1:
            raise VersionConflict('Newborn ownership version changed')
        self._event(newborn.task_id, newborn.version, {
            'event': event, 'stage': 'newborn', 'iteration': 1,
        })

    def acquire_newborn(self, task_id: str, actor: str) -> None:
        newborn = self.load_newborn(task_id)
        self.save_newborn_ownership(newborn.acquire(actor), newborn.version, 'ownership_acquired')

    def release_newborn(self, task_id: str, actor: str) -> None:
        newborn = self.load_newborn(task_id)
        self.save_newborn_ownership(newborn.release(actor), newborn.version, 'ownership_released')

    def cancel_newborn(self, state: TaskState, expected_version: int,
                       actor: str, reason: str) -> None:
        if state.status != TaskStatus.CANCELLED or state.version != expected_version + 1:
            raise VersionConflict("Invalid newborn cancellation transition")
        if self.db.execute("SELECT 1 FROM submissions WHERE task_id=? LIMIT 1",
                           (state.task_id,)).fetchone() is not None:
            raise PoiseError("Newborn Task has historical execution submissions")
        # Preserve metadata byte-for-byte: cancellation is not draft editing,
        # readiness, contract materialization, or removal of historical data.
        changed = self.db.execute(
            "UPDATE tasks SET status=?,claimed_by=NULL,version=? "
            "WHERE id=? AND status='newborn' AND version=? AND claimed_by=? "
            "AND current_submission_id IS NULL",
            (state.status.value, state.version, state.task_id, expected_version, actor),
        )
        if changed.rowcount != 1:
            raise VersionConflict("Newborn cancellation snapshot changed")
        self._event(state.task_id, state.version, {
            "event": "newborn_cancelled", "stage": "newborn", "iteration": 1,
            "actor": actor, "reason": reason,
        })

    def promote_newborn(self, task: Task, metadata: dict, expected_version: int) -> None:
        from .task_snapshot import capture_task
        from .task_writes import TaskPromotionSource, prepare_promotion
        if not self.is_newborn(task.state.task_id):
            raise PoiseError('Only a newborn Task can become available')
        raw = capture_task(self.db, task.state.task_id)
        source = TaskPromotionSource(task.state.task_id, expected_version,
                                     raw.header['metadata'], raw.methods)
        self.promote_prepared(prepare_promotion(source, task, metadata), expected_version)

    def allocate(self, intent, policy, reserved_ids=()):
        from ...modules.tasks.allocation import creation_parts
        from .task_allocation import capture_allocations
        request_id, _, _ = creation_parts(intent)
        return capture_allocations(self.db, () if request_id is None else (request_id,)).allocate(
            intent, policy, reserved_ids)

    def creation_replay(self, allocation):
        from .task_allocation import creation_replay
        return creation_replay(self.db, allocation)

    def require_creation_allocation(self, allocation, receipt):
        from .task_allocation import require_allocation
        return require_allocation(self.db, allocation, receipt)

    def create(self, task: Task, metadata: dict) -> None:
        if self.exists(task.state.task_id):
            row = self.db.execute("SELECT metadata FROM tasks WHERE id=?",(task.state.task_id,)).fetchone()
            existing = json.loads(row[0])
            if metadata.get('creation_request') is not None and existing.get('creation_request') == metadata['creation_request']:
                if existing != metadata:
                    raise PoiseError('Creation request belongs to a different immutable project context')
                return
            raise PoiseError("Task ID already exists; publication cannot replace it")
        from .task_writes import prepare_task_creation
        self.create_prepared(prepare_task_creation(task, metadata, 'created'))

    def create_prepared(self, prepared):
        s = prepared.state
        if self.exists(s.task_id):
            raise PoiseError('Task ID already exists; publication cannot replace it')
        self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,NULL,?)',
            (s.task_id, s.status.value, s.stage_index, s.iteration, s.claimed_by, s.version,
             prepared.metadata))
        self._save_created_fields(prepared)

    def promote_prepared(self, prepared, expected_version):
        s = prepared.state
        changed = self.db.execute('UPDATE tasks SET status=?,stage_index=?,iteration=?,claimed_by=?,'
            'version=?,metadata=? WHERE id=? AND status=? AND version=?',
            (s.status.value, s.stage_index, s.iteration, s.claimed_by, s.version,
             prepared.metadata, s.task_id, 'newborn', expected_version))
        if changed.rowcount != 1:
            raise VersionConflict('Конфликт версии newborn Task')
        self._save_created_fields(prepared)

    def _save_created_fields(self, prepared):
        s = prepared.state
        self.db.execute('INSERT INTO content_contracts VALUES(?,?,?)',
                        (s.task_id, s.version, prepared.content))
        self.db.execute('INSERT INTO task_workflows VALUES(?,?) ON CONFLICT(task_id) '
                        'DO UPDATE SET data=excluded.data', (s.task_id, prepared.workflow))
        self._save_prepared_methods(s.task_id, s.version, prepared.methods, prepared.method_ids)
        self.db.execute('INSERT INTO task_proofs VALUES(?,?) ON CONFLICT(task_id) '
                        'DO UPDATE SET data=excluded.data', (s.task_id, prepared.proof))
        self.db.execute('INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)',
                        (s.task_id, s.version, datetime.now(timezone.utc).isoformat(), prepared.event))

    def patch_prepared_requirements(self, patch):
        changed = self.db.execute('UPDATE tasks SET metadata=? WHERE id=? AND metadata=?',
                                  (patch.after, patch.task_id, patch.before))
        if changed.rowcount != 1:
            raise VersionConflict('Task Requirements context changed; repeat the request')

    def require_unused_identity(self, task_id):
        row = self.db.execute('SELECT id FROM tasks WHERE id=? UNION SELECT id FROM sprints WHERE id=?',
                              (task_id, task_id)).fetchone()
        if row is not None:
            raise PoiseError('Publication cannot replace an existing sprint/task')

    def _load_state_row(self, task_id: str):
        from .ownership import require_unambiguous
        require_unambiguous(self.db, task_id)
        row = self.db.execute("SELECT t.id,t.status,t.stage_index,t.iteration,t.claimed_by,t.version,t.current_submission_id,t.metadata, s.digest AS submission_digest FROM tasks t LEFT JOIN submissions s ON s.seq=t.current_submission_id WHERE t.id=?", (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {task_id}")
        return row

    @staticmethod
    def _restore_state(row) -> TaskState:
        return TaskState(row["id"],row["stage_index"],row["iteration"],TaskStatus.parse(row["status"]),
                         row["claimed_by"],row["version"],row["submission_digest"])

    def load_state(self, task_id: str) -> TaskState:
        """Read current guarded Task state without its execution contract."""
        return self._restore_state(self._load_state_row(task_id))

    def load(self, task_id: str) -> Task:
        from .task_snapshot import capture_task
        return capture_task(self.db, task_id).restore()

    @staticmethod
    def _missing_criteria():
        raise PoiseError('stage criteria migration required')

    def criteria_submission_guard(self, task, actor, report):
        from .task_verification import capture_verification, criteria_submission_guard
        header = self._load_state_row(task.state.task_id)
        execution, _ = SqliteExecutionRepository(self.db).load(task.state.task_id)
        return criteria_submission_guard(capture_verification(self.db, task.state.task_id),
                                          header, execution, task, actor, report)

    def load_check_registry(self, task_id: str, metadata: dict, workflow: dict) -> CheckRegistry:
        from .task_snapshot import capture_methods, restore_registry
        return restore_registry(metadata, workflow, capture_methods(self.db, task_id))

    def restarted_feedback(self, task_id: str) -> FeedbackBook:
        row = self.db.execute(
            'SELECT data FROM task_workflows WHERE task_id=?', (task_id,)
        ).fetchone()
        if row is None:
            raise PoiseError('Restarted Task has no preserved feedback state')
        return FeedbackBook.from_dict(json.loads(row[0])['feedback'])

    def restarted_evidence(self, task_id: str) -> EvidenceBook:
        row = self.db.execute(
            'SELECT data FROM task_proofs WHERE task_id=?', (task_id,)
        ).fetchone()
        if row is None:
            raise PoiseError('Restarted Task has no preserved evidence state')
        return EvidenceBook.from_dict(json.loads(row[0])['book'])

    def restarted_registry(self, task_id: str) -> CheckRegistry:
        from .task_snapshot import capture_methods, restore_restarted_registry
        row = self.db.execute('SELECT metadata FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f'Task not found: {task_id}')
        workflow = self.db.execute('SELECT data FROM task_workflows WHERE task_id=?',
                                   (task_id,)).fetchone()
        return restore_restarted_registry(task_id, row[0], None if workflow is None else workflow[0],
                                          capture_methods(self.db, task_id))

    def start_candidates(self) -> list[dict]:
        """A coarse read projection; the Task and Sprint owners decide readiness."""
        return [dict(row) for row in self.db.execute(
            "SELECT id,metadata FROM tasks WHERE status='available' AND claimed_by IS NULL ORDER BY id")]

    def membership_snapshot(self, task_id: str) -> dict:
        row = self.db.execute(
            "SELECT t.status,t.claimed_by,t.version,t.metadata,e.data AS execution "
            "FROM tasks t LEFT JOIN task_execution e ON e.task_id=t.id WHERE t.id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {task_id}")
        metadata = json.loads(row["metadata"])
        execution = None if row["execution"] is None else json.loads(row["execution"])
        return {
            "task_id": task_id,
            "status": row["status"],
            "claimed_by": row["claimed_by"],
            "version": row["version"],
            "sprint_id": metadata["sprint_id"],
            "execution": execution,
        }

    def change_sprint_membership(
        self, task_id: str, expected_sprint: str | None,
        target_sprint: str | None, expected_version: int,
    ) -> None:
        from .task_writes import prepare_membership
        self.change_prepared_membership(prepare_membership(self._load_state_row(task_id),
            expected_sprint, target_sprint, expected_version))

    def change_prepared_membership(self, prepared):
        updated = self.db.execute("UPDATE tasks SET version=version+1,metadata=? "
            "WHERE id=? AND version=? AND json_extract(metadata,'$.sprint_id') IS ?",
            (prepared.metadata, prepared.task_id, prepared.expected_version, prepared.expected_sprint))
        if updated.rowcount != 1:
            raise VersionConflict('Task Sprint membership changed')
        self.db.execute('INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)',
            (prepared.task_id, prepared.expected_version + 1, datetime.now(timezone.utc).isoformat(), prepared.event))

    def empty_rework_recovery_point(self, task_id, handoff_version, last_report,
                                    transition_event='user_rework'):
        from .task_history import capture_history, empty_rework_recovery_point
        return empty_rework_recovery_point(capture_history(self.db, task_id), task_id,
                                           handoff_version, last_report, transition_event)

    def save(self, change: Change, expected_version: int) -> int | None:
        from .task_snapshot import capture_task
        from .task_writes import prepare_task_write
        return self.save_prepared(prepare_task_write(
            capture_task(self.db, change.task.state.task_id), change, expected_version))

    def save_prepared(self, prepared):
        state = prepared.state
        prior = self.db.execute('SELECT version,current_submission_id FROM tasks WHERE id=?',
                                (state.task_id,)).fetchone()
        if prior is None or prior['version'] != prepared.expected_version:
            raise VersionConflict('Конфликт версии задачи; новая версия не перезаписана')
        submission_id = prior['current_submission_id']
        if state.version == prepared.expected_version:
            return submission_id
        if state.submission_digest is None:
            submission_id = None
        elif prepared.submission is None and submission_id is None:
            restored = self.db.execute('SELECT seq FROM submissions WHERE task_id=? '
                'AND digest=? ORDER BY seq DESC', (state.task_id, state.submission_digest)).fetchall()
            if len(restored) != 1:
                raise PoiseError('Recovered Task requires one exact previous submission')
            submission_id = restored[0]['seq']
        candidate = prepared.submission
        if candidate is not None:
            submission_id = self.db.execute('INSERT INTO submissions(task_id,stage,iteration,digest,data) '
                'VALUES(?,?,?,?,?)', (state.task_id, candidate.stage, candidate.iteration,
                                     candidate.digest, candidate.envelope)).lastrowid
            self.db.executemany('INSERT INTO section_layers VALUES(?,?,?,?,?)',
                [(submission_id, state.task_id, *values) for values in candidate.sections])
            self.db.executemany('INSERT INTO trace_point_layers VALUES(?,?,?,?,?)',
                [(submission_id, state.task_id, *values) for values in candidate.trace])
            self.db.execute('INSERT INTO workflow_layers VALUES(?,?,?)',
                            (submission_id, state.task_id, candidate.workflow))
            if candidate.content is not None:
                self.db.execute('INSERT INTO content_contracts VALUES(?,?,?)',
                                (state.task_id, state.version, candidate.content))
        if prepared.late_criteria:
            self.db.execute("UPDATE tasks SET metadata=json_set(?,'$.criteria_pending.submission',?) "
                            'WHERE id=?', (prepared.metadata, submission_id, state.task_id))
        else:
            self.db.execute('UPDATE tasks SET metadata=? WHERE id=?',
                            (prepared.metadata, state.task_id))
        self.db.execute('UPDATE task_workflows SET data=? WHERE task_id=?',
                        (prepared.workflow, state.task_id))
        self._save_prepared_methods(state.task_id, state.version, prepared.methods, prepared.method_ids)
        if prepared.proof is not None:
            self.db.execute('UPDATE task_proofs SET data=? WHERE task_id=?',
                            (prepared.proof, state.task_id))
            self.db.execute('INSERT INTO task_proof_layers VALUES(?,?,?)',
                            (state.task_id, state.version, prepared.proof))
        changed = self.db.execute('UPDATE tasks SET status=?,stage_index=?,iteration=?,claimed_by=?,'
            'version=?,current_submission_id=? WHERE id=? AND version=?',
            (state.status.value, state.stage_index, state.iteration, state.claimed_by, state.version,
             submission_id, state.task_id, prepared.expected_version))
        if changed.rowcount != 1:
            raise VersionConflict('Конфликт версии задачи')
        for event in prepared.events:
            self.db.execute("INSERT INTO task_events(task_id,version,at,data) "
                "VALUES(?,?,?,json_set(?,'$.submission',?))", (state.task_id, state.version,
                datetime.now(timezone.utc).isoformat(), event, submission_id))
        return submission_id

    def _save_prepared_methods(self, task_id, version, methods, method_ids):
        self.db.execute('DELETE FROM task_methods WHERE task_id=? AND method_id NOT IN '
            f"({','.join('?' for _ in method_ids)})" if method_ids else
            'DELETE FROM task_methods WHERE task_id=?', (task_id, *method_ids))
        self.db.executemany('INSERT INTO task_methods(task_id,method_id,version,data) VALUES(?,?,?,?) '
            'ON CONFLICT(task_id,method_id) DO UPDATE SET version=excluded.version,data=excluded.data',
            [(task_id, method_id, version, data) for method_id, data in methods])

    @staticmethod
    def prepare_result(report):
        return encode(report), encode({'commit':report['commit'], 'tree':report['verified_tree']})

    def record_result(self, task_id: str, submission_id: int, report: dict, *, actor: str) -> None:
        self.record_prepared_result(task_id, submission_id, self.prepare_result(report), actor=actor)

    def record_prepared_result(self, task_id, submission_id, prepared, *, actor):
        report, event = prepared
        self.db.execute("INSERT INTO task_results VALUES(?,?,?)", (task_id,submission_id,report))
        self.db.execute(
            "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(),actor,task_id,'stage.verified',event))

    def _event(self, task_id: str, version: int, data: dict) -> None:
        self.db.execute("INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
                        (task_id,version,datetime.now(timezone.utc).isoformat(),encode(data)))


# Execution bookkeeping is deliberately separate from the Task lifecycle.
EXECUTION_FIELDS = frozenset(("worktree","branch","base","attempts","publication","pending","entry_tree","last_report"))


class SqliteExecutionRepository:
    def __init__(self, connection):
        self.db=connection

    @staticmethod
    def prepare_create(data):
        if set(data) != EXECUTION_FIELDS:
            raise PoiseError("Неполный/неизвестный execution snapshot")
        return encode(data)

    @staticmethod
    def prepare_save(previous, data):
        opaque = set(previous) - EXECUTION_FIELDS
        if (not EXECUTION_FIELDS <= set(data) or set(data) - EXECUTION_FIELDS - opaque
                or any(encode(data[key]) != encode(previous[key]) for key in opaque & set(data))):
            raise PoiseError("Из execution adapter нельзя менять Task lifecycle")
        return encode({**previous, **data})

    def create(self, task_id: str, data: dict) -> None:
        self.create_prepared(task_id, self.prepare_create(data))

    def create_prepared(self, task_id, data):
        self.db.execute("INSERT INTO task_execution VALUES(?,?,?)", (task_id, data, 0))

    def exists(self, task_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM task_execution WHERE task_id=?",(task_id,)).fetchone() is not None

    def load(self, task_id: str) -> tuple[dict,int]:
        row=self.db.execute("SELECT data,version FROM task_execution WHERE task_id=?",(task_id,)).fetchone()
        if row is None: raise PoiseError("Нет execution state задачи")
        return json.loads(row["data"]), row["version"]

    def save(self, task_id: str, data: dict, expected_version: int) -> int:
        previous, version = self.load(task_id)
        if version != expected_version:
            raise VersionConflict('Конфликт версии execution state')
        return self.save_prepared(task_id, self.prepare_save(previous, data), expected_version)

    def save_prepared(self, task_id, data, expected_version):
        r=self.db.execute("UPDATE task_execution SET data=?,version=version+1 WHERE task_id=? AND version=?",
                          (data,task_id,expected_version))
        if r.rowcount != 1: raise VersionConflict("Конфликт версии execution state")
        return expected_version+1

    def patch(self, task_id: str, updates: dict) -> None:
        if not set(updates) <= EXECUTION_FIELDS:
            raise PoiseError("Неизвестные поля execution update")
        if not updates: return
        data,version=self.load(task_id)
        self.save(task_id,{**data,**updates},version)

    def restart(self, task_id: str) -> None:
        data, version = self.load(task_id)
        if data["pending"] is not None:
            raise PoiseError(
                "Task has an unknown external outcome; complete its pending recovery "
                "protocol before restart"
            )
        self.save(task_id, {
            **data,
            "attempts": 0,
            "publication": None,
            "pending": None,
            "last_report": None,
        }, version)
