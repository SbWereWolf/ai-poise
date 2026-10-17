"""Actual declared inputs, independent ordinary and package cache namespaces."""
import hashlib
import json
import os
from pathlib import Path

from ...modules.project_checks.contracts import CheckFailure
from ..git_transport import run_git_receipt


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def hash_value(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class InputSnapshot:
    def __init__(self, checkout, git_seconds):
        self.checkout = Path(checkout)
        self.git_seconds = git_seconds

    def git(self, *arguments):
        environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        environment['GIT_OPTIONAL_LOCKS'] = '0'
        result = run_git_receipt(self.checkout, list(arguments), self.git_seconds, environment)
        if result['actual_exit_code'] != 0:
            raise CheckFailure('git_missing', 'checkout', result['stderr'],
                               'Восстановите доступ к Git выбранной рабочей копии.', status='unavailable')
        return result['stdout']

    def inventory(self):
        files = set(self.git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\0')) - {''}
        directories = {'.'}
        for name in files:
            parent = Path(name).parent
            while parent != Path('.'):
                directories.add(parent.as_posix())
                parent = parent.parent
        return files, directories

    def package_inventory(self, catalogue):
        candidates = []
        for pattern in catalogue.candidate_globs:
            candidates.extend(path.relative_to(self.checkout).as_posix()
                              for path in self.matches(pattern) if path.is_file())
        membership = {}
        for package in catalogue.packages:
            members = []
            for step in package['steps']:
                for pattern in step['test_paths']:
                    for path in self.matches(pattern):
                        if path.is_file():
                            members.append(path.relative_to(self.checkout).as_posix())
                        elif path.is_dir():
                            members.extend(p.relative_to(self.checkout).as_posix()
                                           for p in path.rglob('*') if p.is_file())
            membership[package['package_id']] = members
        return candidates, membership

    def matches(self, pattern):
        if any(c in pattern for c in '*?['):
            return sorted(self.checkout.glob(pattern), key=lambda v: v.as_posix())
        path = self.checkout / pattern
        return [path] if path.exists() else []

    def identity(self, path):
        if not path.resolve().is_relative_to(self.checkout.resolve()):
            raise CheckFailure('invalid_options', 'scope_paths', f'Вход {path} выходит за checkout.',
                               'Укажите входы внутри выбранной рабочей копии.')
        raw = path.read_bytes() if path.is_file() else b''
        return {'path': path.relative_to(self.checkout).as_posix(),
                'kind': 'file' if path.is_file() else 'directory',
                'sha256': hashlib.sha256(raw).hexdigest() if path.is_file() else None,
                'bytes': len(raw), 'mode': path.stat().st_mode & 0o777}

    def filesystem(self, patterns):
        files, directories = self.inventory()
        entries, missing = {}, []
        for pattern in patterns:
            matches = self.matches(pattern)
            if not matches:
                missing.append(pattern)
            explicit = not any(c in pattern for c in '*?[')
            for match in matches:
                relative = match.relative_to(self.checkout).as_posix()
                eligible = relative in (directories if match.is_dir() else files)
                if explicit and not eligible:
                    raise CheckFailure('invalid_options', 'scope_paths',
                                       f'Объявленный вход {relative} игнорируется Git.',
                                       'Выберите исходные файлы, доступные в рабочей инвентаризации Git.')
                candidates = [match, *sorted(match.rglob('*'))] if match.is_dir() else [match]
                for path in candidates:
                    name = path.relative_to(self.checkout).as_posix()
                    if name in (directories if path.is_dir() else files) and path.exists():
                        entries[name] = self.identity(path)
        material = {'patterns': list(patterns), 'missing': sorted(missing),
                    'entries': [entries[k] for k in sorted(entries)]}
        return {**material, 'sha256': hash_value(material)}

    def tracked_markdown(self, cache, scope_paths):
        names = [v for v in self.git('ls-files', '-z', '--cached').split('\0') if v.endswith('.md')]
        scoped = names if scope_paths is None else [v for v in names
                 if any(v == p or v.startswith(p + '/') for p in scope_paths)]
        entries = [self.identity(self.checkout / v) for v in sorted(set(scoped))
                   if (self.checkout / v).exists()]
        if len(entries) > cache['max_input_files'] or sum(v['bytes'] for v in entries) > cache['max_input_bytes']:
            raise CheckFailure('invalid_options', 'profile.cache',
                               'Выбранные Markdown входы превышают настроенный предел.',
                               'Уточните scope_paths или явные ограничения выбранного профиля.')
        material = {'input_selection': cache['input_selection'],
                    'max_input_files': cache['max_input_files'], 'max_input_bytes': cache['max_input_bytes'],
                    'scope_paths': scope_paths, 'entries': entries}
        return {**material, 'sha256': hash_value(material)}

    def package(self, package):
        patterns = [*package['source_paths'], *package['fixture_paths'],
                    *[p for step in package['steps'] for p in step['test_paths']]]
        entries = {}
        for pattern in patterns:
            for match in self.matches(pattern):
                candidates = sorted(match.rglob('*')) if match.is_dir() else [match]
                for path in candidates:
                    if path.is_file():
                        content = self.identity(path)
                        entries[content['path']] = content['sha256']
        material = [{'path': path, 'sha256': entries[path]} for path in sorted(entries)]
        return {'entries': material, 'sha256': hash_value(material)}

    def key(self, profile, scope_paths, package=None):
        if package is not None:
            inputs = self.package(package)
            namespace = {'key_schema': 'test-package-source-test-fixtures-v2',
                         'command_id': profile['command_id'], 'package_id': package['package_id'],
                         'content_sha256': inputs['sha256']}
        else:
            cache = profile['cache']
            if cache['input_selection'] == 'git-tracked-markdown':
                inputs = self.tracked_markdown(cache, scope_paths)
                schema = 'profile-declared-inputs-v1'
            else:
                inputs = self.filesystem(cache['input_paths'] if scope_paths is None else scope_paths)
                schema = 'profile-declared-inputs-v1' if scope_paths is None else 'task-scope-code-test-fixtures-v1'
            namespace = {'key_schema': schema, 'command_id': profile['command_id'],
                         'workload_class': profile['workload_class'], 'inputs_sha256': inputs['sha256']}
        return hash_value(namespace), {'namespace': namespace, 'inputs': inputs}
