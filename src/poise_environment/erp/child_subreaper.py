"""Own one serial Linux action's children, including adopted descendants."""
import ctypes
import errno
import os
import subprocess
import time

import psutil


PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37


class ChildSubreaper:
    def __init__(self):
        self.owner = psutil.Process()
        self.previous = ctypes.c_int()
        try:
            self.prctl = ctypes.CDLL(None, use_errno=True).prctl
        except AttributeError as error:
            raise OSError(errno.ENOSYS, "Linux prctl is unavailable") from error
        self.prctl.restype = ctypes.c_int

    def _call(self, option, value):
        result = self.prctl(ctypes.c_int(option), value,
                            ctypes.c_ulong(0), ctypes.c_ulong(0),
                            ctypes.c_ulong(0))
        if result != 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))

    def __enter__(self):
        if self.owner.children():
            raise OSError(errno.EBUSY,
                          "Command ownership requires an idle action process")
        self._call(PR_GET_CHILD_SUBREAPER, ctypes.byref(self.previous))
        self._call(PR_SET_CHILD_SUBREAPER, ctypes.c_ulong(1))
        return self

    def __exit__(self, error_type, error, traceback):
        self._call(PR_SET_CHILD_SUBREAPER,
                   ctypes.c_ulong(self.previous.value))

    def settle(self, root, bound_seconds):
        deadline = time.monotonic() + bound_seconds

        def remaining():
            return max(0, deadline - time.monotonic())

        if root is not None:
            if root.poll() is None:
                root.kill()
            root.wait(timeout=remaining())
        while children := self.owner.children():
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            for child in children:
                try:
                    child.wait(timeout=remaining())
                except psutil.NoSuchProcess:
                    pass
            if time.monotonic() >= deadline and self.owner.children():
                raise subprocess.TimeoutExpired("Owned command cleanup",
                                                bound_seconds)
