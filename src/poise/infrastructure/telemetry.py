"""In-process best-effort dispatch; pending items are intentionally not durable."""
from __future__ import annotations

from collections import deque
import os
from threading import Condition, Lock, Thread


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
        self._lock = Lock()
        self._changed = Condition(self._lock)
        self._worker = None
        self._receipts = []
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
            if envelope.identity in self._seen:
                self._stats["duplicates"] += 1
                return True
            outstanding = len(self._pending) + len(self._receipts)
            if self._closed or outstanding >= self.max_pending:
                self._stats["dropped"] += 1
                return False
            self._seen.add(envelope.identity)
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

    def _run(self):
        while True:
            with self._changed:
                if not self._pending:
                    self._worker = None
                    self._changed.notify_all()
                    return
                envelope = self._pending.popleft()
            try:
                self.processor(envelope)
            except Exception:
                with self._changed:
                    self._stats["failed"] += 1
            else:
                with self._changed:
                    self._stats["processed"] += 1

    def flush(self):
        while True:
            with self._changed:
                while self._worker is not None:
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
        with self._changed:
            self._closed = True
        self.flush()

    def summary(self):
        with self._changed:
            coverage = "partial" if self._stats["submitted"] else "unavailable"
            return {"coverage": coverage, **self._stats}
