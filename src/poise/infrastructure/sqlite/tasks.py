from __future__ import annotations
import json
from datetime import datetime, timezone
from ...modules.tasks.domain import Task, TaskState, TaskStatus, StageSpec, Change
from ...modules.tasks.contracts import stages_from_process, stored_content_policy_from_metadata, evidence_plan_from_metadata
from ...modules.content.domain import ContentState, SectionValue
from ...modules.verification.domain import CheckRegistry
from ...modules.content_requirements.domain import ContentSnapshot, TraceValue
from ...modules.workflow.domain import RouteDefinition, RouteProgress
from ...modules.inspection.domain import FeedbackBook
from ...modules.evidence.domain import EvidenceBook
from ...modules.foundation.errors import PoiseError, VersionConflict


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def without_retired_method_timeout(value: dict) -> dict:
    normalized = {**value, "method": dict(value["method"])}
    normalized["method"].pop("timeout_seconds", None)
    return normalized


class SqliteTaskRepository:
    """The only writer of Task aggregate, submitted content and domain events."""
    def __init__(self, connection):
        self.db = connection

    def restore_snapshot(self, tables: dict, binding: dict) -> None:
        """Controlled same-format import into absent identities; not a lifecycle edit."""
        from .transfer_records import restore_tasks
        if any(self.exists(row['id']) for row in tables['tasks']):
            raise PoiseError('Task snapshot cannot overwrite an existing aggregate')
        restore_tasks(self.db,tables,binding)

    def exists(self, task_id):
        return self.db.execute("SELECT 1 FROM tasks WHERE id=?",(task_id,)).fetchone() is not None

    def allocate(self, intent, policy, reserved_ids=()):
        from ...modules.tasks.allocation import Allocation, creation_parts
        request_id, task, request_digest = creation_parts(intent)
        if request_id is None:
            return Allocation(None, task['id'], None, False)
        if policy is None:
            raise PoiseError('task_ids allocation policy is required for automatic creation')
        for row in self.db.execute("SELECT id,metadata FROM tasks ORDER BY id"):
            creation = json.loads(row['metadata']).get('creation_request')
            if creation is None or creation.get('request_id') != request_id:
                continue
            if creation.get('digest') != request_digest:
                raise PoiseError('Creation request digest conflict; immutable intent changed')
            return Allocation(request_id, row['id'], request_digest, True)
        occupied = {row[0] for row in self.db.execute("SELECT id FROM tasks")}
        occupied.update(row[0] for row in self.db.execute("SELECT id FROM sprints"))
        occupied.update(reserved_ids)
        for candidate in policy.candidates():
            if candidate not in occupied:
                return Allocation(request_id, candidate, request_digest, False)
        raise PoiseError('Task ID allocation namespace is exhausted')

    def create(self, task: Task, metadata: dict) -> None:
        if self.exists(task.state.task_id):
            row = self.db.execute("SELECT metadata FROM tasks WHERE id=?",(task.state.task_id,)).fetchone()
            existing = json.loads(row[0])
            if metadata.get('creation_request') is not None and existing.get('creation_request') == metadata['creation_request']:
                if existing != metadata:
                    raise PoiseError('Creation request belongs to a different immutable project context')
                return
            raise PoiseError("Task ID already exists; publication cannot replace it")
        s = task.state
        self.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,NULL,?)",
                        (s.task_id,s.status.value,s.stage_index,s.iteration,s.claimed_by,s.version,encode(metadata)))
        self.db.execute("INSERT INTO content_contracts VALUES(?,?,?)", (s.task_id,s.version,encode(task.content_policy.to_layers())))
        self.db.execute("INSERT INTO task_workflows VALUES(?,?)",(s.task_id,encode(task.workflow_snapshot())))
        self._save_methods(task)
        self.db.execute("INSERT INTO task_proofs VALUES(?,?)", (s.task_id,encode(task.evidence_snapshot())))
        self._event(s.task_id,s.version,{"event":"created","stage":task.stage.stage_id,"iteration":s.iteration})

    def load(self, task_id: str) -> Task:
        row = self.db.execute("SELECT t.*, s.digest AS submission_digest FROM tasks t LEFT JOIN submissions s ON s.seq=t.current_submission_id WHERE t.id=?", (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {task_id}")
        state = TaskState(row["id"],row["stage_index"],row["iteration"],TaskStatus(row["status"]),
                          row["claimed_by"],row["version"],row["submission_digest"])
        metadata = json.loads(row["metadata"])
        record = self.db.execute("SELECT data FROM content_contracts WHERE task_id=? ORDER BY version DESC LIMIT 1", (task_id,)).fetchone()
        if record is None:
            raise PoiseError("Нет зарегистрированного контракта содержимого")
        items = [without_retired_method_timeout(json.loads(r[0])) for r in self.db.execute(
            "SELECT data FROM task_methods WHERE task_id=? ORDER BY rowid", (task_id,)
        )]
        from ...modules.tasks.definition import (
            executable_obligations,
            obligation_catalog,
            registry_inspection_stages,
        )
        registry = CheckRegistry.from_items(
            items, tuple(s['id'] for s in metadata['process']['stages'])
        ).with_executable_obligations(
            executable_obligations(metadata['contract'], metadata['process']),
            registry_inspection_stages(metadata['process']),
            obligation_catalog(metadata['contract']),
        )
        metadata['contract']['methods']=[item['method'] for item in items]
        policy = stored_content_policy_from_metadata(metadata, json.loads(record[0]))
        sections = self.db.execute("SELECT section_id,content,content_state FROM (SELECT *, ROW_NUMBER() OVER(PARTITION BY section_id ORDER BY submission_id DESC) AS n FROM section_layers WHERE task_id=?) WHERE n=1 ORDER BY section_id", (task_id,)).fetchall()
        trace = self.db.execute("SELECT route_id,point_id,data FROM (SELECT *, ROW_NUMBER() OVER(PARTITION BY route_id,point_id ORDER BY submission_id DESC) AS n FROM trace_point_layers WHERE task_id=?) WHERE n=1 ORDER BY route_id,point_id", (task_id,)).fetchall()
        snapshot = ContentSnapshot(tuple(SectionValue(s["section_id"],s["content"],ContentState(s["content_state"])) for s in sections),
                                   tuple(TraceValue(t["route_id"],t["point_id"],t["data"]) for t in trace))
        saved = self.db.execute("SELECT data FROM task_workflows WHERE task_id=?", (task_id,)).fetchone()
        if saved is None:
            raise PoiseError("Нет сохранённого состояния маршрута")
        workflow = json.loads(saved[0])
        registry = registry.restore_state(workflow.get('registry'))
        proof_row = self.db.execute("SELECT data FROM task_proofs WHERE task_id=?",(task_id,)).fetchone()
        if proof_row is None: raise PoiseError("Нет обязательного evidence state")
        proof = json.loads(proof_row[0])
        return Task(state, stages_from_process(metadata["process"]), policy, snapshot, registry,
                    RouteDefinition.from_process(metadata["process"]),
                    RouteProgress.from_dict(workflow["progress"]), FeedbackBook.from_dict(workflow["feedback"]),
                    evidence_plan_from_metadata(metadata,registry), EvidenceBook.from_dict(proof["book"]),proof["input"],proof["assessment"],workflow["action_assessment"])

    def save(self, change: Change, expected_version: int) -> int | None:
        state = change.task.state
        prior = self.db.execute("SELECT version,current_submission_id FROM tasks WHERE id=?", (state.task_id,)).fetchone()
        if prior is None or prior["version"] != expected_version:
            raise VersionConflict("Конфликт версии задачи; новая версия не перезаписана")
        submission_id = prior["current_submission_id"]
        if state.version == expected_version:
            if change.submission is not None or change.events:
                raise PoiseError("Изменения без новой версии задачи")
            return submission_id
        if state.version != expected_version + 1:
            raise VersionConflict("Недопустимый шаг версии задачи")
        if state.submission_digest is None:
            submission_id = None
        if change.submission is not None:
            candidate = change.submission
            if candidate.digest != state.submission_digest:
                raise PoiseError("Содержательный результат не соответствует snapshot задачи")
            # Section text has a single canonical location: section_layers.content.
            envelope = {"artifact_paths":list(candidate.artifact_paths),"commit_message":candidate.commit_message,
                        "content_additions":json.loads(candidate.content_additions),"trace":json.loads(candidate.trace_request),"method_additions":json.loads(candidate.method_additions), "stage_work":json.loads(candidate.stage_work), "evidence_work":json.loads(candidate.evidence_work)}
            submission_id = self.db.execute("INSERT INTO submissions(task_id,stage,iteration,digest,data) VALUES(?,?,?,?,?)",
                (state.task_id,change.task.stage.stage_id,state.iteration,candidate.digest,encode(envelope))).lastrowid
            self.db.executemany("INSERT INTO section_layers VALUES(?,?,?,?,?)", [
                (submission_id,state.task_id,v.name,v.content,v.state.value) for v in candidate.sections])
            self.db.executemany("INSERT INTO trace_point_layers VALUES(?,?,?,?,?)", [
                (submission_id,state.task_id,v.route_id,v.point_id,v.data) for v in candidate.trace_updates])
            self.db.execute("INSERT INTO workflow_layers VALUES(?,?,?)",
                (submission_id,state.task_id,encode(change.task.workflow_context())))
            previous = self.db.execute("SELECT data FROM content_contracts WHERE task_id=? ORDER BY version DESC LIMIT 1", (state.task_id,)).fetchone()
            policy_data = encode(change.task.content_policy.to_layers())
            if previous is None or previous[0] != policy_data:
                self.db.execute("INSERT INTO content_contracts VALUES(?,?,?)", (state.task_id,state.version,policy_data))
        self.db.execute("UPDATE task_workflows SET data=? WHERE task_id=?",
                        (encode(change.task.workflow_snapshot()),state.task_id))
        self._save_methods(change.task)
        proof_data=encode(change.task.evidence_snapshot())
        previous_proof=self.db.execute("SELECT data FROM task_proofs WHERE task_id=?",(state.task_id,)).fetchone()[0]
        if proof_data != previous_proof:
            self.db.execute("UPDATE task_proofs SET data=? WHERE task_id=?",(proof_data,state.task_id))
            self.db.execute("INSERT INTO task_proof_layers VALUES(?,?,?)",(state.task_id,state.version,proof_data))
        updated = self.db.execute("UPDATE tasks SET status=?,stage_index=?,iteration=?,claimed_by=?,version=?,current_submission_id=? WHERE id=? AND version=?",
            (state.status.value,state.stage_index,state.iteration,state.claimed_by,state.version,submission_id,state.task_id,expected_version))
        if updated.rowcount != 1:
            raise VersionConflict("Конфликт версии задачи")
        for event in change.events:
            self._event(state.task_id,state.version,{"event":event.kind,"stage":event.stage_id,
                                                    "iteration":event.iteration,"reason":event.reason,"submission":submission_id})
        return submission_id

    def _save_methods(self, task: Task) -> None:
        current_ids = set(task.check_registry.method_ids)
        self.db.execute(
            "DELETE FROM task_methods WHERE task_id=? AND method_id NOT IN "
            f"({','.join('?' for _ in current_ids)})" if current_ids else
            "DELETE FROM task_methods WHERE task_id=?",
            (task.state.task_id, *current_ids) if current_ids else (task.state.task_id,),
        )
        for entry in task.check_registry.entries:
            existing = self.db.execute("SELECT data FROM task_methods WHERE task_id=? AND method_id=?",(task.state.task_id,entry.method_id)).fetchone()
            data = encode(entry.to_dict())
            if existing is None:
                self.db.execute("INSERT INTO task_methods VALUES(?,?,?,?)",(task.state.task_id,entry.method_id,task.state.version,data))
            elif existing[0] != data:
                self.db.execute(
                    "UPDATE task_methods SET version=?,data=? WHERE task_id=? AND method_id=?",
                    (task.state.version, data, task.state.task_id, entry.method_id),
                )

    def record_result(self, task_id: str, submission_id: int, report: dict) -> None:
        self.db.execute("INSERT INTO task_results VALUES(?,?,?)", (task_id,submission_id,encode(report)))

    def _event(self, task_id: str, version: int, data: dict) -> None:
        self.db.execute("INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
                        (task_id,version,datetime.now(timezone.utc).isoformat(),encode(data)))


# Execution bookkeeping is deliberately separate from the Task lifecycle.
EXECUTION_FIELDS = frozenset(("worktree","branch","base","attempts","publication","pending","entry_tree","last_report"))


class SqliteExecutionRepository:
    def __init__(self, connection):
        self.db=connection

    def create(self, task_id: str, data: dict) -> None:
        if set(data) != EXECUTION_FIELDS:
            raise PoiseError("Неполный/неизвестный execution snapshot")
        self.db.execute("INSERT INTO task_execution VALUES(?,?,?)",(task_id,encode(data),0))

    def load(self, task_id: str) -> tuple[dict,int]:
        row=self.db.execute("SELECT data,version FROM task_execution WHERE task_id=?",(task_id,)).fetchone()
        if row is None: raise PoiseError("Нет execution state задачи")
        return json.loads(row["data"]), row["version"]

    def save(self, task_id: str, data: dict, expected_version: int) -> int:
        if set(data) != EXECUTION_FIELDS:
            raise PoiseError("Из execution adapter нельзя менять Task lifecycle")
        r=self.db.execute("UPDATE task_execution SET data=?,version=version+1 WHERE task_id=? AND version=?",
                          (encode(data),task_id,expected_version))
        if r.rowcount != 1: raise VersionConflict("Конфликт версии execution state")
        return expected_version+1

    def patch(self, task_id: str, updates: dict) -> None:
        if not set(updates) <= EXECUTION_FIELDS:
            raise PoiseError("Неизвестные поля execution update")
        if not updates: return
        data,version=self.load(task_id)
        self.save(task_id,{**data,**updates},version)
