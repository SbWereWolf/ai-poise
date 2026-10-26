"""Identity and confirmed results of a Task stage-result transfer, without I/O."""
from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace

from ..foundation.errors import DomainError


def validate_handoff_input(value):
    required = {'request_id', 'task_id', 'reason', 'result', 'artifacts'}
    if not isinstance(value, dict):
        raise DomainError('handoff input must be an object')
    missing = required - value.keys()
    unknown = value.keys() - required - {'target_stage'}
    if missing or unknown:
        raise DomainError(f'handoff input fields: missing={sorted(missing)}, unknown={sorted(unknown)}')
    for name in ('request_id', 'task_id', 'reason', 'target_stage'):
        if name in value and (not isinstance(value[name], str) or not value[name].strip()):
            raise DomainError(f'handoff {name} must be a nonempty string')
    if not isinstance(value['result'], dict):
        raise DomainError('handoff result must be an explicit object')
    if not isinstance(value['artifacts'], list):
        raise DomainError('handoff artifacts must be an explicit list')


def normal_progression(result):
    return result is not None and (
        result['status'] in ('role_handoff_required', 'progression_work_required',
                             'progression_target_reached', 'user_acceptance_required')
        or (result['status'] == 'progression_stopped'
            and result.get('reason') == 'task_acceptance_required')
    )


@dataclass(frozen=True)
class StageHandoffState:
    request_id: str
    actor: str
    task_id: str
    intent_digest: str
    packet_digest: str
    origin: dict
    admitted_source: dict
    progression_id: str
    target_stage: str | None
    release_id: str
    release_reason: str
    revision: int
    verification: dict | None
    verified_source: dict | None
    progression_started: dict | None
    progression: dict | None
    release_context: dict | None
    release_intent: dict | None
    release: dict | None
    completed: bool

    @classmethod
    def restore(cls, data):
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise DomainError('Unknown stage handoff state; preserve the record and inspect its owner')
        return cls(**deepcopy(data))

    def require_identity(self, actor, task_id, intent_digest):
        if (self.actor, self.task_id, self.intent_digest) != (actor, task_id, intent_digest):
            raise DomainError('Stage handoff caller or request identity conflict')

    @property
    def needs_verification(self):
        return self.verification is None or self.verification['status'] == 'content_requirements_failed'

    def changed(self, **values):
        if self.completed:
            raise DomainError('Completed stage handoff is immutable')
        allowed = {'verification', 'verified_source', 'progression_started', 'progression',
                   'release_context', 'release_intent', 'release'}
        if set(values) - allowed:
            raise DomainError('Handoff identity is immutable')
        for name in ('verification', 'verified_source', 'progression_started', 'release_context', 'release'):
            previous = getattr(self, name)
            if previous is not None and name in values and values[name] != previous:
                if not (name == 'verification' and self.needs_verification and values[name] is not None):
                    raise DomainError(f'Confirmed handoff {name} is immutable')
        if normal_progression(self.progression) and values.get('progression', self.progression) != self.progression:
            raise DomainError('Confirmed handoff progression is immutable')
        if self.release_intent is not None and 'release_intent' in values:
            new = values['release_intent']
            if not isinstance(new, dict) or any(
                    new.get(key) != value for key, value in self.release_intent.items()
                    if key != 'expected_version'):
                raise DomainError('Handoff release identity is immutable')
        return replace(self, revision=self.revision + 1, **deepcopy(values))

    def finish(self):
        if (self.completed or self.verification is None or self.verification['status'] != 'verified'
                or self.verified_source != {'commit': self.verification['verification_commit'],
                                            'tree': self.verification['verified_tree']}
                or not normal_progression(self.progression)
                or self.release is None or self.release['status'] != 'ownership_released'
                or self.release['task'] != self.task_id
                or self.release['before']['task_id'] != self.task_id
                or self.release['after']['task_id'] is not None):
            raise DomainError('Handoff completion requires three confirmed native outcomes')
        return replace(self, revision=self.revision + 1, completed=True)

    def project(self, *, phase, blocked=None, replayed=False):
        if replayed and not self.completed:
            raise DomainError('An incomplete handoff is not a completed replay')
        return deepcopy({
            'status': 'handoff_completed' if self.completed else 'handoff_blocked',
            'task': self.task_id, 'request_id': self.request_id,
            'phase': 'complete' if self.completed else phase,
            'verification': self.verification, 'progression': self.progression,
            'release': self.release, 'blocked': None if self.completed else blocked,
            'replayed': replayed,
        })

    def to_dict(self):
        return asdict(self)
