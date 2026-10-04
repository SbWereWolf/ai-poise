"""Protected rollback snapshots without opening the original store in SQLite."""
from contextlib import closing, contextmanager
import fcntl
import os
from pathlib import Path
import platform
import sqlite3
import stat
import struct
import sys

from ...modules.foundation.errors import PoiseError


# Linux generic UAPI, not a configurable retry or an alternate lock protocol:
# include/uapi/asm-generic/fcntl.h; LP64 struct flock uses these native fields.
_LINUX_F_OFD_SETLK = 37
_FLOCK = '@hhqqi4x'


def _require_binding():
    if (sys.platform != 'linux' or platform.machine() not in ('x86_64', 'aarch64')
            or struct.calcsize('@l') != 8 or struct.calcsize('@i') != 4
            or struct.calcsize('@h') != 2 or struct.calcsize(_FLOCK) != 32):
        raise PoiseError('Проверка Task DB требует поддерживаемого Linux LP64 OFD ABI; '
                         'чтение без защиты не выполняется')


def _open_readonly(path, flags):
    return os.open(path, flags | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)


def _protect(descriptor):
    try:
        # Shared through EOF, including SQLite's reserved/pending lock bytes.
        # The guard survives another descriptor's close and conflicts with a
        # POSIX writer even in this process; the last owning close releases it.
        fcntl.fcntl(descriptor, _LINUX_F_OFD_SETLK,
                    struct.pack(_FLOCK, fcntl.F_RDLCK, os.SEEK_SET, 0, 0, 0))
    except OSError as exc:
        raise PoiseError(f'Не удалось защитить Task DB через Linux OFD: {exc}. '
                         'Завершите запись или используйте среду с поддержкой OFD; '
                         'незащищённое чтение не выполняется') from exc


def _identity(source, path):
    actual = os.fstat(source.fileno())
    selected = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(actual.st_mode):
        raise PoiseError(f'Task DB не является обычным файлом: {path}')
    if (actual.st_dev, actual.st_ino) != (selected.st_dev, selected.st_ino):
        raise PoiseError(f'Выбранный файл Task DB изменился во время проверки: {path}')
    return (actual.st_dev, actual.st_ino, actual.st_mode, actual.st_size,
            actual.st_mtime_ns, actual.st_ctime_ns)


def _require_rollback(path, header):
    # SQLite file-format contract: 100-byte header, magic, read/write versions.
    if len(header) != 100 or header[:16] != b'SQLite format 3\x00':
        raise PoiseError(f'Task DB не содержит корректный заголовок SQLite: {path}')
    if 2 in header[18:20]:
        raise PoiseError(f'WAL-состояние Task DB не поддерживает проверку без изменений: {path}. '
                         'Получите согласованное хранилище через его владельца; '
                         'проверка не выполняет checkpoint или миграцию')
    if header[18:20] != b'\x01\x01':
        raise PoiseError(f'Формат Task DB не поддерживает защищённый rollback-снимок: {path}')
    for suffix in ('-wal', '-shm', '-journal'):
        if os.path.lexists(str(path) + suffix):
            kind = 'rollback-journal' if suffix == '-journal' else 'WAL/SHM'
            raise PoiseError(f'Обнаружен {kind} у Task DB: {path}; согласованность основного '
                             'файла не подтверждена. Завершите запись и восстановите '
                             'хранилище через его владельца; проверка не изменяет журнал')


@contextmanager
def readonly_snapshot(path: Path, *, lock_seconds):
    """Keep original OFD protection until the memory consumer has finished."""
    _require_binding()
    if not hasattr(sqlite3.Connection, 'deserialize'):
        raise PoiseError('Среда SQLite не поддерживает deserialize для проверки Task DB; '
                         'чтение исходного файла через SQLite не выполняется')
    try:
        with open(path, 'rb', opener=_open_readonly) as source:
            _protect(source.fileno())
            identity = _identity(source, path)
            header = source.read(100)
            _require_rollback(path, header)
            source.seek(0)
            image = source.read()
            if (_identity(source, path) != identity or len(image) != identity[3]
                    or image[:100] != header):
                raise PoiseError(f'Файл Task DB изменился во время чтения снимка: {path}')
            _require_rollback(path, image[:100])
            with closing(sqlite3.connect(':memory:', timeout=lock_seconds)) as db:
                db.deserialize(image)
                db.execute('PRAGMA temp_store=MEMORY')
                yield db
    except MemoryError as exc:
        raise PoiseError(f'Недостаточно памяти для защищённого снимка Task DB: {path}; '
                         'хранилище не изменено, альтернативное чтение не выполняется') from exc
