"""Intent selection, component paths and explicit placement authority."""
from dataclasses import dataclass

from ..foundation.paths import matches_component_path
from .contracts import (
    PlacementInputError, basename, configured, diagnostic, relative, strings, text,
)


@dataclass(frozen=True)
class PlacementPolicy:
    roots: dict
    rules: tuple
    owner_provider: str | None
    state: dict

    @classmethod
    def from_configuration(cls, document):
        value = configured(document, {'schema', 'version', 'roots', 'rules', 'owner_provider', 'state'})
        fail = PlacementInputError('invalid_policy', 'policy', 'correct:policy')
        if (value['schema'] != 'poise-placement-policy-1' or not text(value['version'])
                or value['owner_provider'] not in (None, 'poise')
                or not isinstance(value['roots'], list) or not value['roots']
                or not isinstance(value['rules'], list) or not value['rules']):
            raise fail
        roots = {}
        for raw in value['roots']:
            root = configured(raw, {'id', 'owner', 'path', 'versioning', 'authority'})
            if (not text(root['id']) or root['id'] in roots
                    or root['owner'] not in ('product', 'harness', 'task', 'sprint')
                    or root['versioning'] not in ('versioned', 'ignored') or not text(root['authority'])):
                raise fail
            if root['owner'] in ('task', 'sprint'):
                if root['path'] is not None:
                    raise fail
            elif not text(root['path']) or not root['path'].startswith('/'):
                raise fail
            roots[root['id']] = root
        rules, seen = [], set()
        for raw in value['rules']:
            rule = configured(raw, {'id', 'priority', 'category', 'when', 'root', 'directory', 'paths'})
            when = configured(rule['when'], {'purposes', 'recipients', 'lifetimes'})
            if (not text(rule['id']) or rule['id'] in seen or type(rule['priority']) is not int
                    or not text(rule['category']) or not text(rule['root']) or rule['root'] not in roots
                    or not relative(rule['directory'], empty=True)
                    or not strings(rule['paths'], empty=False)
                    or any(not relative(v, pattern=True) for v in rule['paths'])
                    or any(not strings(v) for v in when.values())):
                raise fail
            rule['when'] = when
            seen.add(rule['id'])
            rules.append(rule)
        state = configured(value['state'], {'directory', 'filename', 'lock'})
        if (not text(state['directory']) or not state['directory'].startswith('/')
                or not basename(state['filename']) or not basename(state['lock'])
                or state['filename'] == state['lock']):
            raise fail
        return cls(roots, tuple(rules), value['owner_provider'], state)

    def selected(self, item):
        matches = [rule for rule in self.rules if all(
            not rule['when'][axis] or item[field] in rule['when'][axis]
            for axis, field in [('purposes', 'purpose'), ('recipients', 'recipient'), ('lifetimes', 'lifetime')]
        )]
        if not matches:
            return None, 'unmapped_intent'
        priority = max(v['priority'] for v in matches)
        winners = [v for v in matches if v['priority'] == priority]
        return (winners[0], None) if len(winners) == 1 else (None, 'ambiguous_rules')

    def admit_harness_path(self, address, intent):
        """An explicitly granted harness root needs no project reservation."""
        from pathlib import PurePosixPath
        rule, error = self.selected(intent)
        if error:
            raise PlacementInputError(error, 'placement', 'configure:policy')
        root = self.roots[rule['root']]
        if root['owner'] != 'harness' or root['versioning'] != 'ignored':
            raise PlacementInputError('owner_unavailable', 'placement', 'provide:owner')
        path, base = PurePosixPath(address), PurePosixPath(root['path'])
        if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(base):
            raise PlacementInputError('path_not_allowed', 'placement', 'choose:placement')
        relative_path = path.relative_to(base).as_posix()
        if not any(matches_component_path(relative_path, pattern) for pattern in rule['paths']):
            raise PlacementInputError('path_not_allowed', 'placement', 'choose:placement')

    def candidates(self, items, project, owners):
        rows, diagnostics = [], []
        for item in items:
            row = {'key': item['key'], 'status': 'needs_inputs', 'category': None,
                   'rule': None, 'root': None, 'versioning': None, 'owner': None,
                   'path': None, 'absolute_path': None, 'reserved': False, 'authorization_refs': []}
            rule, error = self.selected(item)
            if error:
                diagnostics.append(diagnostic(error, item['key'], 'items:' + item['key'], 'configure:policy'))
                rows.append(row)
                continue
            root = self.roots[rule['root']]
            row.update(category=rule['category'], rule=rule['id'], root=root['id'], versioning=root['versioning'])
            if root['owner'] in ('task', 'sprint'):
                owner = owners.get(root['owner'])
                if self.owner_provider is None or owner is None:
                    disabled = self.owner_provider is None
                    diagnostics.append(diagnostic(
                        'owner_disabled' if disabled else 'owner_unavailable', item['key'],
                        'items:' + item['key'] + ':owner',
                        'enable:owner_provider' if disabled else 'provide:' + root['owner'] + '_owner'))
                    rows.append(row)
                    continue
                base, identity = owner['root'], {'kind': root['owner'], 'id': owner['id']}
            else:
                base, identity = root['path'], {'kind': root['owner'], 'id': project}
            path = item['path']
            if path is None:
                path = (rule['directory'] + '/' if rule['directory'] else '') + item['name']
            row.update(status='allowed', owner=identity, path=path,
                       absolute_path=base.rstrip('/') + '/' + path,
                       authorization_refs=[root['authority']])
            if not any(matches_component_path(path, pattern) for pattern in rule['paths']):
                row['status'] = 'denied'
                diagnostics.append(diagnostic('path_not_allowed', item['key'], 'items:' + item['key'] + ':path', 'choose:placement'))
            rows.append(row)
        return rows, diagnostics

    def needed_owners(self, items):
        kinds = set()
        if self.owner_provider is not None:
            for item in items:
                rule, _ = self.selected(item)
                if rule is not None and self.roots[rule['root']]['owner'] in ('task', 'sprint'):
                    kinds.add(self.roots[rule['root']]['owner'])
        return kinds
