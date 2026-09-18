from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from .sqlite.transaction import write_transaction
from ..modules.foundation.errors import DomainError, PoiseError, VersionConflict
from ..modules.requirements_registry.domain import RequirementsRegistry


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RequirementsStore:
    SCHEMA_VERSION = 1

    def __init__(self, database, lock, lock_seconds, poll_seconds):
        self.database = Path(database)
        self.lock = Path(lock)
        for value in (lock_seconds, poll_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise PoiseError("Requirements store requires positive finite lock limits")
        self.lock_seconds = lock_seconds
        self.poll_seconds = poll_seconds
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
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
                connection.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
            elif version != self.SCHEMA_VERSION:
                raise PoiseError("Requirements DB schema is incompatible; migration is not performed")

    @contextmanager
    def _transaction(self):
        # Reserve the writer before reading revision/request state. Only the
        # shared BEGIN/COMMIT boundary may retry; this body is never replayed.
        with write_transaction(
            self.database, self.lock, self.lock_seconds, self.poll_seconds
        ) as connection:
            yield connection

    @staticmethod
    def _registry(connection):
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
        digest = hashlib.sha256(_encoded(request).encode()).hexdigest()
        with self._transaction() as connection:
            previous = connection.execute(
                "SELECT digest,response FROM requirement_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if previous is not None:
                if previous["digest"] != digest:
                    raise DomainError("Requirements request_id was used for another request")
                return {**json.loads(previous["response"]), "replayed": True}
            revision = connection.execute("SELECT revision FROM requirements_meta").fetchone()[0]
            if revision != request["expected_revision"]:
                raise VersionConflict("Requirements revision changed")
            registry = self._registry(connection).apply(request["operations"], max_items)
            connection.execute("DELETE FROM requirement_links")
            connection.execute("DELETE FROM requirements")
            for requirement in registry.to_dict()["requirements"].values():
                connection.execute(
                    "INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)",
                    (
                        requirement["id"],
                        requirement["level"],
                        requirement["status"],
                        requirement["text"],
                    ),
                )
            for link in registry.to_dict()["links"]:
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


class TaskRequirementsSnapshotStore:
    """Isolated Task snapshot adapter used outside the versioned Task schema."""

    def __init__(self, database):
        self.database = Path(database)

    def initialize(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database, timeout=0, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if version != 0 or tables - {"task_requirements_snapshots"}:
                connection.execute("ROLLBACK")
                raise PoiseError(
                    "Isolated Task snapshot adapter cannot modify the versioned Task DB"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS task_requirements_snapshots("
                "task_id TEXT PRIMARY KEY, data TEXT NOT NULL, agreement TEXT NOT NULL)"
            )
            connection.execute("COMMIT")

    def create(self, task_id, snapshot, agreement=None):
        if not isinstance(task_id, str) or not task_id:
            raise DomainError("Task snapshot requires a task id")
        self.initialize()
        try:
            with sqlite3.connect(self.database, timeout=0, isolation_level=None) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO task_requirements_snapshots(task_id,data,agreement) VALUES(?,?,?)",
                    (task_id, _encoded(snapshot), _encoded(agreement)),
                )
                connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            raise DomainError("Task requirements snapshot is immutable and already exists") from exc

    def exists(self, task_id):
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            return connection.execute(
                "SELECT 1 FROM task_requirements_snapshots WHERE task_id=?",
                (task_id,),
            ).fetchone() is not None

    def read(self, task_id):
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT data FROM task_requirements_snapshots WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise DomainError("Task requirements snapshot does not exist")
        return json.loads(row[0])


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
