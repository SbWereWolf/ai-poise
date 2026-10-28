"""One bounded Linux lock primitive shared by task and configuration stores."""
import fcntl
import math
import time
from contextlib import contextmanager
from pathlib import Path
from ..modules.foundation.errors import LockUnavailable, PoiseError


@contextmanager
def blocking_file_lock(path: Path):
    """A short general filesystem transaction, without a guessed wait budget."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_lock(path: Path, wait: float, poll: float, *, existing=False):
    for value in (wait, poll):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise PoiseError("Требуются явные конечные положительные пределы lock")
    if not existing:
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("rb" if existing else "a+b") as handle:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockUnavailable("Истёк лимит ожидания внешнего lock")
                time.sleep(min(poll, remaining))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
