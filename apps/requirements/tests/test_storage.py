#!/usr/bin/env python3
"""Real SQLite checks through the storage boundary composed by Requirements."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from apps.requirements import shell
from poise.modules.foundation.errors import PoiseError

FIXTURES = ROOT / "apps" / "requirements" / "fixtures"
CONNECT = sqlite3.connect
WAIT_SECONDS = 0.05
POLL_SECONDS = 0.005


def records():
    return json.loads((FIXTURES / "storage-records.json").read_text(encoding="utf-8"))


def identifier(value):
    # Identifiers come only from the test-owned schema/fixture, never a request.
    return '"' + value.replace('"', '""') + '"'


def schema_state(db):
    return {
        "version": db.execute("PRAGMA user_version").fetchone()[0],
        "tables": db.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall(),
    }


def assert_records(test, db, dataset):
    for table, spec in dataset.items():
        columns = ", ".join(identifier(name) for name in spec["columns"])
        actual = db.execute(
            f"SELECT {columns} FROM {identifier(table)} ORDER BY {columns}"
        ).fetchall()
        # Sort the independent literal rows using SQLite itself, including NULL.
        # Neither the product schema nor its serializer supplies expected values.
        with closing(CONNECT(":memory:")) as oracle:
            declarations = ", ".join(identifier(name) for name in spec["columns"])
            oracle.execute(f"CREATE TABLE expected({declarations})")
            placeholders = ", ".join("?" for _ in spec["columns"])
            oracle.executemany(
                f"INSERT INTO expected({columns}) VALUES({placeholders})", spec["rows"]
            )
            expected = oracle.execute(
                f"SELECT {columns} FROM expected ORDER BY {columns}"
            ).fetchall()
        test.assertEqual(actual, expected, table)


def insert_records(db, dataset):
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("DELETE FROM requirements_meta")
    for table, spec in dataset.items():
        columns = ", ".join(identifier(name) for name in spec["columns"])
        placeholders = ", ".join("?" for _ in spec["columns"])
        db.executemany(
            f"INSERT INTO {identifier(table)}({columns}) VALUES({placeholders})",
            spec["rows"],
        )
    db.commit()


def prepare(database, lock):
    # The actual symbol used by shell.execute, not a parallel storage instance.
    return shell.RequirementsStore(database, lock, WAIT_SECONDS, POLL_SECONDS)


def preparation_error(test, database, lock):
    try:
        prepare(database, lock)
    except (PoiseError, sqlite3.Error) as exc:
        reason = str(exc)
        test.assertTrue(reason.strip(), "Preparation error has no description")
        test.assertTrue(
            "sqlite" in reason.lower() or "lock" in reason.lower() or "busy" in reason.lower(),
            reason,
        )
        return reason
    test.fail("Preparation reported success while its required write was blocked")


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="requirements-store-")
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "requirements.sqlite"
        self.lock = Path(temporary.name) / "requirements.lock"

    def test_create(self):
        prepare(self.database, self.lock)
        dataset = records()
        with closing(CONNECT(self.database)) as db:
            actual_tables = {row[0] for row in schema_state(db)["tables"]}
            self.assertFalse(
                set(dataset) - actual_tables,
                f"Prepared store lacks model tables: {sorted(set(dataset) - actual_tables)}",
            )
            try:
                insert_records(db, dataset)
                assert_records(self, db, dataset)
            except sqlite3.Error as exc:
                self.fail(f"Prepared store cannot preserve the model records: {exc}")

    def test_reopen(self):
        dataset = records()
        with closing(CONNECT(self.database)) as db:
            db.executescript((FIXTURES / "storage-schema.sql").read_text(encoding="utf-8"))
            insert_records(db, dataset)
            assert_records(self, db, dataset)
            before = schema_state(db)
        for _ in range(2):
            try:
                prepare(self.database, self.lock)
            except (PoiseError, sqlite3.Error) as exc:
                self.fail(f"Cannot reopen the independent standalone model: {exc}")
            with closing(CONNECT(self.database)) as db:
                self.assertEqual(schema_state(db), before)
                assert_records(self, db, dataset)

    def test_busy_before_write(self):
        with closing(CONNECT(self.database)) as db:
            before = schema_state(db)
        writer = CONNECT(self.database, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute("BEGIN IMMEDIATE")
        try:
            preparation_error(self, self.database, self.lock)
        finally:
            writer.execute("ROLLBACK")
        with closing(CONNECT(self.database)) as db:
            self.assertEqual(schema_state(db), before)

    def test_busy_after_write(self):
        with closing(CONNECT(self.database)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode = DELETE").fetchone()[0], "delete")
            before = schema_state(db)
        reader = CONNECT(self.database, isolation_level=None)
        self.addCleanup(reader.close)
        events = []
        write_states = []
        observation_errors = []

        class ObservedConnection(sqlite3.Connection):
            def __init__(connection, *args, **kwargs):
                super().__init__(*args, **kwargs)
                connection.observing = False
                connection.commit_pending = False
                connection.set_authorizer(connection.observe_transaction)
                connection.set_trace_callback(connection.observe_statement)

            def observe_transaction(connection, action, first, second, database, trigger):
                if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
                    connection.commit_pending = True
                return sqlite3.SQLITE_OK

            def observe_statement(connection, sql):
                # SQLite traces statements from Connection and Cursor APIs alike.
                # The next statement observes the preceding completed write.
                if connection.observing:
                    return
                connection.observing = True
                try:
                    if not write_states:
                        state = schema_state(connection)
                        if state != before:
                            write_states.append(state)
                            events.append(("write_completed", connection.in_transaction))
                            reader.execute("BEGIN")
                            reader.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()
                            events.append(("reader_holds_lock", reader.in_transaction))
                    if connection.commit_pending:
                        events.append(("commit_attempt_with_reader", reader.in_transaction))
                        connection.commit_pending = False
                except Exception as exc:
                    # sqlite3 suppresses callback exceptions; preserve setup errors.
                    observation_errors.append(f"{type(exc).__name__}: {exc}")
                finally:
                    connection.observing = False

        def observed_connect(*args, **kwargs):
            kwargs["factory"] = ObservedConnection
            return CONNECT(*args, **kwargs)

        failure = None
        try:
            with patch.object(sqlite3, "connect", observed_connect):
                try:
                    prepare(self.database, self.lock)
                except (PoiseError, sqlite3.Error) as exc:
                    failure = exc
        finally:
            if reader.in_transaction:
                reader.execute("ROLLBACK")
        self.assertEqual(observation_errors, [], "Fault observation setup failed")
        self.assertTrue(write_states, "Fault injection was not reached: no completed write observed")
        self.assertEqual(events[:2], [("write_completed", True), ("reader_holds_lock", True)])
        self.assertIn(("commit_attempt_with_reader", True), events)
        self.assertIsNotNone(failure, "Preparation reported success after the reader blocked COMMIT")
        reason = str(failure).lower()
        self.assertTrue("lock" in reason or "busy" in reason, str(failure))
        with closing(CONNECT(self.database)) as db:
            self.assertEqual(schema_state(db), before, f"Partial preparation persisted: {events}")

    def test_independent_schema_fixture(self):
        dataset = records()
        with closing(CONNECT(self.database)) as db:
            db.executescript((FIXTURES / "storage-schema.sql").read_text(encoding="utf-8"))
            insert_records(db, dataset)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            assert_records(self, db, dataset)

    def test_oracle_detects_partial_persistence(self):
        with closing(CONNECT(self.database)) as db:
            before = schema_state(db)
            db.execute("CREATE TABLE partial_preparation(value TEXT)")
            db.execute("INSERT INTO partial_preparation(value) VALUES(?)", ("partial",))
            db.commit()
        with closing(CONNECT(self.database)) as db:
            with self.assertRaises(AssertionError):
                self.assertEqual(schema_state(db), before)
            self.assertEqual(
                db.execute("SELECT value FROM partial_preparation").fetchall(), [("partial",)]
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        choices=("create", "reopen", "busy_before_write", "busy_after_write", "independent_schema_fixture", "oracle_detects_partial_persistence"),
    )
    args = parser.parse_args()
    loader = unittest.TestLoader()
    suite = (
        loader.loadTestsFromName(f"test_{args.case}", StorageTests)
        if args.case
        else loader.loadTestsFromTestCase(StorageTests)
    )
    result = unittest.TestResult()
    suite.run(result)
    print(json.dumps({
        "errors": [{"test": test.id(), "reason": reason} for test, reason in result.errors],
        "failures": [{"test": test.id(), "reason": reason} for test, reason in result.failures],
        "tests_run": result.testsRun,
    }, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
