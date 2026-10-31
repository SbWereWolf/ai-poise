"""Literal requirements migrations executed by the standard yoyo backend."""
from __future__ import annotations

import ast
import hashlib
from contextlib import closing, contextmanager, redirect_stdout
from pathlib import Path
import re
import sqlite3
import sys
from tempfile import TemporaryDirectory
import uuid

import sqlparse
from yoyo import get_backend, read_migrations
from yoyo.exceptions import LockTimeout

from ..common import PoiseError
from ..modules.foundation.paths import matches_allowed_path


HISTORY_TABLES = {'yoyo_lock', '_yoyo_migrations', '_yoyo_log', '_yoyo_version'} | {
    f'_poise_requirements_{kind}{suffix}'
    for kind in ('schema', 'data') for suffix in ('', '_log', '_version')
}


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise PoiseError(f'Требуются поля миграции: {", ".join(names)}')


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', value):
        raise PoiseError('name: требуется имя миграции из букв, цифр, _ и -')
    return value


def _selection(value, field):
    if (not isinstance(value, list) or not value
            or any(not isinstance(item, str) or not item for item in value)
            or len(set(value)) != len(value)):
        raise PoiseError(f'{field}: требуется непустой список уникальных идентификаторов')
    return value


def _sql(value, kind, *, readonly=False):
    if not isinstance(value, str) or not value.strip():
        raise PoiseError('sql/rollback_sql: требуется непустой SQL')
    for statement in sqlparse.parse(value):
        tokens = [token for token in statement.flatten()
                  if not token.is_whitespace and token.ttype not in sqlparse.tokens.Comment]
        if not tokens:
            continue
        first = tokens[0].normalized.upper()
        if first in {'BEGIN', 'COMMIT', 'END', 'ROLLBACK', 'SAVEPOINT', 'RELEASE'}:
            raise PoiseError('Транзакцией миграции управляет yoyo; SQL управления транзакцией запрещён')
        allowed = {'INSERT', 'UPDATE', 'DELETE'} | ({'SELECT'} if readonly else set())
        if kind == 'data' and statement.get_type() not in allowed:
            raise PoiseError('В миграции данных запрещены DDL и изменения схемы; допустим только DML INSERT, UPDATE, DELETE')
    return value


def _literal_file(path, kind):
    """Validate literal Python steps before yoyo can execute the module."""
    source = path.read_text(encoding='utf-8')
    if path.suffix == '.sql':
        if re.search(r'^\s*--\s*transactional\s*:\s*false', source, re.I | re.M):
            raise PoiseError('Миграция должна выполняться в транзакции')
        _sql(source, kind, readonly=True)
        rollback = path.with_name(path.stem + '.rollback.sql')
        if not rollback.is_file():
            raise PoiseError(f'Миграция {path.name} требует файл отката {rollback.name}')
        _sql(rollback.read_text(encoding='utf-8'), kind, readonly=True)
        return
    try:
        module = ast.parse(source, filename=str(path))
        for node in module.body:
            if (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                    and target.id == '__transactional__' for target in node.targets)
                    and ast.literal_eval(node.value) is not True):
                raise PoiseError('Миграция должна выполняться в транзакции')
        for node in module.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if isinstance(node, ast.ImportFrom) and node.module == 'yoyo' and all(
                    alias.name == 'step' and alias.asname is None for alias in node.names):
                continue
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name == '__depends__':
                    dependencies = ast.literal_eval(node.value)
                    if not isinstance(dependencies, (set, list, tuple)) or any(
                            not isinstance(item, str) for item in dependencies):
                        raise ValueError('literal dependencies required')
                    continue
                if name == '__transactional__':
                    if ast.literal_eval(node.value) is not True:
                        raise PoiseError('Миграция должна выполняться в транзакции')
                    continue
                if name == 'steps' and isinstance(node.value, (ast.List, ast.Tuple)):
                    for call in node.value.elts:
                        if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name)
                                or call.func.id != 'step' or len(call.args) != 2 or call.keywords):
                            raise ValueError('literal step(sql, rollback_sql) required')
                        for argument in call.args:
                            value = ast.literal_eval(argument)
                            if value != '':
                                _sql(value, kind, readonly=True)
                    continue
            raise ValueError('only literal standard yoyo steps are allowed')
    except (SyntaxError, ValueError, TypeError) as exc:
        raise PoiseError(f'Миграция {path.name} должна содержать прямой SQL: {exc}') from exc


def _render(pairs, depends):
    return ('from yoyo import step\n\n__depends__ = ' + repr(set(depends))
            + '\n__transactional__ = True\nsteps = [\n'
            + ''.join(f'    step({forward!r}, {backward!r}),\n' for forward, backward in pairs)
            + ']\n')


def _statement_pairs(forward, backward):
    statements = sqlparse.split(forward)
    rollback = sqlparse.split(backward)
    count = max(len(statements), len(rollback))
    statements += [''] * (count - len(statements))
    undo = [''] * (count - len(rollback)) + list(reversed(rollback))
    return list(zip(statements, undo))


def _identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    if isinstance(value, str):
        if '\0' in value:
            return "CAST(X'" + value.encode('utf-8').hex() + "' AS TEXT)"
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (int, float)):
        if value == float('inf'):
            return '9e999'
        if value == float('-inf'):
            return '-9e999'
        return repr(value)
    raise PoiseError('Значение SQLite не представимо прямым SQL')


def _capture(database, read_seconds):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True,
                                 timeout=read_seconds)) as connection:
        connection.execute('BEGIN')
        tables = {}
        definitions = []
        for kind, name, owner, definition in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND type IN ('table','index','trigger','view') ORDER BY type='table' DESC,name"):
            if owner in HISTORY_TABLES or owner.startswith('sqlite_'):
                continue
            definitions.append(definition)
            if kind != 'table':
                continue
            identifier = _identifier(name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info({identifier})')]
            primary = [row[1] for row in sorted(connection.execute(f'PRAGMA table_info({identifier})'), key=lambda row: row[5]) if row[5]]
            foreign = list(connection.execute(f'PRAGMA foreign_key_list({identifier})'))
            projection = ','.join(map(_identifier, columns))
            rows = list(connection.execute(f'SELECT {projection} FROM {identifier} ORDER BY {projection}'))
            tables[name] = {'columns': columns, 'primary': primary, 'foreign': foreign, 'rows': rows}
        return {'tables': tables, 'schema': definitions}


def _table_order(capture):
    pending = set(capture['tables'])
    result = []
    while pending:
        ready = sorted(name for name in pending if not (
            {row[2] for row in capture['tables'][name]['foreign']} - {name}) & pending)
        if not ready:
            raise PoiseError('Циклические внешние ключи: требуется явная миграция данных')
        result.extend(ready)
        pending.difference_update(ready)
    return result


def _inserts(name, table):
    columns = ','.join(map(_identifier, table['columns']))
    return [f'INSERT INTO {_identifier(name)}({columns}) VALUES({",".join(map(_literal, row))});'
            for row in table['rows']]


def _snapshot_pairs(capture, baseline):
    order = _table_order(capture)
    pairs = []
    for name in reversed(order):
        clear = f'DELETE FROM {_identifier(name)};'
        table = baseline['tables'][name]
        rows = table['rows']
        if rows:
            columns = ','.join(map(_identifier, table['columns']))
            values = ','.join('(' + ','.join(map(_literal, row)) + ')' for row in rows)
            restore = f'INSERT INTO {_identifier(name)}({columns}) VALUES{values};'
        else:
            restore = clear
        pairs.append((clear, restore))
    for name in order:
        table = capture['tables'][name]
        for row, insert in zip(table['rows'], _inserts(name, table)):
            keys = table['primary'] or table['columns']
            where = ' AND '.join(f'{_identifier(column)} IS {_literal(row[table["columns"].index(column)])}'
                                 for column in keys)
            pairs.append((insert, f'DELETE FROM {_identifier(name)} WHERE {where};'))
    return pairs


def _file_pairs(migration):
    path = Path(migration.path)
    if path.suffix == '.sql':
        return _statement_pairs(path.read_text(encoding='utf-8'),
                                path.with_name(path.stem + '.rollback.sql').read_text(encoding='utf-8'))
    module = ast.parse(path.read_text(encoding='utf-8'))
    steps = next(node.value for node in module.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'steps' for target in node.targets))
    return [(ast.literal_eval(call.args[0]), ast.literal_eval(call.args[1])) for call in steps.elts]


class RequirementsMigrations:
    def __init__(self, runtime):
        self.runtime = runtime
        self.database = runtime.requirements_store.database
        self.prepared_source = None

    def _capture(self, database):
        try:
            return _capture(database, self.runtime.cfg['limits']['sqlite_read_seconds'])
        except sqlite3.Error as exc:
            raise PoiseError(f'Не удалось прочитать SQLite-БД требований {database}: {exc}') from exc

    def _configured_paths(self):
        paths = self.runtime.cfg['paths']
        result = {}
        for key in ('requirements_schema_migrations', 'requirements_data_migrations', 'requirements_schema_file'):
            value = paths.get(key)
            if not isinstance(value, str) or not value.strip() or '\0' in value:
                raise PoiseError(f'paths.{key}: требуется явный непустой путь')
            result[key] = (self.runtime.root / value).resolve()
        return result

    def _context(self, *, require_cwd):
        runtime = self.runtime
        data = runtime._task()
        if data['claimed_by'] != runtime.session or data['worktree'] is None:
            raise PoiseError('Для записи требований требуется владение Task и её worktree')
        if runtime._stage(data)['read_only']:
            raise PoiseError('Этап Task разрешает только чтение; запись требований запрещена')
        if data['status'] != 'active' or data['pending'] is not None:
            raise PoiseError('Текущий этап Task не разрешает запись требований')
        worktree = Path(data['worktree']).resolve()
        if require_cwd and Path.cwd().resolve() != worktree:
            raise PoiseError('Запись требований выполняется из worktree текущей Task')
        if runtime._git(worktree, 'symbolic-ref', '--short', 'HEAD') != data['branch']:
            raise PoiseError('В worktree другая ветка Task')
        return data, worktree

    def _paths(self, *, completion=False):
        configured = self._configured_paths()
        data, worktree = self._context(require_cwd=not completion)
        repository = Path(self.runtime.cfg['git']['repository']).resolve()
        for key in ('requirements_data_migrations', 'requirements_schema_file'):
            try:
                relative = configured[key].relative_to(repository)
            except ValueError as exc:
                raise PoiseError(f'{key}: путь должен принадлежать репозиторию документации Task') from exc
            destination = (worktree / relative).resolve()
            if not destination.is_relative_to(worktree):
                raise PoiseError(f'{key}: путь вне worktree')
            configured[key] = destination
        schema = configured['requirements_schema_migrations']
        if schema == configured['requirements_data_migrations']:
            raise PoiseError('Истории schema и data должны располагаться отдельно')
        return configured

    def _authorize(self, destinations):
        data, worktree = self._context(require_cwd=False)
        scope = data['contract']['stage_contracts'][data['stage_index']]['allowed_paths']
        for destination in destinations:
            resolved = Path(destination).resolve()
            if (not resolved.is_relative_to(worktree) or not any(
                    matches_allowed_path(resolved.relative_to(worktree).as_posix(), pattern)
                    for pattern in scope)):
                raise PoiseError(f'Путь {destination} вне области allowed_paths текущего этапа')

    @staticmethod
    def _new_backend(kind, database, busy_seconds):
        backend = get_backend('sqlite:///' + str(database), migration_table=f'_poise_requirements_{kind}')
        backend.log_table = f'_poise_requirements_{kind}_log'
        backend.version_table = f'_poise_requirements_{kind}_version'
        backend.connection.execute('PRAGMA foreign_keys=ON')
        backend.connection.execute(f'PRAGMA busy_timeout={int(busy_seconds * 1000)}')
        return backend

    @contextmanager
    def _backend(self, kind, database):
        with self.runtime.project_fence.locked():
            with self._standard_backend(kind, database, self.runtime.cfg['limits']['lock_seconds']) as backend:
                yield backend

    @classmethod
    @contextmanager
    def _standard_backend(cls, kind, database, busy_seconds):
        backend = cls._new_backend(kind, database, busy_seconds)
        try:
            with backend.lock(timeout=busy_seconds), redirect_stdout(sys.stderr):
                yield backend
        except LockTimeout as exc:
            raise PoiseError('Не получена блокировка yoyo. Дождитесь завершения другой '
                             f'операции миграций и повторите команду: {exc}') from exc
        finally:
            backend.connection.close()

    @staticmethod
    def _history(paths, kind):
        directory = paths[f'requirements_{kind}_migrations']
        if directory.is_dir():
            for path in directory.iterdir():
                if path.suffix in {'.py', '.sql'} and not path.name.endswith('.rollback.sql'):
                    _literal_file(path, kind)
        history = read_migrations(str(directory))
        if history.post_apply:
            raise PoiseError('Допускаются только обратимые миграции; post-apply обработчики запрещены')
        return history

    def _schema_baseline(self, paths):
        self.runtime.runtime.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='requirements-schema-', dir=self.runtime.runtime) as temporary:
            database = Path(temporary) / 'schema.sqlite'
            with self._backend('schema', database) as backend:
                history = self._history(paths, 'schema')
                backend.apply_migrations(backend.to_apply(history))
            return self._capture(database)

    @classmethod
    def initialize(cls, database, paths, busy_seconds):
        """Build a new infrastructure-owned store exclusively from migration files."""
        database = Path(database)
        if database.exists() or database.is_symlink():
            raise PoiseError('Создание из миграций допускается только для новой БД требований')
        histories = {kind: cls._history(paths, kind) for kind in ('schema', 'data')}
        if any(not history for history in histories.values()):
            raise PoiseError('Для создания БД требований нужны начальные миграции схемы и данных; '
                             'подготовьте файлы в явно настроенных каталогах')
        for kind, history in histories.items():
            with cls._standard_backend(kind, database, busy_seconds) as backend:
                backend.apply_migrations(backend.to_apply(history))

    def _run(self, body, rollback):
        _fields(body, ('kind',))
        kind = body['kind']
        if not isinstance(kind, str) or kind not in {'schema', 'data'}:
            raise PoiseError('kind: требуется schema или data')
        paths = self._paths()
        migrations = self._history(paths, kind)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        data_backend = None
        try:
            if rollback and kind == 'schema':
                # Initialize before the shared schema lock, then reread under it.
                data_backend = self._new_backend('data', self.database, self.runtime.cfg['limits']['lock_seconds'])
                with self.runtime.project_fence.locked():
                    with data_backend.lock(timeout=self.runtime.cfg['limits']['lock_seconds']):
                        data_backend.get_applied_migration_hashes()
            with self._backend(kind, self.database) as backend:
                if data_backend is not None and data_backend.get_applied_migration_hashes():
                    raise PoiseError('Перед откатом схемы выполните откат применённых миграций данных')
                selected = backend.to_rollback(migrations) if rollback else backend.to_apply(migrations)
                if rollback:
                    backend.rollback_migrations(selected)
                else:
                    backend.apply_migrations(selected)
            return {'migrations': [migration.id for migration in selected]}
        except LockTimeout as exc:
            raise PoiseError('Другая операция удерживает блокировку yoyo; дождитесь её '
                             f'завершения и повторите откат схемы: {exc}') from exc
        except (sqlite3.Error, OSError) as exc:
            raise PoiseError(f'Не удалось выполнить миграции {kind}: {exc}. Исправьте SQL и повторите операцию.') from exc
        finally:
            if data_backend is not None:
                data_backend.connection.close()

    def apply(self, body):
        return self._run(body, False)

    def rollback(self, body):
        return self._run(body, True)

    def _write(self, directory, name, pairs, depends=()):
        destination = directory / (name + '.py')
        self._authorize([destination])
        if destination.exists() or destination.with_suffix('.sql').exists():
            raise PoiseError(f'name: миграция {name} уже существует')
        directory.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8') as target:
            target.write(_render(pairs, depends))
        return destination

    def command(self, body):
        _fields(body, ('name', 'sql', 'rollback_sql'))
        name = _name(body['name'])
        forward, backward = _sql(body['sql'], 'data'), _sql(body['rollback_sql'], 'data')
        pairs = _statement_pairs(forward, backward)
        paths = self._paths()
        migrations = self._history(paths, 'data')
        self._write(paths['requirements_data_migrations'], name, pairs, [migration.id for migration in migrations])
        return self.apply({'kind': 'data'})

    def squash(self, body):
        _fields(body, ('name', 'migrations'))
        name = _name(body['name'])
        identifiers = set(_selection(body['migrations'], 'migrations'))
        paths = self._paths()
        return self._squash(name, identifiers, paths)

    def _squash(self, name, identifiers, paths):
        directory = paths['requirements_data_migrations']
        history = self._history(paths, 'data')
        indexed = {migration.id: migration for migration in history}
        missing = identifiers - indexed.keys()
        if missing:
            raise PoiseError(f'Не найдены миграции: {sorted(missing)}')
        if name in indexed or (directory / (name + '.py')).exists():
            raise PoiseError(f'name: миграция {name} уже существует')
        selected = history.filter(lambda migration: migration.id in identifiers)
        destinations = [directory / (name + '.py')]
        for migration in selected:
            source = Path(migration.path)
            destinations.append(source)
            if source.suffix == '.sql':
                destinations.append(source.with_name(source.stem + '.rollback.sql'))
        self._authorize(destinations)
        dependencies = set()
        for migration in history:
            depends = {dependency.id for dependency in migration.depends}
            if migration.id in identifiers:
                dependencies.update(depends - identifiers)
            elif depends & identifiers:
                raise PoiseError(f'Миграция {migration.id} зависит от объединяемой истории')
        before = self._capture(self.database)
        try:
            with self._backend('data', self.database) as backend:
                reverse = list(backend.to_rollback(selected))
                if {migration.id for migration in reverse} != identifiers:
                    raise PoiseError('Объединять можно только применённые миграции')
                ordered = list(reversed(reverse))
                pairs = [pair for migration in ordered for pair in _file_pairs(migration)]
                backend.rollback_migrations(reverse)
                for migration in selected:
                    source = Path(migration.path)
                    source.unlink()
                    if source.suffix == '.sql':
                        source.with_name(source.stem + '.rollback.sql').unlink()
                self._write(directory, name, pairs, dependencies)
                updated = self._history(paths, 'data')
                combined = updated.filter(lambda migration: migration.id == name)
                backend.apply_migrations(backend.to_apply(combined))
                if self._capture(self.database) != before:
                    raise PoiseError('Объединение изменило полный снимок требований')
                backend.rollback_migrations(backend.to_rollback(combined))
                backend.apply_migrations(backend.to_apply(combined))
                if self._capture(self.database) != before:
                    raise PoiseError('Повторное применение изменило полный снимок требований')
            return {'migrations': [name]}
        except (sqlite3.Error, OSError) as exc:
            raise PoiseError(f'Не удалось объединить миграции: {exc}. Исправьте SQL отката и повторите операцию.') from exc

    def rebase(self, body):
        _fields(body, ('name',))
        name = _name(body['name'])
        paths = self._paths()
        directory = paths['requirements_data_migrations']
        history = self._history(paths, 'data')
        if any(migration.id == name for migration in history):
            raise PoiseError(f'name: миграция {name} уже существует')
        destinations = [directory / (name + '.py'), paths['requirements_schema_file']]
        for migration in history:
            source = Path(migration.path)
            destinations.append(source)
            if source.suffix == '.sql':
                destinations.append(source.with_name(source.stem + '.rollback.sql'))
        self._authorize(destinations)
        before = self._capture(self.database)
        pairs = _snapshot_pairs(before, self._schema_baseline(paths))
        bootstrap = ';\n'.join(before['schema']) + ';\n'
        try:
            with self._backend('data', self.database) as backend:
                backend.unmark_migrations(history)
                for migration in history:
                    source = Path(migration.path)
                    source.unlink()
                    if source.suffix == '.sql':
                        source.with_name(source.stem + '.rollback.sql').unlink()
                self._write(directory, name, pairs)
                schema_file = paths['requirements_schema_file']
                schema_file.parent.mkdir(parents=True, exist_ok=True)
                schema_file.write_text(bootstrap, encoding='utf-8')
                updated = self._history(paths, 'data')
                backend.apply_migrations(backend.to_apply(updated))
                if self._capture(self.database) != before:
                    raise PoiseError('Смена истории не сохранила полный снимок требований')
                backend.rollback_migrations(backend.to_rollback(updated))
                backend.apply_migrations(backend.to_apply(updated))
                if self._capture(self.database) != before:
                    raise PoiseError('Повторное применение новой истории изменило данные')
            return {'migrations': [name]}
        except (sqlite3.Error, OSError) as exc:
            raise PoiseError(f'Не удалось заменить историю данных: {exc}. Проверьте новую схему и SQL снимка.') from exc

    def export(self, body):
        _fields(body, ('source', 'requirements', 'name'))
        name = _name(body['name'])
        source = body['source']
        if not isinstance(source, str) or not source.strip() or not Path(source).is_file():
            raise PoiseError(f'source: требуется существующий файл БД требований: {source!r}')
        selection = body['requirements']
        if selection is not None:
            _selection(selection, 'requirements')
        paths = self._paths()
        self._authorize([paths['requirements_data_migrations'] / (name + '.py')])
        captured = self._capture(source)
        pairs = self._export_pairs(captured, selection, paths)
        history = self._history(paths, 'data')
        destination = self._write(paths['requirements_data_migrations'], name, pairs,
                                  [migration.id for migration in history])
        return {'migration': str(destination)}

    def _export_pairs(self, captured, selection, paths):
        self.runtime.runtime.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='requirements-export-', dir=self.runtime.runtime) as temporary:
            directory = Path(temporary)
            database = directory / 'validation.sqlite'
            with self._backend('schema', database) as backend:
                history = self._history(paths, 'schema')
                backend.apply_migrations(backend.to_apply(history))
            baseline = self._capture(database)
            if set(captured['tables']) != set(baseline['tables']):
                raise PoiseError('source: схема БД не соответствует выбранному хранилищу требований')
            for name, table in captured['tables'].items():
                if set(table['columns']) != set(baseline['tables'][name]['columns']):
                    raise PoiseError(f'source: несовместимые столбцы таблицы {name}')
            if len(captured['tables']['requirements_meta']['rows']) != 1:
                raise PoiseError('source: требуется одна служебная ревизия требований')
            if selection is None:
                pairs = _snapshot_pairs(captured, baseline)
            else:
                pairs = self._transfer_pairs(captured, selection)
                with self.runtime.project_fence.locked():
                    with closing(sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True,
                                                 timeout=self.runtime.cfg['limits']['sqlite_read_seconds'])) as source:
                        with closing(sqlite3.connect(database,
                                                     timeout=self.runtime.cfg['limits']['sqlite_read_seconds'])) as target:
                            source.backup(target)
            migration = directory / ('validation-' + uuid.uuid4().hex + '.py')
            migration.write_text(_render(pairs, ()), encoding='utf-8')
            try:
                with self._backend('data', database) as backend:
                    pending = read_migrations(str(directory))
                    backend.apply_migrations(backend.to_apply(pending))
            except sqlite3.Error as exc:
                raise PoiseError(f'Конфликт данных при экспорте: {exc}') from exc
            return pairs

    def _transfer_pairs(self, capture, identifiers):
        tables = capture['tables']
        selected = {name: set() for name in tables}
        requirements = tables['requirements']
        identifier_index = requirements['columns'].index('id')
        by_id = {row[identifier_index]: row for row in requirements['rows']}
        missing = set(identifiers) - by_id.keys()
        if missing:
            raise PoiseError(f'requirements: не найдены требования {sorted(missing)}')
        wanted = set(identifiers)
        links = tables['requirement_links']
        system = links['columns'].index('system_id')
        application = links['columns'].index('application_id')
        while True:
            parents = {row[system] for row in links['rows'] if row[application] in wanted}
            if parents <= wanted:
                break
            wanted.update(parents)
        selected['requirements'].update(by_id[identifier] for identifier in wanted)
        selected['requirement_links'].update(row for row in links['rows'] if row[application] in wanted)
        for name, table in tables.items():
            if name in {'requirements', 'requirement_links', 'requirements_meta', 'requirement_requests'}:
                continue
            references = [foreign for foreign in table['foreign'] if foreign[2] == 'requirements']
            for row in table['rows']:
                if any(row[table['columns'].index(foreign[3])] in wanted for foreign in references):
                    selected[name].add(row)
        changed = True
        while changed:
            changed = False
            for name, rows in selected.items():
                table = tables[name]
                groups = {}
                for foreign in table['foreign']:
                    groups.setdefault(foreign[0], []).append(foreign)
                for row in list(rows):
                    for group in groups.values():
                        parent = group[0][2]
                        if parent not in tables:
                            raise PoiseError(f'Внешний ключ ссылается на неизвестную таблицу {parent}')
                        matches = [candidate for candidate in tables[parent]['rows'] if all(
                            candidate[tables[parent]['columns'].index(foreign[4])]
                            == row[table['columns'].index(foreign[3])] for foreign in group)]
                        for candidate in matches:
                            if candidate not in selected[parent]:
                                selected[parent].add(candidate)
                                changed = True
        target = self._capture(self.database)['tables']
        pairs = []
        for name in _table_order(capture):
            table = tables[name]
            if name not in target or target[name]['columns'] != table['columns']:
                raise PoiseError(f'Несовместимая схема таблицы {name}; примените миграции схемы')
            keys = table['primary'] or table['columns']
            indexes = [table['columns'].index(key) for key in keys]
            existing = {tuple(row[index] for index in indexes): row for row in target[name]['rows']}
            for row in table['rows']:
                if row not in selected[name]:
                    continue
                key = tuple(row[index] for index in indexes)
                if key in existing:
                    if existing[key] != row:
                        raise PoiseError(f'Конфликт требований при переносе: {name} {key}')
                    continue
                forward = _inserts(name, {**table, 'rows': [row]})[0]
                where = ' AND '.join(f'{_identifier(column)} IS {_literal(row[index])}'
                                     for column, index in zip(keys, indexes))
                pairs.append((forward, f'DELETE FROM {_identifier(name)} WHERE {where};'))
        return pairs

    def prepare_task_migrations(self):
        runtime = self.runtime
        data = runtime._task()
        if (runtime._stage(data)['read_only'] or data['status'] != 'active'
                or data['pending'] is not None or data['worktree'] is None):
            return
        paths = self._paths(completion=True)
        worktree = Path(data['worktree'])
        tree = runtime._current_tree(data)
        if self.prepared_source == (data['id'], data['stage_index'], tree):
            return
        added = set(runtime._git(worktree, 'diff', '--name-only', '--diff-filter=A', '-z',
                                 data['base'], tree).split('\0'))
        history = self._history(paths, 'data')
        selected = [migration.id for migration in history
                    if Path(migration.path).relative_to(worktree).as_posix() in added]
        if not selected:
            return
        runtime.validate_stage_scope(data)
        if len(selected) == 1:
            before = self._capture(self.database)
            migration = history.filter(lambda item: item.id in selected)
            try:
                with self._backend('data', self.database) as backend:
                    if {item.id for item in backend.to_rollback(migration)} != set(selected):
                        raise PoiseError('Перед завершением Task примените все её миграции данных')
                    for _ in range(2):
                        backend.rollback_migrations(backend.to_rollback(migration))
                        backend.apply_migrations(backend.to_apply(migration))
                        if self._capture(self.database) != before:
                            raise PoiseError('Проверка отката миграции Task изменила данные')
            except (sqlite3.Error, OSError) as exc:
                raise PoiseError(f'Откат миграции Task не выполнен: {exc}. Исправьте SQL отката.') from exc
            self.prepared_source = (data['id'], data['stage_index'], runtime._current_tree(data))
            return
        suffix = hashlib.sha256('\0'.join(sorted(selected)).encode()).hexdigest()[:12]
        name = re.sub(r'[^A-Za-z0-9_-]', '-', data['id']) + '-' + suffix
        self._squash(name, set(selected), paths)
        self.prepared_source = (data['id'], data['stage_index'], runtime._current_tree(data))
