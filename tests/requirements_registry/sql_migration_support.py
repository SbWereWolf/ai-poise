"""Independent literal inputs/SQL observations; real public work is the subject."""

from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import sqlite3

from poise.application.work import WorkTools
from poise.interfaces.requirements_registry import execute
from runtime_services.stage_criteria_helpers import boot


FIXTURES = Path(__file__).parent / "fixtures" / "sql_migrations"
SOURCE = Path(__file__).resolve().parents[2]
LIBRARY_TABLES = {"_yoyo_migration", "_yoyo_log", "_yoyo_version", "yoyo_lock"}
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


@dataclass
class Subject:
    project: dict
    tools: WorkTools
    context: dict
    database: Path
    directory: Path
    schema_directory: Path
    initial: Path


def start(project):
    settings = deepcopy(literal("contract.json")["settings"])
    initial = SOURCE / settings["initial_directory"]
    settings["initial_directory"] = str(initial)
    project["cfg"]["requirements_migrations"] = settings
    database = (project["root"] / project["cfg"]["paths"]["state"]
                / project["cfg"]["paths"]["requirements_database"])
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript((FIXTURES / "seed.sql").read_text(encoding="utf-8"))
        connection.commit()
    tools, context = boot(project, initial_execution=True)
    directory = Path(context["worktree"]) / settings["data_directory"]
    schema_directory = Path(context["worktree"]) / settings["schema_directory"]
    return Subject(project, tools, context, database, directory, schema_directory, initial)


def owned(subject, packet):
    response = subject.tools.invoke({
        "operation": "requirements", "input": {"request": deepcopy(packet)}, "messages": [],
    })
    # Existing work telemetry is a separate producer contract, not Requirements data.
    return {key: value for key, value in response.items() if key != "interaction"}


def cli(subject, packet):
    output = io.StringIO()
    code = execute(subject.project["config_path"],
                   io.BytesIO(json.dumps(packet).encode("utf-8")), output)
    return code, json.loads(output.getvalue())


def state(database):
    with closing(sqlite3.connect(database)) as connection:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'")}
        tables = {}
        for table, columns in COLUMNS.items():
            tables[table] = ([] if table not in names else [list(row) for row in connection.execute(
                f"SELECT {','.join(columns)} FROM {table} ORDER BY {','.join(columns)}")])
        revision = (None if "requirements_meta" not in names else
                    connection.execute("SELECT revision FROM requirements_meta").fetchone()[0])
        receipts = ([] if "requirement_requests" not in names else [list(row) for row in connection.execute(
            "SELECT request_id,digest,response FROM requirement_requests ORDER BY request_id")])
        return {"schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
                "tables": tables, "revision": revision, "receipts": receipts}


def state_digest(database):
    raw = json.dumps(state(database), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def applied(database):
    with closing(sqlite3.connect(database)) as connection:
        exists = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name='_yoyo_migration'").fetchone()
        if exists is None:
            return []
        return [row[0] for row in connection.execute(
            "SELECT migration_id FROM _yoyo_migration ORDER BY migration_id")]


def files(subject):
    return {f"{role}/{path.name}": path.read_bytes()
            for role, directory in (("data", subject.directory), ("schema", subject.schema_directory))
            for path in (sorted(directory.iterdir()) if directory.is_dir() else []) if path.is_file()}


def migration_request(subject, action, identities, *, expected=None):
    guard = owned(subject, {"operation": "migrations", "input": {"action": "status"}})
    assert guard == {
        "status": "read_only", "revision": state(subject.database)["revision"],
        "state": state_digest(subject.database), "applied": applied(subject.database),
        "pending": sorted(path.stem for directory in (subject.directory, subject.schema_directory)
                          for path in directory.glob("*.sql")
                          if not path.name.endswith(".rollback.sql") and path.stem not in applied(subject.database)),
    }
    return {"operation": "migrations", "input": {
        "action": action, "migration_ids": list(identities),
        "expected_revision": guard["revision"],
        "expected_state": guard["state"] if expected is None else expected,
    }}


def pair(subject, name, forward, reverse, *, source="data"):
    assert source in ("data", "schema")
    directory = subject.directory if source == "data" else subject.schema_directory
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.sql").write_text(forward, encoding="utf-8")
    (directory / f"{name}.rollback.sql").write_text(reverse, encoding="utf-8")
    return name


def schema(database):
    return [row for row in catalog(database)
            if row[1] not in LIBRARY_TABLES and not row[1].startswith("sqlite_")]


def catalog(database):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        return [list(row) for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name")]


def connected_to(connection, database):
    return any(name == "main" and filename == str(database)
               for _, name, filename in connection.execute("PRAGMA database_list"))
