"""Optional durable delivery, called by the existing asynchronous dispatcher only."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

from ..application.telemetry import TelemetryEnvelope
from ..common import PoiseError, encoded
from ..modules.accounting.delivery import TelemetryDeliveryPolicy
from .goal_config import atomic_write, strict_json
from .locking import exclusive_lock


class TelemetrySpool:
    """Bounded at-least-once spool; the sink must commit idempotently.

    Construction does no I/O. Enqueue, replay and status are worker/operator I/O,
    never a prerequisite for a work result. Status never creates storage.
    """

    def __init__(self, directory, policy, process, *, now=time.time):
        self.directory = Path(directory)
        self.policy = TelemetryDeliveryPolicy.parse(policy)
        self.process, self.now = process, now

    def _time(self):
        value = self.now()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise PoiseError('Invalid delivery scheduling time')
        return value

    @contextmanager
    def _lock(self, name):
        if self.directory.is_symlink():
            raise PoiseError('Delivery directory must not be a symlink')
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory/name
        if path.is_symlink():
            raise PoiseError('Delivery lock must not be a symlink')
        with exclusive_lock(path, self.policy.lock_seconds, self.policy.lock_poll_seconds):
            yield

    def _entries(self):
        if self.directory.is_symlink():
            raise PoiseError('Delivery directory must not be a symlink')
        try:
            if not stat.S_ISDIR(self.directory.stat().st_mode):
                raise PoiseError('Delivery storage is not a directory')
        except FileNotFoundError:
            return []
        found = []
        with os.scandir(self.directory) as entries:
            for entry in entries:
                if entry.name in ('queue.lock', 'replay.lock'):
                    continue
                found.append(Path(entry.path))
                if len(found) > self.policy.max_entries:
                    raise PoiseError('Delivery spool capacity exceeded; inspect unexpected files')
        return sorted(found)

    @staticmethod
    def _key(identity):
        return hashlib.sha256(identity.encode('utf-8')).hexdigest() + '.event.json'

    @staticmethod
    def _payload(envelope):
        if not isinstance(envelope.identity, str) or not envelope.identity or len(envelope.identity) > 128:
            raise PoiseError('Bounded telemetry identity required')
        if not isinstance(envelope.data, dict):
            raise PoiseError('Telemetry data must be an object')
        try:
            data = json.dumps({'identity': envelope.identity, 'data': envelope.data},
                              sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise PoiseError('Telemetry must contain finite JSON values') from exc
        return data.encode('utf-8')

    def _record(self, path):
        if path.is_symlink() or not path.name.endswith('.event.json'):
            raise PoiseError('Unexpected delivery entry')
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise PoiseError('Delivery entry must be regular')
            raw = stream.read(self.policy.max_event_bytes + 2049)
        if len(raw) > self.policy.max_event_bytes + 2048:
            raise PoiseError('Delivery record exceeds bound')
        record = strict_json(raw.decode('utf-8'))
        if not isinstance(record, dict) or set(record) != {
                'schema', 'identity', 'data', 'sha256', 'attempts', 'next_attempt_at',
                'enqueued_at', 'last_error'} or record['schema'] != 'poise-telemetry-spool-1':
            raise PoiseError('Invalid delivery record')
        payload = self._payload(TelemetryEnvelope(record['identity'], record['data']))
        if (len(payload) > self.policy.max_event_bytes
                or hashlib.sha256(payload).hexdigest() != record['sha256']
                or path.name != self._key(record['identity'])
                or type(record['attempts']) is not int or record['attempts'] < 0
                or type(record['next_attempt_at']) not in (int, float)
                or not math.isfinite(record['next_attempt_at']) or record['next_attempt_at'] < 0
                or type(record['enqueued_at']) not in (int, float)
                or not math.isfinite(record['enqueued_at']) or record['enqueued_at'] < 0
                or (record['last_error'] is not None and
                    (not isinstance(record['last_error'], str) or len(record['last_error']) > 96))):
            raise PoiseError('Invalid delivery record integrity')
        return record

    def enqueue(self, envelope):
        payload = self._payload(envelope)
        if len(payload) > self.policy.max_event_bytes:
            raise PoiseError('Telemetry event byte bound exceeded')
        record = {'schema': 'poise-telemetry-spool-1', **json.loads(payload),
                  'sha256': hashlib.sha256(payload).hexdigest(), 'attempts': 0,
                  'next_attempt_at': 0, 'enqueued_at': self._time(), 'last_error': None}
        path = self.directory/self._key(envelope.identity)
        with self._lock('queue.lock'):
            entries = self._entries()
            if path in entries:
                if self._record(path)['sha256'] != record['sha256']:
                    raise PoiseError('Telemetry delivery identity conflict')
                return 'duplicate'
            # Charge the maximum permitted per-record overhead, including retry metadata.
            charge = sum(p.lstat().st_size + 2048 for p in entries)
            if len(entries) >= self.policy.max_entries or charge + len(payload) + 2048 > self.policy.max_bytes:
                raise PoiseError('Telemetry spool capacity exhausted')
            atomic_write(path, (encoded(record)+'\n').encode(), 0o600)
        return 'persisted'

    def __call__(self, envelope):
        self.enqueue(envelope)
        self.replay()

    def replay(self):
        result = {'attempted': 0, 'delivered': 0, 'failed': 0, 'corrupt': 0}
        with self._lock('replay.lock'):
            with self._lock('queue.lock'):
                entries = self._entries()
            for path in entries:
                if result['attempted'] >= self.policy.batch_items:
                    break
                try:
                    record = self._record(path)
                except (OSError, ValueError, PoiseError):
                    result['corrupt'] += 1
                    continue
                if record['attempts'] >= self.policy.max_attempts or record['next_attempt_at'] > self._time():
                    continue
                # Save an attempt before invoking the sink; process death is bounded too.
                record['attempts'] += 1
                record['next_attempt_at'] = self._time() + self.policy.retry_seconds
                record['last_error'] = 'attempt_without_ack'
                with self._lock('queue.lock'):
                    atomic_write(path, (encoded(record)+'\n').encode(), 0o600)
                result['attempted'] += 1
                try:
                    self.process(TelemetryEnvelope(record['identity'], record['data']))
                except Exception as exc:
                    record['last_error'] = type(exc).__name__[:96]
                    with self._lock('queue.lock'):
                        atomic_write(path, (encoded(record)+'\n').encode(), 0o600)
                    result['failed'] += 1
                else:
                    with self._lock('queue.lock'):
                        path.unlink()
                        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    result['delivered'] += 1
        return result

    def status(self):
        """Explicit read-only diagnostic, not called on the work response path."""
        result = {'status': 'observed', 'coverage': 'partial', 'pending': 0,
                  'exhausted': 0, 'corrupt': 0, 'failed_attempts': 0,
                  'bytes': 0, 'records': []}
        try:
            for path in self._entries():
                result['bytes'] += path.lstat().st_size
                try:
                    record = self._record(path)
                except (OSError, ValueError, PoiseError):
                    result['corrupt'] += 1
                    continue
                exhausted = record['attempts'] >= self.policy.max_attempts
                result['exhausted' if exhausted else 'pending'] += 1
                result['failed_attempts'] += record['attempts']
                result['records'].append({key: record[key] for key in
                    ('identity', 'sha256', 'attempts', 'next_attempt_at', 'enqueued_at', 'last_error')})
        except (OSError, ValueError, PoiseError) as exc:
            result['status'] = 'unavailable'
            result['error'] = type(exc).__name__
        return result
