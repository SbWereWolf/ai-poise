from __future__ import annotations
import math
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from .common import PoiseError, prohibit_git_push


class RunnerTimeoutPolicy:
    REQUIRED = {
        'initial_seconds', 'history_multiplier', 'progress_gap_seconds',
        'poll_seconds', 'diagnostic_override_max_seconds',
    }

    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def parse(cls, raw: dict) -> "RunnerTimeoutPolicy":
        if not isinstance(raw, dict) or set(raw) != cls.REQUIRED:
            raise PoiseError('check_runner timeout policy must define exactly the required fields')
        data = dict(raw)
        for key in ('initial_seconds', 'history_multiplier', 'progress_gap_seconds', 'poll_seconds', 'diagnostic_override_max_seconds'):
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise PoiseError(f'check_runner.{key} must be a positive finite number')
        if data['history_multiplier'] < 1:
            raise PoiseError('check_runner.history_multiplier must be at least 1')
        if data['diagnostic_override_max_seconds'] < data['initial_seconds']:
            raise PoiseError('diagnostic override maximum must not be below initial timeout')
        return cls(data)

    def select(self, successful_durations, diagnostic_override=None, evidence_ids=()):
        durations = []
        for value in successful_durations:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise PoiseError('verified-success duration must be a finite non-negative number')
            durations.append(float(value))
        initial = float(self.data['initial_seconds'])
        longest = max(durations) if durations else None
        selected = initial if longest is None else max(initial, math.ceil(self.data['history_multiplier'] * longest))
        basis = 'initial' if longest is None else 'verified_success_history'
        override_receipt = None
        override_reason = None
        if diagnostic_override is not None:
            if not isinstance(diagnostic_override, dict) or set(diagnostic_override) != {'seconds', 'evidence_run_id', 'reason'}:
                raise PoiseError('diagnostic override requires seconds, evidence_run_id and reason')
            seconds = diagnostic_override['seconds']
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
                raise PoiseError('diagnostic override seconds must be positive and finite')
            if seconds > self.data['diagnostic_override_max_seconds']:
                raise PoiseError('diagnostic override exceeds configured maximum')
            if seconds < selected:
                raise PoiseError('diagnostic override cannot reduce the normal timeout')
            evidence = diagnostic_override['evidence_run_id']
            if evidence not in set(evidence_ids):
                raise PoiseError('diagnostic override must reference existing evidence')
            reason = diagnostic_override['reason']
            if not isinstance(reason, str) or not reason.strip():
                raise PoiseError('diagnostic override reason must be non-empty')
            selected = float(seconds)
            basis = 'diagnostic_override'
            override_receipt = evidence
            override_reason = reason.strip()
        return {
            'seconds': selected,
            'basis': basis,
            'history_count': len(durations),
            'longest_verified_success_seconds': longest,
            'history_multiplier': float(self.data['history_multiplier']),
            'progress_gap_seconds': float(self.data['progress_gap_seconds']),
            'poll_seconds': float(self.data['poll_seconds']),
            'diagnostic_evidence_run_id': override_receipt,
            'diagnostic_override_reason': override_reason,
        }


def timeout_profile(argv: list[str], workload: str, env: dict[str, str]) -> dict:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise PoiseError('timeout profile requires non-empty argv')
    if not isinstance(workload, str) or not workload:
        raise PoiseError('timeout profile requires workload id')
    executable = argv[0]
    if os.path.isabs(executable):
        resolved = str(Path(executable).resolve())
    else:
        resolved = shutil.which(executable, path=env.get('PATH', os.defpath))
        if resolved is None:
            raise PoiseError(f'Cannot resolve registered command executable: {executable}')
        resolved = str(Path(resolved).resolve())
    return {
        'command': list(argv),
        'workload': workload,
        'runtime': {
            'executable': resolved,
            'platform': sys.platform,
        },
    }


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
        *,
        progress_gap_seconds: float | None = None,
        poll_seconds: float = 0.05,
    ) -> dict:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError('run_id must be a non-empty string')
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
            raise ValueError('timeout must be positive or None')
        if progress_gap_seconds is not None and (isinstance(progress_gap_seconds, bool) or not isinstance(progress_gap_seconds, (int, float)) or progress_gap_seconds <= 0):
            raise ValueError('progress_gap_seconds must be positive or None')
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)) or poll_seconds <= 0:
            raise ValueError('poll_seconds must be positive')
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
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError as exc:
                raise PoiseError(f'Не удалось запустить точную команду {argv[0]}: {exc}') from exc
            state = {'child': child, 'cancel_requested': False, 'last_progress': start}
            with self._lock:
                if run_id in self._active:
                    self._kill_group(child)
                    child.wait()
                    raise PoiseError(f'Проверка уже запущена: {run_id}')
                self._active[run_id] = state

            def pump(stream, target):
                try:
                    while True:
                        chunk = stream.read1(64 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
                        target.flush()
                        state['last_progress'] = time.monotonic()
                finally:
                    stream.close()

            stdout_thread = threading.Thread(target=pump, args=(child.stdout, out), daemon=True)
            stderr_thread = threading.Thread(target=pump, args=(child.stderr, err), daemon=True)
            stdout_thread.start(); stderr_thread.start()
            timeout_reason = None
            try:
                while True:
                    code = child.poll()
                    if code is not None:
                        break
                    now = time.monotonic()
                    if progress_gap_seconds is not None and now - state['last_progress'] >= progress_gap_seconds:
                        timed_out = True
                        timeout_reason = 'progress_gap'
                        self._kill_group(child)
                        code = child.wait()
                        break
                    if timeout is not None and now - start >= timeout:
                        timed_out = True
                        timeout_reason = 'hard_limit'
                        self._kill_group(child)
                        code = child.wait()
                        break
                    time.sleep(poll_seconds)
            finally:
                stdout_thread.join(); stderr_thread.join()
                with self._lock:
                    current = self._active.pop(run_id, state)
                    cancelled = bool(current['cancel_requested'])
        return {
            'actual_exit_code': code,
            'timed_out': timed_out,
            'timeout_reason': timeout_reason,
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
