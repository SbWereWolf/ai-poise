"""Test-owned data and direct SQLite observations for the public CRUD boundary."""
from contextlib import closing
from collections import Counter
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3

from conftest import write_json
from poise.interfaces.requirements_registry import execute


FIXTURES = Path(__file__).parent / "fixtures"
COLUMNS = {
    "requirements": ("id", "level", "status", "text"),
    "domain_areas": ("id", "business_name", "definition"),
    "domain_entities": ("id", "domain_id", "business_name", "code_name", "definition"),
    "domain_actions": ("id", "domain_id", "business_name", "code_name", "definition"),
    "requirement_links": ("system_id", "application_id"),
    "requirement_entities": ("requirement_id", "domain_id", "entity_id"),
    "requirement_actions": ("requirement_id", "domain_id", "action_id"),
}


def literal(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def arrange(project, tmp_path):
    """Use existing disposable project wiring; never a working installation."""
    database = tmp_path / "crud.sqlite"
    lock = tmp_path / "crud.lock"
    config = deepcopy(project["cfg"])
    config["paths"]["requirements_database"] = str(database)
    config["paths"]["requirements_lock"] = str(lock)
    config["batch"]["max_items"] = 4
    config["batch"]["max_input_bytes"] = 4096
    config["limits"]["lock_seconds"] = 0.02
    config["limits"]["lock_poll_seconds"] = 0.001
    write_json(project["config_path"], config)
    with closing(sqlite3.connect(database)) as db:
        db.executescript((FIXTURES / "crud_schema.sql").read_text(encoding="utf-8"))
        for table, rows in literal("crud_seed.json").items():
            columns = COLUMNS[table]
            db.executemany(
                f"INSERT INTO {table}({','.join(columns)}) "
                f"VALUES({','.join('?' for _ in columns)})", rows,
            )
        db.execute("DELETE FROM observed_crud_writes")
        db.commit()
    return database, lock, project["config_path"]


def snapshot(database):
    with closing(sqlite3.connect(database)) as db:
        tables = {}
        identities = {}
        for table, columns in COLUMNS.items():
            key_columns = columns if table.startswith("requirement_") else columns[:1]
            rows = db.execute(
                f"SELECT rowid,{','.join(columns)} FROM {table} "
                f"ORDER BY {','.join(key_columns)}"
            ).fetchall()
            tables[table] = [list(row[1:]) for row in rows]
            identities[table] = {tuple(row[1:1 + len(key_columns)]): row[0] for row in rows}
        return {
            "tables": tables,
            "rowids": identities,
            "revision": db.execute("SELECT revision FROM requirements_meta").fetchall(),
            "receipts": db.execute(
                "SELECT request_id,digest,response FROM requirement_requests ORDER BY request_id"
            ).fetchall(),
            "events": db.execute(
                "SELECT table_name,kind,identity FROM observed_crud_writes ORDER BY rowid"
            ).fetchall(),
        }


def invoke(config_path, request, *, escaped=False):
    raw = json.dumps(request, ensure_ascii=escaped).encode("utf-8")
    return invoke_bytes(config_path, raw)


def invoke_bytes(config_path, raw):
    output = io.StringIO()
    code = execute(config_path, io.BytesIO(raw), output)
    text = output.getvalue()
    # Enforce one JSON value, LF framing and an encodable safe response.
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert text.count("\n") == 1
    text.encode("utf-8")
    return code, json.loads(text)


def assert_effects(before, after, step):
    """Expected mutations originate only in literal fixture rows, not product output."""
    if step["response"]["status"] != "applied":
        assert after == before
        return
    if step["response"]["replayed"]:
        assert after == before
        return
    expected = deepcopy(before["tables"])
    events = list(before["events"])
    for mutation in step["changes"]:
        table, kind, row = mutation["table"], mutation["kind"], mutation["row"]
        key_size = len(COLUMNS[table]) if table.startswith("requirement_") else 1
        key = row[:key_size]
        if kind != "insert":
            expected[table] = [stored for stored in expected[table] if stored[:key_size] != key]
        if kind != "delete":
            expected[table].append(row)
        events.append((table, kind, ":".join(key)))
    for table in expected:
        expected[table].sort(key=lambda row: tuple(row) if table.startswith("requirement_") else row[0])
    assert after["tables"] == expected
    assert after["events"][:len(before["events"])] == before["events"]
    assert Counter(after["events"][len(before["events"]):]) == Counter(events[len(before["events"]):])
    assert after["revision"] == [(step["response"]["revision"],)]
    # Existing and updated identities retain rowid; replacements cannot hide here.
    for table, identities in before["rowids"].items():
        for key, rowid in identities.items():
            if key in after["rowids"][table]:
                assert after["rowids"][table][key] == rowid
    request_id = step["request"]["input"]["request_id"]
    assert [receipt for receipt in after["receipts"] if receipt[0] != request_id] == before["receipts"]
    receipt, = [receipt for receipt in after["receipts"] if receipt[0] == request_id]
    assert receipt[1].startswith("command:")
    stored_response = json.loads(receipt[2])
    assert stored_response == {key: value for key, value in step["response"].items() if key != "status"}
