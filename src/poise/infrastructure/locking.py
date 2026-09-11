"""One bounded Linux lock primitive shared by task and configuration stores."""
import fcntl
import math
import time
from contextlib import contextmanager
from pathlib import Path
from ..modules.foundation.errors import PoiseError


@contextmanager
def exclusive_lock(path: Path, wait: float, poll: float):
    for value in (wait, poll):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise PoiseError("Требуются явные конечные положительные пределы lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PoiseError("Истёк лимит ожидания внешнего lock")
                time.sleep(min(poll, remaining))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
