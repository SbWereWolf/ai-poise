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
from ...modules.tasks.contracts import stages_from_process, stored_content_policy_from_metadata, evidence_plan_from_metadata
from ...modules.content.domain import ContentState, SectionValue
from ...modules.verification.domain import CheckRegistry
from ...modules.content_requirements.domain import ContentSnapshot, TraceValue
from ...modules.workflow.domain import RouteDefinition, RouteProgress
from ...modules.inspection.domain import FeedbackBook
from ...modules.evidence.domain import EvidenceBook
from ...modules.foundation.errors import PoiseError, VersionConflict
from ...modules.tasks.newborn import NewbornTask


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
        "SELECT 1 FROM journal WHERE task_id=? AND event='progression.reached' "
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

    def restore_snapshot(self, tables: dict, binding: dict) -> None:
        """Controlled same-format import into absent identities; not a lifecycle edit."""
        from .transfer_records import restore_tasks
        if any(self.exists(row['id']) for row in tables['tasks']):
            raise PoiseError('Task snapshot cannot overwrite an existing aggregate')
        restore_tasks(self.db,tables,binding)

    def exists(self, task_id):
        return self.db.execute("SELECT 1 FROM tasks WHERE id=?",(task_id,)).fetchone() is not None

    def _newborn_metadata(self, newborn, config_hash):
        metadata = newborn.metadata(config_hash)
        if not newborn.restart_history:
            return metadata
        row = self.db.execute(
            "SELECT metadata FROM tasks WHERE id=?", (newborn.task_id,)
        ).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {newborn.task_id}")
        previous = json.loads(row[0])
        for key in ("requirements_snapshot", "requirements_agreement"):
            if key in previous:
                metadata[key] = deepcopy(previous[key])
        return metadata

    def is_newborn(self, task_id):
        row = self.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {task_id}")
        return row[0] == TaskStatus.NEWBORN.value

    def create_newborn(self, newborn: NewbornTask, config_hash: str) -> None:
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
                encode(newborn.metadata(config_hash)),
            ),
        )
        self._event(newborn.task_id, newborn.version, {
            "event": "created_newborn",
            "stage": "newborn",
            "iteration": 1,
        })

    def action_receipt(self, task_id: str, request_id: str, digest: str):
        for row in self.db.execute(
            "SELECT data FROM journal WHERE task_id=? AND event='newborn.action' ORDER BY seq",
            (task_id,),
        ):
            data = json.loads(row[0])
            if data.get("request_id") != request_id:
                continue
            if data.get("digest") != digest:
                raise PoiseError("Request ID already used with another Task action intent")
            return deepcopy(data["result"])
        return None

    def stage_contract_receipt(self, task_id: str, request_id: str, intent: dict):
        row = self.db.execute(
            "SELECT data FROM journal WHERE task_id=? AND event='stage_contract.action' "
            "ORDER BY seq",
            (task_id,),
        ).fetchall()
        for item in row:
            saved = json.loads(item[0])
            if saved.get("request_id") != request_id:
                continue
            comparable_saved = {
                key: value for key, value in saved.items() if key != "old"
            }
            comparable_intent = {
                key: value for key, value in intent.items() if key != "old"
            }
            if comparable_saved != comparable_intent:
                raise PoiseError("stage contract request conflict: request_id has another intent")
            metadata = json.loads(self.db.execute(
                "SELECT metadata FROM tasks WHERE id=?", (task_id,)
            ).fetchone()[0])
            history = metadata.get("stage_contract_history", [])
            entry = next(value for value in history if value["request_id"] == request_id)
            return {
                "status": (
                    "stage_contracts_initialized"
                    if saved["action"] == "initialize_stage_contracts"
                    else "stage_contract_revised"
                ),
                "task": task_id,
                "request_id": request_id,
                "version": entry["version"],
                "old": deepcopy(saved["old"]),
                "new": deepcopy(saved["new"]),
                "replayed": True,
            }
        return None

    def save_stage_contract_change(
        self, change: Change, expected_version: int, audit: dict
    ) -> dict:
        self.save(change, expected_version)
        task_id = change.task.state.task_id
        row = self.db.execute("SELECT metadata FROM tasks WHERE id=?", (task_id,)).fetchone()
        metadata = json.loads(row[0])
        metadata["contract"]["stage_contracts"] = change.task.stage_contracts.to_list()
        history = list(metadata.get("stage_contract_history", []))
        history.append({**deepcopy(audit), "version": change.task.state.version})
        metadata["stage_contract_history"] = history
        self.db.execute(
            "UPDATE tasks SET metadata=? WHERE id=?",
            (encode(metadata), task_id),
        )
        self.db.execute(
            "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                audit["actor"],
                task_id,
                "stage_contract.action",
                encode(audit),
            ),
        )
        return {
            "status": (
                "stage_contracts_initialized"
                if audit["action"] == "initialize_stage_contracts"
                else "stage_contract_revised"
            ),
            "task": task_id,
            "request_id": audit["request_id"],
            "version": change.task.state.version,
            "old": deepcopy(audit["old"]),
            "new": deepcopy(audit["new"]),
            "replayed": False,
        }

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
        self.db.execute(
            "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), actor, task_id, "newborn.action", encode({
                "request_id": request_id,
                "digest": digest,
                "result": deepcopy(result),
            })),
        )

    def begin_progression(
        self, task_id: str, actor: str, request_id: str, digest: str,
        target_stage: str,
    ) -> dict:
        if not isinstance(request_id, str) or not request_id:
            raise PoiseError("Progression request_id is required")
        existing = progression_view(self.db, task_id, request_id)
        if existing is not None:
            row = self.db.execute(
                "SELECT data FROM journal WHERE task_id=? AND event='progression.started' "
                "AND json_extract(data,'$.request_id')=? ORDER BY seq DESC LIMIT 1",
                (task_id, request_id),
            ).fetchone()
            saved = json.loads(row[0])
            if saved["digest"] != digest:
                raise PoiseError("Progression request conflict: request_id has another target")
            return existing
        active = progression_view(self.db, task_id)
        if active is not None and active["status"] == "active":
            raise PoiseError(
                "Task already has an active progression target; resume its exact request"
            )
        data = {
            "actor": actor,
            "digest": digest,
            "request_id": request_id,
            "target_stage": target_stage,
        }
        self.db.execute(
            "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                actor,
                task_id,
                "progression.started",
                encode(data),
            ),
        )
        return {
            "request_id": request_id,
            "target_stage": target_stage,
            "status": "active",
        }

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
        if newborn.process is None:
            raise PoiseError('Newborn Task has no selected goal_type')
        return newborn, {
            'id':task_id,
            'sprint_id':newborn.sprint_id,
            **deepcopy(newborn.draft),
        }

    def restart_context(self, task_id: str) -> dict:
        row = self.db.execute(
            "SELECT metadata FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {task_id}")
        metadata = json.loads(row[0])
        return {
            "config_hash": metadata["config_hash"],
            "contract": deepcopy(metadata.get("contract")),
            "creation_request": deepcopy(metadata.get("creation_request")),
            "process": deepcopy(metadata["process"]),
            "requirements_agreement": deepcopy(
                metadata.get("requirements_agreement")
            ),
            "requirements_snapshot": deepcopy(
                metadata.get("requirements_snapshot")
            ),
            "restart_history": deepcopy(metadata.get("restart_history", [])),
            "sprint_id": metadata["sprint_id"],
            "stage_contract_history": deepcopy(
                metadata.get("stage_contract_history", [])
            ),
        }

    def publish_requirements_context(self, task_id, snapshot, agreement) -> None:
        row = self.db.execute(
            "SELECT metadata FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise PoiseError(f"Задача не найдена: {task_id}")
        metadata = json.loads(row[0])
        if (
            metadata.get("requirements_snapshot") is not None
            or metadata.get("requirements_agreement") is not None
        ):
            raise PoiseError("Task requirements snapshot is immutable and already exists")
        metadata["requirements_snapshot"] = deepcopy(snapshot)
        metadata["requirements_agreement"] = deepcopy(agreement)
        self.db.execute(
            "UPDATE tasks SET metadata=? WHERE id=?",
            (encode(metadata), task_id),
        )

    def restart_newborn(
        self,
        newborn: NewbornTask,
        expected_version: int,
        config_hash: str,
        reason: str,
        authorization: str,
    ) -> None:
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
                encode(self._newborn_metadata(newborn, config_hash)),
                newborn.task_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise VersionConflict("Task changed before restart")
        self._event(newborn.task_id, newborn.version, {
            "event": "restarted_newborn",
            "stage": "newborn",
            "iteration": 1,
            "reason": reason,
            "authorization": authorization,
        })

    def save_newborn(self, newborn: NewbornTask, expected_version: int,
                     config_hash: str, event: str) -> None:
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
                encode(self._newborn_metadata(newborn, config_hash)),
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

    def acquire_newborn(self, task_id: str, actor: str) -> None:
        newborn = self.load_newborn(task_id)
        changed = newborn.acquire(actor)
        metadata = json.loads(self.db.execute(
            "SELECT metadata FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0])
        self.save_newborn(
            changed, newborn.version, metadata['config_hash'], 'ownership_acquired'
        )

    def release_newborn(self, task_id: str, actor: str) -> None:
        newborn = self.load_newborn(task_id)
        changed = newborn.release(actor)
        metadata = json.loads(self.db.execute(
            "SELECT metadata FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0])
        self.save_newborn(
            changed, newborn.version, metadata['config_hash'], 'ownership_released'
        )

    def promote_newborn(self, task: Task, metadata: dict, expected_version: int) -> None:
        if not self.is_newborn(task.state.task_id):
            raise PoiseError("Only a newborn Task can become available")
        promoted = replace(
            task,
            state=replace(
                task.state,
                version=expected_version + 1,
                status=TaskStatus.AVAILABLE,
                claimed_by=None,
            ),
        )
        s = promoted.state
        updated = self.db.execute(
            "UPDATE tasks SET status=?,stage_index=?,iteration=?,claimed_by=?,version=?,metadata=? "
            "WHERE id=? AND status=? AND version=?",
            (
                s.status.value,
                s.stage_index,
                s.iteration,
                s.claimed_by,
                s.version,
                encode(metadata),
                s.task_id,
                TaskStatus.NEWBORN.value,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise VersionConflict("Конфликт версии newborn Task")
        self.db.execute(
            "INSERT INTO content_contracts VALUES(?,?,?)",
            (s.task_id, s.version, encode(promoted.content_policy.to_layers())),
        )
        self.db.execute(
            "INSERT INTO task_workflows VALUES(?,?) ON CONFLICT(task_id) DO UPDATE "
            "SET data=excluded.data",
            (s.task_id, encode(promoted.workflow_snapshot())),
        )
        self._save_methods(promoted)
        self.db.execute(
            "INSERT INTO task_proofs VALUES(?,?) ON CONFLICT(task_id) DO UPDATE "
            "SET data=excluded.data",
            (s.task_id, encode(promoted.evidence_snapshot())),
        )
        self._event(s.task_id, s.version, {
            "event": "became_available",
            "stage": promoted.stage.stage_id,
            "iteration": s.iteration,
        })

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
        saved = self.db.execute("SELECT data FROM task_workflows WHERE task_id=?", (task_id,)).fetchone()
        if saved is None:
            raise PoiseError("Нет сохранённого состояния маршрута")
        workflow = json.loads(saved[0])
        from ...modules.tasks.definition import (
            obligation_catalog,
            registry_inspection_stages,
            stored_executable_obligations,
        )
        registry = CheckRegistry.from_items(
            items, tuple(s['id'] for s in metadata['process']['stages'])
        ).with_executable_obligations(
            stored_executable_obligations(
                metadata['contract'],
                metadata['process'],
                workflow.get('registry'),
            ),
            registry_inspection_stages(metadata['process']),
            obligation_catalog(metadata['contract']),
        )
        metadata['contract']['methods']=[item['method'] for item in items]
        policy = stored_content_policy_from_metadata(metadata, json.loads(record[0]))
        contracts = (
            None
            if "stage_contracts" not in metadata["contract"]
            else TaskStageContracts.parse(
                metadata["contract"]["stage_contracts"],
                RouteDefinition.from_process(metadata["process"]),
                policy,
            )
        )
        sections = self.db.execute("SELECT section_id,content,content_state FROM (SELECT *, ROW_NUMBER() OVER(PARTITION BY section_id ORDER BY submission_id DESC) AS n FROM section_layers WHERE task_id=?) WHERE n=1 ORDER BY section_id", (task_id,)).fetchall()
        trace = self.db.execute("SELECT route_id,point_id,data FROM (SELECT *, ROW_NUMBER() OVER(PARTITION BY route_id,point_id ORDER BY submission_id DESC) AS n FROM trace_point_layers WHERE task_id=?) WHERE n=1 ORDER BY route_id,point_id", (task_id,)).fetchall()
        snapshot = ContentSnapshot(tuple(SectionValue(s["section_id"],s["content"],ContentState(s["content_state"])) for s in sections),
                                   tuple(TraceValue(t["route_id"],t["point_id"],t["data"]) for t in trace))
        registry = registry.restore_state(workflow.get('registry'))
        proof_row = self.db.execute("SELECT data FROM task_proofs WHERE task_id=?",(task_id,)).fetchone()
        if proof_row is None: raise PoiseError("Нет обязательного evidence state")
        proof = json.loads(proof_row[0])
        return Task(state, stages_from_process(metadata["process"]), policy, snapshot, registry,
                    RouteDefinition.from_process(metadata["process"]),
                    RouteProgress.from_dict(workflow["progress"]), FeedbackBook.from_dict(workflow["feedback"]),
                    evidence_plan_from_metadata(metadata,registry), EvidenceBook.from_dict(proof["book"]),proof["input"],proof["assessment"],workflow["action_assessment"],contracts)

    def empty_rework_recovery_point(
        self, task_id: str, handoff_version: int, last_report: dict,
        transition_event: str = "user_rework",
    ) -> EmptyReworkRecoveryPoint:
        events = self.db.execute(
            "SELECT version,data FROM task_events "
            "WHERE task_id=? AND version<=? ORDER BY seq",
            (task_id, handoff_version),
        ).fetchall()
        parsed = [
            (row["version"], json.loads(row["data"])["event"])
            for row in events
        ]
        transitions = [
            version for version, event in parsed if event == transition_event
        ]
        if not transitions:
            raise PoiseError(f"Recovery requires the exact preceding {transition_event} event")
        transition_version = transitions[-1]
        ownership_events = {
            "handed_off",
            "handoff_resumed",
            "ownership_acquired",
            "ownership_released",
        }
        unexpected = [
            event
            for version, event in parsed
            if transition_version < version <= handoff_version
            and event not in ownership_events
        ]
        if unexpected:
            raise PoiseError(
                f"Recovery event suffix contains non-ownership events: {unexpected}"
            )
        row = self.db.execute(
            "SELECT s.stage,s.iteration,s.digest,s.data,w.data AS workflow,r.data AS result "
            "FROM submissions s "
            "JOIN workflow_layers w ON w.task_id=s.task_id AND w.submission_id=s.seq "
            "JOIN task_results r ON r.task_id=s.task_id AND r.submission_id=s.seq "
            "WHERE s.task_id=? ORDER BY s.seq DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        saved_result = None if row is None else json.loads(row["result"])
        expected_report = (
            None
            if saved_result is None
            else {
                **saved_result,
                "status": (
                    "accepted"
                    if transition_event == "user_accept_and_continue"
                    else saved_result["status"]
                ),
            }
        )
        if row is None or expected_report != last_report:
            raise PoiseError("Recovery requires the immediately preceding verified result")
        if saved_result.get("status") != "verified":
            raise PoiseError("Recovery requires a previously verified result")
        workflow = json.loads(row["workflow"])
        envelope = json.loads(row["data"])
        proof_row = self.db.execute(
            "SELECT data FROM task_proof_layers WHERE task_id=? AND version<? "
            "ORDER BY version DESC LIMIT 1",
            (task_id, transition_version),
        ).fetchone()
        if proof_row is None:
            raise PoiseError("Recovery requires the previous immutable proof snapshot")
        proof = json.loads(proof_row["data"])
        feedback = workflow["feedback"]
        progress = RouteProgress(
            tuple(workflow["visits"].items()),
            workflow["transitions"],
            workflow["outcome"],
            json.dumps(envelope["stage_work"], sort_keys=True, ensure_ascii=False),
        )
        return EmptyReworkRecoveryPoint(
            row["stage"],
            row["iteration"],
            row["digest"],
            progress,
            FeedbackBook.from_dict(
                {name: feedback[name] for name in ("findings", "resolutions", "decisions")}
            ),
            EvidenceBook.from_dict(proof["book"]),
            proof["input"],
            proof["assessment"],
        )

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
        elif change.submission is None and submission_id is None:
            restored = self.db.execute(
                "SELECT seq FROM submissions WHERE task_id=? AND digest=? ORDER BY seq DESC",
                (state.task_id, state.submission_digest),
            ).fetchall()
            if len(restored) != 1:
                raise PoiseError("Recovered Task requires one exact previous submission")
            submission_id = restored[0]["seq"]
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

    def exists(self, task_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM task_execution WHERE task_id=?",(task_id,)).fetchone() is not None

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
