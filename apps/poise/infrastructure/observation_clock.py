"""Production Linux clock; construct only in the optional capture worker."""
from datetime import datetime,timezone
from pathlib import Path
import time
from domain.observations import ClockObservation


class SystemClock:
    def __init__(self):
        boot=Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
        if not boot:raise ValueError('Clock domain unavailable')
        self.domain='linux-boot:'+boot

    def observe(self):
        return ClockObservation(datetime.now(timezone.utc).isoformat(),time.monotonic_ns(),self.domain)
