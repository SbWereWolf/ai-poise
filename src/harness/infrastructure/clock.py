"""Production clock adapter with a conservative cross-process comparison domain."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from uuid import uuid4

from ..modules.accounting.clock import ClockObservation


_PROCESS_DOMAIN = f'process:{uuid4()}'


def _comparison_domain() -> str:
    try:
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    except OSError:
        return _PROCESS_DOMAIN
    return f'linux-boot:{boot_id}' if boot_id else _PROCESS_DOMAIN


class SystemClock:
    def __init__(self):
        self._domain = _comparison_domain()

    def observe(self) -> ClockObservation:
        return ClockObservation(
            audit_utc=datetime.now(timezone.utc).isoformat(),
            monotonic_ns=time.monotonic_ns(),
            comparison_domain=self._domain,
        )
