"""Bounded scoped reads and derived snapshots; no executable/config discovery."""
import hashlib
import json
from pathlib import Path

from ..common import PoiseError, descendant, digest, encoded
from ..application.development_routing import DevelopmentRouting
from ..modules.skills.catalog import SkillCatalog
from ..modules.skills.selection import SkillSelection
from ..modules.skills.routing import RoutingPolicy
from .goal_config import atomic_write
from .test_packages import AiPoiseTestPackages
from .architecture_checks import AiPoiseArchitectureChecks


_LIMIT = 4 * 1024 * 1024


def _read(path):
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(_LIMIT + 1)
        if len(data) > _LIMIT:
            raise PoiseError(f'Routing input exceeds 4 MiB: {path}')
        return data
    except OSError as exc:
        raise PoiseError(f'Cannot read routing input {path}: {exc}') from exc


def _document(data):
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise PoiseError(f'Invalid routing JSON: {exc}') from exc


class RoutingFiles:
    def __init__(self, configuration_digests, packages, boundaries=None):
        self.configuration_digests = dict(configuration_digests)
        self.packages = packages
        self.boundaries = boundaries

    def checkout(self, path):
        return str(AiPoiseTestPackages._assert_ai_poise_checkout(Path(path)))

    def test_impact(self, checkout, paths):
        return self.packages.impact(Path(checkout), paths)

    def check_boundaries(self, checkout, paths, deleted_paths):
        if self.boundaries is None:
            raise PoiseError('Configured authoring boundary checker is unavailable')
        return self.boundaries.check(Path(checkout), paths, deleted_paths=deleted_paths)

    def rules(self, checkout, paths):
        base = Path(checkout)
        directories = {''}
        for name in paths:
            # Resolve just the literal prefix of a glob, never enumerate a subtree.
            parts = name.split('/')
            literal = []
            for part in parts:
                if any(c in part for c in '*?['):
                    break
                literal.append(part)
            all_literal = len(literal) == len(parts)
            if all_literal and not descendant(base, name).is_dir():
                literal = literal[:-1]
            for count in range(1, len(literal) + 1):
                directories.add('/'.join(literal[:count]))
        result = []
        for directory in sorted(directories, key=lambda p: (p.count('/') + bool(p), p)):
            name = f'{directory}/AGENTS.md' if directory else 'AGENTS.md'
            path = descendant(base, name)
            if path.exists():
                result.append({'id': name, 'path': name,
                               'sha256': hashlib.sha256(_read(path)).hexdigest()})
        missing = [] if any(r['path'] == 'AGENTS.md' for r in result) else ['missing_rule:AGENTS.md']
        return result, missing

    def skills(self, checkout, selected):
        results, missing = [], []
        for skill in selected:
            path = descendant(Path(checkout), skill['path'])
            if not path.is_file():
                missing.append('missing_skill:' + skill['id'])
            else:
                results.append({'id': skill['id'], 'path': skill['path'],
                                'sha256': hashlib.sha256(_read(path)).hexdigest()})
        return results, missing

    @staticmethod
    def seal(result):
        return {**result, 'route_digest': digest(result)}

    def write_snapshot(self, path, result):
        path = Path(path)
        if path.is_symlink():
            raise PoiseError('Routing snapshot must not be a symlink')
        atomic_write(path, (encoded(result) + '\n').encode(), 0o600)

    def read_snapshot(self, path):
        raw = _document(_read(path))
        if (not isinstance(raw, dict) or raw.get('schema') != 'ai-poise-development-route-1'
                or 'route_digest' not in raw):
            raise PoiseError('Invalid routing snapshot')
        if raw['route_digest'] != digest({k: v for k, v in raw.items() if k != 'route_digest'}):
            raise PoiseError('Routing snapshot digest mismatch')
        return raw


def load_development_routing(*, catalog, selection, policy, packages):
    paths = dict(catalog=catalog, selection=selection, policy=policy, packages=packages)
    contents = {name: _read(path) for name, path in paths.items()}
    metadata = SkillCatalog.parse(_document(contents['catalog']))
    conditions = SkillSelection.parse(_document(contents['selection']), metadata)
    # Reuse the package owner and reject drift during composition.
    package_catalog = AiPoiseTestPackages.load(Path(packages))
    if _read(packages) != contents['packages']:
        raise PoiseError('Package configuration changed during routing load')
    rules = RoutingPolicy.parse(_document(contents['policy']), conditions, package_catalog.package_ids)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    boundaries = None
    if 'authoring_boundary_gate' in rules.document:
        name = Path(rules.document['authoring_boundary_gate']['policy'])
        # Resolve operational configuration from the supplied routing file, never task code.
        boundary_path = name if name.is_absolute() else Path(policy).parent / name
        boundaries = AiPoiseArchitectureChecks.load(boundary_path)
        hashes['architecture'] = boundaries.policy_sha256
    return DevelopmentRouting(metadata, conditions, rules, RoutingFiles(hashes, package_catalog, boundaries))
