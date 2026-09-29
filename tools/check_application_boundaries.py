#!/usr/bin/env python3
"""Bounded, read-only import and private-storage analysis of seven CLI apps.

The interpreter below evaluates syntax, never application objects or source.
Unknown formal parameters remain explicit unresolved accesses, not runtime proof.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

OWNERS = ('bootstrap', 'execution', 'orchestrator', 'poise', 'requirements',
          'sequencing', 'stride-uat')
EXCLUDED = {'tests', 'fixtures', 'docs', '__pycache__'}
OWN_MODULES = {'poise.application.requirements_registry',
               'poise.infrastructure.requirements_registry',
               'poise.modules.requirements_registry.domain',
               'poise.modules.tasks.allocation'}
SHARED_MODULES = {'poise.infrastructure.locking',
                  'poise.infrastructure.sqlite.transaction',
                  'poise.modules.foundation.errors',
                  'poise.modules.foundation.strict_json'}
ENTRY_SYMBOLS = {
    'poise.application.requirements_registry.RequirementsCommands',
    'poise.infrastructure.requirements_registry.RequirementsStore',
    'poise.modules.foundation.errors.PoiseError',
    'poise.modules.foundation.strict_json.strict_json',
    'poise.modules.tasks.allocation.TaskIdPolicy',
}
LIMIT_MAX = dict(max_files=4096, max_file_bytes=8388608,
                 max_total_bytes=67108864, max_ast_nodes=1000000,
                 max_diagnostics=4096, max_report_bytes=4194304)
DEFERRED = dict(module='poise', function='__getattr__', symbol='Poise',
                target='poise.runtime.Poise')


class Invalid(ValueError):
    """Unusable explicit inputs."""


class Bound(Exception):
    """A configured analysis bound was exhausted."""



def read_regular_bounded(path, limit):
    """Observe at most limit + one sentinel byte, never stream a special file."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not before.st_mode & 0o444:
        raise OSError('regular readable input required')
    flags = os.O_RDONLY
    for name in ('O_NOFOLLOW', 'O_NONBLOCK', 'O_CLOEXEC'):
        flags |= getattr(os, name, 0)
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode) or not current.st_mode & 0o444:
            raise OSError('regular readable input required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(limit + 1)
    finally:
        os.close(fd)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Invalid('duplicate JSON field')
            result[key] = value
        return result

    def nonfinite(_):
        raise Invalid('non-finite JSON number')

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise Invalid('object fields differ from the policy contract')


def path_row(pointer, base='config_directory', kind='private_directory', relative=False):
    return dict(pointer=pointer, base=base, kind=kind, relative_only=relative)


def expected_fields(owner):
    if owner in {'requirements', 'sequencing', 'execution', 'orchestrator'}:
        return [path_row('/paths/state'),
                path_row('/paths/database', '/paths/state', 'private_file', True),
                path_row('/paths/lock', '/paths/state', 'private_file', True)]
    if owner == 'bootstrap':
        return [path_row('/projects_root')]
    if owner == 'stride-uat':
        return [path_row('/state_root'), path_row('/workspace_root'),
                path_row('/input_roots/*', kind='input_root')]
    return [path_row('/state_root'), path_row('/results_root'),
            path_row('/session_store', kind='private_file'),
            path_row('/lock_path', kind='private_file')]


def string_set(value, expected):
    if (not isinstance(value, list) or any(not isinstance(x, str) for x in value)
            or len(set(value)) != len(value) or set(value) != expected):
        raise Invalid('explicit module list differs from declared closure')


def validate_policy(p):
    exact(p, {'schema', 'owners', 'requirements_legacy', 'deferred_export', 'limits'})
    if p['schema'] != 'poise-application-boundaries-policy/v1':
        raise Invalid('unsupported policy schema')
    exact(p['owners'], OWNERS)
    for owner, row in p['owners'].items():
        exact(row, {'root', 'entry', 'config', 'loader_kind', 'path_fields'})
        root = f'apps/{owner}'
        config_name = 'uat' if owner == 'stride-uat' else owner
        loader = ('checkout-src-qualified' if owner in {'requirements', 'sequencing', 'execution'}
                  else 'own-spec' if owner == 'bootstrap' else 'own-shell')
        if (row['root'] != root or row['entry'] != f'{root}/bin/{owner}'
                or row['config'] != f'{root}/config/{config_name}.json'
                or row['loader_kind'] != loader
                or row['path_fields'] != expected_fields(owner)):
            raise Invalid('owner layout or path ownership contract differs')
        for field in row['path_fields']:
            if type(field['relative_only']) is not bool:
                raise Invalid('relative_only must be a boolean')
    exact(p['requirements_legacy'], {'own_modules', 'shared_modules', 'entry_symbols'})
    for key, allowed in [('own_modules', OWN_MODULES), ('shared_modules', SHARED_MODULES),
                         ('entry_symbols', ENTRY_SYMBOLS)]:
        string_set(p['requirements_legacy'][key], allowed)
    if p['deferred_export'] != DEFERRED:
        raise Invalid('deferred export contract differs')
    exact(p['limits'], LIMIT_MAX)
    for key, maximum in LIMIT_MAX.items():
        value = p['limits'][key]
        minimum = 1024 if key == 'max_report_bytes' else 1
        if type(value) is not int or not minimum <= value <= maximum:
            raise Invalid('invalid analysis bound: ' + key)


def report():
    return dict(schema='poise-application-boundaries-report/v1', status='inconclusive',
                passed=False, read_only=True, policy_sha256=None,
                scope=dict(imports='production-local-closure/v1',
                           storage='literal-and-declared-config-paths/v1',
                           runtime_effects_proven=False, root_aliases_checked=False),
                coverage=dict(complete=True, owners=[], entrypoints=[],
                              source_count=0, config_count=0),
                files=[], private_paths=[], accesses=[], unresolved_accesses=[],
                diagnostics=[], stable_inputs=True)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            + '\n').encode('utf-8')


def norm(path):
    return os.path.normpath(str(path))


def below(path, parent):
    return path == parent or path.startswith(parent.rstrip('/') + '/')


@dataclass(frozen=True)
class P:
    """Lexically normalized, known path; no filesystem dereference."""
    value: str


@dataclass(frozen=True)
class PathText:
    """A string retaining a known path, not a pathlib object."""
    value: str


@dataclass(frozen=True)
class Symbol:
    name: str


@dataclass(frozen=True)
class Unknown:
    """Unsupported value with retained known path provenance."""
    paths: tuple[str, ...] = ()


UNKNOWN = Unknown()


def known_paths(value):
    if isinstance(value, (P, PathText)):
        return {value.value}
    if isinstance(value, Unknown):
        return set(value.paths)
    if isinstance(value, (tuple, list)):
        return set().union(*(known_paths(x) for x in value)) if value else set()
    if isinstance(value, dict):
        return known_paths(list(value.values()))
    return set()


def unknown(*values):
    return Unknown(tuple(sorted(set().union(*(known_paths(v) for v in values)))))


def clean_tree(text):
    tree = ast.parse(text)
    tree.body = [n for n in tree.body if not (isinstance(n, ast.Expr)
                 and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
    return ast.dump(tree, include_attributes=False)


def entry_template(owner):
    if owner in {'requirements', 'sequencing', 'execution'}:
        src = 'SRC = ROOT / "src"\n' if owner == 'requirements' else ''
        values = '(ROOT, SRC)' if src else '(ROOT, ROOT / "src")'
        return f'''from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
{src}for entry in {values}:
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
from apps.{owner}.shell import main
raise SystemExit(main())
'''
    if owner == 'bootstrap':
        return '''from pathlib import Path
import importlib.util
import sys
HERE = Path(__file__).resolve()
MODULE = HERE.parents[1] / "shell.py"
spec = importlib.util.spec_from_file_location("bootstrap_shell", MODULE)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)
raise SystemExit(mod.main())
'''
    if owner == 'orchestrator':
        return '''from pathlib import Path
import sys
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from shell import main
raise SystemExit(main())
'''
    return '''from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shell import main
raise SystemExit(main())
'''


LAZY_TEMPLATE = '''def __getattr__(name):
    if name == "Poise":
        from .runtime import Poise
        globals()["Poise"] = Poise
        return Poise
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
'''


class Analyzer:
    def __init__(self, root, policy_path, output):
        self.root = root
        self.policy_path = policy_path
        self.out = output
        self.policy = None
        self.limits = dict(LIMIT_MAX)
        self.raw = {}
        self.configs = {}
        self.private = []
        self.sources = {}
        self.total = 0
        self.nodes = 0

    def rel(self, path):
        return os.path.relpath(norm(path), str(self.root)).replace(os.sep, '/')

    def diagnostic(self, file, line, rule, kind, owner='', target='', message=''):
        if kind != 'violation':
            self.out['coverage']['complete'] = False
        if len(self.out['diagnostics']) >= self.limits['max_diagnostics']:
            self.out['coverage']['complete'] = False
            return
        item = dict(file=file, line=line, rule=rule, kind=kind, owner=owner,
                    target=target, message=message or rule.replace('_', ' '))
        if item not in self.out['diagnostics']:
            self.out['diagnostics'].append(item)

    def safe(self, path):
        path = Path(norm(path))
        if not path.is_relative_to(self.root):
            return False
        for part in [path, *path.parents]:
            if part.is_symlink():
                return False
            if part == self.root:
                break
        return True

    def read(self, path, kind, owner):
        path = Path(norm(path))
        if path in self.raw:
            return self.raw[path]
        name = self.rel(path)
        if not self.safe(path):
            self.diagnostic(name, 0, 'unsafe_path', 'inconclusive', owner)
            return None
        try:
            raw = read_regular_bounded(path, self.limits['max_file_bytes'])
            if len(raw) > self.limits['max_file_bytes']:
                raise Bound('max_file_bytes')
            if len(self.raw) >= self.limits['max_files']:
                raise Bound('max_files')
            if self.total + len(raw) > self.limits['max_total_bytes']:
                raise Bound('max_total_bytes')
        except OSError:
            self.diagnostic(name, 0, 'missing_source', 'inconclusive', owner)
            return None
        self.raw[path] = raw
        self.total += len(raw)
        self.out['files'].append(dict(path=name, kind=kind, sha256=hashlib.sha256(raw).hexdigest()))
        return raw

    def load_policy(self):
        try:
            if not self.policy_path.is_file() or not self.safe(self.policy_path):
                raise Invalid('policy missing or unsafe')
            raw = read_regular_bounded(self.policy_path, LIMIT_MAX['max_file_bytes'])
            if len(raw) > LIMIT_MAX['max_file_bytes']:
                raise Invalid('policy exceeds bootstrap bound')
            self.policy_raw = raw
            self.out['policy_sha256'] = hashlib.sha256(raw).hexdigest()
            self.policy = strict_json(raw)
            validate_policy(self.policy)
            self.limits = self.policy['limits']
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            self.diagnostic('', 0, 'invalid_policy', 'invalid')
            return False
        return True

    def load_configs(self):
        for owner in OWNERS:
            spec = self.policy['owners'][owner]
            path = self.root / spec['config']
            raw = self.read(path, 'config', owner)
            if raw is None:
                continue
            try:
                data = strict_json(raw)
                if not isinstance(data, dict):
                    raise Invalid('config object required')
                self.configs[norm(path)] = data
                self.private.append((norm(path), owner, 'private_file'))
                self.out['private_paths'].append(dict(owner=owner, pointer='/',
                                                     kind='private_file', path=spec['config']))
                resolved = {}
                for f in spec['path_fields']:
                    pointer = f['pointer']
                    value = data
                    for key in pointer.strip('/').split('/'):
                        if key != '*':
                            value = value[key]
                    values = value if pointer.endswith('/*') else [value]
                    if not isinstance(values, list) or not values:
                        raise Invalid('path list required')
                    for index, text in enumerate(values):
                        if not isinstance(text, str) or not text.strip() or '\x00' in text:
                            raise Invalid('path text required')
                        if f['relative_only'] and (Path(text).is_absolute() or '..' in Path(text).parts):
                            raise Invalid('relative child path required')
                        base = norm(path.parent) if f['base'] == 'config_directory' else resolved[f['base']]
                        target = norm(Path(base) / text)
                        resolved[pointer] = target
                        if not self.safe(target):
                            self.diagnostic(spec['config'], 0, 'unsafe_path', 'inconclusive', owner,
                                            self.rel(target))
                        if f['relative_only'] and (target == base or not below(target, base)):
                            raise Invalid('private file must be a child')
                        p = pointer.replace('*', str(index))
                        self.out['private_paths'].append(dict(owner=owner, pointer=p, kind=f['kind'], path=self.rel(target)))
                        if f['kind'].startswith('private_'):
                            self.private.append((target, owner, f['kind']))
                if '/paths/database' in resolved and resolved['/paths/database'] == resolved['/paths/lock']:
                    raise Invalid('database and lock collide')
                if owner == 'poise':
                    state, results = resolved['/state_root'], resolved['/results_root']
                    if below(state, results) or below(results, state):
                        raise Invalid('state and results overlap')
                    if (resolved['/session_store'] == resolved['/lock_path'] or
                        any(not below(resolved[k], state) or resolved[k] == state
                            for k in ['/session_store', '/lock_path'])):
                        raise Invalid('Poise private files require distinct state children')
            except (KeyError, ValueError, TypeError, UnicodeError, RecursionError):
                self.diagnostic(spec['config'], 0, 'invalid_config', 'invalid', owner)
        for i, (p, owner, kind) in enumerate(self.private):
            for q, other, qkind in self.private[i + 1:]:
                if owner != other and (p == q or kind == 'private_directory' and below(q, p)
                                      or qkind == 'private_directory' and below(p, q)):
                    self.diagnostic(self.policy['owners'][owner]['config'], 0, 'owner_collision',
                                    'violation', owner, self.rel(q))

    def add_source(self, path, owner):
        path = Path(norm(path))
        if path in self.sources:
            return
        raw = self.read(path, 'source', owner)
        if raw is None:
            return
        try:
            tree = ast.parse(raw, filename=self.rel(path))
            self.nodes += sum(1 for _ in ast.walk(tree))
            if self.nodes > self.limits['max_ast_nodes']:
                raise Bound('max_ast_nodes')
            self.sources[path] = (owner, tree, raw)
        except (SyntaxError, ValueError, UnicodeError, RecursionError):
            self.diagnostic(self.rel(path), 0, 'invalid_source', 'inconclusive', owner)

    def discover(self):
        for owner in OWNERS:
            spec = self.policy['owners'][owner]
            self.out['coverage']['owners'].append(owner)
            self.out['coverage']['entrypoints'].append(spec['entry'])
            self.add_source(self.root / spec['entry'], owner)
            self.add_source(self.root / spec['root'] / 'shell.py', owner)
            base = self.root / spec['root']
            if not self.safe(base):
                self.diagnostic(spec['root'], 0, 'unsafe_path', 'inconclusive', owner)
                continue
            for directory, dirs, names in os.walk(base, followlinks=False):
                for d in list(dirs):
                    if d in EXCLUDED:
                        dirs.remove(d)
                    elif (Path(directory) / d).is_symlink():
                        self.diagnostic(self.rel(Path(directory) / d), 0, 'unsafe_path', 'inconclusive', owner)
                        dirs.remove(d)
                for name in sorted(names):
                    if name.endswith('.py'):
                        self.add_source(Path(directory) / name, owner)
        done = set()
        while True:
            pending = sorted(set(self.sources) - done)
            if not pending:
                break
            for path in pending:
                done.add(path)
                owner, tree, raw = self.sources[path]
                Source(self, path, owner, tree, raw).analyze()

    def target_owner(self, target):
        candidates = [(len(p), owner) for p, owner, kind in self.private
                      if target == p or kind == 'private_directory' and below(target, p)]
        return max(candidates)[1] if candidates else None

    def finish(self):
        for path, raw in self.raw.items():
            try:
                if not self.safe(path) or read_regular_bounded(path, len(raw)) != raw:
                    raise OSError()
            except OSError:
                self.out['stable_inputs'] = False
                self.diagnostic(self.rel(path), 0, 'changed_input', 'inconclusive')
        try:
            if hasattr(self, 'policy_raw') and (not self.safe(self.policy_path) or
                    read_regular_bounded(self.policy_path, len(self.policy_raw)) != self.policy_raw):
                raise OSError()
        except OSError:
            self.out['stable_inputs'] = False
            self.diagnostic('', 0, 'changed_policy', 'inconclusive')
        self.out['coverage']['source_count'] = sum(f['kind'] == 'source' for f in self.out['files'])
        self.out['coverage']['config_count'] = sum(f['kind'] == 'config' for f in self.out['files'])
        for key in ('files', 'private_paths', 'accesses', 'unresolved_accesses', 'diagnostics'):
            unique = {json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in self.out[key]}
            self.out[key] = [unique[k] for k in sorted(unique)]
        return emit(self.out, self.limits['max_report_bytes'])


class Source:
    def __init__(self, analyzer, path, owner, tree, raw):
        self.a, self.path, self.owner, self.tree = analyzer, path, owner, tree
        self.name = analyzer.rel(path)
        self.loader = (self.name == analyzer.policy['owners'][owner]['entry']
                       and clean_tree(raw) == clean_tree(entry_template(owner)))
        self.lazy_import = set()
        if self.name == 'src/poise/__init__.py':
            expected = ast.parse(LAZY_TEMPLATE).body[0]
            for n in tree.body:
                if ast.dump(n) == ast.dump(expected):
                    self.lazy_import.update(id(x) for x in ast.walk(n) if isinstance(x, ast.ImportFrom))

    def diag(self, node, rule, kind='inconclusive', target=''):
        self.a.diagnostic(self.name, getattr(node, 'lineno', 0), rule, kind, self.owner, target)

    def local_module(self, name):
        if not name:
            return None
        relative = Path(*name.split('.'))
        bases = [self.path.parent, self.a.root, self.a.root / 'src']
        if name == 'shell' and self.loader:
            bases.insert(0, self.a.root / self.a.policy['owners'][self.owner]['root'])
        for base in bases:
            for p in [base / relative.with_suffix('.py'), base / relative / '__init__.py']:
                if p.is_file() or p.is_symlink():
                    return p
        return None

    def module_name(self):
        p = self.path.relative_to(self.a.root)
        parts = list(p.with_suffix('').parts)
        if parts[0] == 'src':
            parts.pop(0)
        if parts[-1] == '__init__':
            parts.pop()
        return '.'.join(parts)

    def absolute_import(self, node):
        if not node.level:
            return node.module or ''
        base = self.module_name().split('.')
        if self.path.name != '__init__.py':
            base.pop()
        if node.level > len(base):
            return '<invalid-relative>'
        base = base[:len(base) - node.level + 1]
        return '.'.join(base + ((node.module or '').split('.') if node.module else []))

    def dependency(self, name, node):
        path = self.local_module(name)
        if path:
            rel = self.a.rel(path)
            own_root = self.a.policy['owners'][self.owner]['root']
            if below(rel, own_root):
                allowed = True
            elif rel.startswith('src/poise/') and self.owner == 'requirements':
                module = rel[4:].replace('/', '.')[:-3]
                if module.endswith('.__init__'):
                    module = module[:-9]
                allowed = name in OWN_MODULES | SHARED_MODULES or (
                    path.name == '__init__.py' and any(m.startswith(module + '.') for m in OWN_MODULES | SHARED_MODULES))
            else:
                allowed = False
            if not allowed:
                self.diag(node, 'foreign_import', 'violation', name)
                return
            self.a.add_source(path, self.owner)
            for parent in path.parents:
                if parent in {self.a.root, self.a.root / 'src'}:
                    break
                init = parent / '__init__.py'
                if init.exists():
                    self.a.add_source(init, self.owner)
        elif name.startswith(('apps.', 'poise.')):
            self.diag(node, 'foreign_import', 'violation', name)
        elif name.split('.')[0] not in sys.stdlib_module_names | {'__future__'}:
            self.diag(node, 'unknown_import', target=name)

    def bind_import(self, node, env):
        if isinstance(node, ast.Import):
            for alias in node.names:
                self.dependency(alias.name, node)
                env[alias.asname or alias.name.split('.')[0]] = Symbol(alias.name if alias.asname else alias.name.split('.')[0])
        else:
            base = self.absolute_import(node)
            if id(node) not in self.lazy_import:
                self.dependency(base, node)
                for alias in node.names:
                    if base == 'poise' and alias.name in {'Poise', '*'}:
                        self.diag(node, 'foreign_import', 'violation', base + '.' + alias.name)
                    elif self.local_module(base + '.' + alias.name):
                        self.dependency(base + '.' + alias.name, node)
                    if self.name.startswith('apps/requirements/') and base.startswith('poise.') and base + '.' + alias.name not in ENTRY_SYMBOLS:
                        self.diag(node, 'foreign_import', 'violation', base + '.' + alias.name)
            for alias in node.names:
                env[alias.asname or alias.name] = Symbol(base + '.' + alias.name)

    def value(self, node, env):
        if node is None:
            return UNKNOWN
        if isinstance(node, ast.Name):
            return env.get(node.id, Symbol(node.id) if node.id in {'str', 'open', '__import__', 'eval', 'exec'} else UNKNOWN)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return tuple(self.value(x, env) for x in node.elts)
        if isinstance(node, ast.Dict):
            keys = [self.value(k, env) for k in node.keys]
            if all(isinstance(k, (str, int)) for k in keys):
                return {k: self.value(v, env) for k, v in zip(keys, node.values)}
        if isinstance(node, ast.Attribute):
            obj = self.value(node.value, env)
            if isinstance(obj, Symbol):
                return Symbol(obj.name + '.' + node.attr)
            if isinstance(obj, P) and node.attr == 'parent':
                return P(norm(Path(obj.value).parent))
            if isinstance(obj, P) and node.attr == 'parents':
                return tuple(P(norm(x)) for x in Path(obj.value).parents)
            return unknown(obj)
        if isinstance(node, ast.Subscript):
            obj, key = self.value(node.value, env), self.value(node.slice, env)
            try:
                if isinstance(obj, (dict, tuple, list)) and isinstance(key, (str, int)):
                    return obj[key]
            except (KeyError, IndexError, TypeError):
                pass
            return unknown(obj)
        if isinstance(node, ast.BinOp):
            left, right = self.value(node.left, env), self.value(node.right, env)
            if isinstance(node.op, ast.Div) and isinstance(left, P) and isinstance(right, (str, P)):
                return P(norm(Path(left.value) / (right.value if isinstance(right, P) else right)))
            if isinstance(node.op, ast.Add) and isinstance(left, str) and isinstance(right, str):
                return left + right
            return unknown(left, right)
        if isinstance(node, ast.Call):
            func = self.value(node.func, env)
            args = [self.value(x, env) for x in node.args]
            name = func.name if isinstance(func, Symbol) else ''
            obj = self.value(node.func.value, env) if isinstance(node.func, ast.Attribute) else UNKNOWN
            attr = node.func.attr if isinstance(node.func, ast.Attribute) else ''
            if name in {'pathlib.Path', 'pathlib.PurePath', 'pathlib.PosixPath'} and len(args) == 1:
                if isinstance(args[0], (P, PathText)):
                    return P(args[0].value)
                if isinstance(args[0], str) and os.path.isabs(args[0]):
                    return P(norm(args[0]))
                return unknown(*args)
            if name == 'str' and len(args) == 1:
                # Preserve provenance without confusing str.replace with Path.replace.
                return PathText(args[0].value) if isinstance(args[0], P) else args[0]
            if isinstance(obj, P) and attr in {'resolve', 'absolute'} and not args:
                return obj
            if isinstance(obj, P) and attr == 'joinpath' and all(isinstance(x, (str, P)) for x in args):
                return P(norm(Path(obj.value).joinpath(*(x.value if isinstance(x, P) else x for x in args))))
            if name == 'os.path.join' and args and isinstance(args[0], P) and all(isinstance(x, (P, str)) for x in args):
                return P(norm(os.path.join(*(x.value if isinstance(x, P) else x for x in args))))
            if name in {'json.loads', 'poise.modules.foundation.strict_json.strict_json'} and node.args:
                inner = node.args[0]
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == 'read_text':
                    target = self.value(inner.func.value, env)
                    if isinstance(target, P) and target.value in self.a.configs:
                        return self.a.configs[target.value]
            return unknown(obj, *args)
        return unknown(*(self.value(c, env) for c in ast.iter_child_nodes(node) if isinstance(c, ast.expr)))

    def access(self, node, sink, value, mode):
        if isinstance(value, PathText):
            value = P(value.value)
        if isinstance(value, str) and os.path.isabs(value):
            value = P(norm(value))
        if isinstance(value, P):
            target_owner = self.a.target_owner(value.value)
            self.a.out['accesses'].append(dict(file=self.name, line=node.lineno, owner=self.owner,
                                               sink=sink, target=self.a.rel(value.value),
                                               target_owner=target_owner, access=mode))
            if target_owner and target_owner != self.owner:
                self.diag(node, 'foreign_storage', 'violation', self.a.rel(value.value))
            if not self.a.safe(value.value):
                self.diag(node, 'unsafe_path', target=self.a.rel(value.value))
        else:
            self.a.out['unresolved_accesses'].append(dict(file=self.name, line=node.lineno, owner=self.owner,
                                                         sink=sink, reason='unsupported or dynamic path'))
            for target in known_paths(value):
                target_owner = self.a.target_owner(target)
                if target_owner and target_owner != self.owner:
                    self.diag(node, 'foreign_storage', 'inconclusive', self.a.rel(target))

    def expression(self, node, env):
        if node is None:
            return
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.stmt):
                self.expression(child, env)
        if isinstance(node, ast.Attribute):
            value = self.value(node, env)
            if isinstance(value, Symbol) and value.name == 'poise.Poise':
                self.diag(node, 'foreign_import', 'violation', value.name)
        if not isinstance(node, ast.Call):
            return
        func = self.value(node.func, env)
        name = func.name if isinstance(func, Symbol) else ''
        obj = self.value(node.func.value, env) if isinstance(node.func, ast.Attribute) else UNKNOWN
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else ''
        args = [self.value(a, env) for a in node.args]
        kw = {k.arg: self.value(k.value, env) for k in node.keywords}
        if not self.loader and (name in {'__import__', 'eval', 'exec', 'importlib.import_module',
                                         'importlib.util.spec_from_file_location', 'importlib.util.module_from_spec',
                                         'runpy.run_path', 'runpy.run_module'} or attr == 'exec_module'
                                or name.startswith('sys.path.')):
            self.diag(node, 'dynamic_import', target=name or attr)
        if name == 'sqlite3.connect':
            self.access(node, name, args[0] if args else kw.get('database', UNKNOWN), 'write')
        elif name in {'open', 'io.open', 'builtins.open', 'os.open'}:
            target = args[0] if args else kw.get('file', kw.get('path', UNKNOWN))
            mode = args[1] if len(args) > 1 else kw.get('mode', 'r')
            kind = 'write' if not isinstance(mode, str) or any(c in mode for c in 'wax+') else 'read'
            self.access(node, name, target, kind)
        elif name in {'os.rename', 'os.replace'}:
            self.access(node, name, args[0] if args else kw.get('src', UNKNOWN), 'write')
            self.access(node, name, args[1] if len(args) > 1 else kw.get('dst', UNKNOWN), 'write')
        elif name in {'os.remove', 'os.unlink', 'os.mkdir', 'os.makedirs'}:
            self.access(node, name, args[0] if args else kw.get('path', UNKNOWN), 'write')
        elif name == 'poise.infrastructure.requirements_registry.RequirementsStore':
            for i, key in enumerate(('database', 'lock')):
                self.access(node, name + '.' + key, args[i] if len(args) > i else kw.get(key, UNKNOWN), 'write')
        elif name in {'poise.infrastructure.sqlite.transaction.write_transaction',
                      'poise.infrastructure.locking.exclusive_lock'}:
            self.access(node, name, args[0] if args else kw.get('path', UNKNOWN), 'write')
            if name.endswith('.write_transaction'):
                self.access(node, name + '.lock', args[1] if len(args) > 1 else kw.get('lock', UNKNOWN), 'write')
        elif isinstance(node.func, ast.Attribute) and not isinstance(obj, (str, PathText, Symbol, dict, tuple)):
            if attr in {'write_text', 'write_bytes', 'touch', 'mkdir', 'unlink', 'read_text', 'read_bytes', 'open'}:
                mode = 'read' if attr.startswith('read') else 'write'
                if attr == 'open':
                    m = args[0] if args else kw.get('mode', 'r')
                    mode = 'write' if not isinstance(m, str) or any(c in m for c in 'wax+') else 'read'
                self.access(node, 'Path.' + attr, obj, mode)
            elif attr in {'rename', 'replace'}:
                self.access(node, 'Path.' + attr, obj, 'write')
                self.access(node, 'Path.' + attr, args[0] if args else kw.get('target', UNKNOWN), 'write')

    def assign(self, target, value, env):
        if isinstance(target, ast.Name):
            env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for i, sub in enumerate(target.elts):
                self.assign(sub, value[i] if isinstance(value, tuple) and i < len(value) else unknown(value), env)
        elif not self.loader and isinstance(target, (ast.Subscript, ast.Attribute)):
            base = self.value(target.value, env)
            if isinstance(base, Symbol) and base.name.startswith('sys.path'):
                self.diag(target, 'dynamic_import', target='sys.path mutation')

    def statements(self, statements, env):
        for node in statements:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.bind_import(node, env)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for decorator in node.decorator_list:
                    self.expression(decorator, env)
                local = dict(env)
                for parameter in getattr(node, 'type_params', []):
                    self.expression(parameter, env)
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        self.expression(base, env)
                    for keyword in node.keywords:
                        self.expression(keyword.value, env)
                else:
                    for default in node.args.defaults + node.args.kw_defaults:
                        self.expression(default, env)
                    arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                    arguments += [a for a in (node.args.vararg, node.args.kwarg) if a is not None]
                    for arg in arguments:
                        self.expression(arg.annotation, env)
                        local[arg.arg] = UNKNOWN
                    self.expression(node.returns, env)
                self.statements(node.body, local)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                if isinstance(node, ast.AnnAssign):
                    self.expression(node.annotation, env)
                self.expression(node.value, env)
                value = self.value(node.value, env)
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    self.assign(target, value, env)
            elif isinstance(node, ast.AugAssign):
                self.expression(node.value, env)
                self.assign(node.target, unknown(self.value(node.target, env), self.value(node.value, env)), env)
            elif isinstance(node, ast.If):
                self.expression(node.test, env)
                left, right = dict(env), dict(env)
                self.statements(node.body, left)
                self.statements(node.orelse, right)
                for key in left.keys() | right.keys():
                    a, b = left.get(key, UNKNOWN), right.get(key, UNKNOWN)
                    env[key] = a if a == b else unknown(a, b)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                expression = node.iter if isinstance(node, (ast.For, ast.AsyncFor)) else node.test
                self.expression(expression, env)
                local = dict(env)
                if hasattr(node, 'target'):
                    self.assign(node.target, unknown(self.value(expression, env)), local)
                self.statements(node.body, local)
                self.statements(node.orelse, local)
                for key in local.keys() | env.keys():
                    a, b = local.get(key, UNKNOWN), env.get(key, UNKNOWN)
                    env[key] = a if a == b else unknown(a, b)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    self.expression(item.context_expr, env)
                    if item.optional_vars:
                        self.assign(item.optional_vars, UNKNOWN, env)
                self.statements(node.body, env)
            elif isinstance(node, (ast.Try, ast.TryStar)):
                branches = []
                for body in [node.body, *(h.body for h in node.handlers), node.orelse]:
                    local = dict(env)
                    self.statements(body, local)
                    branches.append(local)
                for key in set().union(*(b.keys() for b in branches)):
                    values = [b.get(key, UNKNOWN) for b in branches]
                    env[key] = values[0] if all(v == values[0] for v in values) else unknown(*values)
                self.statements(node.finalbody, env)
            else:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.expr):
                        self.expression(child, env)
                    elif isinstance(child, ast.stmt):
                        self.statements([child], dict(env))

    def analyze(self):
        # Import statements count even in uncommon or unreachable syntax.
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.bind_import(node, {})
            elif isinstance(node, ast.Match):
                self.diag(node, 'unsupported_control_flow')
        self.statements(self.tree.body, {'__file__': P(norm(self.path))})


def emit(out, limit):
    kinds = {d['kind'] for d in out['diagnostics']}
    if 'invalid' in kinds:
        out['status'] = 'invalid'
    elif 'violation' in kinds:
        out['status'] = 'violation'
    elif kinds or not out['coverage']['complete'] or not out['stable_inputs']:
        out['status'] = 'inconclusive'
    else:
        out['status'], out['passed'] = 'passed', True
    raw = encode(out)
    if len(raw) > limit:
        out.update(status='inconclusive', passed=False, files=[], private_paths=[], accesses=[],
                   unresolved_accesses=[], diagnostics=[dict(file='', line=0, rule='analysis_limit',
                   kind='inconclusive', owner='', target='', message='max_report_bytes exhausted')])
        out['coverage'].update(complete=False, owners=[], entrypoints=[])
        raw = encode(out)
    sys.stdout.buffer.write(raw)
    return 0 if out['passed'] else 2 if out['status'] == 'invalid' else 1


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Invalid('required --checkout and --policy arguments')


def main():
    out = report()
    parser = Parser(description=__doc__)
    parser.add_argument('--checkout', required=True)
    parser.add_argument('--policy', required=True)
    try:
        args = parser.parse_args()
        root = Path(os.path.abspath(args.checkout))
        policy = Path(os.path.abspath(args.policy))
        if not root.is_dir() or root.is_symlink():
            raise Invalid('checkout must be an existing regular directory')
    except Invalid:
        out['coverage']['complete'] = False
        out['diagnostics'].append(dict(file='', line=0, rule='invalid_arguments', kind='invalid',
                                       owner='', target='', message='required safe checkout and policy'))
        return emit(out, LIMIT_MAX['max_report_bytes'])
    analyzer = Analyzer(root, policy, out)
    if analyzer.load_policy():
        try:
            analyzer.load_configs()
            analyzer.discover()
        except Bound as exc:
            analyzer.diagnostic('', 0, 'analysis_limit', 'inconclusive', message=str(exc) + ' exhausted')
        except (OSError, RecursionError, MemoryError):
            analyzer.diagnostic('', 0, 'incomplete_analysis', 'inconclusive')
    return analyzer.finish()


if __name__ == '__main__':
    raise SystemExit(main())
