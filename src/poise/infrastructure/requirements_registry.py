from __future__ import annotations

from contextlib import contextmanager, nullcontext
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from .sqlite.transaction import write_transaction
from .requirements_migrations import schema_2, validate_schema
from ..modules.foundation.errors import (
    DomainError, LockUnavailable, PoiseError, SQLiteBoundaryUnavailable, VersionConflict,
)
from ..modules.requirements_registry.domain import RequirementsRegistry
from ..modules.requirements_registry.crud import (
    RequirementsCrudError, require, validate_command, validate_query_batch,
)
from .requirements_crud import execute_operation, query_page


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RequirementsStore:
    SCHEMA_VERSION = 2

    def __init__(self, database, lock, lock_seconds, poll_seconds, project_fence=None):
        self._configure(database, lock, lock_seconds, poll_seconds, project_fence)
        with self._transaction() as connection:
            self._initialize_legacy(connection)

    def _configure(self, database, lock, lock_seconds, poll_seconds, project_fence=None):
        self._backend = None
        self.project_fence = project_fence
        self.database = Path(database)
        self.lock = Path(lock)
        for value in (lock_seconds, poll_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise PoiseError("Requirements store requires positive finite lock limits")
        self.lock_seconds = lock_seconds
        self.poll_seconds = poll_seconds

    @classmethod
    def standalone(cls, database, lock, lock_seconds, poll_seconds, backend):
        """Compose an explicitly selected persistence backend with this owner."""
        store = cls.__new__(cls)
        store._configure(database, lock, lock_seconds, poll_seconds)
        store._backend = backend
        with store._transaction() as connection:
            backend.initialize(connection)
        return store

    def _initialize_legacy(self, connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                raise PoiseError("Unknown Requirements DB; automatic migration is forbidden")
            connection.execute("CREATE TABLE requirements_meta(revision INTEGER NOT NULL)")
            connection.execute("INSERT INTO requirements_meta(revision) VALUES(0)")
            connection.execute(
                "CREATE TABLE requirements("
                "id TEXT PRIMARY KEY, level TEXT NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE requirement_links("
                "system_id TEXT NOT NULL REFERENCES requirements(id), "
                "application_id TEXT NOT NULL REFERENCES requirements(id), "
                "PRIMARY KEY(system_id,application_id))"
            )
            connection.execute(
                "CREATE TABLE requirement_requests("
                "request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, response TEXT NOT NULL)"
            )
            schema_2(connection)
        elif version == 1:
            raise PoiseError(
                'Requirements DB schema 1 requires explicit migration. Send '
                '{"operation":"migrate","input":{}} to python -m poise requirements '
                '--config <selected-project-config> using the same project manifest; '
                'automatic migration is forbidden.'
            )
        elif version != self.SCHEMA_VERSION:
            raise PoiseError("Requirements DB schema is incompatible; migration is not performed")

    @staticmethod
    def migrate(database, lock, lock_seconds, poll_seconds):
        database, lock = Path(database), Path(lock)
        if not database.is_file():
            raise PoiseError("Requirements migration requires an existing database")
        try:
            with write_transaction(database, lock, lock_seconds, poll_seconds) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (1, 2):
                    raise PoiseError(f"Requirements DB schema {version} cannot migrate to 2")
                validate_schema(connection, version)
                if version == 1:
                    schema_2(connection)
                return {"status": "migrated", "from_version": version,
                        "to_version": 2, "changed": version == 1}
        except (sqlite3.Error, ValueError) as exc:
            raise PoiseError(f"Requirements migration failed: {exc}") from exc

    @contextmanager
    def _transaction(self):
        # Reserve the writer before reading revision/request state. Only the
        # shared BEGIN/COMMIT boundary may retry; this body is never replayed.
        try:
            with (self.project_fence.locked() if self.project_fence is not None else nullcontext()):
                with write_transaction(
                    self.database, self.lock, self.lock_seconds, self.poll_seconds
                ) as connection:
                    yield connection
        except (sqlite3.Error, OSError) as exc:
            if self._backend is not None:
                raise PoiseError(f"Requirements database operation failed: {exc}") from exc
            raise

    def _registry(self, connection):
        if self._backend is not None:
            rows = self._backend.registry_rows(connection)
            return RequirementsRegistry.restore(rows['requirements'], rows['links'])
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
        return RequirementsRegistry.restore(requirements, links)

    def revision(self):
        with self._transaction() as connection:
            return connection.execute("SELECT revision FROM requirements_meta").fetchone()[0]

    def registry(self):
        with self._transaction() as connection:
            return self._registry(connection)

    def read(self):
        with self._transaction() as connection:
            revision = connection.execute("SELECT revision FROM requirements_meta").fetchone()[0]
            return revision, self._registry(connection)

    @staticmethod
    def read_bootstrap(path):
        source = Path(path)
        try:
            return json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DomainError(f"Requirements bootstrap cannot be read: {exc}") from exc

    def query(self, queries, max_items):
        queries = validate_query_batch(queries, max_items)
        context = (None, None)
        try:
            with self._transaction() as connection:
                revision = connection.execute("SELECT revision FROM requirements_meta").fetchone()[0]
                if self._backend is not None:
                    self._backend.prepare_queries(connection)
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

    def command(self, request, max_items):
        request = validate_command(request, max_items)
        digest = "command:" + hashlib.sha256(_encoded(request).encode("utf-8")).hexdigest()
        context = (None, None)
        try:
            with self._transaction() as connection:
                previous = connection.execute(
                    "SELECT digest,response FROM requirement_requests WHERE request_id=?",
                    (request["request_id"],),
                ).fetchone()
                if previous is not None:
                    require(previous["digest"] == digest, "request_conflict")
                    response = {**json.loads(previous["response"]), "replayed": True}
                else:
                    revision = connection.execute("SELECT revision FROM requirements_meta").fetchone()[0]
                    require(revision == request["expected_revision"], "revision_conflict")
                    results = []
                    for index, item in enumerate(request["operations"]):
                        context = (index, item["id"])
                        try:
                            count = execute_operation(connection, item, context)
                        except sqlite3.IntegrityError as exc:
                            raise RequirementsCrudError("integrity_conflict", context) from exc
                        results.append({"id": item["id"], "affected_rows": count})
                        context = (None, None)
                    revision += 1
                    response = {"revision": revision, "replayed": False, "results": results}
                    connection.execute("UPDATE requirements_meta SET revision=?", (revision,))
                    connection.execute(
                        "INSERT INTO requirement_requests(request_id,digest,response) VALUES(?,?,?)",
                        (request["request_id"], digest, _encoded(response)),
                    )
            # Exit may fail after confirmed COMMIT; retain the receipt for exact replay.
            return deepcopy(response)
        except (LockUnavailable, SQLiteBoundaryUnavailable, sqlite3.Error, OSError) as exc:
            raise RequirementsCrudError("store_error", context) from exc

    def apply(self, request, max_items):
        if not isinstance(request, dict) or set(request) != {
            "request_id",
            "expected_revision",
            "operations",
        }:
            raise DomainError("Requirements apply requires request_id, expected_revision and operations")
        request_id = request["request_id"]
        if not isinstance(request_id, str) or not request_id:
            raise DomainError("Requirements request_id must be nonempty")
        if type(request["expected_revision"]) is not int or request["expected_revision"] < 0:
            raise DomainError("Requirements expected_revision must be nonnegative")
        digest = (hashlib.sha256(_encoded(request).encode()).hexdigest()
                  if self._backend is None else None)
        with self._transaction() as connection:
            if self._backend is not None:
                previous = self._backend.previous_request(connection, 'registry.apply', request_id)
            else:
                previous = connection.execute(
                    "SELECT digest,response FROM requirement_requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            if previous is not None:
                matches = (previous['intent'] == request if self._backend is not None
                           else previous['digest'] == digest)
                if not matches:
                    raise DomainError("Requirements request_id was used for another request")
                saved = (previous['response'] if self._backend is not None
                         else json.loads(previous['response']))
                return {**saved, "replayed": True}
            revision = connection.execute("SELECT revision FROM requirements_meta").fetchone()[0]
            if revision != request["expected_revision"]:
                raise VersionConflict("Requirements revision changed")
            current = self._registry(connection)
            registry = current.apply(request["operations"], max_items)
            before = current.to_dict()
            after = registry.to_dict()
            if self._backend is not None:
                revision += 1
                response = {'revision': revision, 'replayed': False}
                self._backend.persist_apply(
                    connection, before, after, revision, request_id, request, response,
                )
                return deepcopy(response)
            for requirement in after["requirements"].values():
                if before["requirements"].get(requirement["id"]) == requirement:
                    continue
                connection.execute(
                    "INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET status=excluded.status,text=excluded.text",
                    (
                        requirement["id"],
                        requirement["level"],
                        requirement["status"],
                        requirement["text"],
                    ),
                )
            existing_links = {
                (link["system"], link["application"]) for link in before["links"]
            }
            for link in after["links"]:
                if (link["system"], link["application"]) in existing_links:
                    continue
                connection.execute(
                    "INSERT INTO requirement_links(system_id,application_id) VALUES(?,?)",
                    (link["system"], link["application"]),
                )
            revision += 1
            connection.execute("UPDATE requirements_meta SET revision=?", (revision,))
            response = {"revision": revision, "replayed": False}
            connection.execute(
                "INSERT INTO requirement_requests(request_id,digest,response) VALUES(?,?,?)",
                (request_id, digest, _encoded(response)),
            )
            return deepcopy(response)


class TaskRequirementsMetadataStore:
    """Immutable Requirements context stored in the existing Task metadata."""

    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def exists(self, task_id):
        with self.unit_of_work() as uow:
            return uow.tasks.restart_context(task_id)["requirements_snapshot"] is not None

    def create(self, task_id, snapshot, agreement):
        with self.unit_of_work() as uow:
            uow.tasks.publish_requirements_context(task_id, snapshot, agreement)

    def read(self, task_id):
        with self.unit_of_work() as uow:
            snapshot = uow.tasks.restart_context(task_id)["requirements_snapshot"]
        if snapshot is None:
            raise DomainError("Task requirements snapshot does not exist")
        return snapshot
