"""Production clock adapter with an explicit system-boot comparison domain."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time

from ..common import PoiseError
from ..modules.accounting.clock import ClockObservation


def _comparison_domain() -> str:
    try:
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    except OSError as exc:
        raise PoiseError(
            'Stable system boot identity is unavailable; ensure '
            '/proc/sys/kernel/random/boot_id is readable'
        ) from exc
    if not boot_id:
        raise PoiseError(
            'Stable system boot identity is unavailable; ensure '
            '/proc/sys/kernel/random/boot_id is nonempty'
        )
    return f'linux-boot:{boot_id}'


class SystemClock:
    def __init__(self):
        self._domain = _comparison_domain()

    def observe(self) -> ClockObservation:
        return ClockObservation(
            audit_utc=datetime.now(timezone.utc).isoformat(),
            monotonic_ns=time.monotonic_ns(),
            comparison_domain=self._domain,
        )
