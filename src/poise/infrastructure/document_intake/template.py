"""Closed schema-2 registry templates; no hot migrations or replacement."""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
from .schema2 import SQL


class TemplateError(RuntimeError):
    """A template cannot safely be prepared or consumed."""


class TemplatePublicationUnconfirmed(TemplateError):
    """The target exists; preserve it and choose a new free address."""


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _authorize_candidate(action, first, second, database, trigger) -> int:
    # Compile-time admission for literal main-database migrations. No ATTACH,
    # PRAGMA, extension/function execution or temporary/external schema writes.
    allowed = {
        sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_REINDEX,
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
    }
    if action not in allowed or database not in (None, "main"):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_ALTER_TABLE and first != "main":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _schema(connection: sqlite3.Connection) -> tuple:
    objects = connection.execute(
        "SELECT type, name, tbl_name FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    tables = []
    for kind, name, _ in objects:
        if kind != "table":
            continue
        indices = []
        for _, index, unique, origin, partial in connection.execute(f"PRAGMA index_list({_quote(name)})"):
            keys = connection.execute(f"PRAGMA index_xinfo({_quote(index)})").fetchall()
            indices.append((index, unique, origin, partial, keys))
        tables.append((
            name,
            connection.execute(f"PRAGMA table_xinfo({_quote(name)})").fetchall(),
            connection.execute(f"PRAGMA foreign_key_list({_quote(name)})").fetchall(),
            sorted(indices),
        ))
    flags = sorted(row[1:] for row in connection.execute("PRAGMA main.table_list"))
    return objects, tables, flags


def _copy_empty_schema(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    target.set_authorizer(_authorize_candidate)
    definitions = source.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence' ORDER BY name"
    ).fetchall()
    definitions += source.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name"
    ).fetchall()
    for (definition,) in definitions:
        target.execute(definition)


def _insert_program(connection: sqlite3.Connection, table: str, columns: list) -> tuple:
    names = ", ".join(_quote(column[1]) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = connection.execute(
        f"EXPLAIN INSERT INTO {_quote(table)} ({names}) VALUES ({placeholders})",
        [None] * len(columns),
    ).fetchall()
    # Both empty schemas use the same sorted creation order and SQLite runtime.
    # Ignore only debug comments and CHECK error wording, never executable ops.
    return tuple(
        row[:5] + (None if row[1] == "Halt" and row[2] == sqlite3.SQLITE_CONSTRAINT_CHECK else row[5], row[6])
        for row in rows
    )


def _probe_constraints(actual: sqlite3.Connection, expected: sqlite3.Connection, path: Path) -> None:
    """Probe only an empty private schema representation, never live rows."""
    with closing(sqlite3.connect(":memory:")) as probe, closing(sqlite3.connect(":memory:")) as canonical_probe:
        _copy_empty_schema(actual, probe)
        _copy_empty_schema(expected, canonical_probe)
        for (table,) in expected.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence' ORDER BY name"):
            columns = expected.execute(f"PRAGMA table_xinfo({_quote(table)})").fetchall()
            if _insert_program(probe, table, columns) != _insert_program(canonical_probe, table, columns):
                raise TemplateError(f"Registry constraints differ in {table}: {path}; preserve it and use current migrations")
        probe.execute("INSERT INTO registry_schema (singleton, schema_version) VALUES (1, 2)")
        for singleton in (0, 2, -1):
            try:
                probe.execute("INSERT INTO registry_schema (singleton, schema_version) VALUES (?, 2)", (singleton,))
            except sqlite3.IntegrityError:
                continue
            raise TemplateError(f"Registry CHECK constraint is unsupported: {path}; preserve it and prepare a supported registry")
        for table in ("read_events", "content_read_events"):
            columns = actual.execute(f"PRAGMA table_xinfo({_quote(table)})").fetchall()
            names = ", ".join(_quote(column[1]) for column in columns)
            values = [1 if column[1] == "id" else 0 if column[2] == "INTEGER" else "schema-check" for column in columns]
            placeholders = ", ".join("?" for _ in columns)
            probe.execute(f"INSERT INTO {_quote(table)} ({names}) VALUES ({placeholders})", values)
            probe.execute(f"DELETE FROM {_quote(table)} WHERE id=1")
            values[0] = None
            probe.execute(f"INSERT INTO {_quote(table)} ({names}) VALUES ({placeholders})", values)
            if probe.execute(f"SELECT id FROM {_quote(table)}").fetchall() != [(2,)]:
                raise TemplateError(f"Registry AUTOINCREMENT is unsupported: {path}; preserve it and prepare a supported registry")


def validate_template(path: Path) -> None:
    """Read-only validation against the current literal migration owner."""
    _validate(path, empty=True)


def validate_registry(path: Path) -> None:
    """Validate supported live schema without copying or probing live data."""
    _validate(path, empty=False)


def _validate(path: Path, *, empty: bool) -> None:
    kind = "Template" if empty else "Registry"
    try:
        if not path.is_file():
            raise TemplateError(f"{kind} is missing: {path}; prepare it before starting readers")
        if empty:
            for suffix in ("-wal", "-shm", "-journal"):
                if Path(str(path) + suffix).exists():
                    raise TemplateError(f"Template has a sidecar: {path}{suffix}; prepare a closed template")
            with path.open("rb") as source:
                header = source.read(20)
            if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
                raise TemplateError(f"Template has an invalid or WAL-dependent header: {path}; prepare a closed DELETE-mode template")
        with closing(sqlite3.connect(":memory:")) as expected, closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as actual:
            expected.set_authorizer(_authorize_candidate)
            expected.executescript(SQL)
            expected.set_authorizer(None)
            actual.execute("BEGIN")
            if actual.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise TemplateError(f"{kind} integrity failed: {path}; preserve it and prepare a supported registry")
            if empty and actual.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise TemplateError(f"Template is not self-contained: {path}; prepare it in DELETE journal mode")
            if _schema(actual) != _schema(expected):
                raise TemplateError(f"{kind} schema differs from supported schema 2: {path}; preserve it and use current migrations")
            if actual.execute("SELECT singleton, schema_version FROM registry_schema").fetchall() != [(1, 2)]:
                raise TemplateError(f"{kind} schema marker is invalid: {path}; preserve it and use current migrations")
            _probe_constraints(actual, expected, path)
            if empty:
                for (name,) in actual.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
                    if name != "registry_schema" and actual.execute(f"SELECT 1 FROM {_quote(name)} LIMIT 1").fetchone() is not None:
                        raise TemplateError(f"Template contains data in {name}: {path}; prepare an empty template")
    except (sqlite3.Error, OSError, UnicodeError) as error:
        raise TemplateError(f"{kind} validation failed: {path}: {error}; preserve it and prepare a supported registry") from error


def _cleanup_candidate(candidate: Path) -> list[tuple[Path, OSError]]:
    errors = []
    for path in (candidate, *(Path(str(candidate) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            errors.append((path, error))
    return errors


def _cleanup_message(errors) -> str:
    return "".join(f"; private candidate cleanup failed at {path}: {type(error).__name__}: {error}" for path, error in errors)


def prepare_template(target: Path) -> None:
    """Publish a closed schema-2 candidate at a free address."""
    if target.exists():
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=target.name + '.', suffix='.sqlite', dir=target.parent)
    os.close(descriptor)
    candidate = Path(name)
    published = False
    try:
        with closing(sqlite3.connect(candidate)) as connection:
            connection.execute('PRAGMA journal_mode=DELETE')
            connection.set_authorizer(_authorize_candidate)
            connection.executescript(SQL)
            connection.commit()
        validate_template(candidate)
        with candidate.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(candidate, target)
        published = True
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except (OSError, sqlite3.Error, TemplateError) as exc:
        if published:
            raise TemplatePublicationUnconfirmed(str(target)) from exc
        raise TemplateError(str(target)) from exc
    finally:
        errors = _cleanup_candidate(candidate)
        if errors:
            if published:
                raise TemplatePublicationUnconfirmed(str(target))
            raise TemplateError(str(target))


class PreparedTemplate:
    """Typed publication outcomes belong to the installed template adapter."""
    def __init__(self, path):
        self.path = path

    def prepare(self):
        import hashlib
        from ...modules.document_intake.contracts import ReadFailure
        try:
            prepare_template(self.path)
        except FileExistsError as exc:
            raise ReadFailure('template_exists') from exc
        except TemplatePublicationUnconfirmed as exc:
            details = dict(path=str(self.path), schema_version=2,
                           sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                           status='published_durability_unconfirmed')
            raise ReadFailure('template_publication_unconfirmed', details=details) from exc
        except (TemplateError, OSError) as exc:
            raise ReadFailure('template_preparation_failed') from exc
        return dict(path=str(self.path), schema_version=2,
                    sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(), status='prepared')
