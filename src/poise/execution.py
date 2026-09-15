from __future__ import annotations
import os
import signal
import shutil
import subprocess
import threading
import time
from pathlib import Path
from .common import PoiseError, prohibit_git_push


class RegisteredCheckRunner:
    """Own one Poise verification process from launch through cancellation.

    The caller supplies the exact argv and cwd.  The runner does not resolve a
    project, checkout or tool location on its own; it only owns the spawned
    process group and the terminal logs for registered Poise checks.
    """

    def __init__(self, preview_chars: int = 2000):
        if type(preview_chars) is not int or preview_chars <= 0:
            raise ValueError('preview_chars must be a positive int')
        self.preview_chars = preview_chars
        self._lock = threading.Lock()
        self._active: dict[str, dict] = {}

    def active_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._active))

    @staticmethod
    def _kill_group(child: subprocess.Popen) -> None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def cancel(self, run_id: str) -> bool:
        """Cancel only a process group currently owned by this runner."""
        with self._lock:
            state = self._active.get(run_id)
            if state is None:
                return False
            state['cancel_requested'] = True
            child = state['child']
        self._kill_group(child)
        return True

    def run(
        self,
        run_id: str,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout: float | None,
        stdout_path: Path,
        stderr_path: Path,
    ) -> dict:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError('run_id must be a non-empty string')
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
            raise ValueError('timeout must be positive or None')
        prohibit_git_push(argv)
        start = time.monotonic()
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        timed_out = False
        with stdout_path.open('wb') as out, stderr_path.open('wb') as err:
            try:
                child = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
            except OSError as exc:
                raise PoiseError(f'Не удалось запустить точную команду {argv[0]}: {exc}') from exc
            state = {'child': child, 'cancel_requested': False}
            with self._lock:
                if run_id in self._active:
                    self._kill_group(child)
                    child.wait()
                    raise PoiseError(f'Проверка уже запущена: {run_id}')
                self._active[run_id] = state
            try:
                try:
                    code = child.wait() if timeout is None else child.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._kill_group(child)
                    code = child.wait()
            finally:
                with self._lock:
                    current = self._active.pop(run_id, state)
                    cancelled = bool(current['cancel_requested'])
        return {
            'actual_exit_code': code,
            'timed_out': timed_out,
            'cancelled': cancelled,
            'duration_seconds': time.monotonic() - start,
            'stdout': str(stdout_path),
            'stderr': str(stderr_path),
            'stdout_preview': preview(stdout_path, self.preview_chars),
            'stderr_preview': preview(stderr_path, self.preview_chars),
        }


def run_command(argv: list[str], cwd: Path, env: dict[str,str], timeout: float | None,
                stdout_path: Path, stderr_path: Path) -> dict:
    """Compatibility wrapper around the single registered-check execution API."""
    return RegisteredCheckRunner().run(
        'direct', argv, cwd, env, timeout, stdout_path, stderr_path
    )



def capture_declared_outputs(declarations: list[dict], output_dir: Path, capture_dir: Path) -> tuple[list[dict], bool]:
    """Snapshot declared files produced in the runner-owned output directory."""
    root = output_dir.resolve()
    capture_dir.mkdir(parents=True, exist_ok=True)
    records = []
    complete = True
    for declaration in declarations:
        source = (root / declaration['path']).resolve()
        if not source.is_relative_to(root):
            raise PoiseError(f"Declared output escapes runner output directory: {declaration['path']}")
        base = {
            'id': declaration['id'],
            'declared_path': declaration['path'],
            'required': declaration['required'],
        }
        if not source.exists():
            records.append({**base, 'status': 'missing', 'path': None, 'digest': None, 'size': None})
            if declaration['required']:
                complete = False
            continue
        if source.is_symlink() or not source.is_file():
            records.append({**base, 'status': 'invalid', 'path': None, 'digest': None, 'size': None})
            complete = False
            continue
        target = capture_dir / declaration['id']
        before_size = source.stat().st_size
        before_digest = _file_sha256(source)
        shutil.copyfile(source, target)
        if source.stat().st_size != before_size or _file_sha256(source) != before_digest:
            target.unlink(missing_ok=True)
            raise PoiseError(f"Declared output changed while being captured: {declaration['path']}")
        records.append({
            **base,
            'status': 'captured',
            'path': str(target),
            'digest': _file_sha256(target),
            'size': target.stat().st_size,
        })
    return records, complete


def _file_sha256(path: Path) -> str:
    import hashlib
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            value.update(chunk)
    return value.hexdigest()

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


def equals(path: Path, expected: str) -> bool:
    target = expected.encode('utf-8')
    offset = 0
    with path.open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            if chunk != target[offset:offset + len(chunk)]:
                return False
            offset += len(chunk)
    return offset == len(target)


def method_passed(method: dict, result: dict) -> bool:
    passed = (
        not result['timed_out']
        and not result.get('cancelled', False)
        and result['actual_exit_code'] == method['expected_exit_code']
        and all(contains(Path(result['stdout']), marker) for marker in method['stdout_contains'])
        and all(contains(Path(result['stderr']), marker) for marker in method['stderr_contains'])
    )
    plan = method.get('verification_plan')
    failure = None if plan is None else plan.get('red_failure')
    if failure is None:
        return passed
    return (
        passed
        and result['actual_exit_code'] == failure['exit_code']
        and equals(Path(result['stdout']), failure['stdout_equals'])
        and equals(Path(result['stderr']), failure['stderr_equals'])
    )


def preview(path: Path, chars: int) -> str:
    with path.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell()-chars*4))
        return stream.read().decode('utf-8', errors='replace')[-chars:]
