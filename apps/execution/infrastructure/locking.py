"""Bounded flock for one configured application resource operation."""
from contextlib import contextmanager
import fcntl
import time


@contextmanager
def resource_lock(path, seconds, poll_seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock_file:
        deadline = time.monotonic() + seconds
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if time.monotonic() >= deadline:
                    raise OSError('Execution resource lock timeout') from error
                time.sleep(poll_seconds)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
