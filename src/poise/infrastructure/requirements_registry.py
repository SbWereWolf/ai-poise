from __future__ import annotations

from contextlib import contextmanager, nullcontext
from copy import deepcopy
import math
from pathlib import Path
import sqlite3

from ..modules.foundation.errors import (
    DomainError, LockUnavailable, PoiseError, SQLiteBoundaryUnavailable, VersionConflict,
)
from ..modules.requirements_registry.domain import RequirementsRegistry
from ..modules.requirements_registry.crud import (
    RequirementsCrudError, validate_query_batch,
)
from .requirements_crud import query_page


class RequirementsStore:
    def __init__(self, database, lock, lock_seconds, poll_seconds, project_fence=None,
                 *, read_only=False, read_wait=None):
        self.project_fence = project_fence
        self.read_only = read_only
        self.read_wait = read_wait
        if read_wait is not None and (type(read_wait) not in (int,float)
                                     or not math.isfinite(read_wait) or read_wait <= 0):
            raise PoiseError('sqlite_read_seconds: требуется конечное положительное число')
        self.database = Path(database)
        self.lock = Path(lock)
        for value in (lock_seconds, poll_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise PoiseError("Requirements store requires positive finite lock limits")
        self.lock_seconds = lock_seconds
        self.poll_seconds = poll_seconds

    @contextmanager
    def _transaction(self):
        from .diagnostic_io import readonly_transaction
        if self.read_wait is None:
            raise PoiseError('Требуется явный sqlite_read_seconds для чтения требований')
        try:
            with (self.project_fence.locked() if self.project_fence is not None else nullcontext()):
                with readonly_transaction(self.database, self.read_wait, self.poll_seconds) as connection:
                    yield connection
        except (OSError, sqlite3.Error) as exc:
            raise PoiseError(
                f'Хранилище требований недоступно: {self.database}: {exc}. '
                'Проверьте пути и примените миграции схемы и данных через poise requirements.'
            ) from exc

    @staticmethod
    def _capture_registry(connection):
        requirements = [
            dict(row)
            for row in connection.execute(
                "SELECT id,level,status,text FROM requirements ORDER BY id"
            )
        ]
        links = [
            {"system": row[0], "application": row[1]}
            for row in connection.execute(
                "SELECT system_id,application_id FROM requirement_links "
                "ORDER BY system_id,application_id"
            )
        ]
        return requirements, links

    @staticmethod
    def _restore_registry(capture):
        return RequirementsRegistry.restore(*capture)

    @classmethod
    def _registry(cls, connection):
        return cls._restore_registry(cls._capture_registry(connection))

    @staticmethod
    def _read_revision(connection):
        rows = connection.execute('SELECT revision FROM requirements_meta').fetchall()
        if len(rows) != 1:
            raise PoiseError('Служебная ревизия требований не инициализирована однозначно; '
                             'примените миграции данных через poise requirements')
        return rows[0][0]

    def revision(self):
        with self._transaction() as connection:
            return self._read_revision(connection)

    def registry(self):
        with self._transaction() as connection:
            capture = self._capture_registry(connection)
        return self._restore_registry(capture)

    def read(self):
        with self._transaction() as connection:
            revision = self._read_revision(connection)
            capture = self._capture_registry(connection)
        return revision, self._restore_registry(capture)

    def query(self, queries, max_items):
        queries = validate_query_batch(queries, max_items)
        context = (None, None)
        try:
            with self._transaction() as connection:
                revision = self._read_revision(connection)
                registry = None
                results = []
                for index, item in enumerate(queries):
                    if item["kind"] in ("entities", "relations"):
                        context = (index, item["id"])
                        value = query_page(connection, item, revision, max_items, context)
                        context = (None, None)
                    else:
                        if registry is None:
                            registry = self._registry(connection)
                        # Only expected evaluator failures belong to this query item.
                        # Registry restoration/storage failures are deliberately outside.
                        try:
                            value = registry.query(item, max_items)
                        except RequirementsCrudError:
                            raise
                        except DomainError as exc:
                            raise RequirementsCrudError("invalid_item", (index, item["id"])) from exc
                    results.append({"id": item["id"], "value": value})
            return {"revision": revision, "results": results}
        except (LockUnavailable, SQLiteBoundaryUnavailable, sqlite3.Error, OSError) as exc:
            raise RequirementsCrudError("store_error", context) from exc

class SqliteRegistrySnapshotProvider:
    """Registry semantics only; CRUD presentation revisions are not Task vetoes."""
    def __init__(self, store):
        self.store = store

    def registry(self):
        return self.store.registry()

    @staticmethod
    def prepare_identity(expected):
        return (tuple((r['id'], r['level'], r['status'], r['text'])
                      for _, r in sorted(expected['requirements'].items())),
                tuple(sorted((r['system'], r['application']) for r in expected['links'])))

    def require_same(self, expected):
        with self.store._transaction() as db:
            actual = (tuple(tuple(r) for r in db.execute(
                'SELECT id,level,status,text FROM requirements ORDER BY id')),
                tuple(tuple(r) for r in db.execute('SELECT system_id,application_id '
                    'FROM requirement_links ORDER BY system_id,application_id')))
        if actual != expected:
            raise DomainError('Task requirements registry changed after creation preflight')


class TaskRequirementsMetadataStore:
    """Immutable Requirements context stored in the existing Task metadata."""

    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def exists(self, task_id):
        from ..modules.tasks.ports import TaskReadSet
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        return inputs.restart_context(task_id)['requirements_snapshot'] is not None

    def create(self, task_id, snapshot, agreement):
        from ..modules.tasks.ports import TaskReadSet
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,)))
        prepared = facade.prepare_requirements_context(inputs, task_id, snapshot, agreement)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.tasks.patch_prepared_requirements(prepared)

    def read(self, task_id):
        from ..modules.tasks.ports import TaskReadSet
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        snapshot = inputs.restart_context(task_id)['requirements_snapshot']
        if snapshot is None:
            raise DomainError('Task requirements snapshot does not exist')
        return snapshot
