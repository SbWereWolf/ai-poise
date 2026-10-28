"""Ordinary native path metadata and the declared target writer."""
from pathlib import Path

from domain.filesystem import Effect, PolicyIssue, Root


class LocalFilesystem:
    def _resolve(self, path, field, index=None, *, strict=True):
        try:
            return path.resolve(strict=strict)
        except (OSError, ValueError):
            raise PolicyIssue('path_unresolvable', field, index) from None

    def roots(self, specifications):
        result, directories = [], set()
        for index, value in enumerate(specifications):
            field = '/policy/roots/' + str(index) + '/path'
            path = Path(value['path'])
            if not path.is_absolute():
                raise PolicyIssue('invalid_policy', field)
            path = self._resolve(path, field)
            if not path.is_dir() or path in directories:
                raise PolicyIssue('invalid_policy', field)
            directories.add(path)
            result.append(Root(value['id'], path, value['read'], value['write']))
        return tuple(result)

    def base(self, value):
        field = '/request/base'
        path = Path(value)
        if not path.is_absolute():
            raise PolicyIssue('invalid_request', field)
        path = self._resolve(path, field)
        if not path.is_dir():
            raise PolicyIssue('invalid_request', field)
        return path

    def effects(self, specifications, base):
        result = []
        for index, value in enumerate(specifications):
            field = '/request/effects/' + str(index) + '/path'
            path = Path(value['path'])
            path = path if path.is_absolute() else base / path
            path = self._resolve(path, field, index, strict=value['operation'] == 'read')
            if value['operation'] == 'write':
                parent = self._resolve(path.parent, field, index)
                if not parent.is_dir():
                    raise PolicyIssue('path_unresolvable', field, index)
            result.append(Effect(index, value['operation'], path))
        return tuple(result)

    def file_or_missing(self, path):
        return not path.exists() or path.is_file()

    def write(self, path, data):
        return path.write_bytes(data)
