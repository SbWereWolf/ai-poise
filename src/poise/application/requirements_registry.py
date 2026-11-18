from __future__ import annotations

from ..modules.foundation.errors import DomainError, PoiseError
from ..modules.requirements_registry.crud import is_crud_query_batch


class RequirementsQueries:
    """Bounded registry reads independent of mutable Task ownership."""

    def __init__(self, store, max_items):
        if type(max_items) is not int or max_items <= 0:
            raise DomainError("Requirements max_items must be a positive integer")
        self.store = store
        self.max_items = max_items

    def query(self, queries):
        if is_crud_query_batch(queries):
            return self.store.query(queries, self.max_items)
        if not isinstance(queries, list) or not queries or len(queries) > self.max_items:
            raise DomainError("Requirements queries must be bounded and nonempty")
        identifiers = [query.get("id") for query in queries if isinstance(query, dict)]
        if len(identifiers) != len(queries) or any(not isinstance(value, str) or not value for value in identifiers):
            raise DomainError("Every Requirements query requires an id")
        if len(identifiers) != len(set(identifiers)):
            raise DomainError("Requirements query ids must be unique")
        revision, registry = self.store.read()
        results = []
        for query in queries:
            value = registry.query(query, self.max_items)
            results.append({"id": query["id"], "value": value})
        return {"revision": revision, "results": results}


class RequirementsCommands(RequirementsQueries):
    """Canonical changes create literal migrations and apply them through yoyo."""

    def __init__(self, store, max_items, migrations, task_migrations=None):
        super().__init__(store, max_items)
        self.migrations = migrations
        self.task_migrations = task_migrations

    def _mutate(self, operation, request):
        with self.migrations.mutation():
            return getattr(self.migrations, operation)(request)

    def apply(self, request):
        return self._mutate('apply', request)

    def command(self, request):
        return self._mutate('command', request)

    def rollback(self, request):
        return self._mutate('rollback', request)

    def squash(self, request):
        return self._mutate('squash', request)

    def rebase(self, request):
        return self._mutate('rebase', request)

    def export(self, request):
        return self._mutate('export', request)

    def prepare_task_migrations(self):
        try:
            if self.task_migrations is None:
                raise PoiseError('Адаптер завершения Task не настроен')
            with self.task_migrations.mutation():
                return self.task_migrations.prepare_task_migrations()
        except PoiseError as exc:
            return {'status': 'requirements_migration_failed', 'reason': str(exc),
                    'recovery': 'Исправьте SQL миграции требований и повторите передачу этапа.'}
