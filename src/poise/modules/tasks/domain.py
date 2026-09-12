from __future__ import annotations
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
from ..content.domain import SectionBook, SectionRule, SectionValue
from ..foundation.errors import DomainError
from ..workflow.domain import HandlerKind, RouteDefinition, RouteProgress
from ..workflow.handlers import handler, HandlerResult
from ..evidence.domain import EvidencePlan, EvidenceBook
from ..inspection.domain import FeedbackBook
from ..verification.domain import CheckRegistry
from ..content_requirements.domain import ContentPolicy, ContentSnapshot, TraceValue, ArtifactFact, Assessment


class TaskStatus(StrEnum):
    AVAILABLE = "available"
    ACTIVE = "active"
    VERIFIED = "verified"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


TERMINAL_TASK_STATUSES = frozenset({
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
    TaskStatus.SUPERSEDED,
})


def is_terminal_task_status(status: str | TaskStatus) -> bool:
    return status in TERMINAL_TASK_STATUSES


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainError("Идентификатор обязан быть непустой строкой")


@dataclass(frozen=True)
class StageSpec:
    stage_id: str
    rules: tuple[SectionRule, ...]

    def __post_init__(self) -> None:
        identifier(self.stage_id)
        SectionBook(self.rules)


@dataclass(frozen=True)
class Submission:
    sections: tuple[SectionValue, ...]
    artifact_paths: tuple[str, ...]
    commit_message: str
    content_additions: str
    trace_request: str
    trace_updates: tuple[TraceValue, ...]
    method_additions: str
    stage_work: str
    evidence_work: str

    def __post_init__(self) -> None:
        if (type(self.artifact_paths) is not tuple or
                any(not isinstance(p, str) or not p for p in self.artifact_paths) or
                not isinstance(self.commit_message, str)):
            raise DomainError("Пути артефактов и сообщение коммита должны быть строками")

    @property
    def digest(self) -> str:
        # Identity for submitted content, NOT identity for command execution.
        payload = {"sections": {v.name:v.content for v in self.sections},
                   "artifact_paths": list(self.artifact_paths), "commit_message": self.commit_message,
                   "content_additions":json.loads(self.content_additions), "trace":json.loads(self.trace_request), "method_additions":json.loads(self.method_additions), "stage_work":json.loads(self.stage_work), "evidence_work":json.loads(self.evidence_work)}
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TaskState:
    task_id: str
    stage_index: int
    iteration: int
    status: TaskStatus
    claimed_by: str | None
    version: int
    submission_digest: str | None


@dataclass(frozen=True)
class TaskEvent:
    kind: str
    stage_id: str
    iteration: int
    reason: str | None


@dataclass(frozen=True)
class Change:
    task: Task
    submission: Submission | None
    events: tuple[TaskEvent, ...]


@dataclass(frozen=True)
class Task:
    """Владелец переходов. Изменения возвращаются новым snapshot + ChangeSet."""
    state: TaskState
    stages: tuple[StageSpec, ...]
    content_policy: ContentPolicy
    content_snapshot: ContentSnapshot
    check_registry: CheckRegistry
    route: RouteDefinition
    progress: RouteProgress
    feedback: FeedbackBook
    evidence_plan: EvidencePlan
    evidence_book: EvidenceBook
    evidence_input: str | None
    evidence_assessment: str | None
    action_assessment: str | None

    def __post_init__(self) -> None:
        identifier(self.state.task_id)
        if (type(self.stages) is not tuple or not self.stages or
                any(not isinstance(s, StageSpec) for s in self.stages)):
            raise DomainError("Задаче требуется явный непустой маршрут")
        names = [s.stage_id for s in self.stages]
        if tuple(names) != self.content_policy.stages or tuple(names) != self.check_registry.stages:
            raise DomainError("Маршрут Task не соответствует content/check контракту")
        if self.content_policy.method_ids != self.check_registry.method_ids:
            raise DomainError("Точки проверки ссылаются не на текущий реестр методов")
        if tuple(names) != tuple(n.stage_id for n in self.route.nodes):
            raise DomainError("Маршрут Task не соответствует конфигурации handlers")
        if (type(self.state.stage_index) is not int or not 0 <= self.state.stage_index < len(names)
                or type(self.state.iteration) is not int or self.state.iteration < 1):
            raise DomainError("Неверная позиция задачи")
        if tuple(s.stage for s in self.evidence_plan.stages) != tuple(names):
            raise DomainError("Evidence plan не соответствует этапам Task")
        for s in self.evidence_plan.stages:
            if s.handler != self.route.node(s.stage).handler.value:
                raise DomainError("Evidence plan относится к другому обработчику")
        visits = dict(self.progress.visits)
        if set(visits) != set(names) or visits[names[self.state.stage_index]] != self.state.iteration:
            raise DomainError("Позиция Task не соответствует сохранённым посещениям")
        if len(names) != len(set(names)):
            raise DomainError("Повтор этапа в маршруте")
        if (not 0 <= self.state.stage_index < len(self.stages) or
                self.state.iteration < 1 or self.state.version < 0):
            raise DomainError("Нарушена позиция задачи")
        if self.state.status in (TaskStatus.AVAILABLE, TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED) and self.state.claimed_by is not None:
            raise DomainError("Завершённая задача не может оставаться занятой")

    @classmethod
    def new(cls, task_id: str, stages: tuple[StageSpec, ...], actor: str, content_policy: ContentPolicy, check_registry: CheckRegistry, route: RouteDefinition, evidence_plan: EvidencePlan) -> Task:
        identifier(actor)
        return cls(TaskState(task_id, route.index(route.entry), 1, TaskStatus.ACTIVE, actor, 0, None), stages, content_policy, ContentSnapshot((), ()), check_registry, route, RouteProgress.initial(route), FeedbackBook.empty(), evidence_plan, EvidenceBook.empty(), None, None, None)

    @classmethod
    def planned(cls, task_id, stages, content_policy, check_registry, route, evidence_plan):
        return cls(TaskState(task_id,route.index(route.entry),1,TaskStatus.AVAILABLE,None,0,None),
                   stages,content_policy,ContentSnapshot((),()),check_registry,route,
                   RouteProgress.initial(route),FeedbackBook.empty(),evidence_plan,EvidenceBook.empty(),None,None,None)

    def start(self, actor):
        identifier(actor)
        if self.state.status != TaskStatus.AVAILABLE:
            raise DomainError("Only an available task can be started")
        return self._change("started",None,None,status=TaskStatus.ACTIVE,claimed_by=actor)

    def handoff(self, actor: str, reason: str) -> Change:
        self._owned(actor)
        if self.state.claimed_by != actor or self.state.status not in (TaskStatus.ACTIVE,TaskStatus.VERIFIED,TaskStatus.ACCEPTED):
            raise DomainError("Handoff requires current owned unfinished work")
        if not isinstance(reason,str) or not reason.strip():
            raise DomainError("Handoff reason is required")
        return self._change("handed_off",reason,None,claimed_by=None)

    def resume_handoff(self, actor: str) -> Change:
        identifier(actor)
        if self.state.claimed_by is not None or self.state.status not in (TaskStatus.ACTIVE,TaskStatus.VERIFIED,TaskStatus.ACCEPTED):
            raise DomainError("Only unowned preserved work can be resumed")
        return self._change("handoff_resumed",None,None,claimed_by=actor)

    @property
    def stage(self) -> StageSpec:
        return self.stages[self.state.stage_index]

    def _owned(self, actor: str) -> None:
        identifier(actor)
        if self.state.claimed_by not in (actor, None):
            raise DomainError("Задача связана с другой сессией")

    def _change(self, kind: str, reason: str | None, submission: Submission | None, **updates) -> Change:
        state = replace(self.state, version=self.state.version + 1, **updates)
        event = TaskEvent(kind, self.stage.stage_id, self.state.iteration, reason)
        return Change(replace(self, state=state), submission, (event,))

    def _unchanged(self) -> Change:
        return Change(self, None, ())

    def submit(self, actor: str, sections: dict[str, str], artifact_paths: tuple[str, ...],
               commit_message: str, content_additions: dict, trace: dict, method_additions: list[dict], stage_work: dict, evidence_work: dict) -> Change:
        self._owned(actor)
        if self.state.status != TaskStatus.ACTIVE or self.state.claimed_by != actor:
            raise DomainError("Результат принимается только в активный этап текущей сессии")
        self.evidence_plan.validate_work(self.stage.stage_id, evidence_work)
        stage_handler = handler(self.route.node(self.stage.stage_id).handler)
        handling = stage_handler.evaluate(stage_work, self.feedback, self.stage.stage_id, self.state.iteration)
        work_json = json.dumps(stage_work, sort_keys=True, ensure_ascii=False)
        registry = self.check_registry.extend(method_additions)
        registry.validate_route(self.route)
        policy = replace(self.content_policy, method_ids=registry.method_ids).extend(content_additions)
        if not isinstance(sections, dict):
            raise DomainError("sections должен быть объектом")
        standard = {r.name for r in self.stage.rules}
        prepared = SectionBook(self.stage.rules).prepare({k:v for k,v in sections.items() if k in standard})
        snapshot = policy.apply(self.content_snapshot.with_sections(prepared), self.stage.stage_id,
                                {k:v for k,v in sections.items() if k not in standard}, trace)
        extra = tuple(v for v in snapshot.sections if v.name in sections and v.name not in standard)
        before = self.content_snapshot.trace_map()
        updates = tuple(v for v in snapshot.trace
                        if (v.route_id, v.point_id) not in before or before[(v.route_id, v.point_id)] != v.value)
        submission = Submission(prepared + extra, artifact_paths, commit_message,
                                json.dumps(content_additions,sort_keys=True,ensure_ascii=False),
                                json.dumps(trace,sort_keys=True,ensure_ascii=False), updates,
                                json.dumps(method_additions,sort_keys=True,ensure_ascii=False), work_json,
                                json.dumps(evidence_work,sort_keys=True,ensure_ascii=False))
        if submission.digest == self.state.submission_digest:
            return self._unchanged()
        change = self._change("submitted", None, submission, submission_digest=submission.digest)
        return replace(change, task=replace(change.task, content_policy=policy, content_snapshot=snapshot, check_registry=registry, evidence_input=submission.evidence_work, evidence_assessment=None, action_assessment=None,
                       progress=replace(change.task.progress, outcome=handling.outcome, stage_work=work_json)))

    def assess_content(self, phase: str, artifacts: tuple[ArtifactFact, ...]) -> Assessment:
        return self.content_policy.evaluate(self.stage.stage_id, phase, self.content_snapshot, artifacts)

    def mark_verified(self, actor: str, submission_digest: str, artifacts: tuple[ArtifactFact, ...]) -> Change:
        self._owned(actor)
        if self.state.submission_digest is None or submission_digest != self.state.submission_digest:
            raise DomainError("Проверен не текущий содержательный результат")
        self.assess_content("pre", artifacts).require()
        self.assess_content("post", artifacts).require()
        if self.evidence_plan.stage(self.stage.stage_id).active:
            assessed = None if self.evidence_assessment is None else json.loads(self.evidence_assessment)
            if assessed is None or not assessed['ready'] or assessed['submission_digest'] != submission_digest:
                raise DomainError("Нет завершённой проверки обязательных доказательств текущего результата")
        if self.state.status == TaskStatus.VERIFIED:
            return self._unchanged()
        if self.state.status != TaskStatus.ACTIVE:
            raise DomainError("Нет активного этапа для verify")
        if self.progress.stage_work is None:
            raise DomainError("Нет содержательного результата обработчика")
        handling = self._handling()
        if handling.outcome is None:
            raise DomainError("No successful receipt for current external action")
        change = self._change("verified", None, None, status=TaskStatus.VERIFIED)
        return replace(change, task=replace(change.task, feedback=handling.feedback,
                                           progress=replace(self.progress, outcome=handling.outcome)))

    def accept(self, actor: str, advance: bool) -> Change:
        self._owned(actor)
        if type(advance) is not bool:
            raise DomainError("Нужно явное решение о продолжении")
        if self.state.status == TaskStatus.COMPLETED:
            return self._unchanged()
        if self.state.status not in (TaskStatus.VERIFIED, TaskStatus.ACCEPTED):
            raise DomainError("Можно принять только проверенный результат")
        if self.progress.outcome is None:
            raise DomainError("Нет проверенного outcome этапа")
        target = self.route.node(self.stage.stage_id).target(self.progress.outcome)
        if target is None:
            if self.feedback.open_findings:
                raise DomainError("Нельзя завершить задачу с открытыми внутренними находками")
            return self._change("user_accept", None, None, status=TaskStatus.COMPLETED, claimed_by=None)
        if advance:
            progress = self.route.enter(self.progress, target)
            return self._enter("user_accept_and_continue", None, actor, target, progress)
        if self.state.status == TaskStatus.ACCEPTED:
            return self._unchanged()
        return self._change("user_accept", None, None, status=TaskStatus.ACCEPTED)

    def _enter(self, event: str, reason: str | None, actor: str, target: str, progress: RouteProgress) -> Change:
        state = replace(self.state, version=self.state.version + 1,
                        stage_index=self.route.index(target), iteration=dict(progress.visits)[target],
                        status=TaskStatus.ACTIVE, claimed_by=actor, submission_digest=None)
        # Replace progress and position together: __post_init__ checks their equality.
        updated = replace(self, state=state, progress=progress, evidence_input=None, evidence_assessment=None, action_assessment=None)
        return Change(updated, None, (TaskEvent(event, self.stage.stage_id, self.state.iteration, reason),))

    def rework(self, actor: str, feedback: str, target: str | None = None) -> Change:
        self._owned(actor)
        if not isinstance(feedback, str) or not feedback.strip():
            raise DomainError("Для rework требуется замечание пользователя")
        if self.state.status not in (TaskStatus.VERIFIED, TaskStatus.ACCEPTED, TaskStatus.COMPLETED):
            raise DomainError("Rework открывает ранее предъявленный результат")
        destination = self.stage.stage_id if target is None else target
        if destination not in self.route.node(self.stage.stage_id).rework_targets:
            raise DomainError("Возврат на этот этап не разрешён конфигурацией")
        progress = self.route.enter(self.progress, destination)
        return self._enter("user_rework", feedback, actor, destination, progress)

    def rework_failed(self, actor: str, feedback: str, tree: str, execution_key: str,
                      target: str | None = None) -> Change:
        self._owned(actor)
        if not isinstance(feedback,str) or not feedback.strip():
            raise DomainError("Для rework требуется замечание пользователя")
        if self.state.status != TaskStatus.ACTIVE or self.state.submission_digest is None:
            raise DomainError("Failed-check rework относится к активному submitted этапу")
        batch=self.evidence_book.failed_batch(
            self.stage.stage_id,self.state.iteration,self.state.submission_digest,tree,execution_key)
        if batch is None:
            raise DomainError("Нет точного известного failed check batch текущего результата")
        destination=self.stage.stage_id if target is None else target
        if destination not in self.route.node(self.stage.stage_id).rework_targets:
            raise DomainError("Возврат на этот этап не разрешён конфигурацией")
        if self.route.node(destination).handler == HandlerKind.REVISE and not self.feedback.open_findings:
            raise DomainError("Нельзя перейти к исправлению без открытых находок")
        progress=self.route.enter(self.progress,destination)
        return self._enter("user_failed_check_rework",feedback,actor,destination,progress)

    def restart_action(self, actor, feedback, target):
        self._owned(actor)
        if self.state.status != TaskStatus.ACTIVE or self.route.node(self.stage.stage_id).handler.value not in ('apply_plan','publish'):
            raise DomainError('Only a current action can be explicitly restarted')
        if not isinstance(feedback,str) or not feedback.strip():
            raise DomainError('User instruction is required for restarting an external plan')
        destination=self.stage.stage_id if target is None else target
        if destination not in self.route.node(self.stage.stage_id).rework_targets:
            raise DomainError('Rework target is not declared')
        progress=self.route.enter(self.progress,destination)
        return self._enter('user_action_rework',feedback,actor,destination,progress)

    def workflow_context(self) -> dict:
        node = self.route.node(self.stage.stage_id)
        feedback = self.feedback
        if self.state.status == TaskStatus.ACTIVE and self.progress.stage_work is not None:
            feedback = self._handling().feedback
        next_stage = None if self.progress.outcome is None else node.target(self.progress.outcome)
        return {"handler":node.handler.value, "outcome":self.progress.outcome,
                "next_stage":next_stage, "terminal":self.progress.outcome is not None and next_stage is None,
                "visits":dict(self.progress.visits), "transitions":self.progress.transitions,
                "rework_targets":list(node.rework_targets), "feedback":feedback.context(),
                "stage_work_template":handler(node.handler).template(), "evidence":self.evidence_context()}

    def _handling(self):
        handling = handler(self.route.node(self.stage.stage_id).handler).evaluate(
            json.loads(self.progress.stage_work), self.feedback, self.stage.stage_id, self.state.iteration)
        if self.route.node(self.stage.stage_id).handler.value in ('apply_plan','publish'):
            if self.action_assessment is None:
                return handling
            assessed = json.loads(self.action_assessment)
            if assessed['submission_digest'] == self.state.submission_digest and assessed['receipt']['status'] == 'complete':
                return HandlerResult('complete',handling.feedback)
            return handling
        if self.evidence_assessment is not None:
            assessed = json.loads(self.evidence_assessment)
            if assessed['ready'] and assessed['submission_digest'] == self.state.submission_digest:
                if handling.outcome is None:
                    return HandlerResult(assessed['outcome'], handling.feedback)
                if assessed['outcome'] == 'changes_requested':
                    return HandlerResult('changes_requested', handling.feedback)
        return handling

    def record_observations(self, actor, tree, execution_key, receipts):
        self._owned(actor)
        if self.state.status != TaskStatus.ACTIVE or self.state.submission_digest is None:
            raise DomainError("Наблюдения принадлежат активному submitted этапу")
        book = self.evidence_book.record_submission_batch(
            self.stage.stage_id,self.state.iteration,tree,execution_key,self.state.submission_digest,receipts)
        if book == self.evidence_book:
            return self._unchanged()
        change = self._change('observations_recorded',None,None)
        base=handler(self.route.node(self.stage.stage_id).handler).evaluate(
            json.loads(self.progress.stage_work),self.feedback,self.stage.stage_id,self.state.iteration)
        return replace(change,task=replace(change.task,evidence_book=book,evidence_assessment=None,
                                           progress=replace(self.progress,outcome=base.outcome)))

    def assess_evidence(self, actor, tree, execution_key):
        self._owned(actor)
        if self.state.status != TaskStatus.ACTIVE or self.evidence_input is None:
            raise DomainError("Нет текущего результата для оценки evidence")
        assessment = self.evidence_book.assess(self.evidence_plan,self.stage.stage_id,self.state.iteration,
                                              tree,execution_key,json.loads(self.evidence_input))
        value = json.dumps({**assessment.to_dict(),'tree':tree,'execution_key':execution_key,
                            'submission_digest':self.state.submission_digest},sort_keys=True,ensure_ascii=False)
        if value == self.evidence_assessment and assessment.book == self.evidence_book:
            return self._unchanged(), assessment
        change = self._change('evidence_assessed',None,None)
        updated = replace(change.task,evidence_book=assessment.book,evidence_assessment=value)
        handling = updated._handling()
        updated = replace(updated,progress=replace(updated.progress,outcome=handling.outcome))
        return replace(change,task=updated), assessment

    def record_action_result(self, actor, tree, receipt):
        self._owned(actor)
        if self.state.status != TaskStatus.ACTIVE or self.state.submission_digest is None:
            raise DomainError("Action receipt requires an active submitted iteration")
        if self.route.node(self.stage.stage_id).handler.value not in ('apply_plan','publish'):
            raise DomainError("This handler does not own external action receipts")
        if not isinstance(tree,str) or not tree or receipt.get('status') != 'complete':
            raise DomainError("Successful external receipt and tree are required")
        value=json.dumps({'tree':tree,'receipt':receipt,'submission_digest':self.state.submission_digest},sort_keys=True,ensure_ascii=False)
        if self.action_assessment == value:return self._unchanged()
        change=self._change('action_assessed',None,None)
        return replace(change,task=replace(change.task,action_assessment=value,
                                           progress=replace(self.progress,outcome='complete')))

    def evidence_context(self):
        book = self.evidence_book.to_dict()
        return {'plan':self.evidence_plan.describe(self.stage.stage_id),
                'assessment':None if self.evidence_assessment is None else json.loads(self.evidence_assessment),
                'arguments':book['arguments'],'decisions':book['decisions'],
                'observations':[{'id':r['id'],'method':r['method'],'obligations':r['obligations'],
                                 'tree':b['tree'],'stage':b['stage'],'iteration':b['iteration'],
                                 'passed':r['passed'],'actual_exit_code':r['actual_exit_code']}
                                for b in book['batches'] for r in b['receipts']]}

    def evidence_snapshot(self):
        return {'book':self.evidence_book.to_dict(),'input':self.evidence_input,'assessment':self.evidence_assessment}

    def workflow_snapshot(self) -> dict:
        return {"progress":self.progress.to_dict(),"feedback":self.feedback.to_dict(),"action_assessment":self.action_assessment}

    def cancel(self, actor: str, reason: str) -> Change:
        self._owned(actor)
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("Нужна инструкция пользователя об отмене")
        if self.state.status == TaskStatus.SUPERSEDED:
            raise DomainError("A superseded Task is immutable")
        if self.state.status == TaskStatus.CANCELLED:
            return self._unchanged()
        return self._change("user_cancel", reason, None, status=TaskStatus.CANCELLED, claimed_by=None)

    def cancel_from_sprint(self, reason: str) -> Change:
        """Apply explicit Sprint authority without impersonating the Task claimant."""
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("Нужна инструкция пользователя об отмене")
        if self.state.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return self._unchanged()
        return self._change("user_cancel", reason, None, status=TaskStatus.CANCELLED, claimed_by=None)

    def supersede(self, actor: str, reason: str) -> Change:
        self._owned(actor)
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("Task replacement reason is required")
        if self.state.status not in (
            TaskStatus.AVAILABLE,
            TaskStatus.ACTIVE,
            TaskStatus.VERIFIED,
            TaskStatus.ACCEPTED,
        ):
            raise DomainError("Only an unfinished Task can be superseded")
        return self._change("superseded", reason, None, status=TaskStatus.SUPERSEDED, claimed_by=None)
