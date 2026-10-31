"""Integration subject: public Requirements adapter, literal v1 DB and direct SQLite oracles."""
from contextlib import contextmanager
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
from threading import Barrier, Thread

import pytest

from poise.interfaces.requirements_registry import execute
from tests.conftest import write_json


@contextmanager
def connection(database):
    db = sqlite3.connect(database)
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def invoke(config, operation="migrate", body=None):
    output = io.StringIO()
    code = execute(config, io.BytesIO(json.dumps({
        "operation": operation, "input": {} if body is None else body,
    }).encode()), output)
    return code, json.loads(output.getvalue())


def old_rows(database):
    with connection(database) as db:
        return (
            db.execute("SELECT id,level,status,text FROM requirements ORDER BY id").fetchall(),
            db.execute(
                "SELECT system_id,application_id FROM requirement_links ORDER BY system_id,application_id"
            ).fetchall(),
            db.execute("SELECT revision FROM requirements_meta").fetchall(),
            db.execute(
                "SELECT request_id,digest,response FROM requirement_requests ORDER BY request_id"
            ).fetchall(),
        )


def assert_empty_dictionary(db):
    for table in ("domain_areas", "domain_entities", "domain_actions",
                  "requirement_entities", "requirement_actions"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


@pytest.fixture
def legacy(project, tmp_path):
    database = tmp_path / "legacy.sqlite"
    with connection(database) as db:
        db.executescript((Path(__file__).parent / "fixtures/schema_v1.sql").read_text())
        db.execute("INSERT INTO requirements_meta(revision) VALUES(?)", (7,))
        db.executemany("INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)", [
            ("SYS", "system", "current", "Согласовать язык области."),
            ("APP", "application", "future", "Хранить определения."),
        ])
        db.execute(
            "INSERT INTO requirement_links(system_id,application_id) VALUES(?,?)", ("SYS", "APP"),
        )
        db.execute(
            "INSERT INTO requirement_requests(request_id,digest,response) VALUES(?,?,?)",
            ("historical", "540b88f9c831853cdf1a2dd96580e94f59a8ff34f3835e336dfb5a01b8df51fc",
             '{"revision":1,"replayed":false}'),
        )
    cfg = deepcopy(project["cfg"])
    cfg["paths"]["requirements_database"] = str(database)
    cfg["paths"]["requirements_lock"] = str(tmp_path / "legacy.lock")
    config = write_json(project["root"] / "migration-project.json", cfg)
    return config, database


def migrate(legacy):
    config, database = legacy
    code, reply = invoke(config)
    assert code == 0, reply
    assert reply == {"status": "migrated", "from_version": 1, "to_version": 2, "changed": True}
    return config, database


def test_populated_v1_preserves_all_old_values_and_creates_exact_tables(legacy):
    _, database = legacy
    before = old_rows(database)
    migrate(legacy)
    assert old_rows(database) == before
    with connection(database) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert_empty_dictionary(db)
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall() == [
            ("domain_actions",), ("domain_areas",), ("domain_entities",),
            ("requirement_actions",), ("requirement_entities",),
            ("requirement_links",), ("requirement_requests",),
            ("requirements",), ("requirements_meta",),
        ]


@pytest.mark.parametrize("table,item", [("domain_entities", "E3"), ("domain_actions", "A3")])
@pytest.mark.parametrize("business,code", [("Account", "different"), ("Different", "Account")])
def test_entity_names_are_unique_within_area(vocabulary, table, item, business, code):
    _, database = vocabulary
    if table == "domain_actions":
        business = "Open" if business == "Account" else business
        code = "open" if code == "Account" else code
    with connection(database) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                f"INSERT INTO {table}(id,domain_id,business_name,code_name,definition) VALUES(?,?,?,?,?)",
                (item, "D1", business, code, "Definition."),
            )


@pytest.mark.parametrize("table,column,valid,foreign", [
    ("requirement_entities", "entity_id", "E1", "E2"),
    ("requirement_actions", "action_id", "A1", "A2"),
])
def test_links_enforce_area_membership_uniqueness_and_immediate_requirement_fk(
    vocabulary, table, column, valid, foreign,
):
    _, database = vocabulary
    statement = f"INSERT INTO {table}(requirement_id,domain_id,{column}) VALUES(?,?,?)"
    with connection(database) as db:
        for values in [("APP", "D1", foreign), ("missing", "D1", valid), ("APP", "D1", "missing")]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement, values)
        db.execute(statement, ("APP", "D1", valid))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(statement, ("APP", "D1", valid))
        db.execute("DELETE FROM requirement_links WHERE application_id=?", ("APP",))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM requirements WHERE id=?", ("APP",))


@pytest.mark.parametrize("field", ["id", "business_name", "definition"])
@pytest.mark.parametrize("bad", [None, "", " ", "\t\n", "\u00a0\u2003"])
def test_area_fields_require_nonblank_values(vocabulary, field, bad):
    _, database = vocabulary
    values = {"id": "D3", "business_name": "Support", "definition": "Definition."}
    values[field] = bad
    with connection(database) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO domain_areas(id,business_name,definition) VALUES(?,?,?)",
                       tuple(values.values()))


@pytest.mark.parametrize("table", ["domain_entities", "domain_actions"])
@pytest.mark.parametrize("field", ["id", "domain_id", "business_name", "code_name", "definition"])
@pytest.mark.parametrize("bad", [None, "", "\t\u00a0"])
def test_name_fields_require_nonblank_values(vocabulary, table, field, bad):
    _, database = vocabulary
    values = {"id": "N3", "domain_id": "D1", "business_name": "Other",
              "code_name": "Other", "definition": "Definition."}
    values[field] = bad
    with connection(database) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                f"INSERT INTO {table}(id,domain_id,business_name,code_name,definition) VALUES(?,?,?,?,?)",
                tuple(values.values()),
            )


def test_names_preserve_case_and_reject_surrounding_whitespace(vocabulary):
    _, database = vocabulary
    with connection(database) as db:
        db.execute("INSERT INTO domain_areas(id,business_name,definition) VALUES(?,?,?)",
                   ("d1", "sales", "  Meaningful definition.  "))
        for identifier, name in [(" D3", "Support"), ("D3", "Support\t"), ("D3\u00a0", "Support")]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO domain_areas(id,business_name,definition) VALUES(?,?,?)",
                           (identifier, name, "Definition."))


def test_empty_v1_migrates_without_seeding_requirements(legacy):
    _, database = legacy
    with connection(database) as db:
        db.execute("DELETE FROM requirement_links")
        db.execute("DELETE FROM requirements")
        db.execute("DELETE FROM requirement_requests")
        db.execute("UPDATE requirements_meta SET revision=?", (0,))
    before = old_rows(database)
    migrate(legacy)
    assert old_rows(database) == before
    with connection(database) as db:
        assert_empty_dictionary(db)


def test_fresh_database_initializes_v2_via_ordinary_public_query(legacy):
    config, database = legacy
    database.unlink()  # Exact test-owned fixture, not a working database.
    code, reply = invoke(config, "query", {"queries": [{"id": "r", "kind": "registry"}]})
    assert code == 0, reply
    assert reply["revision"] == 0
    with connection(database) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert_empty_dictionary(db)


def test_ordinary_read_rejects_v1_without_silent_migration(legacy):
    config, database = legacy
    before = old_rows(database)
    code, reply = invoke(config, "query", {"queries": [{"id": "r", "kind": "registry"}]})
    assert code == 2
    assert reply["status"] == "rejected"
    assert old_rows(database) == before
    with connection(database) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (1,)


@pytest.mark.parametrize("body", [{"unexpected": True}, [], "invalid"])
def test_invalid_migration_input_does_not_initialize_missing_database(legacy, body):
    config, database = legacy
    database.unlink()
    code, reply = invoke(config, body=body)
    assert code == 2 and reply["status"] == "rejected"
    assert not database.exists()


def test_explicit_migration_requires_existing_database(legacy):
    config, database = legacy
    database.unlink()
    code, reply = invoke(config)
    assert code == 2 and reply["status"] == "rejected"
    assert not database.exists()


@pytest.mark.parametrize("version", [0, 3, 99])
def test_unknown_versions_reject_without_database_writes(legacy, version):
    config, database = legacy
    with connection(database) as db:
        db.execute(f"PRAGMA user_version={version}")  # Fixed test-owned version values.
    before = database.read_bytes()
    code, reply = invoke(config)
    assert code == 2 and reply["status"] == "rejected"
    assert reply["reason"] == f"Requirements DB schema {version} cannot migrate to 2"
    assert database.read_bytes() == before


def test_mid_ddl_failure_rolls_back_added_tables_version_and_all_values(legacy, monkeypatch):
    config, database = legacy
    with connection(database) as db:
        db.execute("CREATE TABLE domain_entities(sentinel TEXT)")
        db.execute("INSERT INTO domain_entities(sentinel) VALUES(?)", ("preserve",))
        before = list(db.iterdump())
    executed = []
    connect = sqlite3.connect
    class Observed(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.startswith("CREATE TABLE domain_areas"):
                executed.append("domain_areas created")
            return result
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: connect(*a, **k, factory=Observed))
    code, reply = invoke(config)
    assert code == 2 and reply["status"] == "rejected"
    assert "domain_entities already exists" in reply["reason"]
    assert executed == ["domain_areas created"]
    with connection(database) as db:
        assert list(db.iterdump()) == before
        assert db.execute("PRAGMA user_version").fetchone() == (1,)


@pytest.fixture
def vocabulary(project, tmp_path):
    from requirements_registry.migration_support import create_database
    database = tmp_path / 'vocabulary.sqlite'
    create_database(database)
    cfg = deepcopy(project['cfg'])
    cfg['paths']['requirements_database'] = str(database)
    cfg['paths']['requirements_lock'] = str(tmp_path / 'vocabulary.lock')
    config = write_json(project['root'] / 'vocabulary-project.json', cfg)
    with connection(database) as db:
        db.execute('UPDATE requirements_meta SET revision=?', (7,))
        db.executemany('INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)', [
            ('SYS', 'system', 'current', 'Согласовать язык области.'),
            ('APP', 'application', 'future', 'Хранить определения.'),
        ])
        db.execute('INSERT INTO requirement_links(system_id,application_id) VALUES(?,?)', ('SYS', 'APP'))
        db.execute('INSERT INTO requirement_requests(request_id,digest,response) VALUES(?,?,?)',
                   ('historical', '540b88f9c831853cdf1a2dd96580e94f59a8ff34f3835e336dfb5a01b8df51fc',
                    '{"revision":1,"replayed":false}'))
        db.executemany("INSERT INTO domain_areas(id,business_name,definition) VALUES(?,?,?)", [
            ("D1", "Sales", "Продажи."), ("D2", "Finance", "Финансы."),
        ])
        db.executemany(
            "INSERT INTO domain_entities(id,domain_id,business_name,code_name,definition) "
            "VALUES(?,?,?,?,?)", [
                ("E1", "D1", "Account", "Account", "Покупатель."),
                ("E2", "D2", "Account", "Account", "Учётная запись."),
            ],
        )
        db.executemany(
            "INSERT INTO domain_actions(id,domain_id,business_name,code_name,definition) "
            "VALUES(?,?,?,?,?)", [
                ("A1", "D1", "Open", "open", "Открыть продажу."),
                ("A2", "D2", "Open", "open", "Открыть счёт."),
            ],
        )
    return config, database


def test_one_requirement_combines_areas_and_exact_replay_preserves_vocabulary(vocabulary):
    config, database = vocabulary
    with connection(database) as db:
        db.executemany(
            "INSERT INTO requirement_entities(requirement_id,domain_id,entity_id) VALUES(?,?,?)",
            [("APP", "D1", "E1"), ("APP", "D2", "E2")],
        )
        db.executemany(
            "INSERT INTO requirement_actions(requirement_id,domain_id,action_id) VALUES(?,?,?)",
            [("APP", "D1", "A1"), ("APP", "D2", "A2")],
        )
        before = list(db.iterdump())
    code, reply = invoke(config)
    assert code == 0
    assert reply == {"status": "migrated", "from_version": 2, "to_version": 2, "changed": False}
    with connection(database) as db:
        assert list(db.iterdump()) == before
        assert db.execute(
            "SELECT a.business_name,e.business_name FROM requirement_entities r "
            "JOIN domain_areas a ON a.id=r.domain_id "
            "JOIN domain_entities e ON e.id=r.entity_id WHERE r.requirement_id=? "
            "ORDER BY a.business_name", ("APP",),
        ).fetchall() == [("Finance", "Account"), ("Sales", "Account")]


def test_public_apply_and_replay_preserve_dictionary_links(vocabulary):
    config, database = vocabulary
    with connection(database) as db:
        db.execute(
            "INSERT INTO requirement_entities(requirement_id,domain_id,entity_id) VALUES(?,?,?)",
            ("APP", "D1", "E1"),
        )
        db.execute(
            "INSERT INTO requirement_actions(requirement_id,domain_id,action_id) VALUES(?,?,?)",
            ("APP", "D2", "A2"),
        )
    request = {"request_id": "change", "expected_revision": 7, "operations": [{
        "kind": "put_requirement", "requirement": {
            "id": "APP", "level": "application", "status": "current", "text": "Хранить имена.",
        },
    }]}
    assert invoke(config, "apply", request) == (0, {"status": "applied", "revision": 8, "replayed": False})
    assert invoke(config, "apply", request) == (0, {"status": "applied", "revision": 8, "replayed": True})
    with connection(database) as db:
        assert db.execute(
            "SELECT requirement_id,domain_id,entity_id FROM requirement_entities"
        ).fetchall() == [("APP", "D1", "E1")]
        assert db.execute(
            "SELECT requirement_id,domain_id,action_id FROM requirement_actions"
        ).fetchall() == [("APP", "D2", "A2")]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("table,column,name", [
    ("requirement_entities", "entity_id", "E1"),
    ("requirement_actions", "action_id", "A1"),
])
def test_link_columns_are_not_nullable_and_system_requirements_can_link(vocabulary, table, column, name):
    _, database = vocabulary
    sql = f"INSERT INTO {table}(requirement_id,domain_id,{column}) VALUES(?,?,?)"
    with connection(database) as db:
        for values in [(None, "D1", name), ("SYS", None, name), ("SYS", "D1", None)]:
            with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
                db.execute(sql, values)
        db.execute(sql, ("SYS", "D1", name))
        assert db.execute(f"SELECT requirement_id,domain_id,{column} FROM {table}").fetchall() == [
            ("SYS", "D1", name),
        ]


def test_area_business_name_is_unique(vocabulary):
    _, database = vocabulary
    with connection(database) as db:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            db.execute("INSERT INTO domain_areas(id,business_name,definition) VALUES(?,?,?)",
                       ("D3", "Sales", "Another meaning."))


@pytest.mark.parametrize("table,existing", [("domain_entities", "E1"), ("domain_actions", "A1")])
def test_name_identity_is_unique_across_areas_and_names_are_case_sensitive(vocabulary, table, existing):
    _, database = vocabulary
    sql = f"INSERT INTO {table}(id,domain_id,business_name,code_name,definition) VALUES(?,?,?,?,?)"
    with connection(database) as db:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            db.execute(sql, (existing, "D2", "Different", "different", "Definition."))
        business, code = ("account", "account") if table == "domain_entities" else ("open", "Open")
        db.execute(sql, ("N3", "D1", business, code, "Definition."))
        for index in (0, 2, 3):
            values = ["N4", "D1", "Other", "other", "Definition."]
            values[index] = "\u00a0" + values[index] + "\t"
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                db.execute(sql, values)


def test_historical_request_replays_original_receipt_after_migration(vocabulary):
    config, database = vocabulary
    request = json.loads((Path(__file__).parent / "fixtures/historical_request.json").read_text())
    with connection(database) as db:
        before = list(db.iterdump())
    assert invoke(config, "apply", request) == (
        0, {"status": "applied", "revision": 1, "replayed": True},
    )
    with connection(database) as db:
        assert list(db.iterdump()) == before


def test_concurrent_public_migrations_serialize_without_repeating_ddl(legacy, monkeypatch):
    config, database = legacy
    before = old_rows(database)
    connect = sqlite3.connect
    observations = []
    class Observed(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql == "PRAGMA user_version":
                assert self.in_transaction
                observations.append("version inside transaction")
            return result
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: connect(*a, **k, factory=Observed))
    barrier = Barrier(2)
    results, errors = [], []
    def run():
        try:
            barrier.wait(timeout=3)
            results.append(invoke(config))
        except BaseException as exc:
            errors.append(exc)
    workers = [Thread(target=run) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert sorted(results, key=lambda item: item[1].get("from_version", 0)) == [
        (0, {"status": "migrated", "from_version": 1, "to_version": 2, "changed": True}),
        (0, {"status": "migrated", "from_version": 2, "to_version": 2, "changed": False}),
    ]
    assert observations == ["version inside transaction", "version inside transaction"]
    monkeypatch.undo()
    assert old_rows(database) == before
    with connection(database) as db:
        assert_empty_dictionary(db)
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
