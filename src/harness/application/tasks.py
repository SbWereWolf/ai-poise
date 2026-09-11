from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from ..modules.tasks.domain import Task, TaskState, TaskStatus
from ..modules.workflow.domain import RouteDefinition
from ..modules.tasks.ports import TaskUnitOfWork
from ..modules.tasks.contracts import stages_from_process, content_policy_from_metadata, evidence_plan_from_metadata
from ..modules.verification.domain import CheckRegistry
from ..modules.content_requirements.domain import ArtifactFact, Assessment
from ..modules.foundation.errors import DomainError


@dataclass(frozen=True)
class SubmissionReceipt:
    submission_id: int
    created: bool
    version: int
    digest: str


class TaskCommands:
    """One application API for Task changes. Every call uses a short UoW."""
    def __init__(self, unit_of_work: Callable[[], TaskUnitOfWork]):
        self.unit_of_work = unit_of_work

    def create(self, task_id: str, actor: str, metadata: dict, execution: dict) -> None:
        from ..modules.tasks.definition import build_task
        if task_id != metadata['contract']['id']:
            raise DomainError('Task identity does not match creation contract')
        task = build_task(metadata,actor)
        with self.unit_of_work() as uow:
            uow.tasks.create(task, metadata)
            uow.execution.create(task_id, execution)

    def start(self, task_id, actor, execution):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            change=task.start(actor)
            uow.tasks.save(change,task.state.version)
            uow.execution.create(task_id,execution)

    def submit(self, task_id: str, actor: str, payload: dict) -> SubmissionReceipt:
        if not isinstance(payload, dict) or set(payload) != {"sections","artifact_paths","commit_message","content_additions","trace","method_additions","stage_work","evidence_work"}:
            raise DomainError("Требуется точный stage result: sections, artifact_paths, commit_message, content_additions, trace, method_additions, stage_work, evidence_work")
        if not isinstance(payload["artifact_paths"],list):
            raise DomainError("artifact_paths должен быть списком путей")
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            change = task.submit(actor, payload["sections"], tuple(payload["artifact_paths"]), payload["commit_message"],
                                 payload["content_additions"], payload["trace"], payload["method_additions"], payload["stage_work"], payload["evidence_work"])
            submission_id = uow.tasks.save(change, task.state.version)
            if submission_id is None:
                raise DomainError("Нарушен контракт сохранения submission")
            return SubmissionReceipt(submission_id, change.submission is not None,
                                     change.task.state.version, change.task.state.submission_digest)

    def validate_submission(self, task_id: str, actor: str, payload: dict) -> None:
        """Pure candidate validation before ArtifactFactory external side effects."""
        if not isinstance(payload,dict) or set(payload)!={"sections","artifact_paths","commit_message","content_additions","trace","method_additions","stage_work","evidence_work"}:
            raise DomainError("Invalid stage result fields")
        if not isinstance(payload['artifact_paths'],list):
            raise DomainError('artifact_paths must be a list')
        with self.unit_of_work() as uow:
            uow.tasks.load(task_id).submit(actor,payload['sections'],tuple(payload['artifact_paths']),
                payload['commit_message'],payload['content_additions'],payload['trace'],
                payload['method_additions'],payload['stage_work'],payload['evidence_work'])

    def assess_content(self, task_id: str, phase: str, artifacts: tuple[ArtifactFact, ...]) -> Assessment:
        with self.unit_of_work() as uow:
            return uow.tasks.load(task_id).assess_content(phase, artifacts)

    def workflow_context(self, task_id: str) -> dict:
        with self.unit_of_work() as uow:
            return uow.tasks.load(task_id).workflow_context()

    def content_context(self, task_id: str) -> dict:
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            return task.content_policy.describe(task.stage.stage_id, task.content_snapshot)

    def mark_verified(self, task_id: str, actor: str, digest: str, report: dict, artifacts: tuple[ArtifactFact, ...]) -> TaskState:
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            if task.route.node(task.stage.stage_id).handler.value in ('apply_plan','publish'):
                import json
                assessed = None if task.action_assessment is None else json.loads(task.action_assessment)
                if assessed is None or assessed['tree'] != report['verified_tree']:
                    raise DomainError("Action receipt is not for the verified tree")
            if task.evidence_plan.stage(task.stage.stage_id).active:
                import json
                assessed = None if task.evidence_assessment is None else json.loads(task.evidence_assessment)
                if assessed is None or assessed['tree'] != report['verified_tree']:
                    raise DomainError("Доклад относится не к проверенному состоянию evidence")
            change=task.mark_verified(actor,digest,artifacts)
            if change.task.state.version == task.state.version:
                execution,_=uow.execution.load(task_id)
                if execution["last_report"] != report:
                    raise DomainError("Повтор не может заменить сохранённый доклад/receipt")
                return task.state
            submission_id = uow.tasks.save(change,task.state.version)
            if submission_id is None:
                raise DomainError("Нет submission для проверенного результата")
            uow.tasks.record_result(task_id, submission_id, report)
            uow.execution.patch(task_id,{"last_report":report,"publication":None})
            return change.task.state

    def accept(self, task_id: str, actor: str, advance: bool, entry_tree: str | None) -> TaskState:
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            change=task.accept(actor,advance)
            updates={}
            if change.task.state.version != task.state.version:
                execution,_=uow.execution.load(task_id)
                report=execution["last_report"]
                if report is None:
                    raise DomainError("Нет сохранённого доклада проверенного этапа")
                updates["last_report"]={**report,"status":"completed" if change.task.state.status == TaskStatus.COMPLETED else "accepted"}
                if (change.task.state.stage_index, change.task.state.iteration) != (task.state.stage_index, task.state.iteration):
                    if not isinstance(entry_tree,str) or not entry_tree:
                        raise DomainError("Для нового этапа требуется наблюдаемое entry_tree")
                    if entry_tree != report["verified_tree"]:
                        raise DomainError("Результат изменён после доклада: сначала явный rework, не переход к осмотру другого дерева")
                    updates.update(entry_tree=entry_tree,attempts=0,publication=None,pending=None)
            uow.tasks.save(change,task.state.version)
            uow.execution.patch(task_id,updates)
            return change.task.state

    def rework(self, task_id: str, actor: str, feedback: str, entry_tree: str, target: str | None = None) -> TaskState:
        if not isinstance(entry_tree,str) or not entry_tree:
            raise DomainError("Для rework требуется наблюдаемое entry_tree")
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            change=task.rework(actor,feedback,target)
            uow.tasks.save(change,task.state.version)
            uow.execution.patch(task_id,{"entry_tree":entry_tree,"attempts":0,"publication":None,"pending":None})
            return change.task.state

    def rework_failed(self, task_id: str, actor: str, feedback: str, entry_tree: str,
                      execution_key: str, target: str | None = None) -> TaskState:
        if not isinstance(entry_tree,str) or not entry_tree or not isinstance(execution_key,str) or not execution_key:
            raise DomainError("Failed-check rework требует точные tree и execution key")
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            change=task.rework_failed(actor,feedback,entry_tree,execution_key,target)
            uow.tasks.save(change,task.state.version)
            uow.execution.patch(task_id,{"entry_tree":entry_tree,"attempts":0,"publication":None,"pending":None})
            return change.task.state

    def failed_observation_batch(self, task_id, tree, execution_key):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            return task.evidence_book.failed_batch(
                task.stage.stage_id,task.state.iteration,task.state.submission_digest,tree,execution_key)

    def cancel(self, task_id: str, actor: str, reason: str) -> TaskState:
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            change=task.cancel(actor,reason)
            uow.tasks.save(change,task.state.version)
            return change.task.state

    def evidence_precheck(self, task_id, actor, tree, execution_key):
        import json
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            task._owned(actor)
            if task.evidence_input is None:
                raise DomainError('Нет evidence submission')
            return task.evidence_book.precheck(task.evidence_plan,task.stage.stage_id,task.state.iteration,
                                               tree,execution_key,json.loads(task.evidence_input))

    def observation_batch(self, task_id, tree, execution_key):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            return task.evidence_book.batch(task.stage.stage_id,task.state.iteration,tree,execution_key)

    def record_observations(self, task_id, actor, tree, execution_key, receipts):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            change=task.record_observations(actor,tree,execution_key,receipts)
            uow.tasks.save(change,task.state.version)

    def assess_evidence(self, task_id, actor, tree, execution_key):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            change,assessment=task.assess_evidence(actor,tree,execution_key)
            uow.tasks.save(change,task.state.version)
            return assessment.to_dict()
