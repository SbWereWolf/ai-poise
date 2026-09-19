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
from ..foundation.paths import matches_allowed_path


class TaskStatus(StrEnum):
    NEWBORN = "newborn"
    AVAILABLE = "available"
    ACTIVE = "active"
    VERIFIED = "verified"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @classmethod
    def parse(cls, value):
        try:
            return cls(value)
        except (TypeError, ValueError) as exc:
            raise DomainError(f"Unsupported Task status: {value!r}; explicit migration is required") from exc


TERMINAL_TASK_STATUSES = frozenset({
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
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
class TaskStageContract:
    stage_id: str
    allowed_paths: tuple[str, ...]
    entry_requirements: tuple[str, ...]
    exit_requirements: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "stage_id": self.stage_id,
            "allowed_paths": list(self.allowed_paths),
            "entry_requirements": list(self.entry_requirements),
            "exit_requirements": list(self.exit_requirements),
        }


def _contract_names(value, label):
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))):
        raise DomainError(f"{label}: requires unique nonempty strings")
    return tuple(value)


def _reachable_without(route: RouteDefinition, start: str, target: str, excluded: str) -> bool:
    pending = [start]
    reached = set()
    while pending:
        stage = pending.pop()
        if stage == excluded or stage in reached:
            continue
        if stage == target:
            return True
        reached.add(stage)
        node = route.node(stage)
        pending.extend(value for _, value in node.transitions if value is not None)
        pending.extend(node.rework_targets)
    return False


@dataclass(frozen=True)
class TaskStageContracts:
    items: tuple[TaskStageContract, ...]

    @classmethod
    def parse(cls, raw, route: RouteDefinition, policy: ContentPolicy):
        if not isinstance(raw, list):
            raise DomainError("stage contracts require an explicit list with exact route coverage")
        parsed = []
        for value in raw:
            if not isinstance(value, dict) or set(value) != {
                "stage_id", "allowed_paths", "entry_requirements", "exit_requirements"
            }:
                raise DomainError("stage contract requires exact fields")
            stage_id = value["stage_id"]
            if not isinstance(stage_id, str) or not stage_id:
                raise DomainError("stage contract stage_id is required")
            parsed.append(TaskStageContract(
                stage_id,
                _contract_names(value["allowed_paths"], f"{stage_id}.allowed_paths"),
                _contract_names(value["entry_requirements"], f"{stage_id}.entry_requirements"),
                _contract_names(value["exit_requirements"], f"{stage_id}.exit_requirements"),
            ))
        route_stages = tuple(node.stage_id for node in route.nodes)
        if (tuple(item.stage_id for item in parsed) != route_stages
                or len({item.stage_id for item in parsed}) != len(parsed)):
            raise DomainError("stage contracts require exact route coverage in route order")
        requirements = {requirement.id: requirement for requirement in policy.requirements}
        for item in parsed:
            node = route.node(item.stage_id)
            if node.read_only and item.allowed_paths:
                raise DomainError(f"{item.stage_id}: read-only stage cannot have writable scope")
            for phase, refs in (("pre", item.entry_requirements), ("post", item.exit_requirements)):
                for ref in refs:
                    requirement = requirements.get(ref)
                    if requirement is None or requirement.phase != phase or item.stage_id not in requirement.stages:
                        raise DomainError(f"{item.stage_id}: requirement phase mismatch")
        book = cls(tuple(parsed))
        book._validate_artifact_sources(route, policy)
        return book

    def stage(self, stage_id: str) -> TaskStageContract:
        for item in self.items:
            if item.stage_id == stage_id:
                return item
        raise DomainError(f"Unknown Task stage contract: {stage_id}")

    def to_list(self) -> list[dict]:
        return [item.to_dict() for item in self.items]

    def replace(self, stage_id: str, replacement: TaskStageContract) -> TaskStageContracts:
        if replacement.stage_id != stage_id:
            raise DomainError("Replacement stage contract identity mismatch")
        self.stage(stage_id)
        return TaskStageContracts(tuple(
            replacement if item.stage_id == stage_id else item for item in self.items
        ))

    def _validate_artifact_sources(self, route: RouteDefinition, policy: ContentPolicy) -> None:
        active_ids = {
            requirement_id
            for contract in self.items
            for requirement_id in contract.entry_requirements + contract.exit_requirements
        }
        artifact_requirements = [
            requirement for requirement in policy.requirements
            if requirement.kind == "artifact" and requirement.id in active_ids
        ]
        for requirement in artifact_requirements:
            details = json.loads(requirement.details)
            source = details.get("source")
            if source is None or source["kind"] == "preexisting":
                continue
            producer = source.get("producer_stage")
            starts = {route.entry} | {
                target for node in route.nodes for target in node.rework_targets
            }
            for consumer in requirement.stages:
                if source["kind"] == "declared_arrival":
                    arrival = source["arrival_stage"]
                    if consumer != arrival and any(
                        _reachable_without(route, start, consumer, arrival) for start in starts
                    ):
                        raise DomainError(
                            "declared artifact arrival cannot satisfy an earlier entry gate"
                        )
                    continue
                if requirement.phase == "post":
                    if consumer != producer:
                        raise DomainError("stage output must be an exit gate of its producer")
                    consumed = any(
                        candidate.phase == "pre"
                        and json.loads(candidate.details) == details
                        for candidate in artifact_requirements
                    )
                    if consumed and not any(
                        details["pattern"] == pattern
                        or matches_allowed_path(details["pattern"], pattern)
                        for pattern in self.stage(producer).allowed_paths
                    ):
                        raise DomainError("artifact producer scope does not cover its declared output")
                    continue
                if consumer == producer:
                    raise DomainError(
                        "stage output is unavailable at producer entry; require producer exit"
                    )
                bypass = any(
                    _reachable_without(route, start, consumer, producer) for start in starts
                )
                if bypass:
                    label = "rework path does not let producer dominate consumer" if any(
                        start != route.entry and _reachable_without(route, start, consumer, producer)
                        for start in starts
                    ) else "producer does not dominate consumer"
                    raise DomainError(label)
                output = next((candidate for candidate in artifact_requirements
                    if candidate.phase == "post" and producer in candidate.stages
                    and json.loads(candidate.details) == details), None)
                if output is None or output.id not in self.stage(producer).exit_requirements:
                    raise DomainError("artifact producer requires an independent exit gate")


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

    def __post_init__(self):
        if not isinstance(self.status, TaskStatus):
            raise DomainError(f"Unsupported Task status: {self.status!r}")


@dataclass(frozen=True)
class TaskEvent:
    kind: str
    stage_id: str
    iteration: int
    reason: str | None


@dataclass(frozen=True)
class EmptyReworkRecoveryPoint:
    stage_id: str
    iteration: int
    submission_digest: str
    progress: RouteProgress
    feedback: FeedbackBook
    evidence_book: EvidenceBook
    evidence_input: str
    evidence_assessment: str | None


@dataclass(frozen=True)
class Change:
    task: Task
    submission: Submission | None
    events: tuple[TaskEvent, ...]
    registry_change: dict | None = None


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
    stage_contracts: TaskStageContracts | None = None
    duplicate_reuse: str | None = None

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
        if (self.stage_contracts is not None
                and tuple(item.stage_id for item in self.stage_contracts.items) != tuple(names)):
            raise DomainError("Stage contracts do not match the Task route")
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
        if self.state.status in (TaskStatus.AVAILABLE, TaskStatus.COMPLETED, TaskStatus.CANCELLED) and self.state.claimed_by is not None:
            raise DomainError("Завершённая задача не может оставаться занятой")

    @classmethod
    def new(cls, task_id: str, stages: tuple[StageSpec, ...], actor: str | None, content_policy: ContentPolicy, check_registry: CheckRegistry, route: RouteDefinition, evidence_plan: EvidencePlan, stage_contracts: TaskStageContracts | None = None) -> Task:
        if actor is not None:
            identifier(actor)
        return cls(TaskState(task_id, route.index(route.entry), 1, TaskStatus.ACTIVE, actor, 0, None), stages, content_policy, ContentSnapshot((), ()), check_registry, route, RouteProgress.initial(route), FeedbackBook.empty(), evidence_plan, EvidenceBook.empty(), None, None, None, stage_contracts)

    @classmethod
    def planned(cls, task_id, stages, content_policy, check_registry, route, evidence_plan, stage_contracts=None):
        return cls(TaskState(task_id,route.index(route.entry),1,TaskStatus.AVAILABLE,None,0,None),
                   stages,content_policy,ContentSnapshot((),()),check_registry,route,
                   RouteProgress.initial(route),FeedbackBook.empty(),evidence_plan,EvidenceBook.empty(),None,None,None,stage_contracts)

    def _require_stage_contracts(self) -> TaskStageContracts:
        if self.stage_contracts is None:
            raise DomainError(
                "stage_contract_transition_required: initialize_stage_contracts is required"
            )
        return self.stage_contracts

    def initialize_stage_contracts(self, contracts: TaskStageContracts) -> Change:
        if self.stage_contracts is not None:
            raise DomainError("Stage contracts are already initialized")
        if not isinstance(contracts, TaskStageContracts):
            raise DomainError("Exact parsed stage contracts are required")
        change = self._change("stage_contracts_initialized", None, None)
        return replace(change, task=replace(change.task, stage_contracts=contracts))

    def revise_stage_contract(
        self, actor: str, stage_id: str, replacement: TaskStageContract
    ) -> Change:
        contracts = self._require_stage_contracts()
        identifier(actor)
        if self.state.claimed_by not in (None, actor):
            raise DomainError("Stage contract revision requires released or current ownership")
        if self.route.node(self.stage.stage_id).handler != HandlerKind.INSPECT:
            raise DomainError("Stage contract revision requires the current inspection stage")
        revised = contracts.replace(stage_id, replacement)
        revised = TaskStageContracts.parse(revised.to_list(), self.route, self.content_policy)
        change = self._change("stage_contract_revised", None, None)
        return replace(change, task=replace(
            change.task,
            stage_contracts=revised,
            state=replace(change.task.state, claimed_by=actor),
        ))

    def start(self, actor=None):
        self._require_ordinary_stage()
        self._require_stage_contracts()
        if actor is not None:
            identifier(actor)
        if self.state.status != TaskStatus.AVAILABLE:
            raise DomainError("Only an available task can be started")
        return self._change("started",None,None,status=TaskStatus.ACTIVE,claimed_by=actor)

    def can_start(self, artifacts=()) -> bool:
        """Task-owned, side-effect-free readiness; resumable work is not a start."""
        if self.state.status != TaskStatus.AVAILABLE or self.state.claimed_by is not None:
            return False
        self.start()  # Validate the same saved route contract; do not persist a change.
        return self.assess_content('pre', artifacts).passed

    def handoff(self, actor: str, reason: str) -> Change:
        self._owned(actor)
        if self.state.claimed_by != actor or self.state.status not in (TaskStatus.ACTIVE,TaskStatus.VERIFIED,TaskStatus.ACCEPTED):
            raise DomainError("Handoff requires current owned unfinished work")
        if not isinstance(reason,str) or not reason.strip():
            raise DomainError("Handoff reason is required")
        return self._change("handed_off",reason,None,claimed_by=None)

    def resume_handoff(self, actor: str) -> Change:
        self._require_stage_contracts()
        identifier(actor)
        if self.state.claimed_by is not None or self.state.status not in (TaskStatus.ACTIVE,TaskStatus.VERIFIED,TaskStatus.ACCEPTED):
            raise DomainError("Only unowned preserved work can be resumed")
        return self._change("handoff_resumed",None,None,claimed_by=actor)

    def acquire_ownership(self, actor: str) -> Change:
        self._require_stage_contracts()
        identifier(actor)
        if self.state.claimed_by is not None or self.state.status not in (
                TaskStatus.ACTIVE, TaskStatus.VERIFIED, TaskStatus.ACCEPTED):
            raise DomainError("Only an unowned unfinished Task can be acquired")
        return self._change("ownership_acquired", None, None, claimed_by=actor)

    def release_ownership(self, actor: str) -> Change:
        self._owned(actor)
        if self.state.claimed_by != actor or self.state.status not in (
                TaskStatus.ACTIVE, TaskStatus.VERIFIED, TaskStatus.ACCEPTED):
            raise DomainError("Only an owned unfinished Task can be released")
        return self._change("ownership_released", None, None, claimed_by=None)

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

    def _unchanged(self, registry_change: dict | None = None) -> Change:
        return Change(self, None, (), registry_change)

    def _observe_registry_change(self, raw: dict):
        subject_methods = set(
            self.evidence_plan.stage(self.stage.stage_id).subject_methods
        )
        if (isinstance(raw, dict)
                and set(raw) == {
                    'request_id', 'expected_revision', 'operations',
                    'executable_obligations',
                }
                and isinstance(raw['operations'], list)
                and not any(
                    request.request_id == raw['request_id']
                    for request in self.check_registry.requests
                )
                and raw['expected_revision'] == self.check_registry.revision):
            for operation in raw['operations']:
                if isinstance(operation, dict) and operation.get('kind') != 'replace':
                    raise DomainError(
                        'observe registry change permits only exact method replacement'
                    )
                if (isinstance(operation, dict)
                        and operation.get('kind') == 'replace'
                        and operation.get('method_id') in self.check_registry.method_ids
                        and isinstance(operation.get('registration'), dict)
                        and operation['registration'].get('stages') != list(next(
                            entry.stages for entry in self.check_registry.entries
                            if entry.method_id == operation['method_id']
                        ))):
                    raise DomainError('observe registry change cannot reschedule a method')
        result = self.check_registry.apply_change(raw)
        if tuple(raw['executable_obligations']) != self.check_registry.executable_obligations:
            raise DomainError(
                'observe registry change cannot alter executable_obligations'
            )
        current = {entry.method_id: entry for entry in self.check_registry.entries}
        updated = {entry.method_id: entry for entry in result.registry.entries}
        for operation in raw['operations']:
            method_id = operation['method_id']
            if operation['kind'] != 'replace':
                raise DomainError('observe registry change permits only exact method replacement')
            if method_id not in subject_methods:
                raise DomainError(
                    'observe registry change requires a current subject method'
                )
            if updated[method_id].stages != current[method_id].stages:
                raise DomainError('observe registry change cannot reschedule a method')
            if (updated[method_id].evidence_kind != current[method_id].evidence_kind
                    or updated[method_id].covers != current[method_id].covers):
                raise DomainError(
                    'observe registry change cannot alter evidence_kind or covers'
                )
        return result

    def _registry_change_audit(self, raw: dict, registry, actor: str, replayed: bool):
        request = next(
            request for request in registry.requests
            if request.request_id == raw['request_id']
        )
        if replayed:
            if request.audit is None:
                raise DomainError('registry change replay has no durable audit receipt')
            return registry, {**json.loads(request.audit), 'replayed': True}
        audit = {
            'actor': actor,
            'methods': [operation['method_id'] for operation in raw['operations']],
            'new_revision': request.revision,
            'previous_revision': raw['expected_revision'],
            'request_id': raw['request_id'],
            'stage': self.stage.stage_id,
            'iteration': self.state.iteration,
            'task': self.state.task_id,
        }
        receipt_id = hashlib.sha256(json.dumps(
            audit, sort_keys=True, ensure_ascii=False, separators=(',', ':')
        ).encode('utf-8')).hexdigest()
        durable = {**audit, 'receipt_id': receipt_id}
        return registry.bind_request_audit(raw['request_id'], durable), {
            **durable, 'replayed': False,
        }

    def submit(self, actor: str, sections: dict[str, str], artifact_paths: tuple[str, ...],
               commit_message: str, content_additions: dict, trace: dict,
               method_additions: list[dict] | dict, stage_work: dict, evidence_work: dict) -> Change:
        self._require_ordinary_stage()
        self._require_stage_contracts()
        self._owned(actor)
        if self.state.status != TaskStatus.ACTIVE or self.state.claimed_by != actor:
            raise DomainError("Результат принимается только в активный этап текущей сессии")
        self.evidence_plan.validate_work(self.stage.stage_id, evidence_work)
        stage_handler = handler(self.route.node(self.stage.stage_id).handler)
        handling = stage_handler.evaluate(stage_work, self.feedback, self.stage.stage_id, self.state.iteration)
        work_json = json.dumps(stage_work, sort_keys=True, ensure_ascii=False)
        registry_change = None
        registry_event = None
        if isinstance(method_additions, dict):
            owns_test_registry = 'test_registry' in {rule.name for rule in self.stage.rules}
            observes_evidence = (
                self.route.node(self.stage.stage_id).handler == HandlerKind.OBSERVE
            )
            if owns_test_registry:
                result = self.check_registry.apply_change(method_additions)
            elif observes_evidence:
                result = self._observe_registry_change(method_additions)
                observe_registry, registry_change = self._registry_change_audit(
                    method_additions, result.registry, actor, result.replayed
                )
                result = replace(result, registry=observe_registry)
                if not result.replayed:
                    durable = {key: value for key, value in registry_change.items()
                               if key != 'replayed'}
                    registry_event = TaskEvent(
                        'verification_registry_changed',
                        self.stage.stage_id,
                        self.state.iteration,
                        json.dumps(durable, sort_keys=True, ensure_ascii=False),
                    )
            else:
                raise DomainError(
                    f'{self.stage.stage_id}: изменение test_registry на этом этапе запрещено'
                )
            registry = result.registry
        else:
            registry = self.check_registry.extend(method_additions)
        registry.validate_route(self.route)
        # Rehydrate the prospective evidence contract before any submission is
        # persisted. An otherwise valid registry can activate an empty observe
        # stage or remove an observation still required by this plan.
        evidence_plan = EvidencePlan.parse(
            {node.stage_id: self.evidence_plan.describe(node.stage_id)
             for node in self.route.nodes},
            {node.stage_id: node.handler.value for node in self.route.nodes},
            {node.stage_id: [entry.method_id for entry in registry.entries
                             if node.stage_id in entry.stages]
             for node in self.route.nodes},
        )
        policy = replace(self.content_policy, method_ids=registry.method_ids).extend(content_additions)
        contracts = TaskStageContracts.parse(
            self._require_stage_contracts().to_list(), self.route, policy
        )
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
            return self._unchanged(registry_change)
        change = self._change("submitted", None, submission, submission_digest=submission.digest)
        events = change.events + (() if registry_event is None else (registry_event,))
        return replace(change, task=replace(change.task, content_policy=policy, content_snapshot=snapshot, check_registry=registry, evidence_plan=evidence_plan, stage_contracts=contracts, evidence_input=submission.evidence_work, evidence_assessment=None, action_assessment=None,
                       progress=replace(change.task.progress, outcome=handling.outcome, stage_work=work_json)),
                       events=events, registry_change=registry_change)

    def assess_content(self, phase: str, artifacts: tuple[ArtifactFact, ...]) -> Assessment:
        return self.assess_stage_content(self.stage.stage_id, phase, artifacts)

    def assess_stage_content(
        self, stage_id: str, phase: str, artifacts: tuple[ArtifactFact, ...]
    ) -> Assessment:
        self.route.node(stage_id)
        contract = self._require_stage_contracts().stage(stage_id)
        requirement_ids = (
            contract.entry_requirements if phase == "pre" else contract.exit_requirements
        )
        return self.content_policy.evaluate(
            stage_id,
            phase,
            self.content_snapshot,
            artifacts,
            requirement_ids,
        )

    @property
    def check_candidate_digest(self) -> str | None:
        if self.duplicate_reuse is None:
            return self.state.submission_digest
        return hashlib.sha256(json.dumps(json.loads(self.duplicate_reuse)["candidate"],
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def prepare_duplicate_reuse(self, actor: str, candidate: dict) -> Change:
        """Reserve verification-only work without entering a different route stage."""
        self._owned(actor)
        self._require_stage_contracts()
        if self.state.status not in (TaskStatus.AVAILABLE, TaskStatus.ACTIVE,
                                     TaskStatus.VERIFIED, TaskStatus.ACCEPTED):
            raise DomainError("Reuse requires an unfinished executable Task")
        if self.feedback.open_findings or self.feedback.pending_resolutions:
            raise DomainError("Reuse cannot discard unresolved local findings")
        if self.duplicate_reuse is not None:
            saved = json.loads(self.duplicate_reuse)
            if saved["candidate"] != candidate:
                raise DomainError("Reuse candidate changed; restart explicitly")
            if self.state.claimed_by == actor:
                return self._unchanged()
            return self._change("duplicate_reuse_resumed", None, None, claimed_by=actor)
        change = self._change("duplicate_reuse_prepared", candidate["source_task_id"], None,
                              status=TaskStatus.ACTIVE, claimed_by=actor)
        return replace(change, task=replace(change.task, duplicate_reuse=json.dumps({
            "candidate": candidate, "verified": False, "execution_key": None,
        }, sort_keys=True)))

    def mark_duplicate_reuse_verified(self, actor: str, tree: str,
                                     execution_key: str, receipts: list[dict]) -> Change:
        self._owned(actor)
        if (self.duplicate_reuse is None or self.state.status != TaskStatus.ACTIVE
                or self.state.claimed_by != actor):
            raise DomainError("No active owned reuse verification")
        reuse = json.loads(self.duplicate_reuse)
        candidate = reuse["candidate"]
        batch = self.evidence_book.submission_batch(
            self.stage.stage_id, self.state.iteration, self.check_candidate_digest,
            tree, execution_key)
        if (tree != candidate["verified_tree"] or batch is None
                or batch["receipts"] != receipts
                or [r["method"] for r in receipts] != candidate["method_ids"]
                or any(not r["passed"] or not r["interpretable"] for r in receipts)):
            raise DomainError("Reuse requires its complete successful local checks")
        change = self._change("duplicate_reuse_verified", candidate["source_task_id"], None,
                              status=TaskStatus.VERIFIED)
        return replace(change, task=replace(change.task, duplicate_reuse=json.dumps({
            **reuse, "verified": True, "execution_key": execution_key,
        }, sort_keys=True)))

    def accept_duplicate_reuse(self, actor: str) -> Change:
        self._owned(actor)
        if (self.duplicate_reuse is None or self.state.status != TaskStatus.VERIFIED
                or self.state.claimed_by != actor
                or not json.loads(self.duplicate_reuse)["verified"]):
            raise DomainError("Reuse acceptance requires a locally verified owned result")
        return self._change("duplicate_reuse_accepted", None, None,
                            status=TaskStatus.COMPLETED, claimed_by=None)

    def _require_ordinary_stage(self) -> None:
        if self.duplicate_reuse is not None:
            raise DomainError("Verification-only reuse does not permit ordinary stage work")

    def mark_verified(self, actor: str, submission_digest: str, artifacts: tuple[ArtifactFact, ...]) -> Change:
        self._require_ordinary_stage()
        self._require_stage_contracts()
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
        if self.stage.stage_id in self.check_registry.inspection_stages:
            self.check_registry.validate_inspection_exit()
        handling = self._handling()
        if handling.outcome is None:
            raise DomainError("No successful receipt for current external action")
        change = self._change("verified", None, None, status=TaskStatus.VERIFIED)
        return replace(change, task=replace(change.task, feedback=handling.feedback,
                                           progress=replace(self.progress, outcome=handling.outcome)))

    def accept(self, actor: str, advance: bool) -> Change:
        self._require_ordinary_stage()
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

    def progress_stage(self, actor: str, target: str) -> Change:
        """Enter an ordinary next stage without recording user acceptance."""
        self._require_ordinary_stage()
        self._owned(actor)
        if self.state.status not in (TaskStatus.VERIFIED, TaskStatus.ACCEPTED):
            raise DomainError("Stage progression requires a verified result")
        if self.progress.outcome is None:
            raise DomainError("Stage progression requires an exact verified outcome")
        expected = self.route.node(self.stage.stage_id).target(self.progress.outcome)
        if expected is None or target != expected:
            raise DomainError("Stage progression requires the ordinary next stage")
        if self.route.node(target).handler == HandlerKind.PUBLISH:
            raise DomainError("Publish entry requires separate user acceptance")
        return self._enter(
            "stage_progressed",
            None,
            actor,
            target,
            self.route.enter(self.progress, target),
        )

    def _enter(self, event: str, reason: str | None, actor: str, target: str, progress: RouteProgress) -> Change:
        state = replace(self.state, version=self.state.version + 1,
                        stage_index=self.route.index(target), iteration=dict(progress.visits)[target],
                        status=TaskStatus.ACTIVE, claimed_by=actor, submission_digest=None)
        # Replace progress and position together: __post_init__ checks their equality.
        updated = replace(self, state=state, progress=progress, evidence_input=None, evidence_assessment=None, action_assessment=None)
        return Change(updated, None, (TaskEvent(event, self.stage.stage_id, self.state.iteration, reason),))

    def _ensure_pending_resolutions_inspected(self) -> None:
        pending_resolutions = self.feedback.pending_resolutions
        if not pending_resolutions:
            return
        current = self.route.node(self.stage.stage_id)
        inspection_stage = (
            self.stage.stage_id
            if current.handler == HandlerKind.INSPECT
            else current.target(self.progress.outcome)
        )
        if (inspection_stage is None or
                self.route.node(inspection_stage).handler != HandlerKind.INSPECT):
            raise DomainError(
                "Rework недоступен: маршрут не определяет обязательный этап "
                "осмотра ожидающих исправлений"
            )
        resolution_ids = ", ".join(
            resolution.id for resolution in pending_resolutions
        )
        raise DomainError(
            f"Rework недоступен: исправления {resolution_ids} ещё не осмотрены. "
            f"Продолжите задачу на этап {inspection_stage} и рассмотрите каждое "
            "исправление."
        )

    def inspection_registry_blocker(self, target: str | None) -> str | None:
        """Observe a current gate defect, never authorize an unchecked transition."""
        node = self.route.node(self.stage.stage_id)
        if (self.state.status != TaskStatus.ACTIVE or
                self.state.submission_digest is None or self.progress.stage_work is None or
                node.handler != HandlerKind.INSPECT or
                self.stage.stage_id not in self.check_registry.inspection_stages or
                target is None or target not in node.rework_targets):
            return None
        destination = self.stages[self.route.index(target)]
        if not any(rule.name == "test_registry" for rule in destination.rules):
            return None
        try:
            self.check_registry.validate_inspection_exit()
        except DomainError as error:
            return str(error)
        return None

    def rework(self, actor: str, feedback: str, target: str | None = None) -> Change:
        self._require_ordinary_stage()
        self._owned(actor)
        if not isinstance(feedback, str) or not feedback.strip():
            raise DomainError("Для rework требуется замечание пользователя")
        if self.state.status == TaskStatus.ACTIVE:
            blocker = self.inspection_registry_blocker(target)
            if blocker is None:
                raise DomainError("Rework requires a reproducible inspection registry blocker")
            self._ensure_pending_resolutions_inspected()
            inspected = replace(self, feedback=self._handling().feedback)
            inspected._ensure_pending_resolutions_inspected()
            return inspected._enter(
                "user_inspection_registry_rework", feedback, actor, target,
                self.route.enter(self.progress, target),
            )
        if self.state.status not in (TaskStatus.VERIFIED, TaskStatus.ACCEPTED, TaskStatus.COMPLETED):
            raise DomainError("Rework открывает ранее предъявленный результат")
        self._ensure_pending_resolutions_inspected()
        destination = self.stage.stage_id if target is None else target
        if destination not in self.route.node(self.stage.stage_id).rework_targets:
            raise DomainError("Возврат на этот этап не разрешён конфигурацией")
        progress = self.route.enter(self.progress, destination)
        return self._enter("user_rework", feedback, actor, destination, progress)

    def recover_empty_transition(
        self, reason: str, point: EmptyReworkRecoveryPoint, recovery_event: str
    ) -> Change:
        self._require_ordinary_stage()
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("Empty rework recovery requires an explicit reason")
        if self.state.status != TaskStatus.ACTIVE or self.state.claimed_by is not None:
            raise DomainError("Empty rework recovery requires released active work")
        if self.state.submission_digest is not None:
            raise DomainError("Current rework iteration is not empty: submission exists")
        if self.progress.outcome is not None or self.progress.stage_work is not None:
            raise DomainError("Current rework iteration contains work")
        if not isinstance(point, EmptyReworkRecoveryPoint):
            raise DomainError("Exact previous verified recovery point is required")
        if self.route.node(point.stage_id).handler in (HandlerKind.APPLY_PLAN, HandlerKind.PUBLISH):
            raise DomainError("External-action results cannot use empty rework recovery")
        if self.route.enter(point.progress, self.stage.stage_id) != self.progress:
            raise DomainError("Current iteration is not the single empty transition after the verified result")
        if dict(point.progress.visits)[point.stage_id] != point.iteration:
            raise DomainError("Previous verified recovery point has inconsistent iteration")
        if self.feedback != point.feedback or self.evidence_book != point.evidence_book:
            raise DomainError("Feedback or evidence changed after the verified result")
        state = replace(
            self.state,
            stage_index=self.route.index(point.stage_id),
            iteration=point.iteration,
            status=TaskStatus.VERIFIED,
            version=self.state.version + 1,
            submission_digest=point.submission_digest,
        )
        recovered = replace(
            self,
            state=state,
            progress=point.progress,
            feedback=point.feedback,
            evidence_book=point.evidence_book,
            evidence_input=point.evidence_input,
            evidence_assessment=point.evidence_assessment,
            action_assessment=None,
        )
        if recovery_event not in ("empty_rework_recovered", "empty_advance_recovered"):
            raise DomainError("Unknown empty transition recovery event")
        event = TaskEvent(recovery_event, self.stage.stage_id, self.state.iteration, reason)
        return Change(recovered, None, (event,))

    def rework_failed(self, actor: str, feedback: str, tree: str, execution_key: str,
                      target: str | None = None) -> Change:
        self._require_ordinary_stage()
        self._owned(actor)
        if not isinstance(feedback,str) or not feedback.strip():
            raise DomainError("Для rework требуется замечание пользователя")
        if self.state.status != TaskStatus.ACTIVE or self.state.submission_digest is None:
            raise DomainError("Failed-check rework относится к активному submitted этапу")
        batch=self.evidence_book.failed_batch(
            self.stage.stage_id,self.state.iteration,self.state.submission_digest,tree,execution_key)
        if batch is None:
            raise DomainError("Нет точного известного failed check batch текущего результата")
        self._ensure_pending_resolutions_inspected()
        destination=self.stage.stage_id if target is None else target
        if destination not in self.route.node(self.stage.stage_id).rework_targets:
            raise DomainError("Возврат на этот этап не разрешён конфигурацией")
        progress=self.route.enter(self.progress,destination)
        return self._enter("user_failed_check_rework",feedback,actor,destination,progress)

    def restart_action(self, actor, feedback, target):
        self._require_ordinary_stage()
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
        contracts = self._require_stage_contracts()
        node = self.route.node(self.stage.stage_id)
        feedback = self.feedback
        if self.state.status == TaskStatus.ACTIVE and self.progress.stage_work is not None:
            feedback = self._handling().feedback
        next_stage = None if self.progress.outcome is None else node.target(self.progress.outcome)
        return {"handler":node.handler.value, "outcome":self.progress.outcome,
                "next_stage":next_stage, "terminal":self.progress.outcome is not None and next_stage is None,
                "visits":dict(self.progress.visits), "transitions":self.progress.transitions,
                "rework_targets":list(node.rework_targets), "feedback":feedback.context(),
                "stage_work_template":handler(node.handler).template(), "evidence":self.evidence_context(),
                "stage_contract":contracts.stage(self.stage.stage_id).to_dict()}

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
        if self.duplicate_reuse is not None:
            if self.state.status != TaskStatus.ACTIVE or self.state.claimed_by != actor:
                raise DomainError("Reuse observations require the active owner")
            book = self.evidence_book.record_submission_batch(
                self.stage.stage_id, self.state.iteration, tree, execution_key,
                self.check_candidate_digest, receipts)
            change = self._change("duplicate_reuse_observations", None, None)
            return replace(change, task=replace(change.task, evidence_book=book))
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

    def recover_pending_checks(self, actor):
        self._require_ordinary_stage()
        self._owned(actor)
        if self.state.status != TaskStatus.ACTIVE or self.state.submission_digest is None:
            raise DomainError("Check recovery requires the current active submission")
        return self._change("pending_checks_recovered", None, None)

    def assess_evidence(self, actor, tree, execution_key):
        self._require_ordinary_stage()
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
        self._require_ordinary_stage()
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
        return {
            "progress": self.progress.to_dict(),
            "feedback": self.feedback.to_dict(),
            "action_assessment": self.action_assessment,
            "registry": self.check_registry.to_state(),
            **({"duplicate_reuse": json.loads(self.duplicate_reuse)}
               if self.duplicate_reuse is not None else {}),
        }

    def recover_cancelled(
        self, actor: str, previous: TaskState, reason: str, authorization: str,
    ) -> Change:
        identifier(actor)
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("Cancellation recovery reason is required")
        if not isinstance(authorization, str) or not authorization.strip():
            raise DomainError("Cancellation recovery authorization is required")
        if self.state.status != TaskStatus.CANCELLED:
            raise DomainError("Recovery requires a cancelled unfinished Task")
        if previous.status not in (TaskStatus.AVAILABLE, TaskStatus.ACTIVE,
                                   TaskStatus.VERIFIED, TaskStatus.ACCEPTED):
            raise DomainError("A terminal completed/integrated Task cannot be recovered")
        if (previous.task_id != self.state.task_id
                or previous.version + 1 != self.state.version
                or previous.stage_index != self.state.stage_index
                or previous.iteration != self.state.iteration
                or previous.submission_digest != self.state.submission_digest):
            raise DomainError("Cancellation recovery point does not match the exact Task")
        # Recovery does not grant a claim, restart the route, or change evidence.
        return self._change("cancellation_recovered", reason, None,
                            status=previous.status, claimed_by=None)

    def cancel(self, actor: str, reason: str) -> Change:
        self._owned(actor)
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("Нужна инструкция пользователя об отмене")
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
