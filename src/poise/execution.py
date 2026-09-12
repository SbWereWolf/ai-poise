from __future__ import annotations
import os
import signal
import subprocess
import time
from pathlib import Path
from .common import PoiseError


def run_command(argv: list[str], cwd: Path, env: dict[str,str], timeout: float | None,
                stdout_path: Path, stderr_path: Path) -> dict:
    """Не загружает полный вывод в память; ждёт конечного результата процесса."""
    start = time.monotonic()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    timed_out = False
    with stdout_path.open('wb') as out, stderr_path.open('wb') as err:
        try:
            child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=err, start_new_session=True)
        except OSError as exc:
            raise PoiseError(f'Не удалось запустить точную команду {argv[0]}: {exc}') from exc
        try:
            code = child.wait() if timeout is None else child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(child.pid, signal.SIGKILL)
            code = child.wait()
    return {'actual_exit_code': code, 'timed_out': timed_out,
            'duration_seconds': time.monotonic()-start,
            'stdout': str(stdout_path), 'stderr': str(stderr_path)}


def contains(path: Path, needle: str) -> bool:
    target = needle.encode('utf-8')
    if not target: return True
    tail = b''
    with path.open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            block = tail + chunk
            if target in block: return True
            tail = block[-(len(target)-1):] if len(target)>1 else b''
    return False


def preview(path: Path, chars: int) -> str:
    with path.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell()-chars*4))
        return stream.read().decode('utf-8', errors='replace')[-chars:]
