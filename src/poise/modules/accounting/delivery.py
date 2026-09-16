"""Explicit bounds for optional telemetry delivery; no work lifecycle policy."""
from dataclasses import dataclass
import math
from ..foundation.errors import PoiseError
from ..foundation.validation import validate_exact_keys


@dataclass(frozen=True)
class TelemetryDeliveryPolicy:
    max_entries: int
    max_bytes: int
    max_event_bytes: int
    max_attempts: int
    batch_items: int
    retry_seconds: float
    lock_seconds: float
    lock_poll_seconds: float

    @classmethod
    def parse(cls, value):
        validate_exact_keys(value, set(cls.__annotations__), 'telemetry delivery policy', PoiseError)
        for key in ('max_entries', 'max_bytes', 'max_event_bytes', 'max_attempts', 'batch_items'):
            if type(value[key]) is not int or value[key] <= 0:
                raise PoiseError(f'{key}: positive integer required')
        for key in ('retry_seconds', 'lock_seconds', 'lock_poll_seconds'):
            if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] <= 0:
                raise PoiseError(f'{key}: finite positive bound required')
        if value['batch_items'] > value['max_entries'] or value['max_attempts'] > 1000000:
            raise PoiseError('Delivery batch/attempt bounds are inconsistent')
        if value['max_event_bytes'] + 2048 > value['max_bytes']:
            raise PoiseError('Delivery byte bound cannot hold one event')
        return cls(**value)
