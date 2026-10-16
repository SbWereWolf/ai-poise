"""Test-only receipt failure: a detached worker must never return to work."""
import json
import os
from pathlib import Path
import sys

from poise.application.telemetry import OptionalTelemetry, TelemetryEnvelope
from poise.infrastructure.telemetry import AsyncTelemetryDispatcher, DetachedTelemetryProcessor
from poise.modules.accounting.clock import ClockObservation


root = Path(sys.argv[1])
mode = sys.argv[2]
parent = os.getpid()
original_write = os.write


class Clock:
    fork_safe = True

    def __init__(self):
        self.counter = 0

    def observe(self):
        self.counter += 1
        return ClockObservation('2026-10-16T00:00:00+00:00', self.counter, 'test-clock')


def broken_receipt(descriptor, data):
    if os.getpid() != parent and data in (b'0', b'1'):
        (root / 'child-pid.txt').write_text(str(os.getpid()))
        raise BrokenPipeError('Test-owned closed receipt consumer')
    return original_write(descriptor, data)


os.write = broken_receipt
dispatcher = AsyncTelemetryDispatcher(DetachedTelemetryProcessor(lambda envelope: None), max_pending=1)
if mode == 'capture':
    telemetry = OptionalTelemetry(Clock(), dispatcher, 'test-session')
    token = telemetry.capture('show', None, None, None)
    telemetry.complete(token, None, {'status': 'read_only'})
else:
    assert mode == 'dispatch'
    dispatcher.submit(TelemetryEnvelope('test-envelope', {}))

# The application continuation is represented by this independent effect.
# A detached child returning through an exception would perform it a second time.
with (root / 'foreground.txt').open('a') as stream:
    stream.write(str(os.getpid()) + '\n')
dispatcher.flush()
print(json.dumps({'parent': parent, 'summary': dispatcher.summary()}))
