"""Bounded nonblocking dispatch; optional durability belongs to the worker sink."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import json
import os
import select
from pathlib import Path
from threading import Condition, Lock, Thread

from .sqlite.accounting_database import AccountingDatabase, validate_accounting_schema


class TelemetryDatabase:
    """Lazy optional store: composition never waits for its writer lock."""
    accounting_only = True

    def __init__(self,path:Path,lock:Path,wait:float,poll:float,project_fence=None):
        self.path,self.lock,self.wait,self.poll=path,lock,wait,poll
        self.project_fence=project_fence
        self._database=None

    def _ready(self):
        database = self._database
        if database is None:
            # Optional initialization never creates authoritative Task tables.
            # A cache mutex could retain a vanished thread owner after fork.
            database = AccountingDatabase(self.path,self.lock,self.wait,self.poll,self.project_fence)
            self._database = database
        return database

    @contextmanager
    def transaction(self):
        with self._ready().transaction() as db:
            yield db

    @contextmanager
    def read_transaction(self):
        """Read the last committed SQLite snapshot without the writer flock."""
        from .diagnostic_io import preparation_transaction
        with preparation_transaction(self.path, self.wait, self.poll) as database:
            validate_accounting_schema(database, unavailable_if_empty=True)
            yield database


class _ThreadCapture:
    def __init__(self, clock, base, builder):
        self.clock = clock
        self.base = base
        self.builder = builder
        self.final = None
        self.ready = Condition()


class _DetachedCapture:
    def __init__(self, write_fd):
        self.write_fd = write_fd


def _close_unrelated_pipes(*preserved):
    """Keep detached workers from retaining caller/runner transport pipes."""
    preserved = set(preserved)
    for entry in os.scandir('/proc/self/fd'):
        try:
            descriptor = int(entry.name)
            target = os.readlink(entry.path)
        except (OSError, ValueError):
            continue
        if descriptor > 2 and descriptor not in preserved and target.startswith('pipe:['):
            try:
                os.close(descriptor)
            except OSError:
                pass


class DetachedTelemetryProcessor:
    """Mark a Linux-safe processor for work beyond a one-shot caller lifetime."""

    detached = True

    def __init__(self, process):
        self.process = process

    def __call__(self, envelope):
        self.process(envelope)


class AsyncTelemetryDispatcher:
    def __init__(self, processor, max_pending):
        if type(max_pending) is not int or max_pending <= 0:
            raise ValueError("max_pending must be a positive integer")
        self.processor = processor
        self.max_pending = max_pending
        self._seen = set()
        self._recent_ids = deque()
        self._inflight = 0
        self._lock = Lock()
        self._changed = Condition(self._lock)
        self._receipts = {}
        self._active_ids = set()
        self._flush_lock = Lock()
        self._captures = 0
        self._closed = False
        self._stats = {
            "submitted": 0,
            "processed": 0,
            "duplicates": 0,
            "failed": 0,
            "dropped": 0,
        }

    def submit(self, envelope):
        with self._changed:
            self._stats["submitted"] += 1
            return self._accept(envelope)

    def _outstanding(self):
        return len(self._receipts) + self._captures + self._inflight

    def _known(self, identity):
        return identity in self._seen or identity in self._active_ids

    def _remember(self, identity):
        self._active_ids.add(identity)
        self._seen.add(identity)
        self._recent_ids.append(identity)
        if len(self._recent_ids) > self.max_pending * 2:
            self._seen.discard(self._recent_ids.popleft())

    def _rollback_identity(self, identity):
        self._active_ids.discard(identity)
        self._seen.discard(identity)
        self._recent_ids.remove(identity)

    def _accept(self, envelope):
        self._harvest()
        if self._known(envelope.identity):
            self._stats["duplicates"] += 1
            return True
        if self._closed or self._outstanding() >= self.max_pending:
            self._stats["dropped"] += 1
            return False
        self._remember(envelope.identity)
        if getattr(self.processor, "detached", False):
            try:
                self._receipts[self._spawn(envelope)] = envelope.identity
            except OSError:
                self._rollback_identity(envelope.identity)
                self._stats["dropped"] += 1
                return False
            return True
        self._inflight += 1
        try:
            Thread(target=self._run, args=(envelope,), daemon=True).start()
        except (OSError, RuntimeError):
            self._inflight -= 1
            self._rollback_identity(envelope.identity)
            self._stats["dropped"] += 1
            self._changed.notify_all()
            return False
        return True

    def begin_capture(self, clock, base, builder):
        with self._changed:
            self._stats["submitted"] += 1
            self._harvest()
            if self._closed or self._outstanding() >= self.max_pending:
                self._stats["dropped"] += 1
                return None
            if (
                getattr(self.processor, "detached", False)
                and getattr(clock, "fork_safe", False)
            ):
                try:
                    token, receipt = self._spawn_capture(clock, base, builder)
                except OSError:
                    self._stats["dropped"] += 1
                    return None
                self._receipts[receipt] = None
                return token
            token = _ThreadCapture(clock, base, builder)
            self._captures += 1
            try:
                Thread(target=self._run_capture, args=(token,), daemon=True).start()
            except (OSError, RuntimeError):
                self._captures -= 1
                self._stats["dropped"] += 1
                self._changed.notify_all()
                return None
            return token

    def finish_capture(self, token, final):
        if isinstance(token, _DetachedCapture):
            payload = json.dumps(final, ensure_ascii=False, separators=(",", ":")).encode()
            try:
                while payload:
                    written = os.write(token.write_fd, payload)
                    payload = payload[written:]
            finally:
                os.close(token.write_fd)
            return True
        with token.ready:
            token.final = final
            token.ready.notify_all()
        return True

    def _run_capture(self, token):
        identity = None
        outcome = "failed"
        try:
            started = token.clock.observe()
            with token.ready:
                while token.final is None:
                    token.ready.wait()
                final = token.final
            envelope = token.builder(token.base, started, token.clock.observe(), final)
            with self._changed:
                if self._known(envelope.identity):
                    outcome = "duplicates"
                    return
                self._remember(envelope.identity)
                identity = envelope.identity
            self.processor(envelope)
            outcome = "processed"
        except Exception:
            pass
        finally:
            with self._changed:
                self._captures -= 1
                if identity is not None:
                    self._active_ids.discard(identity)
                self._stats[outcome] += 1
                self._changed.notify_all()

    def _spawn(self, envelope):
        read_fd, write_fd = os.pipe()
        try:
            child = os.fork()
        except OSError:
            os.close(read_fd)
            os.close(write_fd)
            raise
        if child == 0:
            try:
                os.close(read_fd)
                try:
                    grandchild = os.fork()
                except OSError:
                    os.close(write_fd)
                    os._exit(1)
                if grandchild:
                    os._exit(0)
                devnull = os.open(os.devnull, os.O_RDWR)
                os.dup2(devnull, 0)
                os.dup2(devnull, 1)
                os.dup2(devnull, 2)
                if devnull > 2:
                    os.close(devnull)
                _close_unrelated_pipes(write_fd)
                status = b"1"
                try:
                    self.processor(envelope)
                except Exception:
                    status = b"0"
                try:
                    os.write(write_fd, status)
                finally:
                    os.close(write_fd)
                os._exit(0)
            finally:
                os._exit(0)
        os.close(write_fd)
        while True:
            try:
                os.waitpid(child, 0)
                break
            except InterruptedError:
                continue
        return read_fd

    def _spawn_capture(self, clock, base, builder):
        final_read, final_write = os.pipe()
        receipt_read, receipt_write = os.pipe()
        try:
            child = os.fork()
        except OSError:
            for descriptor in (final_read, final_write, receipt_read, receipt_write):
                os.close(descriptor)
            raise
        if child == 0:
            try:
                os.close(final_write)
                os.close(receipt_read)
                try:
                    grandchild = os.fork()
                except OSError:
                    os.close(final_read)
                    os.close(receipt_write)
                    os._exit(1)
                if grandchild:
                    os._exit(0)
                devnull = os.open(os.devnull, os.O_RDWR)
                os.dup2(devnull, 0)
                os.dup2(devnull, 1)
                os.dup2(devnull, 2)
                if devnull > 2:
                    os.close(devnull)
                _close_unrelated_pipes(final_read, receipt_write)
                status = b"1"
                try:
                    started = clock.observe()
                    chunks = []
                    while True:
                        chunk = os.read(final_read, 65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    final = json.loads(b"".join(chunks).decode())
                    envelope = builder(base, started, clock.observe(), final)
                    self.processor(envelope)
                except Exception:
                    status = b"0"
                try:
                    os.write(receipt_write, status)
                finally:
                    os.close(final_read)
                    os.close(receipt_write)
                os._exit(0)
            finally:
                os._exit(0)
        os.close(final_read)
        os.close(receipt_write)
        while True:
            try:
                os.waitpid(child, 0)
                break
            except InterruptedError:
                continue
        return _DetachedCapture(final_write), receipt_read

    def _run(self, envelope):
        outcome = "failed"
        try:
            self.processor(envelope)
            outcome = "processed"
        except Exception:
            pass
        finally:
            with self._changed:
                self._inflight -= 1
                self._active_ids.discard(envelope.identity)
                self._stats[outcome] += 1
                self._changed.notify_all()

    def _harvest(self):
        if not self._receipts:
            return
        poller = select.poll()
        for descriptor in self._receipts:
            poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
        for receipt, _ in poller.poll(0):
            outcome = b""
            try:
                outcome = os.read(receipt, 1)
            except OSError:
                pass
            finally:
                os.close(receipt)
                identity = self._receipts.pop(receipt)
                if identity is not None:
                    self._active_ids.discard(identity)
            self._stats["processed" if outcome == b"1" else "failed"] += 1
        self._changed.notify_all()

    def flush(self):
        # Duplicates protect the poll snapshot against concurrent harvest/FD reuse.
        # Only _harvest reads receipts and releases the admission budget.
        with self._flush_lock:
            while True:
                receipts = []
                try:
                    with self._changed:
                        self._harvest()
                        while self._captures or self._inflight:
                            self._changed.wait()
                            self._harvest()
                        if not self._receipts:
                            return
                        for descriptor in self._receipts:
                            receipts.append(os.dup(descriptor))
                    poller = select.poll()
                    for descriptor in receipts:
                        poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
                    poller.poll()
                finally:
                    for descriptor in receipts:
                        os.close(descriptor)

    def close(self):
        with self._changed:
            self._closed = True
        self.flush()

    def summary(self):
        with self._changed:
            self._harvest()
            coverage = "partial" if self._stats["submitted"] else "unavailable"
            pending = self._stats["submitted"] - sum(
                self._stats[key]
                for key in ("processed", "duplicates", "failed", "dropped")
            )
            return {"coverage": coverage, **self._stats, "pending": pending}
