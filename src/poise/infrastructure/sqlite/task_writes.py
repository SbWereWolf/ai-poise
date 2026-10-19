"""Canonical Task persistence encoding, performed on detached domain results."""
from dataclasses import dataclass, replace
from copy import deepcopy
import json
from .tasks import encode
from ...modules.foundation.errors import PoiseError, VersionConflict
from ...modules.tasks.stage_criteria import split_work
from ...modules.tasks.domain import TaskState, TaskStatus


@dataclass(frozen=True)
class PreparedSubmission:
    digest: str
    stage: str
    iteration: int
    envelope: str
    sections: tuple
    trace: tuple
    workflow: str
    content: str | None


@dataclass(frozen=True)
class PreparedTaskWrite:
    state: TaskState
    expected_version: int
    submission: PreparedSubmission | None
    metadata: str | None
    late_criteria: bool
    workflow: str | None
    methods: tuple
    method_ids: tuple
    proof: str | None
    events: tuple[str, ...]


@dataclass(frozen=True)
class PreparedTaskCreation:
    state: TaskState
    metadata: str
    content: str
    workflow: str
    proof: str
    methods: tuple
    method_ids: tuple
    event: str


@dataclass(frozen=True)
class TaskPromotionSource:
    task_id: str
    version: int
    metadata: str
    methods: tuple[str, ...]


@dataclass(frozen=True)
class PreparedRequirementsPatch:
    task_id: str
    before: str
    after: str


def prepare_promotion(source, task, metadata):
    if source.task_id != task.state.task_id:
        raise PoiseError('Prepared promotion belongs to another Task')
    previous = json.loads(source.metadata)
    metadata = deepcopy(metadata)
    if 'duplicate_parent_id' in previous:
        metadata['duplicate_parent_id'] = previous['duplicate_parent_id']
    promoted = replace(task, state=replace(task.state, version=source.version + 1,
                                         status=TaskStatus.AVAILABLE, claimed_by=None))
    return prepare_task_creation(promoted, metadata, 'became_available', source.methods)


def prepare_methods(previous_methods, registry):
    existing = {v['method']['id']:v for v in map(json.loads, previous_methods)}
    values = [(entry.method_id, entry.to_dict()) for entry in registry.entries]
    return (tuple((method_id, encode(value)) for method_id, value in values
                  if existing.get(method_id) != value), registry.method_ids)


def prepare_task_creation(task, metadata, event, previous_methods=()):
    methods, method_ids = prepare_methods(previous_methods, task.check_registry)
    state = task.state
    return PreparedTaskCreation(state, encode(metadata), encode(task.content_policy.to_layers()),
        encode(task.workflow_snapshot()), encode(task.evidence_snapshot()),
        methods, method_ids,
        encode({'event': event, 'stage': task.stage.stage_id, 'iteration': state.iteration}))


def prepare_task_write(capture, change, expected_version):
    task = change.task
    state = task.state
    if capture.header['version'] != expected_version:
        raise VersionConflict('Конфликт версии задачи; новая версия не перезаписана')
    if state.version == expected_version:
        if change.submission is not None or change.events:
            raise PoiseError('Изменения без новой версии задачи')
        return PreparedTaskWrite(state, expected_version, None, None, False, None, (), (), None, ())
    if state.version != expected_version + 1:
        raise VersionConflict('Недопустимый шаг версии задачи')
    submission = None
    if change.submission is not None:
        candidate = change.submission
        if candidate.digest != state.submission_digest:
            raise PoiseError('Содержательный результат не соответствует snapshot задачи')
        envelope = {'artifact_paths': list(candidate.artifact_paths),
            'commit_message': candidate.commit_message,
            'content_additions': json.loads(candidate.content_additions),
            'trace': json.loads(candidate.trace_request),
            'method_additions': json.loads(candidate.method_additions),
            'stage_work': json.loads(candidate.stage_work),
            'evidence_work': json.loads(candidate.evidence_work)}
        content = encode(task.content_policy.to_layers())
        submission = PreparedSubmission(candidate.digest, task.stage.stage_id, state.iteration,
            encode(envelope), tuple((v.name, v.content, v.state.value) for v in candidate.sections),
            tuple((v.route_id, v.point_id, v.data) for v in candidate.trace_updates),
            encode(task.workflow_context()), None if capture.content == content else content)
    metadata = json.loads(capture.header['metadata'])
    if task.criteria_book is None:
        raise PoiseError('stage criteria migration required')
    metadata['stage_criteria'] = task.criteria_book.data
    if task.stage_contracts is not None:
        metadata['contract']['stage_contracts'] = task.stage_contracts.to_list()
    late_criteria = False
    if change.submission is not None:
        _, intent = split_work(json.loads(change.submission.stage_work))
        metadata.pop('criteria_pending', None)
        if intent is not None:
            late_criteria = True
            metadata['criteria_pending'] = {'actor': state.claimed_by, 'submission': None,
                'stage': task.stage.stage_id, 'iteration': state.iteration,
                'entry_version': expected_version, 'intent': intent.document}
    elif state.submission_digest is None:
        metadata.pop('criteria_pending', None)
    methods, method_ids = prepare_methods(capture.methods, task.check_registry)
    proof = task.evidence_snapshot()
    encoded_proof = None if proof == json.loads(capture.proof) else encode(proof)
    prior = {key: capture.header[key] for key in
             ('status', 'stage_index', 'iteration', 'claimed_by', 'version', 'current_submission_id')}
    events = []
    for event in change.events:
        data = {'event': event.kind, 'stage': event.stage_id, 'iteration': event.iteration,
                'reason': event.reason, 'submission': None, 'actor': state.claimed_by}
        if event.kind == 'user_cancel':
            data['previous'] = prior
        events.append(encode(data))
    return PreparedTaskWrite(state, expected_version, submission, encode(metadata), late_criteria,
        encode(task.workflow_snapshot()), methods, method_ids,
        encoded_proof, tuple(events))


@dataclass(frozen=True)
class PreparedStageContractChange:
    write: PreparedTaskWrite
    actor: str
    audit: str
    result: dict


def prepare_stage_contract_change(capture, change, expected_version, audit):
    write = prepare_task_write(capture, change, expected_version)
    metadata = json.loads(write.metadata)
    history = list(metadata.get('stage_contract_history', []))
    history.append({**deepcopy(audit), 'version':change.task.state.version})
    metadata['stage_contract_history'] = history
    result = {'status':('stage_contracts_initialized' if audit['action'] == 'initialize_stage_contracts'
                       else 'stage_contract_revised'), 'task':change.task.state.task_id,
              'request_id':audit['request_id'], 'version':change.task.state.version,
              'old':deepcopy(audit['old']), 'new':deepcopy(audit['new']), 'replayed':False}
    return PreparedStageContractChange(replace(write, metadata=encode(metadata)),
                                       audit['actor'], encode(audit), result)


@dataclass(frozen=True)
class PreparedMembership:
    task_id: str
    expected_version: int
    expected_sprint: str | None
    metadata: str
    event: str


def prepare_membership(header, expected_sprint, target_sprint, expected_version):
    metadata = json.loads(header['metadata'])
    if header['version'] != expected_version or metadata['sprint_id'] != expected_sprint:
        raise VersionConflict('Task Sprint membership changed')
    metadata['sprint_id'] = target_sprint
    return PreparedMembership(header['id'], expected_version, expected_sprint, encode(metadata),
        encode({'event':'sprint_membership_changed', 'from_sprint':expected_sprint, 'to_sprint':target_sprint}))
