"""Linux lifetime/output boundary for one owned maintenance snapshot worker.

The core creates new sessions for commands, so worker process-group cancellation
alone is insufficient. This adapter tracks descendants using pidfds and adopts
orphans as a subreaper. It is not a sandbox for deliberately escaping executables.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time


class OwnedTree:
    def __init__(self):
        if sys.platform != "linux":
            raise OSError("Linux pidfd supervision is unavailable")
        self.libc = ctypes.CDLL(None, use_errno=True)
        # Portable Python builds may omit wrappers even on a capable Linux host.
        # Use the same public libc ABI, not PID-number signalling or guessed
        # architecture-specific syscall numbers.
        if not (hasattr(self.libc, "pidfd_open") and hasattr(self.libc, "pidfd_send_signal")):
            raise OSError("Linux pidfd ABI is unavailable")
        self.libc.pidfd_open.argtypes = [ctypes.c_int, ctypes.c_uint]
        self.libc.pidfd_open.restype = ctypes.c_int
        self.libc.pidfd_send_signal.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        self.libc.pidfd_send_signal.restype = ctypes.c_int
        self.prior = ctypes.c_int()
        if self.libc.prctl(37, ctypes.byref(self.prior), 0, 0, 0) != 0:  # GET_CHILD_SUBREAPER
            raise OSError("subreaper state unavailable")
        if self.libc.prctl(36, 1, 0, 0, 0) != 0:  # SET_CHILD_SUBREAPER
            raise OSError("subreaper unavailable")
        self.handles = {}
        self.baseline = {pid for pid, (parent, _) in self.table().items() if parent == os.getpid()}

    @staticmethod
    def table():
        result = {}
        for item in Path("/proc").iterdir():
            if not item.name.isdecimal():
                continue
            try:
                raw = (item / "stat").read_text()
                fields = raw[raw.rfind(")") + 2:].split()
                result[int(item.name)] = (int(fields[1]), int(fields[19]))
            except (OSError, ValueError, IndexError):
                continue  # exited during observation
        return result

    def observe(self, root):
        table = self.table()
        owned = {pid for pid, (_, stamp) in table.items()
                 if pid in self.handles and self.handles[pid][0] == stamp}
        if root in table and (root not in self.handles or self.handles[root][0] == table[root][1]):
            owned.add(root)
        # Only this command-line process's new children can be adopted here. There
        # are no other concurrent child owners; baseline children are excluded.
        owned.update(pid for pid, (parent, _) in table.items()
                     if parent == os.getpid() and pid not in self.baseline)
        while True:
            extra = {pid for pid, (parent, _) in table.items() if parent in owned} - owned
            if not extra:
                break
            owned.update(extra)
        added = []
        for pid in owned:
            if pid in self.handles:
                continue
            try:
                handle = self.libc.pidfd_open(pid, 0)
                if handle < 0:
                    code = ctypes.get_errno()
                    raise OSError(code, os.strerror(code))
            except ProcessLookupError:
                continue
            # Pin identity across table->pidfd acquisition; never signal a reused PID.
            current = self.table().get(pid)
            if current != table.get(pid):
                os.close(handle)
                continue
            self.handles[pid] = (current[1], handle)
            added.append(pid)
        return added

    def send(self, pid, sig):
        try:
            if self.libc.pidfd_send_signal(self.handles[pid][1], sig, None, 0) < 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))
        except ProcessLookupError:
            pass

    def finish(self, proc):
        # Freeze spawning ancestors first, discover their new children, then kill
        # the complete owned set. Repeat to cover orphans adopted during exit.
        self.observe(proc.pid)
        if proc.pid in self.handles:
            self.send(proc.pid, signal.SIGSTOP)
        for pid in self.handles:
            self.send(pid, signal.SIGSTOP)
        while True:
            new = self.observe(proc.pid)
            for pid in new:
                self.send(pid, signal.SIGSTOP)
            if not new:
                break
        for pid in reversed(list(self.handles)):
            self.send(pid, signal.SIGKILL)
        proc.wait()
        for pid in self.handles:
            if pid == proc.pid:
                continue
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

    def close(self):
        for _, handle in self.handles.values():
            os.close(handle)
        self.libc.prctl(36, self.prior.value, 0, 0, 0)


def run_worker(payload, timeout, output_limit):
    """Return bounded raw bytes and transport failure, never a synthetic core report."""
    proc = None
    owner = None
    settled = False
    out, err = bytearray(), bytearray()
    failure = None
    try:
        owner = OwnedTree()
        start = time.monotonic()
        flags = ["-S"] if sys.flags.no_site else []
        if sys.flags.no_user_site:
            flags.append("-s")
        command = [sys.executable, *flags, "-B", str(Path(__file__).with_name("maintenance_worker.py"))]
        environment = dict(payload["environment"])
        environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        with tempfile.TemporaryFile() as inputs:
            inputs.write(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            inputs.seek(0)
            proc = subprocess.Popen(command, stdin=inputs, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=environment, start_new_session=True)
            with selectors.DefaultSelector() as poll:
                poll.register(proc.stdout, selectors.EVENT_READ, out)
                poll.register(proc.stderr, selectors.EVENT_READ, err)
                while poll.get_map():
                    owner.observe(proc.pid)
                    remaining = timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        failure = "target_timeout"
                        break
                    for key, _ in poll.select(min(remaining, 0.01)):
                        block = os.read(key.fileobj.fileno(), min(65536, output_limit + 1))
                        if not block:
                            poll.unregister(key.fileobj)
                            continue
                        key.data.extend(block)
                        if len(out) + len(err) > output_limit:
                            failure = "output_limit_exceeded"
                            break
                    if failure:
                        break
                if failure is None:
                    remaining = timeout - (time.monotonic() - start)
                    try:
                        proc.wait(timeout=max(0, remaining))
                    except subprocess.TimeoutExpired:
                        failure = "target_timeout"
                if failure is None and (proc.returncode != 0 or err):
                    failure = "worker_failed"
            if failure is not None:
                owner.finish(proc)
            # A complete normal worker return leaves the core's authorized child
            # lifetime unchanged. Cancellation belongs only to abnormal exits.
            settled = True
        return bytes(out) if failure is None else b"", failure
    except (OSError, ValueError):
        return b"", "worker_failed" if proc is not None else "maintenance_unavailable"
    finally:
        try:
            if proc is not None and owner is not None and not settled:
                # The root can already have exited while owned children remain.
                owner.finish(proc)
        finally:
            if proc is not None:
                proc.stdout.close()
                proc.stderr.close()
            if owner is not None:
                owner.close()
