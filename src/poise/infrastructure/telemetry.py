"""Bounded nonblocking dispatch; optional durability belongs to the worker sink."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import json
import os
import select
from pathlib import Path
import sqlite3
from threading import Condition, Lock, Thread

from .sqlite.database import Database


class TelemetryDatabase:
    """Lazy optional store: composition never waits for its writer lock."""

    def __init__(self,path:Path,lock:Path,wait:float,poll:float):
        self.path,self.lock,self.wait,self.poll=path,lock,wait,poll
        self._database=None
        self._initialization=Lock()

    def _ready(self):
        if self._database is None:
            with self._initialization:
                if self._database is None:
                    self._database=Database(self.path,self.lock,self.wait,self.poll)
        return self._database

    @contextmanager
    def transaction(self):
        with self._ready().transaction() as db:
            yield db

    @contextmanager
    def read_transaction(self):
        """Read the last committed SQLite snapshot without the writer flock."""
        database=None
        try:
            database=sqlite3.connect(
                self.path.resolve().as_uri()+'?mode=ro',
                uri=True,
                timeout=0,
                isolation_level=None,
                autocommit=True,
            )
            database.row_factory=sqlite3.Row
            database.execute('PRAGMA query_only=ON')
            database.execute('BEGIN')
            yield database
            database.execute('COMMIT')
        except BaseException:
            if database is not None and database.in_transaction:
                database.execute('ROLLBACK')
            raise
        finally:
            if database is not None:
                database.close()


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
        self._pending = deque()
        self._seen = set()
        self._recent_ids = deque()
        self._inflight = 0
        self._lock = Lock()
        self._changed = Condition(self._lock)
        self._worker = None
        self._receipts = []
        self._captures = 0
        self._capture_pending = deque()
        self._capture_worker = None
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

    def _accept(self, envelope):
        self._harvest()
        if envelope.identity in self._seen:
            self._stats["duplicates"] += 1
            return True
        outstanding = len(self._pending) + len(self._receipts) + self._captures + self._inflight
        if self._closed or outstanding >= self.max_pending:
            self._stats["dropped"] += 1
            return False
        self._seen.add(envelope.identity)
        self._recent_ids.append(envelope.identity)
        if len(self._recent_ids) > self.max_pending * 2:
            self._seen.discard(self._recent_ids.popleft())
        if getattr(self.processor, "detached", False):
            try:
                self._receipts.append(self._spawn(envelope))
            except OSError:
                self._seen.remove(envelope.identity)
                self._stats["dropped"] += 1
                return False
            return True
        self._pending.append(envelope)
        if self._worker is None:
            self._worker = Thread(target=self._run, daemon=True)
            self._worker.start()
        return True

    def begin_capture(self, clock, base, builder):
        with self._changed:
            self._stats["submitted"] += 1
            self._harvest()
            outstanding = len(self._pending) + len(self._receipts) + self._captures + self._inflight
            if self._closed or outstanding >= self.max_pending:
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
                self._receipts.append(receipt)
                return token
            token = _ThreadCapture(clock, base, builder)
            self._captures += 1
            self._capture_pending.append(token)
            if self._capture_worker is None:
                self._capture_worker = Thread(target=self._run_captures, daemon=True)
                self._capture_worker.start()
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

    def _run_captures(self):
        while True:
            with self._changed:
                if not self._capture_pending:
                    self._capture_worker = None
                    self._changed.notify_all()
                    return
                token = self._capture_pending.popleft()
            try:
                started = token.clock.observe()
                with token.ready:
                    while token.final is None:
                        token.ready.wait()
                    final = token.final
                envelope = token.builder(
                    token.base,
                    started,
                    token.clock.observe(),
                    final,
                )
                if getattr(self.processor, "detached", False):
                    self.processor(envelope)
                    with self._changed:
                        self._captures -= 1
                        self._stats["processed"] += 1
                        self._changed.notify_all()
                else:
                    with self._changed:
                        self._captures -= 1
                        self._accept(envelope)
                        self._changed.notify_all()
            except Exception:
                with self._changed:
                    self._captures -= 1
                    self._stats["failed"] += 1
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
        os.close(final_read)
        os.close(receipt_write)
        while True:
            try:
                os.waitpid(child, 0)
                break
            except InterruptedError:
                continue
        return _DetachedCapture(final_write), receipt_read

    def _run(self):
        while True:
            with self._changed:
                if not self._pending:
                    self._worker = None
                    self._changed.notify_all()
                    return
                envelope = self._pending.popleft()
                self._inflight += 1
            try:
                self.processor(envelope)
            except Exception:
                with self._changed:
                    self._stats["failed"] += 1
            else:
                with self._changed:
                    self._stats["processed"] += 1
            finally:
                with self._changed:
                    self._inflight -= 1
                    self._changed.notify_all()

    def _harvest(self):
        if not self._receipts:
            return
        poller = select.poll()
        for descriptor in self._receipts:
            poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
        for receipt, _ in poller.poll(0):
            try:
                outcome = os.read(receipt, 1)
            finally:
                os.close(receipt)
                self._receipts.remove(receipt)
            self._stats["processed" if outcome == b"1" else "failed"] += 1

    def flush(self):
        while True:
            with self._changed:
                while self._capture_worker is not None or self._worker is not None:
                    self._changed.wait()
                receipts = self._receipts
                self._receipts = []
            if not receipts:
                return
            outcomes = []
            for receipt in receipts:
                try:
                    outcomes.append(os.read(receipt, 1))
                finally:
                    os.close(receipt)
            with self._changed:
                self._stats["processed"] += outcomes.count(b"1")
                self._stats["failed"] += len(outcomes) - outcomes.count(b"1")

    def close(self):
        self.flush()
        with self._changed:
            self._closed = True

    def summary(self):
        with self._changed:
            self._harvest()
            coverage = "partial" if self._stats["submitted"] else "unavailable"
            pending = self._stats["submitted"] - sum(
                self._stats[key]
                for key in ("processed", "duplicates", "failed", "dropped")
            )
            return {"coverage": coverage, **self._stats, "pending": pending}
