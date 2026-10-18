"""Attempt identity and confirmed versus uncertain preparation outcomes."""
from dataclasses import dataclass, replace
from ..foundation.errors import DomainError


@dataclass(frozen=True)
class PreparationAttempt:
    coordinates: dict[str, str]
    attempt_id: str
    request_id: str | None
    revision: int = 0
    phase: str = 'begun'
    capture: dict | None = None
    receipt: dict | None = None
    reason: str | None = None

    @classmethod
    def begin(cls, coordinates, attempt_id, request_id):
        return cls(dict(coordinates), attempt_id, request_id)

    def captured(self, material):
        if self.phase != 'begun':
            raise DomainError('Preparation capture requires a begun attempt')
        return replace(self, phase='captured', capture=material, reason=None)

    def complete(self, receipt):
        if self.phase != 'captured':
            raise DomainError('Preparation completion requires a confirmed capture')
        return replace(self, phase='completed', receipt=receipt, reason=None)

    def unconfirmed(self, reason):
        return replace(self, reason=reason)

    def projection(self):
        result = self.receipt
        return {'status': result['status'] if result else 'unconfirmed',
                'attempt_id': self.attempt_id, 'request_id': self.request_id,
                'receipt_path': result['path'] if result else None,
                'receipt_sha256': result['sha256'] if result else None,
                'reason': result['reason'] if result else self.reason}
