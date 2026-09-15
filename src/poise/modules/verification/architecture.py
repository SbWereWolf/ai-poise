"""Pure static checks for the documented Python boundaries of AI-poise only."""
from __future__ import annotations
import ast
from copy import deepcopy
import re

from ..foundation.errors import DomainError
from ..foundation.paths import matches_allowed_path
from ..foundation.validation import validate_exact_keys


def _strings(values, label):
    if (not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values)
            or len(values) != len(set(values))):
        raise DomainError(f'{label}: explicit unique strings required')


class ArchitecturePolicy:
    def __init__(self, rules):
        self.rules = deepcopy(rules)

    @classmethod
    def parse(cls, raw):
        validate_exact_keys(raw, {'schema', 'rules'}, 'AI-poise architecture', DomainError)
        if raw['schema'] != 'ai-poise-architecture-boundaries-1' or not isinstance(raw['rules'], list):
            raise DomainError('Unsupported AI-poise architecture policy')
        if not raw['rules'] or len(raw['rules']) > 1024:
            raise DomainError('Explicit bounded architecture rules required')
        ids = set()
        keys = {'id', 'paths', 'forbidden_imports', 'forbidden_calls', 'protected_subscripts',
                'protected_attributes', 'forbidden_sql_tables'}
        for rule in raw['rules']:
            validate_exact_keys(rule, keys, 'architecture rule', DomainError)
            _strings([rule['id']], 'rule identity')
            if rule['id'] in ids:
                raise DomainError('Duplicate architecture rule identity')
            ids.add(rule['id'])
            for key in keys - {'id', 'protected_subscripts'}:
                _strings(rule[key], key)
            if not rule['paths'] or any(not p.startswith('src/poise/') or '..' in p.split('/')
                    or '\\' in p or '\0' in p for p in rule['paths']):
                raise DomainError('Architecture policy paths must belong to src/poise')
            protected = rule['protected_subscripts']
            validate_exact_keys(protected, {'variables', 'fields'}, 'protected subscripts', DomainError)
            _strings(protected['variables'], 'protected variables')
            _strings(protected['fields'], 'protected fields')
        return cls(raw['rules'])

    def applicable(self, path: str) -> list[dict]:
        if not path.endswith('.py'):
            return []
        return [rule for rule in self.rules if any(matches_allowed_path(path, pattern) for pattern in rule['paths'])]

    def inspect(self, path: str, source: str) -> list[dict]:
        rules = self.applicable(path)
        if not rules:
            return []
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            return [{'file': path, 'line': exc.lineno, 'rule': 'python-syntax',
                     'kind': 'syntax', 'symbol': exc.msg}]
        imports, aliases = [], {}
        package = path.removeprefix('src/').removesuffix('.py').split('/')[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((node, alias.name))
                    aliases[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
            elif isinstance(node, ast.ImportFrom):
                prefix = '.'.join(package[:len(package) - node.level + 1]) if node.level else ''
                base = '.'.join(p for p in (prefix, node.module or '') if p)
                for alias in node.names:
                    name = '.'.join(p for p in (base, alias.name) if p)
                    imports.append((node, name))
                    aliases[alias.asname or alias.name] = name

        def qualname(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                prefix = qualname(node.value)
                return prefix + '.' + node.attr if prefix else ''
            return ''

        diagnostics = []
        for rule in rules:
            def add(node, kind, symbol):
                diagnostics.append({'file': path, 'line': node.lineno, 'rule': rule['id'],
                                    'kind': kind, 'symbol': symbol})
            for node, name in imports:
                if any(name == forbidden or name.startswith(forbidden + '.') for forbidden in rule['forbidden_imports']):
                    add(node, 'import', name)
            protected = rule['protected_subscripts']
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = qualname(node.func)
                    if name in rule['forbidden_calls']:
                        add(node, 'call', name)
                    if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                            and node.func.value.id in protected['variables']):
                        keys = []
                        if node.func.attr == 'update':
                            keys = [kw.arg for kw in node.keywords]
                            for arg in node.args:
                                if isinstance(arg, ast.Dict):
                                    keys.extend(k.value for k in arg.keys if isinstance(k, ast.Constant))
                        elif node.func.attr in ('pop', 'setdefault') and node.args and isinstance(node.args[0], ast.Constant):
                            keys = [node.args[0].value]
                        for key in keys:
                            if key in protected['fields']:
                                add(node, 'state_write', node.func.value.id + '.' + str(key))
                targets = []
                if isinstance(node, (ast.Assign, ast.Delete)):
                    targets = node.targets
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                    targets = [node.target]
                for target in targets:
                    for part in ast.walk(target):
                        if isinstance(part, ast.Attribute) and part.attr in rule['protected_attributes']:
                            add(node, 'state_write', part.attr)
                        if (isinstance(part, ast.Subscript) and isinstance(part.value, ast.Name)
                                and part.value.id in protected['variables'] and isinstance(part.slice, ast.Constant)
                                and part.slice.value in protected['fields']):
                            add(node, 'state_write', part.value.id + '.' + str(part.slice.value))
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for table in rule['forbidden_sql_tables']:
                        pattern = r'\b(?:UPDATE|INSERT(?:\s+OR\s+\w+)?\s+INTO|DELETE\s+FROM)\s+["`\[]?' + re.escape(table) + r'\b'
                        if re.search(pattern, node.value, re.IGNORECASE):
                            add(node, 'sql_write', table)
        unique = {(d['file'], d['line'], d['rule'], d['kind'], d['symbol']): d for d in diagnostics}
        return [unique[key] for key in sorted(unique)]
