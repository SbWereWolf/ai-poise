"""Accounting clock values: monotonic duration plus UTC audit representation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ..foundation.errors import DomainError


@dataclass(frozen=True)
class ClockObservation:
    audit_utc: str
    monotonic_ns: int
    comparison_domain: str

    def __post_init__(self):
        try:
            parsed = datetime.fromisoformat(self.audit_utc)
        except (TypeError, ValueError) as exc:
            raise DomainError('Clock audit timestamp requires ISO UTC datetime') from exc
        if parsed.utcoffset() != timedelta(0):
            raise DomainError('Clock audit timestamp requires explicit UTC offset')
        if type(self.monotonic_ns) is not int or self.monotonic_ns < 0:
            raise DomainError('Clock monotonic value requires a nonnegative integer')
        if not isinstance(self.comparison_domain, str) or not self.comparison_domain.strip():
            raise DomainError('Clock comparison domain requires a nonempty identifier')


class Clock(Protocol):
    def observe(self) -> ClockObservation: ...


def validate_source_observation(value):
    """Validate a supplied timestamp without observing a clock or normalizing its offset."""
    try:
        if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
            raise ValueError('timezone missing')
    except (TypeError, ValueError) as exc:
        raise DomainError('Source observation requires ISO datetime with timezone') from exc
