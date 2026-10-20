"""Optional durable delivery, called by the existing asynchronous dispatcher only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
from threading import Lock

from ..application.telemetry import TelemetryEnvelope
from ..common import PoiseError, encoded
from ..modules.accounting.delivery import TelemetryDeliveryPolicy
from .goal_config import atomic_write, strict_json
from ..modules.foundation.errors import LockUnavailable


class _SpoolForkScope:
    """Track only this adapter's opening/open/closing flock paths across fork."""

    def __init__(self):
        self._guard = Lock()
        self._paths = {}
        self._inherited = ()
        os.register_at_fork(before=self._before, after_in_parent=self._parent,
                            after_in_child=self._child)

    def add(self, path):
        with self._guard:
            self._paths[path] = self._paths.get(path, 0) + 1

    def discard(self, path):
        with self._guard:
            count = self._paths[path] - 1
            if count:
                self._paths[path] = count
            else:
                del self._paths[path]

    def _before(self):
        self._guard.acquire()
        self._inherited = tuple(self._paths)

    def _parent(self):
        self._inherited = ()
        self._guard.release()

    def _child(self):
        paths = self._inherited
        self._guard = Lock()
        self._paths = {}
        self._inherited = ()
        if not paths:
            return
        try:
            with os.scandir('/proc/self/fd') as entries:
                for entry in entries:
                    try:
                        target = os.readlink(entry.path)
                    except OSError as exc:
                        if exc.errno == errno.ENOENT:
                            continue
                        raise
                    if target in paths or (target.endswith(' (deleted)')
                                           and target[:-10] in paths):
                        # Closing a copy preserves the live parent's flock.
                        # LOCK_UN here would release the shared open description.
                        os.close(int(entry.name))
        except BaseException:
            # At-fork callback exceptions are otherwise ignored by Python.
            # Existing receipt EOF reports this detached job as failed.
            os._exit(1)


_FORK_SCOPE = _SpoolForkScope()


class _SpoolFileLock:
    """A raw descriptor owned by exactly its opening process, never by GC."""

    def __init__(self, path, flags, mode):
        self.path = str(path.resolve())
        self.owner_pid = os.getpid()
        self.fd = None
        self._locked = False
        # Registration precedes open, including the open-before-publication gap.
        _FORK_SCOPE.add(self.path)
        try:
            self.fd = os.open(self.path, flags, mode)
        except BaseException:
            _FORK_SCOPE.discard(self.path)
            raise

    def owned(self):
        return self.owner_pid == os.getpid() and self.fd is not None

    def try_acquire(self):
        if not self.owned():
            raise PoiseError('Delivery lock belongs to another process or is closed')
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        self._locked = True
        return True

    def acquire(self, wait, poll):
        deadline = time.monotonic() + wait
        while not self.try_acquire():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LockUnavailable('Истёк лимит ожидания внешнего lock')
            time.sleep(min(poll, remaining))

    def close(self):
        if not self.owned():
            return
        descriptor, self.fd = self.fd, None
        try:
            if self._locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            try:
                os.close(descriptor)
            finally:
                _FORK_SCOPE.discard(self.path)


class _DeliveryClaim:
    def __init__(self, path, record, lock):
        self.path, self.record, self.lock = path, record, lock


class TelemetrySpool:
    """Bounded at-least-once spool; the sink must commit idempotently.

    Construction does no I/O. Enqueue, replay and status are worker/operator I/O,
    never a prerequisite for a work result. Status never creates storage.
    """

    def __init__(self, directory, policy, process, *, now=time.time, project_fence=None):
        self.directory = Path(directory)
        self.policy = TelemetryDeliveryPolicy.parse(policy)
        self.process, self.now = process, now
        self.project_fence = project_fence

    def _time(self):
        value = self.now()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise PoiseError('Invalid delivery scheduling time')
        return value

    @contextmanager
    def _lock(self, name):
        with (self.project_fence.locked() if self.project_fence is not None else nullcontext()):
            with self._directory_lock(name):
                yield

    @contextmanager
    def _directory_lock(self, name):
        if self.directory.is_symlink():
            raise PoiseError('Delivery directory must not be a symlink')
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory/name
        if path.is_symlink():
            raise PoiseError('Delivery lock must not be a symlink')
        lock = _SpoolFileLock(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o666)
        try:
            lock.acquire(self.policy.lock_seconds, self.policy.lock_poll_seconds)
            yield
        finally:
            lock.close()

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
                if entry.name in ('queue.lock', 'replay.lock') or self._is_claim(entry.name):
                    continue
                found.append(Path(entry.path))
                if len(found) > self.policy.max_entries:
                    raise PoiseError('Delivery spool capacity exceeded; inspect unexpected files')
        return sorted(found)

    @staticmethod
    def _is_claim(name):
        return (len(name) == 64 + len('.event.json.lock')
                and name.endswith('.event.json.lock')
                and all(character in '0123456789abcdef' for character in name[:64]))

    def _cleanup_claims(self):
        # Caller holds queue.lock; never unlink a busy inode or an unknown file.
        with os.scandir(self.directory) as entries:
            for entry in entries:
                if not self._is_claim(entry.name):
                    continue
                event_path = self.directory / entry.name[:-5]
                if event_path.exists():
                    continue
                path = self.directory / entry.name
                lock = _SpoolFileLock(path, os.O_RDWR, 0o600)
                try:
                    if lock.try_acquire():
                        lock.close()
                        path.unlink()
                finally:
                    lock.close()

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
            self._cleanup_claims()
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

    def _claim_due(self, path, record):
        # The record was just read under this same queue ownership. No waiter
        # may retain an old claim inode across its unlink/recreate boundary.
        now = self._time()
        if record['attempts'] >= self.policy.max_attempts or record['next_attempt_at'] > now:
            return None
        lock = _SpoolFileLock(Path(str(path) + '.lock'), os.O_RDWR | os.O_CREAT, 0o600)
        adopted = False
        try:
            if not lock.try_acquire():
                return None
            record['attempts'] += 1
            record['next_attempt_at'] = now + self.policy.retry_seconds
            record['last_error'] = 'attempt_without_ack'
            atomic_write(path, (encoded(record) + '\n').encode(), 0o600)
            claim = _DeliveryClaim(path, record, lock)
            adopted = True
            return claim
        finally:
            if not adopted:
                lock.close()

    def _release_claim(self, claim):
        if not claim.lock.owned():
            return
        try:
            with self._lock('queue.lock'):
                claim.lock.close()
                Path(claim.lock.path).unlink()
        finally:
            # If queue admission failed, leave metadata for the next mutator.
            claim.lock.close()

    def _deliver(self, claim):
        if not claim.lock.owned():
            raise PoiseError('Delivery claim belongs to another process or is closed')
        try:
            try:
                self.process(TelemetryEnvelope(claim.record['identity'], claim.record['data']))
            except Exception as exc:
                claim.record['last_error'] = type(exc).__name__[:96]
                with self._lock('queue.lock'):
                    atomic_write(claim.path, (encoded(claim.record) + '\n').encode(), 0o600)
                return 'failed'
            with self._lock('queue.lock'):
                claim.path.unlink()
                fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return 'delivered'
        finally:
            self._release_claim(claim)

    def replay(self):
        result = {'attempted': 0, 'delivered': 0, 'failed': 0, 'corrupt': 0}
        claims = []
        try:
            with self._lock('queue.lock'):
                self._cleanup_claims()
                for path in self._entries():
                    if len(claims) >= self.policy.batch_items:
                        break
                    try:
                        record = self._record(path)
                    except FileNotFoundError:
                        continue
                    except (OSError, ValueError, PoiseError):
                        result['corrupt'] += 1
                        continue
                    claim = self._claim_due(path, record)
                    if claim is not None:
                        claims.append(claim)
            result['attempted'] = len(claims)
            if claims:
                # The existing batch bound is also the maximum active fan-out.
                with ThreadPoolExecutor(max_workers=len(claims)) as workers:
                    futures = [workers.submit(self._deliver, claim) for claim in claims]
                    for future in futures:
                        result[future.result()] += 1
            return result
        finally:
            # The pool has drained even on submission/ack failure. Closing again
            # is harmless; claims not handed to a worker still need their owner.
            for claim in claims:
                self._release_claim(claim)

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
