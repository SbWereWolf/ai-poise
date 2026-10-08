"""Consume a protected rollback image without caller-owned original-store FDs."""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from ...modules.foundation.errors import PoiseError


class SnapshotWorkerError(RuntimeError):
    """Worker/transport uncertainty; do not classify it as invalid input."""


def _finish_reader(child):
    problems = []
    trailing = False
    try:
        try:
            child.stdin.close()
        except Exception as exc:
            problems.append(f'release: {type(exc).__name__}: {exc}')
    finally:
        try:
            # Drain before wait, including an image interrupted by a caller error.
            while child.stdout.read(64 * 1024):
                trailing = True
        except Exception as exc:
            problems.append(f'drain: {type(exc).__name__}: {exc}')
        finally:
            try:
                child.stdout.close()
            except Exception as exc:
                problems.append(f'close: {type(exc).__name__}: {exc}')
            finally:
                code = child.wait()
    if code != 0:
        problems.append(f'worker exit: {code}')
    if trailing:
        problems.append('unexpected remaining worker output')
    if problems:
        return SnapshotWorkerError('Task DB snapshot cleanup: ' + '; '.join(problems))
    return None


def _metadata(stream):
    try:
        value = json.loads(stream.readline())
    except (ValueError, UnicodeError) as exc:
        raise SnapshotWorkerError('Task DB snapshot worker returned no valid metadata') from exc
    if not isinstance(value, dict):
        raise SnapshotWorkerError('Task DB snapshot worker metadata is not an object')
    status = value.get('status')
    if status == 'rejected' and set(value) == {'status', 'reason'} and isinstance(value['reason'], str):
        raise PoiseError(value['reason'])
    if (status == 'unknown' and set(value) == {'status', 'error_type', 'reason'}
            and isinstance(value['error_type'], str) and value['error_type']
            and isinstance(value['reason'], str)):
        raise SnapshotWorkerError(f"{value['error_type']}: {value['reason']}")
    if (status != 'ready' or set(value) != {'status', 'size'}
            or type(value['size']) is not int or value['size'] < 100):
        raise SnapshotWorkerError('Task DB snapshot worker metadata violates its contract')
    return value['size']


@contextmanager
def _guarded_image(path):
    if not sys.executable:
        raise PoiseError('Task DB snapshot requires the current Python executable')
    worker = Path(__file__).resolve().with_name('readonly_worker.py')
    child = subprocess.Popen(
        [sys.executable, '-I', '-B', str(worker), str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        close_fds=True,
    )
    primary = None
    try:
        size = _metadata(child.stdout)
        image = child.stdout.read(size)
        if len(image) != size:
            raise SnapshotWorkerError('Task DB snapshot worker ended before the complete image')
        if child.poll() is not None:
            raise SnapshotWorkerError('Task DB snapshot worker released protection before consumption')
        yield image
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            error = _finish_reader(child)
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note(f'Snapshot cleanup: {type(exc).__name__}: {exc}')
        else:
            if error is not None:
                if primary is not None:
                    primary.add_note(str(error))
                else:
                    raise error


@contextmanager
def readonly_snapshot(path: Path, *, lock_seconds):
    """Keep the child's original OFD protection through memory-consumer cleanup."""
    if not hasattr(sqlite3.Connection, 'deserialize'):
        raise PoiseError('Среда SQLite не поддерживает deserialize для проверки Task DB; '
                         'чтение исходного файла через SQLite не выполняется')
    try:
        with _guarded_image(path) as image:
            with closing(sqlite3.connect(':memory:', timeout=lock_seconds)) as db:
                db.deserialize(image)
                db.execute('PRAGMA temp_store=MEMORY')
                yield db
    except MemoryError as exc:
        raise PoiseError(f'Недостаточно памяти для защищённого снимка Task DB: {path}; '
                         'хранилище не изменено, альтернативное чтение не выполняется') from exc
