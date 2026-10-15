#!/usr/bin/env python3
"""Observe standalone storage through its actual public provider process."""
from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from test_storage import insert_records, records, schema_state, identifier

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps" / "requirements"
FIXTURES = APP / "fixtures"


class ConsumerStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="requirements-consumer-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.database = self.state / "requirements.sqlite"
        self.config = self.root / "config" / "requirements.json"
        self.config.parent.mkdir()
        self.settings = json.loads((FIXTURES / "requirements-config.json").read_text())
        self.settings["lock"] = {"seconds": 0.05, "poll_seconds": 0.005}
        self.cases = json.loads((FIXTURES / "storage-consumer-cases.json").read_text())
        self.neighbor = self.root / "neighbor.txt"
        self.neighbor.write_bytes(b"independent-neighbor")
        self.save_config()

    def save_config(self):
        self.config.write_text(json.dumps(self.settings, ensure_ascii=False) + "\n")

    def invoke(self, packet=None):
        env = os.environ.copy()
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [str(APP / "bin" / "requirements"), "--config", str(self.config)],
            input=json.dumps(packet or self.cases["registry_packet"], ensure_ascii=False),
            text=True,
            capture_output=True,
            cwd=self.root,
            env=env,
            check=False,
        )

    def rejected(self, process):
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stderr, "")
        try:
            body = json.loads(process.stdout)
        except ValueError:
            self.fail(f"Provider did not produce one JSON response: {process.stdout!r}")
        self.assertEqual(set(body), {"status", "reason"})
        self.assertEqual(body["status"], "rejected")
        self.assertIsInstance(body["reason"], str)
        self.assertTrue(body["reason"].strip())
        return body["reason"]

    def seed_model(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript((FIXTURES / "storage-schema.sql").read_text())
            insert_records(db, records())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def model_state(self):
        with closing(sqlite3.connect(self.database)) as db:
            state = schema_state(db)
            state["rows"] = {}
            present = {name for name, _ in state["tables"]}
            for table, spec in {**records(), **self.cases["metadata"]}.items():
                if table not in present:
                    continue
                columns = ", ".join(identifier(value) for value in spec["columns"])
                state["rows"][table] = db.execute(
                    f"SELECT {columns} FROM {identifier(table)} ORDER BY {columns}"
                ).fetchall()
            return state

    def add_metadata(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript((FIXTURES / "storage-consumer-metadata.sql").read_text())
            for table, spec in self.cases["metadata"].items():
                columns = ", ".join(identifier(value) for value in spec["columns"])
                parameters = ", ".join("?" for _ in spec["columns"])
                db.executemany(
                    f"INSERT INTO {identifier(table)}({columns}) VALUES({parameters})",
                    spec["rows"],
                )
            db.commit()
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def successful(self, process, expected=None):
        self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
        self.assertEqual(process.stderr, "")
        body = json.loads(process.stdout)
        if expected is not None:
            self.assertEqual(body, expected)
        return body

    def query(self, queries):
        return self.invoke({"operation": "query", "input": {"queries": queries}})

    def assert_page(self, key):
        body = self.successful(self.query([self.cases["queries"][key]]))
        self.assertEqual(body, self.cases["query_responses"][key])
        return body

    def update(self):
        self.successful(self.invoke(self.cases["update_packet"]), self.cases["update_response"])

    def test_update_history_and_reopen(self):
        self.seed_model()
        before = self.model_state()
        self.update()
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute(
                "SELECT id,revision,status,text,owner_revision FROM requirement_revisions "
                "WHERE id=? ORDER BY revision", ("APP-A",)
            ).fetchall(), [tuple(row) for row in self.cases["app_history_after_update"]])
            self.assertEqual(db.execute(
                "SELECT id,level,current_revision FROM requirements WHERE id=?", ("APP-A",)
            ).fetchall(), [("APP-A", "application", 3)])
            self.assertEqual(db.execute(
                "SELECT revision,schema_version FROM requirements_meta"
            ).fetchall(), [(3, 1)])
        after = self.model_state()
        self.assertEqual(after["rows"]["system_application_versions"], before["rows"]["system_application_versions"])
        # Each subprocess reopens the same independently seeded file.
        for _ in range(2):
            self.successful(self.invoke(), self.cases["registry_after_update_response"])
        self.assertEqual(self.model_state(), after)
        with closing(sqlite3.connect(self.database)) as db:
            intent, response = db.execute(
                "SELECT intent,response FROM requirement_requests WHERE request_id=?",
                ("consumer-update",),
            ).fetchone()
        self.assertEqual(json.loads(intent), self.cases["update_packet"]["input"])
        self.assertEqual(json.loads(response), {"revision": 3, "replayed": False})

    def test_old_request_replay_after_later_update(self):
        self.seed_model()
        self.update()
        self.successful(self.invoke(self.cases["later_packet"]), self.cases["later_response"])
        before = self.model_state()
        self.successful(self.invoke(self.cases["update_packet"]), {
            "status": "applied", "revision": 3, "replayed": True,
        })
        self.assertEqual(self.model_state(), before)

    def test_object_key_order_preserves_intent(self):
        self.seed_model()
        self.update()
        before = self.model_state()
        packet = self.cases["update_packet"]
        # Reorder every object while keeping the operation sequence unchanged.
        def reversed_objects(value):
            if isinstance(value, dict):
                return {key: reversed_objects(value[key]) for key in reversed(value)}
            if isinstance(value, list):
                return [reversed_objects(item) for item in value]
            return value
        self.successful(self.invoke(reversed_objects(packet)), {
            "status": "applied", "revision": 3, "replayed": True,
        })
        self.assertEqual(self.model_state(), before)

    def test_conflicting_request_preserves_state(self):
        self.seed_model()
        self.update()
        before = self.model_state()
        reason = self.rejected(self.invoke(self.cases["conflict_packet"]))
        self.assertIn("request", reason.lower())
        self.assertEqual(self.model_state(), before)

    def test_stale_revision_preserves_state(self):
        self.seed_model()
        self.update()
        before = self.model_state()
        reason = self.rejected(self.invoke(self.cases["stale_packet"]))
        self.assertIn("revision", reason.lower())
        self.assertEqual(self.model_state(), before)

    def test_pages_have_current_values_without_duplicates(self):
        self.seed_model()
        before = self.model_state()
        item = deepcopy(self.cases["queries"]["apps_first"])
        first = self.successful(self.query([item]))
        self.assertEqual(first, self.cases["first_app_response"])
        item["page"]["after"] = first["results"][0]["value"]["next"]
        second = self.successful(self.query([item]))
        self.assertEqual(second, self.cases["second_app_response"])
        self.assertEqual(self.model_state(), before)

    def test_relation_pages_preserve_binary_order(self):
        self.seed_model()
        before = self.model_state()
        item = deepcopy(self.cases["queries"]["system_links_first"])
        first = self.successful(self.query([item]), self.cases["first_link_response"])
        item["page"]["after"] = first["results"][0]["value"]["next"]
        self.successful(self.query([item]), self.cases["second_link_response"])
        self.assertEqual(self.model_state(), before)

    def test_stale_cursor_rejected_without_writes(self):
        self.seed_model()
        item = deepcopy(self.cases["queries"]["apps_first"])
        first = self.successful(self.query([item]))
        self.assertEqual(first, self.cases["first_app_response"])
        self.update()
        before = self.model_state()
        item["page"]["after"] = first["results"][0]["value"]["next"]
        reason = self.rejected(self.query([item])).lower()
        self.assertTrue("cursor" in reason or "revision" in reason, reason)
        self.assertEqual(self.model_state(), before)

    def test_active_many_to_many_links_both_directions(self):
        self.seed_model()
        before = self.model_state()
        self.assert_page("system_links")
        self.assert_page("application_links")
        self.assertEqual(self.model_state(), before)

    def test_metadata_entities_and_relations(self):
        self.seed_model()
        self.add_metadata()
        before = self.model_state()
        for key in ("areas", "entities", "actions", "requirement_entities",
                    "entity_requirements", "requirement_actions", "action_requirements"):
            with self.subTest(query=key):
                self.assert_page(key)
        self.assertEqual(self.model_state(), before)

    def test_mixed_registry_and_typed_queries_both_orders(self):
        self.seed_model()
        for metadata in (False, True):
            if metadata:
                self.add_metadata()
            before = self.model_state()
            for case in self.cases["mixed_cases"]:
                with self.subTest(metadata=metadata, order=[q["id"] for q in case["queries"]]):
                    self.successful(self.query(case["queries"]), case["response"])
                    self.assertEqual(self.model_state(), before)

    def test_model_only_metadata_failure_then_available_query(self):
        self.seed_model()
        before = self.model_state()
        registry = self.cases["registry_packet"]["input"]["queries"][0]
        self.rejected(self.query([registry, self.cases["queries"]["areas"]]))
        self.assertEqual(self.model_state(), before)
        self.assert_page("apps")
        self.assertEqual(self.model_state(), before)

    def test_fresh_store_prepares_model_and_metadata(self):
        self.successful(self.invoke(), self.cases["empty_registry_response"])
        with closing(sqlite3.connect(self.database)) as db:
            state = schema_state(db)
            self.assertEqual(state["version"], 1)
            self.assertEqual({name for name, _ in state["tables"]}, set(records()) | set(self.cases["metadata"]))
            self.assertEqual(db.execute(
                "SELECT revision,schema_version FROM requirements_meta"
            ).fetchall(), [(0, 1)])
        for key in ("areas", "entities", "actions"):
            body = self.successful(self.query([self.cases["queries"][key]]))
            self.assertEqual(body, self.cases["empty_page_response"])

    def test_correct_bootstrap_creates_initial_history(self):
        (self.root / "bootstrap.json").write_text(json.dumps(self.cases["bootstrap"], ensure_ascii=False))
        self.successful(self.invoke(self.cases["bootstrap_packet"]), self.cases["bootstrap_response"])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT id,revision,status,text,owner_revision FROM requirement_revisions ORDER BY id,revision"
            ).fetchall(), [tuple(row) for row in self.cases["bootstrap_history"]])
            self.assertEqual(db.execute(
                "SELECT system_id,application_id,valid_from,valid_to FROM system_application_versions"
            ).fetchall(), [("BOOT-S", "BOOT-A", 1, None)])

    def test_valid_public_query_control(self):
        process = self.invoke()
        self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
        self.assertEqual(process.stderr, "")
        self.assertEqual(json.loads(process.stdout), self.cases["empty_registry_response"])

    def test_non_sqlite_preparation_rejected(self):
        self.database.write_bytes(b"literal content which is not a SQLite database")
        before = self.database.read_bytes()
        process = self.invoke()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.neighbor.read_bytes(), b"independent-neighbor")
        reason = self.rejected(process).lower()
        self.assertTrue("database" in reason or "sqlite" in reason, reason)

    def test_occupied_parent_preparation_rejected(self):
        occupied = self.state / "occupied"
        occupied.write_bytes(b"ordinary-file-at-required-parent")
        self.settings["paths"]["database"] = "occupied/requirements.sqlite"
        self.save_config()
        process = self.invoke()
        self.assertEqual(occupied.read_bytes(), b"ordinary-file-at-required-parent")
        self.assertEqual(self.neighbor.read_bytes(), b"independent-neighbor")
        reason = self.rejected(process)
        self.assertIn("occupied", reason)

    def test_unsupported_own_format_preserved(self):
        self.seed_model()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA user_version = 42")
            db.commit()
        before = self.model_state()
        process = self.invoke()
        self.assertEqual(self.model_state(), before)
        reason = self.rejected(process).lower()
        self.assertTrue(any(word in reason for word in ("schema", "format", "version")), reason)

    def test_required_owner_revision_missing_preserved(self):
        self.seed_model()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM requirements_meta")
            db.commit()
        before = self.model_state()
        process = self.invoke()
        self.assertEqual(self.model_state(), before)
        reason = self.rejected(process).lower()
        self.assertTrue("revision" in reason or "meta" in reason, reason)

    def test_operation_order_changes_intent(self):
        self.seed_model()
        case = json.loads((FIXTURES / "storage-operation-order.json").read_text())
        self.successful(self.invoke(case["packet"]), case["applied_response"])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute(
                "SELECT r.id,v.status,v.text FROM requirements r "
                "JOIN requirement_revisions v ON v.id=r.id "
                "AND v.revision=r.current_revision WHERE r.id=?", ("SYS-B",)
            ).fetchall(), [tuple(row) for row in case["current_requirement"]])
            self.assertEqual(db.execute(
                "SELECT revision,schema_version FROM requirements_meta"
            ).fetchall(), [(3, 1)])
            saved = db.execute(
                "SELECT intent,response FROM requirement_requests WHERE request_id=?",
                ("consumer-operation-order",),
            ).fetchall()
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0][0]), case["packet"]["input"])
        self.assertEqual(json.loads(saved[0][1]), {"revision": 3, "replayed": False})
        before = self.model_state()
        self.successful(self.invoke(case["packet"]), case["replayed_response"])
        self.assertEqual(self.model_state(), before)
        reordered = deepcopy(case["packet"])
        reordered["input"]["operations"].reverse()
        reason = self.rejected(self.invoke(reordered))
        self.assertIn("request", reason.lower())
        self.assertEqual(self.model_state(), before)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case")
    args = parser.parse_args()
    loader = unittest.TestLoader()
    suite = (
        loader.loadTestsFromName(f"test_{args.case}", ConsumerStorageTests)
        if args.case else loader.loadTestsFromTestCase(ConsumerStorageTests)
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
