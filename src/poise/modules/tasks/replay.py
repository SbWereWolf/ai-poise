"""Immutable accepted visits and bounded replay projections, without I/O."""
from dataclasses import dataclass

from ..foundation.errors import DomainError


def proof_contract_compatible(contract, source):
    """Accepted proof cannot replace explicitly revised verification obligations."""
    stage = source['stage']
    historical = {method['id']: method for method in source['methods']}
    current = {method['id']: method for method in contract['methods']}
    if not set(contract['checks'][stage]) <= historical.keys():
        return False
    if contract['evidence_plan'][stage] != source['report']['evidence']['plan']:
        return False
    return all(current.get(method_id) == method for method_id, method in historical.items())


@dataclass(frozen=True)
class ReplayIntent:
    task_id: str
    request_id: str
    target_stage: str | None

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.task_id, self.request_id)):
            raise DomainError('Replay requires Task and request identity')
        if self.target_stage is not None and (
                not isinstance(self.target_stage, str) or not self.target_stage.strip()):
            raise DomainError('Replay target must be nonempty when supplied')

    @property
    def mode(self):
        return 'maximum' if self.target_stage is None else 'target'


@dataclass(frozen=True)
class AcceptedVisit:
    stage: str
    visit_id: int
    commit: str

    def __post_init__(self):
        if type(self.visit_id) is not int or self.visit_id < 1:
            raise DomainError('Accepted visit requires an exact submission ID')

    def to_dict(self):
        return {'stage': self.stage, 'visit_id': self.visit_id, 'commit': self.commit}


@dataclass(frozen=True)
class ReplayResult:
    intent: ReplayIntent
    start_commit: str
    recovery_ref: str | None
    subject_commit: str | None
    stopped_at: str
    reason: str
    passed: tuple[AcceptedVisit, ...]

    def to_dict(self):
        return {'mode': self.intent.mode, 'target_stage': self.intent.target_stage,
                'recovery_ref': self.recovery_ref, 'start_commit': self.start_commit,
                'subject_commit': self.subject_commit, 'stopped_at': self.stopped_at,
                'reason': self.reason, 'passed': [visit.to_dict() for visit in self.passed]}
