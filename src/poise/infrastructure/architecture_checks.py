"""Read-only, explicit-path adapter for AI-poise's own Python architecture rules."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath

from ..common import PoiseError
from ..modules.verification.architecture import ArchitecturePolicy
from .test_packages import AiPoiseTestPackages

_LIMIT = 4 * 1024 * 1024


def _bytes(path):
    with path.open('rb') as stream:
        data = stream.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise PoiseError(f'Architecture input exceeds {_LIMIT} bytes: {path}')
    return data


class AiPoiseArchitectureChecks:
    def __init__(self, policy, policy_sha256):
        self.policy, self.policy_sha256 = policy, policy_sha256

    @classmethod
    def load(cls, policy_path: Path):
        try:
            data = _bytes(Path(policy_path))
            return cls(ArchitecturePolicy.parse(json.loads(data)), hashlib.sha256(data).hexdigest())
        except (OSError, ValueError, UnicodeError) as exc:
            raise PoiseError(f'Cannot load AI-poise architecture policy: {exc}') from exc

    @staticmethod
    def _path(checkout, value):
        if not isinstance(value, str) or not value or '\\' in value or '\0' in value:
            raise PoiseError('Expected a concrete AI-poise relative source path')
        path = PurePosixPath(value)
        if (path.is_absolute() or '..' in path.parts or path.as_posix() != value or value == '.'
                or any(c in value for c in '*?[]') or not (checkout / value).resolve().is_relative_to(checkout)):
            raise PoiseError('Architecture source path must remain in the supplied AI-poise copy')
        return checkout / value

    def covered_paths(self, checkout: Path) -> list[str]:
        directory = AiPoiseTestPackages._assert_ai_poise_checkout(Path(checkout))
        # Use the same pure path predicate as explicit checks. Path.glob has
        # different '*' semantics from the existing boundary contract.
        return sorted(p.relative_to(directory).as_posix()
                      for p in (directory / 'src/poise').rglob('*.py')
                      if p.is_file() and self.policy.applicable(p.relative_to(directory).as_posix()))

    def check(self, checkout: Path, paths: list[str], *, deleted_paths=()) -> dict:
        directory = AiPoiseTestPackages._assert_ai_poise_checkout(Path(checkout))
        if (not isinstance(paths, list) or not isinstance(deleted_paths, (list, tuple))
                or len(paths) > 4096 or len(deleted_paths) > 4096):
            raise PoiseError('Expected bounded explicit architecture path lists')
        for value in [*paths, *deleted_paths]:
            self._path(directory, value)
        removed = set(deleted_paths)
        if not removed.issubset(paths) or any((directory / p).exists() for p in removed):
            raise PoiseError('Deleted paths must be absent members of the explicit change list')
        diagnostics, checked, unchecked, skipped = [], [], [], []
        for name in sorted(set(paths)):
            if not self.policy.applicable(name):
                unchecked.append(name)
                continue
            if name in removed:
                skipped.append(name)
                continue
            try:
                data = _bytes(self._path(directory, name))
                source = data.decode('utf-8')
                checked.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest()})
                diagnostics.extend(self.policy.inspect(name, source))
            except (OSError, UnicodeError, PoiseError) as exc:
                diagnostics.append({'file': name, 'line': None, 'rule': 'source-read',
                                    'kind': 'unavailable', 'symbol': str(exc)})
        # Detect edits observed during this pass; this is not a filesystem transaction.
        for item in checked:
            try:
                same = hashlib.sha256(_bytes(self._path(directory, item['path']))).hexdigest() == item['sha256']
            except (OSError, PoiseError):
                same = False
            if not same:
                diagnostics.append({'file': item['path'], 'line': None, 'rule': 'source-stability',
                                    'kind': 'source_changed', 'symbol': 'source changed during architecture check'})
        uncertain = any(d['kind'] in ('syntax', 'unavailable', 'source_changed') for d in diagnostics)
        status = 'inconclusive' if uncertain else 'failed' if diagnostics else 'passed'
        return {'schema': 'ai-poise-architecture-result-1', 'status': status, 'passed': status == 'passed',
                'policy_sha256': self.policy_sha256, 'checked_files': checked,
                'deleted_paths': skipped, 'unchecked_paths': unchecked,
                'diagnostics': diagnostics, 'read_only': True}
