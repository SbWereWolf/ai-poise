"""Explicit input files and complete local output; no Task or Session store."""
import json
import os
from pathlib import Path
from uuid import uuid4

from domain.generic import Problem, invalid, text
from infrastructure.json_io import loads


class FileSurface:
    def __init__(self, limit):
        self.limit = limit

    def absolute(self, value, pointer):
        text(value, pointer)
        path = Path(value)
        if not path.is_absolute():
            invalid(pointer)
        return path

    def checkout(self, value, pointer='/input/checkout'):
        path = self.absolute(value, pointer)
        if not path.is_dir():
            invalid(pointer)
        return str(path.resolve())

    def read_bytes(self, path):
        path = Path(path)
        with path.open('rb') as stream:
            raw = stream.read(self.limit + 1)
        if len(raw) > self.limit:
            raise Problem('input_limit', {'path': str(path), 'limit': 'input_bytes'}, 'adjust:input')
        return raw

    def read_json(self, path):
        return loads(self.read_bytes(path))

    def rule_paths(self, checkout, paths):
        candidates = {}
        for path in paths:
            parents = path.split('/')[:-1]
            for count in range(len(parents) + 1):
                name = '/'.join([*parents[:count], 'AGENTS.md'])
                candidates.setdefault(name, []).append(path)
        return [(name, scopes) for name, scopes in sorted(candidates.items())
                if (Path(checkout) / name).exists()]

    def materials(self, context, specs):
        cache, result = {}, []
        for spec in specs:
            root = context['checkout'] if spec.root == 'checkout' else context['instruction_root']
            key = (root, spec.path)
            if key not in cache:
                try:
                    content = self.read_bytes(Path(root) / spec.path).decode('utf-8')
                    cache[key] = {'available': True, 'content': content}
                except (OSError, UnicodeError):
                    cache[key] = {'available': False, 'content': None}
            result.append({**spec.public(), **cache[key]})
        return result


class OutputStore:
    def __init__(self, config):
        self.root = Path(config['results_root'])
        self.mode, self.names = config['file_mode'], config['files']
        self.references = []

    def capture_paths(self, identity=None):
        directory = self.root / (uuid4().hex if identity is None else identity)
        directory.mkdir(parents=True)
        return {kind: directory / name for kind, name in self.names.items()}

    def evidence(self, path, kind):
        os.chmod(path, self.mode)
        item = {'kind': kind, 'path': str(Path(path).absolute())}
        if item not in self.references:
            self.references.append(item)
        return item

    def save_receipt(self, paths, value):
        paths['receipt'].write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        self.evidence(paths['receipt'], 'receipt')

    def save_result(self, result):
        paths = self.capture_paths()
        paths['result'].write_bytes(json.dumps(result, ensure_ascii=False,
                                               separators=(',', ':')).encode('utf-8') + b'\n')
        self.evidence(paths['result'], 'result')
