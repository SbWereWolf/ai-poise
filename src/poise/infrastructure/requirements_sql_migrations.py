"""Literal Requirements SQL publication and ordinary yoyo execution."""
from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import quote

import sqlparse

from yoyo import read_migrations
from yoyo.backends.core.sqlite3 import SQLiteBackend
from yoyo.connections import parse_uri
from yoyo.migrations import parse_metadata_from_sql_comments, read_sql_migration, topological_sort

from ..modules.foundation.errors import PoiseError
from ..modules.requirements_registry.crud import validate_command
from .requirements_crud import execute_operation
from .requirements_registry import RequirementsStore
from .requirements_migrations import validate_schema
from .goal_config import atomic_write
from .locking import exclusive_lock


COLUMNS = {
    "requirements": ("id", "level", "status", "text"),
    "domain_areas": ("id", "business_name", "definition"),
    "domain_entities": ("id", "domain_id", "business_name", "code_name", "definition"),
    "domain_actions": ("id", "domain_id", "business_name", "code_name", "definition"),
    "requirement_links": ("system_id", "application_id"),
    "requirement_entities": ("requirement_id", "domain_id", "entity_id"),
    "requirement_actions": ("requirement_id", "domain_id", "action_id"),
}
KEYS = {table: (("id",) if table in (
    "requirements", "domain_areas", "domain_entities", "domain_actions"
) else columns) for table, columns in COLUMNS.items()}
LIBRARY_TABLES = frozenset(("_yoyo_migration", "_yoyo_log", "_yoyo_version", "yoyo_lock"))
MAX_REVISION = 9223372036854775807


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def join_sql(bodies):
    """Keep literal bodies, adding only missing statement/file separators."""
    return ''.join(body + ('' if body.endswith(('\n', '\r')) else '\n')
                   + ('' if not body.strip() or body.rstrip().endswith(';') else ';\n')
                   for body in bodies)


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    if value is None:
        return "NULL"
    if type(value) is int and -MAX_REVISION - 1 <= value <= MAX_REVISION:
        return str(value)
    if type(value) is str:
        if "\x00" in value or "\r" in value:
            return "CAST(X'" + value.encode("utf-8").hex() + "' AS TEXT)"
        return "'" + value.replace("'", "''") + "'"
    raise PoiseError("Unsupported captured SQL value; correct the source type before generating a migration")


def row_changes(before, after):
    """Render one already validated operation's exact row transition."""
    deletes, inserts, updates = [], [], []
    for table, columns in COLUMNS.items():
        positions = [columns.index(key) for key in KEYS[table]]
        key = lambda row: tuple(row[index] for index in positions)
        old = {key(row): row for row in before[table]}
        new = {key(row): row for row in after[table]}

        def predicate(row):
            return " AND ".join(f"{name}={literal(row[columns.index(name)])}" for name in KEYS[table])

        table_deletes = [f"DELETE FROM {table} WHERE {predicate(old[identity])};"
                         for identity in old.keys() - new.keys()]
        deletes[0:0] = sorted(table_deletes)
        for identity in sorted(new):
            row = new[identity]
            if identity not in old:
                inserts.append(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join(map(literal, row))});")
            elif row != old[identity]:
                changed = [index for index, value in enumerate(row) if value != old[identity][index]]
                updates.append(f"UPDATE {table} SET " + ",".join(
                    f"{columns[index]}={literal(row[index])}" for index in changed
                ) + f" WHERE {predicate(old[identity])};")
    return deletes + inserts + updates


@dataclass(frozen=True)
class RequirementsSqlSnapshot:
    state: dict
    schema: list
    applied: list
    connection: sqlite3.Connection
    application: dict
    records: dict

    @property
    def state_hash(self):
        return digest(self.state)

    @property
    def private_hash(self):
        return digest({"state": self.state, "schema": self.schema, "application": self.application})

    def proof(self):
        return {"state": self.state, "schema": self.schema, "applied": self.applied,
                "application": self.application, "records": self.records}

    def preview(self):
        connection = sqlite3.connect(":memory:", isolation_level=None)
        try:
            self.connection.backup(connection)
        except Exception:
            connection.close()
            raise
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def close(self):
        self.connection.close()


class RequirementsSqlMigrations:
    def __init__(self, database, lock, lock_seconds, poll_seconds, read_seconds,
                 schema_directory, data_directory, initial_directory):
        self.database = Path(database).resolve()
        self.lock = Path(lock)
        self.lock_seconds = lock_seconds
        self.poll_seconds = poll_seconds
        self.read_seconds = read_seconds
        self.schema_directory = Path(schema_directory)
        self.data_directory = Path(data_directory)
        self.initial_directory = Path(initial_directory)

    def configuration(self):
        return {"database": str(self.database), "schema_directory": str(self.schema_directory),
                "data_directory": str(self.data_directory), "initial_directory": str(self.initial_directory)}

    def require_untracked(self):
        root = subprocess.run(["git", "-C", str(self.database.parent), "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True)
        if root.returncode:
            if root.returncode == 128 and "not a git repository" in root.stderr:
                return
            raise PoiseError("Cannot verify the Requirements database Git ownership; inspect its parent repository")
        repository = Path(root.stdout.strip())
        tracked = subprocess.run(["git", "-C", str(repository), "ls-files", "-z", "--",
                                  self.database.relative_to(repository).as_posix()], capture_output=True)
        if tracked.returncode or tracked.stdout:
            raise PoiseError("Requirements database is Git-tracked or tracking is uncertain; untrack it without deleting the physical file")

    def role(self, migration):
        parent = Path(migration.path).parent.resolve()
        if parent == self.schema_directory.resolve():
            return "schema"
        if parent == self.data_directory.resolve():
            return "data"
        raise PoiseError("Migration is outside both explicitly configured source directories")

    @contextmanager
    def locked(self):
        with exclusive_lock(self.lock, self.lock_seconds, self.poll_seconds):
            yield

    def capture(self, database=None):
        path = self.database if database is None else Path(database).resolve()
        if not path.is_file():
            raise PoiseError(f"Requirements comparison database does not exist: {path}; select an existing database")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                     timeout=self.read_seconds, isolation_level=None)) as source:
            source.execute("BEGIN")
            # Read before backup to pin a committed WAL snapshot.
            source.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchone()
            snapshot = sqlite3.connect(":memory:", isolation_level=None)
            try:
                source.backup(snapshot)
                state = self.read_state(snapshot)
                schema = [list(row) for row in snapshot.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
                ) if row[1] not in LIBRARY_TABLES and not row[1].startswith("sqlite_")]
                names = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
                applied = ([] if "_yoyo_migration" not in names else [row[0] for row in snapshot.execute(
                    "SELECT migration_id FROM _yoyo_migration ORDER BY migration_id")])
                application = {}
                for table in sorted(names - set(COLUMNS) - LIBRARY_TABLES
                                    - {"requirements_meta", "requirement_requests"}):
                    if table.startswith("sqlite_") and table != "sqlite_sequence":
                        continue
                    cursor = snapshot.execute(f"SELECT * FROM {identifier(table)}")
                    rows = []
                    for row in cursor:
                        rows.append([[type(value).__name__,
                                      value.hex() if isinstance(value, (bytes, float)) else value]
                                     for value in row])
                    application[table] = {"columns": [item[0] for item in cursor.description],
                                          "rows": sorted(rows, key=encoded)}
                # An absent and an empty internal table both mean no counters.
                # Persistent counter rows affect future IDs and must be restored.
                application.setdefault('sqlite_sequence', {'columns': ['name', 'seq'], 'rows': []})
                records = ({} if "_yoyo_migration" not in names else {
                    row[1]: list(row) for row in snapshot.execute(
                        "SELECT migration_hash,migration_id,applied_at_utc FROM _yoyo_migration ORDER BY migration_id")})
                return RequirementsSqlSnapshot(state, schema, applied, snapshot, application, records)
            except Exception:
                snapshot.close()
                raise

    @staticmethod
    def read_state(connection):
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        tables = {table: ([] if table not in names else [list(row) for row in connection.execute(
            f"SELECT {','.join(columns)} FROM {table} ORDER BY {','.join(columns)}"
        )]) for table, columns in COLUMNS.items()}
        revision = None
        if "requirements_meta" in names:
            rows = connection.execute("SELECT revision FROM requirements_meta").fetchall()
            if len(rows) != 1:
                raise PoiseError("Requirements revision must have exactly one row; restore the registry before proceeding")
            revision = rows[0][0]
        receipts = ([] if "requirement_requests" not in names else [list(row) for row in connection.execute(
            "SELECT request_id,digest,response FROM requirement_requests ORDER BY request_id")])
        return {"schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
                "tables": tables, "revision": revision, "receipts": receipts}

    def sources(self):
        result = {}
        for role, directory in (("initial", self.initial_directory),
                                ("schema", self.schema_directory), ("data", self.data_directory)):
            if directory.exists() and not directory.is_dir():
                raise PoiseError(f"Migration source is not a directory: {directory}; correct its configuration")
            for path in sorted(directory.iterdir()) if directory.is_dir() else []:
                if role == "initial" and path.is_dir() and not path.is_symlink():
                    continue
                if path.is_symlink() or not path.is_file() or path.suffix != ".sql":
                    raise PoiseError(f"Only plain literal SQL migration files are supported: {path}")
                result[f"{role}/{path.name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    @staticmethod
    def _validate_statements(directives, statements, name, role):
        if directives.get("transactional", "true").lower() != "true":
            raise PoiseError(
                f"SQL {name} задаёт неподдерживаемый режим transactional; "
                "удалите transactional:false и оставьте транзакцию штатному yoyo"
            )
        families = set()
        for sql in statements:
            for statement in sqlparse.parse(sql):
                if all(part.is_whitespace or part.ttype in sqlparse.tokens.Comment
                       or part.match(sqlparse.tokens.Punctuation, ';') for part in statement.flatten()):
                    continue
                position, token = statement.token_next(-1, skip_ws=True, skip_cm=True)
                while token is not None and token.value == "\ufeff":
                    position, token = statement.token_next(position, skip_ws=True, skip_cm=True)
                if token is not None and token.normalized.upper() in {
                    "BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE",
                }:
                    raise PoiseError(
                        f"SQL {name} содержит верхнеуровневую команду {token.normalized}; "
                        "удалите управление транзакцией из тела миграции и повторите штатную операцию"
                    )
                family = statement.get_type()
                if ((role == 'data' and family not in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'SELECT'})
                        or (role == 'schema' and family in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'MERGE'})):
                    raise PoiseError(
                        f'SQL {name} содержит команду, несовместимую с ролью {role}; '
                        'разделите DDL и DML на соответствующие полные SQL-пары до публикации'
                    )
                if family in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'MERGE'}:
                    families.add('data')
                elif family in {'CREATE', 'ALTER', 'DROP', 'TRUNCATE'} or (
                    token is not None and token.normalized.upper() == 'PRAGMA'
                ):
                    families.add('schema')
                elif role == 'initial' and family != 'SELECT':
                    raise PoiseError(
                        f'SQL {name} содержит неподдерживаемую начальную команду; '
                        'используйте отдельные буквальные DDL- и DML-пары'
                    )
        if role == 'initial' and len(families) > 1:
            raise PoiseError(f'SQL {name} смешивает DDL и DML; разделите их на отдельные полные SQL-пары')
        return families

    @classmethod
    def validate_text(cls, body, name, *, role):
        directives, _, sql = parse_metadata_from_sql_comments(body)
        cls._validate_statements(directives, sqlparse.split(sql), name, role)

    @classmethod
    def validate_pair(cls, path, *, role):
        """Admit literal bodies; yoyo alone owns their transactions and execution."""
        path = Path(path)
        reverse = path.with_name(path.stem + ".rollback.sql")
        families = set()
        for candidate in (path, reverse):
            if candidate.is_symlink() or not candidate.is_file():
                raise PoiseError(f"Неполная SQL-пара: {candidate}; восстановите оба обычных SQL-файла")
            try:
                directives, _, statements = read_sql_migration(str(candidate))
                families.update(cls._validate_statements(directives, statements, candidate.name, role))
            except (OSError, UnicodeError, sqlparse.exceptions.SQLParseError) as exc:
                raise PoiseError(f"SQL {candidate.name} нельзя прочитать: {exc}; исправьте исходную SQL-пару") from exc
        if role == 'initial' and len(families) > 1:
            raise PoiseError(f'SQL-пара {path.name} смешивает DDL и DML; разделите схему и данные на отдельные пары')

    @classmethod
    def validate_directory(cls, directory, *, role):
        directory = Path(directory)
        if not directory.is_dir():
            raise PoiseError(f"Каталог SQL отсутствует: {directory}; исправьте явную конфигурацию миграций")
        for path in sorted(directory.glob("*.sql")):
            if path.name.endswith(".rollback.sql"):
                forward = path.with_name(path.name.removesuffix(".rollback.sql") + ".sql")
                if not forward.is_file():
                    raise PoiseError(f"Обратный SQL без прямого: {path}; восстановите полную SQL-пару")
            else:
                cls.validate_pair(path, role=role)

    def catalog_migrations(self):
        self.sources()
        for role, directory in (('initial', self.initial_directory), ('schema', self.schema_directory),
                                ('data', self.data_directory)):
            if directory.is_dir():
                self.validate_directory(directory, role=role)
        try:
            catalog = read_migrations(*[str(path) for path in (
                self.initial_directory, self.schema_directory, self.data_directory
            ) if path.is_dir()])
        except Exception as exc:
            raise PoiseError(f"Migration source/dependency conflict: {exc}; correct the SQL source set") from exc
        if catalog.post_apply:
            raise PoiseError('Допустимы только обычные SQL-пары; удалите post-apply hooks из выбранных источников')
        actual_ids = {migration.id for migration in catalog}
        for migration in catalog:
            path = Path(migration.path)
            if not path.with_name(path.stem + ".rollback.sql").is_file():
                raise PoiseError(f"Migration inverse is missing: {path}; publish the complete pair before applying")
            migration.load()
            if not {item.id for item in migration.depends} <= actual_ids:
                raise PoiseError(f'Migration {migration.id} references a source absent from the actual catalog; restore its SQL prerequisite')
            if migration.use_transactions is not True:
                raise PoiseError(f"Миграция {migration.id} не транзакционна; исправьте SQL и используйте штатную транзакцию yoyo")
        return catalog

    def migrations(self):
        catalog = self.catalog_migrations()
        return catalog.__class__([migration for migration in catalog
            if Path(migration.path).parent.resolve() in {
                self.schema_directory.resolve(), self.data_directory.resolve()}], catalog.post_apply)

    def status(self):
        snapshot = self.capture()
        snapshot.close()
        if type(snapshot.state["revision"]) is not int or not 0 <= snapshot.state["revision"] <= MAX_REVISION:
            raise PoiseError("Счётчик требований не является допустимым INTEGER; восстановите его перед записью")
        identities = [migration.id for migration in self.migrations()]
        return {"status": "read_only", "revision": snapshot.state["revision"],
                "state": snapshot.state_hash, "applied": snapshot.applied,
                "pending": sorted(set(identities) - set(snapshot.applied))}

    @staticmethod
    def _require_target(snapshot, guard, direction):
        if guard is not None and (
            snapshot.state_hash != guard[direction]
            or (direction + "_private" in guard
                and snapshot.private_hash != guard[direction + "_private"])
        ):
            raise PoiseError(
                "Captured migration target changed; restore its captured state before applying SQL"
            )

    def _backend(self, *, guards, observations, admission=None):
        read_seconds = self.read_seconds
        owner = self

        class RequirementsSQLiteBackend(SQLiteBackend):
            # Ordinary yoyo copies instantiate this class, retaining the profile.
            def connect(self, dburi):
                connection = super().connect(dburi)
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(f"PRAGMA busy_timeout={math.ceil(read_seconds * 1000)}")
                return connection

            def before_body(self, migration, direction):
                snapshot = owner.capture()
                snapshot.close()
                owner._require_target(snapshot, guards.get(migration.id), direction)
                if admission is not None:
                    admission(migration.id, direction)
                return {"state": snapshot.state_hash, "private": snapshot.private_hash}

            def apply_one(self, migration, force=False, mark=True):
                before = self.before_body(migration, "before")
                result = super().apply_one(migration, force=force, mark=mark)
                if observations is not None:
                    after = owner.capture()
                    after.close()
                    observations.append({"id": migration.id, "before": before["state"],
                        "before_private": before["private"], "after": after.state_hash,
                        "after_private": after.private_hash})
                return result

            def rollback_one(self, migration, force=False):
                self.before_body(migration, "after")
                return super().rollback_one(migration, force=force)

        backend = RequirementsSQLiteBackend(parse_uri("sqlite:///" + quote(str(self.database), safe="/")), "_yoyo_migration")
        try:
            # The same ordinary initialization performed by yoyo.get_backend.
            backend.init_database()
        except Exception as exc:
            backend.connection.close()
            raise PoiseError(
                f"yoyo metadata initialization failed: {exc}; no Requirements body was executed. "
                "Inspect and repair the ordinary yoyo metadata before retrying."
            ) from exc
        return backend

    def selected_migrations(self, identities):
        migrations = self.migrations()
        by_id = {migration.id: migration for migration in migrations}
        if any(identity not in by_id for identity in identities):
            raise PoiseError("A requested migration is outside the configured sources; inspect migration status")
        return migrations.__class__([by_id[identity] for identity in identities], migrations.post_apply)

    def ordered_ids(self, action, identities):
        selected = self.selected_migrations(identities)
        backend = self._backend(guards={}, observations=None)
        try:
            with backend.lock():
                ordered = backend.to_apply(selected) if action == "apply" else backend.to_rollback(selected)
                return [migration.id for migration in ordered]
        finally:
            backend.connection.close()

    def completion_plan(self, context):
        catalog = self.catalog_migrations()
        by_id = {migration.id: migration for migration in catalog}
        owned = set(context['containers'])
        entry = set(context['entry']['applied'])
        sequence = context['order']
        if len(sequence) != len(owned) or set(sequence) != owned or not (owned | entry) <= by_id.keys():
            raise PoiseError('Migration history/order is incomplete or foreign; restore the recorded Task sources before completion')
        ordered = list(topological_sort([by_id[identity] for identity in sequence]))
        if [migration.id for migration in ordered] != sequence:
            raise PoiseError('Migration dependencies changed the recorded execution order; restore the original sources')
        protected = set()
        for migration in catalog:
            dependencies = {item.id for item in migration.depends}
            if migration.id in owned and not dependencies <= owned | entry:
                raise PoiseError('An owned migration has unapplied foreign dependencies; apply a supported source history explicitly')
            if migration.id not in owned or self.role(migration) == 'schema':
                protected.update(dependencies & owned)
            if migration.id in entry and dependencies & owned:
                raise PoiseError('An applied foreign migration depends on this Task; do not reverse its prerequisites')
        while True:
            blocks, run = [], []
            for migration in ordered:
                if self.role(migration) == 'schema':
                    if run:
                        blocks.append(run)
                        run = []
                    blocks.append([migration])
                else:
                    run.append(migration)
                    if migration.id in protected:
                        blocks.append(run)
                        run = []
            if run:
                blocks.append(run)
            # Singleton bytes are retained, including every original dependency.
            # New cuts can create further singletons, so retain their targets too.
            required = {dependency.id for block in blocks if len(block) == 1
                        for dependency in block[0].depends if dependency.id in owned}
            if required <= protected:
                break
            protected.update(required)
        identities = []
        for block in blocks:
            identity = block[-1].id
            if len(block) > 1 and identity not in protected:
                identity = 'requirements_combined_' + digest([
                    [item.id, context['containers'][item.id]['forward'],
                     context['containers'][item.id]['reverse']] for item in block])
                if identity in by_id:
                    raise PoiseError('Combined migration identity conflicts with an existing source; preserve both histories')
            identities.append(identity)
        if len(set(identities)) != len(identities):
            raise PoiseError('Combined migration identities conflict; preserve the original source set')
        remap = {migration.id: identity for identity, block in zip(identities, blocks, strict=True)
                 for migration in block}
        result = []
        for identity, block in zip(identities, blocks, strict=True):
            forward = [Path(item.path).read_bytes().decode('utf-8') for item in block]
            reverse = [Path(item.path).with_name(Path(item.path).stem + '.rollback.sql').read_bytes().decode('utf-8')
                       for item in reversed(block)]
            if len(block) > 1:
                dependencies = {remap.get(item.id, item.id) for migration in block for item in migration.depends}
                dependencies.discard(identity)
                bodies = []
                for body in forward:
                    directives, _, sql = parse_metadata_from_sql_comments(body)
                    bodies.append(sql if directives else body)
                header = ('-- depends: ' + ' '.join(sorted(dependencies)) + '\n') if dependencies else ''
                forward = [header + join_sql(bodies)]
            sql_forward = ''.join(forward)
            sql_reverse = join_sql(reverse) if len(block) > 1 else reverse[0]
            self.validate_text(sql_forward, identity + '.sql', role=self.role(block[0]))
            self.validate_text(sql_reverse, identity + '.rollback.sql', role=self.role(block[0]))
            result.append({'id': identity, 'role': self.role(block[0]),
                           'members': [item.id for item in block],
                           'sql_forward': sql_forward, 'sql_reverse': sql_reverse})
        # Check actual delivered IDs, not yoyo's process-global cached objects.
        available = (by_id.keys() - owned) | set(identities)
        candidates = {group['id']: group['sql_forward'] for group in result}
        for migration in catalog:
            if migration.id not in owned:
                candidates[migration.id] = Path(migration.path).read_bytes().decode('utf-8')
        for identity, body in candidates.items():
            directives, _, _ = parse_metadata_from_sql_comments(body)
            if not set(directives.get('depends', '').split()) <= available:
                raise PoiseError(f'Final migration {identity} has a missing SQL prerequisite; preserve the referenced source before completion')
        return result

    def historical_plan(self, snapshot):
        version = snapshot.state['schema_version']
        if version not in (1, 2):
            raise PoiseError('Unsupported Requirements schema; restore a supported schema before migration')
        try:
            validate_schema(snapshot.connection, version)
        except ValueError as exc:
            raise PoiseError(f'Неподдерживаемая структура Requirements: {exc}; восстановите поддерживаемую схему') from exc
        if version == 2:
            return None
        resource = Path(__file__).with_name('requirements_sql') / 'historical/0003_requirements_schema_1_to_2.sql'
        self.validate_pair(resource, role='schema')
        return {'forward': resource.read_bytes().decode('utf-8'),
                'reverse': resource.with_name(resource.stem + '.rollback.sql').read_bytes().decode('utf-8')}

    def publish_completion(self, plan, containers):
        delivered = {}
        for group in plan:
            identity, role = group['id'], group['role']
            if len(group['members']) == 1:
                delivered[identity] = deepcopy(containers[identity])
                continue
            directory = self.schema_directory if role == 'schema' else self.data_directory
            if identity in containers:
                for suffix, field in (('.sql', 'forward'), ('.rollback.sql', 'reverse')):
                    path = directory / (identity + suffix)
                    if hashlib.sha256(path.read_bytes()).hexdigest() != containers[identity][field]:
                        raise PoiseError('Retained migration bytes changed; restore the original checkpoint before publication')
                for suffix, body in (('.sql', group['sql_forward']), ('.rollback.sql', group['sql_reverse'])):
                    atomic_write(directory / (identity + suffix), body.encode('utf-8'), mode=0o644)
                descriptor = {'id': identity, 'role': role,
                    'forward': hashlib.sha256(group['sql_forward'].encode('utf-8')).hexdigest(),
                    'reverse': hashlib.sha256(group['sql_reverse'].encode('utf-8')).hexdigest()}
            else:
                descriptor = self.publish_pair(identity, group['sql_forward'], group['sql_reverse'], role=role)
            descriptor['contributions'] = [deepcopy(item) for member in group['members']
                                            for item in containers[member]['contributions']]
            delivered[identity] = descriptor
        return delivered

    def remove_superseded(self, original, delivered):
        for identity in original.keys() - delivered.keys():
            if original[identity]['role'] != 'data':
                raise PoiseError('Completion cannot remove a DDL pair')
            for suffix, field in (('.sql', 'forward'), ('.rollback.sql', 'reverse')):
                path = self.data_directory / (identity + suffix)
                if hashlib.sha256(path.read_bytes()).hexdigest() != original[identity][field]:
                    raise PoiseError('Superseded migration bytes changed; preserve the file and inspect the original checkpoint')
                path.unlink()

    def publish_pair(self, identity, forward, reverse, *, role):
        self.validate_text(forward, identity + ".sql", role=role)
        self.validate_text(reverse, identity + ".rollback.sql", role=role)
        directory = self.schema_directory if role == "schema" else self.data_directory
        directory.mkdir(parents=True, exist_ok=True)
        paths = (directory / f"{identity}.sql", directory / f"{identity}.rollback.sql")
        bodies = (forward.encode("utf-8"), reverse.encode("utf-8"))
        if any(path.exists() for path in paths):
            if not all(path.is_file() and path.read_bytes() == body for path, body in zip(paths, bodies)):
                raise PoiseError("Migration publication conflict or partial pair; inspect the existing files and repair manually")
        else:
            try:
                for path, body in zip(paths, bodies):
                    atomic_write(path, body, mode=0o644)
            except OSError as exc:
                raise PoiseError(
                    f"Migration publication failed: {exc}; no business SQL was executed. "
                    "Inspect any partial pair and repair publication manually."
                ) from exc
        return {"id": identity, "role": role,
                "forward": hashlib.sha256(bodies[0]).hexdigest(),
                "reverse": hashlib.sha256(bodies[1]).hexdigest()}

    @staticmethod
    def validate_action(action, identities, expected_revision, expected_state):
        if action not in ("apply", "rollback"):
            raise PoiseError("Migration action must be apply or rollback")
        if (not isinstance(identities, list) or not identities
                or any(type(value) is not str or not value or value in (".", "..")
                       or "/" in value or "\\" in value or "\x00" in value for value in identities)
                or len(set(identities)) != len(identities)):
            raise PoiseError("Migration IDs must be nonempty and unique")
        if (type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION
                or type(expected_state) is not str or len(expected_state) != 64
                or any(value not in "0123456789abcdef" for value in expected_state)):
            raise PoiseError("Migration action requires explicit revision and state guards")

    def execute_ids(self, action, identities, *, expected_revision, expected_state, guards, observations=None, admission=None):
        self.validate_action(action, identities, expected_revision, expected_state)
        selected = self.selected_migrations(identities)
        # Refuse a wrong first target before even initializing stock metadata.
        # Actual selection/execution remains backend.to_apply/to_rollback below.
        snapshot = self.capture()
        snapshot.close()
        hashes = {record[0] for record in snapshot.records.values()}
        pending = list(topological_sort(migration for migration in selected
            if (migration.hash in hashes) == (action == 'rollback')))
        if action == 'rollback':
            pending.reverse()
        if pending:
            self._require_target(snapshot, guards.get(pending[0].id),
                                 'before' if action == 'apply' else 'after')
        backend = self._backend(guards=guards, observations=observations, admission=admission)
        try:
            with backend.lock():
                before = self.capture()
                before.close()
                if before.state["revision"] != expected_revision or before.state_hash != expected_state:
                    raise PoiseError("Requirements revision/state changed; read fresh migration status before retrying")
                ordered = (backend.to_apply(selected) if action == "apply" else backend.to_rollback(selected))
                affected = [migration.id for migration in ordered]
                if action == "apply":
                    backend.apply_migrations(ordered)
                else:
                    backend.rollback_migrations(ordered)
                return {"status": "applied" if action == "apply" else "rolled_back",
                        "migration_ids": affected}
        except PoiseError:
            raise
        except Exception as exc:
            raise PoiseError(
                f"Migration {action} failed: {exc}. SQL may already be committed while ordinary "
                "yoyo status is incomplete; inspect the database and use yoyo manual status repair. "
                "No automatic recovery was performed."
            ) from exc
        finally:
            backend.connection.close()

    @staticmethod
    def request_identity(request_id):
        return "requirements_" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _request(snapshot, request, request_digest):
        if (type(request.get("request_id")) is not str or not request["request_id"]
                or type(request.get("expected_revision")) is not int
                or not 0 <= request["expected_revision"] <= MAX_REVISION):
            raise PoiseError("Requirements request requires a nonempty request_id and an INTEGER revision")
        for request_id, prior_digest, response in snapshot.state["receipts"]:
            if request_id == request["request_id"]:
                if prior_digest != request_digest:
                    raise PoiseError("Requirements request conflict: this request_id has different intent")
                return {"status": "applied", **json.loads(response), "replayed": True}
        revision = snapshot.state["revision"]
        if type(revision) is not int or not 0 <= revision < MAX_REVISION:
            raise PoiseError("Requirements INTEGER revision cannot increment; repair the revision before writing")
        if revision != request["expected_revision"]:
            raise PoiseError("Requirements revision changed; read the current revision before writing")
        return None

    @staticmethod
    def _header(dependencies):
        return "-- Requirements data change.\n" + (
            "-- depends: " + " ".join(sorted(dependencies)) + "\n" if dependencies else ""
        )

    def plan_command(self, snapshot, request, max_items, dependencies):
        request = validate_command(request, max_items)
        request_digest = "command:" + digest(request)
        replay = self._request(snapshot, request, request_digest)
        identity = self.request_identity(request["request_id"])
        if replay is not None:
            return {"id": identity, "response": replay, "replay": True}
        forward, reverse, results = [], [], []
        with closing(snapshot.preview()) as preview:
            for index, operation in enumerate(request["operations"]):
                before = self.read_state(preview)["tables"]
                count = execute_operation(preview, operation, (index, operation["id"]))
                after = self.read_state(preview)["tables"]
                forward.extend(row_changes(before, after))
                reverse[0:0] = row_changes(after, before)
                results.append({"id": operation["id"], "affected_rows": count})
            response = {"revision": snapshot.state["revision"] + 1, "replayed": False, "results": results}
            return self._mutation(snapshot, preview, request, request_digest,
                                  response, forward, reverse, dependencies)

    def plan_apply(self, snapshot, request, max_items, dependencies):
        if not isinstance(request, dict) or set(request) != {"request_id", "expected_revision", "operations"}:
            raise PoiseError("Requirements apply requires request_id, expected_revision and operations")
        request_digest = digest(request)
        replay = self._request(snapshot, request, request_digest)
        identity = self.request_identity(request["request_id"])
        if replay is not None:
            return {"id": identity, "response": replay, "replay": True}
        with closing(snapshot.preview()) as preview:
            current = RequirementsStore._registry(preview)
            after = current.apply(request["operations"], max_items).to_dict()
            before_tables = self.read_state(preview)["tables"]
            for requirement in after["requirements"].values():
                preview.execute(
                    "INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET status=excluded.status,text=excluded.text",
                    tuple(requirement[column] for column in COLUMNS["requirements"]),
                )
            for link in after["links"]:
                values = (link["system"], link["application"])
                if list(values) not in before_tables["requirement_links"]:
                    preview.execute("INSERT INTO requirement_links(system_id,application_id) VALUES(?,?)", values)
            after_tables = self.read_state(preview)["tables"]
            response = {"revision": snapshot.state["revision"] + 1, "replayed": False}
            return self._mutation(snapshot, preview, request, request_digest, response,
                                  row_changes(before_tables, after_tables), row_changes(after_tables, before_tables),
                                  dependencies)

    def _mutation(self, snapshot, preview, request, request_digest, response, forward, reverse, dependencies):
        receipt_values = (request["request_id"], request_digest, encoded(response))
        preview.execute("UPDATE requirements_meta SET revision=?", (response["revision"],))
        preview.execute("INSERT INTO requirement_requests(request_id,digest,response) VALUES(?,?,?)", receipt_values)
        forward.extend([
            f"UPDATE requirements_meta SET revision={response['revision']};",
            "INSERT INTO requirement_requests(request_id,digest,response) VALUES(" +
            ",".join(map(literal, receipt_values)) + ");",
        ])
        reverse[0:0] = [
            f"DELETE FROM requirement_requests WHERE request_id={literal(request['request_id'])};",
            f"UPDATE requirements_meta SET revision={snapshot.state['revision']};",
        ]
        return {"id": self.request_identity(request["request_id"]), "replay": False,
                "forward": self._header(dependencies) + "\n".join(forward) + "\n",
                "reverse": "\n".join(reverse) + "\n", "response": {"status": "applied", **response},
                "before": snapshot.state_hash, "after": digest(self.read_state(preview)),
                "request_digest": request_digest}

    @staticmethod
    def _validate_capture(snapshot):
        state = snapshot.state
        if state['schema_version'] != 2:
            raise PoiseError('Captured Requirements schema must be version 2; explicitly migrate the source first')
        if type(state['revision']) is not int or not 0 <= state['revision'] <= MAX_REVISION:
            raise PoiseError('Captured revision must be a nonnegative signed SQLite INTEGER')
        for table, rows in state['tables'].items():
            if any(type(value) is not str or not value for row in rows for value in row):
                raise PoiseError(f'Captured {table} identifiers/text must retain their supported TEXT types')
        if any(type(value) is not str or not value for row in state['receipts'] for value in row):
            raise PoiseError('Captured request identifiers/digests/responses must have nonempty TEXT types')
        try:
            with closing(snapshot.preview()) as connection:
                validate_schema(connection, 2)
                RequirementsStore._registry(connection)
                if [row[0] for row in connection.execute('PRAGMA integrity_check')] != ['ok']:
                    raise PoiseError('Captured schema/data integrity is invalid; repair the source before export')
                if connection.execute('PRAGMA foreign_key_check').fetchall():
                    raise PoiseError('Captured schema has missing relationship dependencies; repair the source before export')
        except sqlite3.Error as exc:
            raise PoiseError(f'Captured schema cannot be read consistently: {exc}; repair the source before export') from exc

    @staticmethod
    def _selected_tables(source, target, identities):
        source_tables = source.state['tables']
        requirements = {row[0]: row for row in source_tables['requirements']}
        selected = set(identities)
        if selected - requirements.keys():
            raise PoiseError('Selected requirement is missing from the captured source')
        while True:
            parents = {row[0] for row in source_tables['requirement_links'] if row[1] in selected}
            if parents <= selected:
                break
            selected.update(parents)
        chosen = {table: [] for table in COLUMNS}
        chosen['requirements'] = [row for row in source_tables['requirements'] if row[0] in selected]
        chosen['requirement_links'] = [row for row in source_tables['requirement_links']
                                       if row[0] in selected and row[1] in selected]
        domains = set()
        for relation, entity in (('requirement_entities', 'domain_entities'),
                                 ('requirement_actions', 'domain_actions')):
            chosen[relation] = [row for row in source_tables[relation] if row[0] in selected]
            references = {(row[1], row[2]) for row in chosen[relation]}
            chosen[entity] = [row for row in source_tables[entity] if (row[1], row[0]) in references]
            domains.update(row[1] for row in chosen[relation])
        chosen['domain_areas'] = [row for row in source_tables['domain_areas'] if row[0] in domains]
        result = deepcopy(target.state['tables'])
        for table, rows in chosen.items():
            columns = COLUMNS[table]
            positions = [columns.index(key) for key in KEYS[table]]
            def key(row):
                return tuple(row[index] for index in positions)
            existing = {key(row): row for row in result[table]}
            for row in rows:
                identity = key(row)
                if identity in existing and existing[identity] != row:
                    raise PoiseError(f'Captured selected {table} identity conflicts with the comparison target')
                existing[identity] = row
            result[table] = sorted(existing.values())
        return result

    def export_legacy(self, request, existing):
        fields = {'request_id', 'source', 'target', 'mode', 'requirement_ids', 'output_directory'}
        if not isinstance(request, dict) or set(request) != fields:
            raise PoiseError('Legacy export requires its exact explicit request fields')
        if type(request['request_id']) is not str or not request['request_id']:
            raise PoiseError('Legacy export request_id must be a nonempty string')
        for name in ('source', 'target', 'output_directory'):
            if type(request[name]) is not str or not Path(request[name]).is_absolute():
                raise PoiseError(f'Legacy export {name} requires an explicit absolute path')
        if Path(request['output_directory']).resolve() != self.data_directory.resolve():
            raise PoiseError('Legacy export output directory is outside the actual Task data scope')
        identities = request['requirement_ids']
        if (not isinstance(identities, list) or any(type(value) is not str or not value for value in identities)
                or len(set(identities)) != len(identities)
                or request['mode'] not in ('full', 'selected')
                or request['mode'] == 'full' and identities
                or request['mode'] == 'selected' and not identities):
            raise PoiseError('Legacy export mode and explicit distinct requirement IDs are invalid')
        with closing(self.capture(request['source'])) as source, closing(self.capture(request['target'])) as target:
            return self._export_captured(request, existing, source, target)

    def _export_captured(self, request, existing, source, target):
        identities = request['requirement_ids']
        self._validate_capture(source)
        self._validate_capture(target)
        identity = self.export_identity(request['request_id'])
        if existing is not None:
            if (existing.get('export_request') != request or existing.get('source_state') != source.state_hash
                    or target.state_hash not in (existing['target_guard']['before'], existing['target_guard']['after'])):
                raise PoiseError('Legacy export request conflicts with its immutable captured intent')
            return {'reuse': True, 'id': identity}
        desired = deepcopy(target.state)
        if request['mode'] == 'full':
            if target.state['revision'] != 0 or target.state['receipts'] or any(target.state['tables'].values()):
                raise PoiseError('Full legacy export requires an empty supported comparison target')
            desired = deepcopy(source.state)
        else:
            desired['tables'] = self._selected_tables(source, target, identities)
            if desired['tables'] == target.state['tables']:
                return {'unchanged': True}
            if target.state['revision'] == MAX_REVISION:
                raise PoiseError('Selected export target INTEGER revision cannot increment')
            if any(row[0] == request['request_id'] for row in target.state['receipts']):
                raise PoiseError('Legacy export request_id conflicts with existing target history')
            desired['revision'] += 1
            response = {'revision': desired['revision'], 'replayed': False}
            desired['receipts'].append([request['request_id'], 'export:' + digest({
                'request': request, 'source': source.state_hash, 'target': target.state_hash,
            }), encoded(response)])
            desired['receipts'].sort()
        forward = row_changes(target.state['tables'], desired['tables'])
        reverse = row_changes(desired['tables'], target.state['tables'])
        old_receipts = {row[0]: row for row in target.state['receipts']}
        for row in desired['receipts']:
            if row[0] not in old_receipts:
                forward.append('INSERT INTO requirement_requests(request_id,digest,response) VALUES(' +
                               ','.join(map(literal, row)) + ');')
                reverse.insert(0, f'DELETE FROM requirement_requests WHERE request_id={literal(row[0])};')
        forward.append(f"UPDATE requirements_meta SET revision={desired['revision']};")
        reverse.append(f"UPDATE requirements_meta SET revision={target.state['revision']};")
        try:
            with closing(target.preview()) as preview:
                for statement in forward:
                    preview.execute(statement)
                if self.read_state(preview) != desired:
                    raise PoiseError('Captured export literal result does not match the declared target state')
        except sqlite3.Error as exc:
            raise PoiseError(f'Captured export conflicts with target constraints: {exc}; resolve the conflict before export') from exc
        # The captured comparison schema/state is the prerequisite. Working-store
        # history is not a portable dependency of this standalone data artifact.
        return {'id': identity, 'forward': self._header([]) + '\n'.join(forward) + '\n',
                'reverse': '\n'.join(reverse) + '\n', 'export_request': deepcopy(request),
                'source_state': source.state_hash,
                'target_guard': {'before': target.state_hash, 'after': digest(desired)}}

    @staticmethod
    def export_identity(request_id):
        if type(request_id) is not str or not request_id:
            raise PoiseError('Legacy export request_id must be a nonempty string')
        return 'legacy_' + hashlib.sha256(request_id.encode('utf-8')).hexdigest()
