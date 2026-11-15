"""Test-owned observer of real source I/O; never replaces product output."""
import builtins
import io
import json
import os
from pathlib import Path
import runpy
import sys


mode, entry, config, audit_path, source_json = sys.argv[1:]
sources = set(json.loads(source_json))
events, descriptors = [], {}
plain_open, plain_io, plain_os_open = builtins.open, io.open, os.open
plain_read, plain_pread, plain_close = os.read, os.pread, os.close


def source_name(value):
    if isinstance(value, int):
        return descriptors.get(value)
    try:
        name = os.path.abspath(os.fspath(value))
    except TypeError:
        return None
    return name if name in sources else None


class ObservedFile:
    def __init__(self, stream, name):
        self.stream, self.name = stream, name
        descriptors[stream.fileno()] = name

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if not self.stream.closed:
            descriptors.pop(self.stream.fileno(), None)
        self.stream.close()

    def observe(self, operation, count, action):
        offset = self.stream.tell()
        value = action()
        actual = value if isinstance(value, int) else len(value)
        events.append({'path': self.name, 'operation': operation, 'offset': offset,
                       'requested': count, 'actual': actual})
        return value

    def read(self, count=-1):
        return self.observe('read', count, lambda: self.stream.read(count))

    def read1(self, count=-1):
        return self.observe('read1', count, lambda: self.stream.read1(count))

    def readinto(self, buffer):
        return self.observe('readinto', len(buffer), lambda: self.stream.readinto(buffer))

    def readline(self, count=-1):
        return self.observe('readline', count, lambda: self.stream.readline(count))

    def readall(self):
        return self.read()

    def readlines(self, hint=-1):
        return list(self)

    def __iter__(self):
        return self

    def __next__(self):
        value = self.readline()
        if not value:
            raise StopIteration
        return value


def opened(factory, value, *args, **kwargs):
    stream = factory(value, *args, **kwargs)
    name = source_name(value)
    return ObservedFile(stream, name) if name is not None else stream


def os_open(path, *args, **kwargs):
    fd = plain_os_open(path, *args, **kwargs)
    name = source_name(path)
    if name is not None:
        descriptors[fd] = name
    return fd


def fd_read(fd, count, offset=None):
    name = descriptors.get(fd)
    position = os.lseek(fd, 0, os.SEEK_CUR) if offset is None and name else offset
    value = plain_read(fd, count) if offset is None else plain_pread(fd, count, offset)
    if name:
        events.append({'path': name, 'operation': 'os.read' if offset is None else 'os.pread',
                       'offset': position, 'requested': count, 'actual': len(value)})
    return value


def close(fd):
    descriptors.pop(fd, None)
    return plain_close(fd)


builtins.open = lambda value, *a, **kw: opened(plain_open, value, *a, **kw)
io.open = lambda value, *a, **kw: opened(plain_io, value, *a, **kw)
os.open, os.close = os_open, close
os.read = lambda fd, count: fd_read(fd, count)
os.pread = lambda fd, count, offset: fd_read(fd, count, offset)
try:
    if mode == 'control':
        with open(next(iter(sources)), 'rb') as stream:
            stream.seek(1)
            assert stream.read(3) == b'B\xff\n'
    else:
        assert mode == 'subject'
        sys.argv = [entry, '--config', config]
        runpy.run_path(entry, run_name='__main__')
finally:
    builtins.open, io.open, os.open = plain_open, plain_io, plain_os_open
    os.read, os.pread, os.close = plain_read, plain_pread, plain_close
    Path(audit_path).write_text(json.dumps(events) + '\n')
