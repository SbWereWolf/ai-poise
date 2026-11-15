"""Readonly metadata and bounded byte selections from retained ordinary files."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat

from domain.generic import Problem


class OutputFiles:
    @contextmanager
    def opened(self, path, index):
        try:
            path = Path(path)
            if not stat.S_ISREG(path.stat().st_mode):
                raise Problem('output_source_unavailable',
                              {'index': index, 'reason': 'not_regular', 'errno': None},
                              'select:output', 'unavailable')
            with path.open('rb') as stream:
                yield stream
        except OSError as error:
            raise Problem('output_source_unavailable',
                          {'index': index, 'reason': 'io', 'errno': error.errno},
                          'select:output', 'unavailable') from None

    def size(self, source, index):
        with self.opened(source['path'], index) as stream:
            return os.fstat(stream.fileno()).st_size

    def read_range(self, source, start, count, index):
        with self.opened(source['path'], index) as stream:
            size = os.fstat(stream.fileno()).st_size
            if start > size:
                raise Problem('output_range_bounds', {'index': index, 'start': start, 'bytes': size},
                              'correct:request')
            stream.seek(start)
            remaining, chunks = min(count, size - start), []
            while remaining:
                chunk = stream.read(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return size, b''.join(chunks)
